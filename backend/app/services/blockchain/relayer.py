"""Blockchain transaction relayer and secure signer abstraction.

Security Boundary:
- Hard-fails on Base Mainnet transaction submission (MainnetSubmissionBlockedError).
- Enforces contract and operation allowlists.
- Strictly rejects raw arbitrary calldata.
- Zero private key leakage: private keys never touch DB, Redis, or logs.
"""

from abc import ABC, abstractmethod
import logging
from typing import Any
from eth_account import Account
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from app.models.base import utc_now
from app.models.blockchain import (
    AttemptStatus,
    BlockchainTransaction,
    BlockchainTransactionAttempt,
    BlockchainTransactionIntent,
    IntentStatus,
    TxLifecycleStatus,
)
from app.services.blockchain.abi import (
    ESCROW_ABI,
    REPUTATION_REGISTRY_ABI,
    RESULT_NOTARY_ABI,
    normalize_hex,
)
from app.services.blockchain.config import (
    ALLOWED_OPERATIONS,
    CHAIN_ID_BASE_MAINNET,
    ChainConfig,
    get_chain_config,
)
from app.services.blockchain.signer import (
    ANVIL_DEFAULT_ADDRESS,
    BLOCKED_DEVELOPMENT_ADDRESSES,
    LocalAccountSigner,
    LocalDevelopmentSigner,
    MockSigner,
    ProductionSigner,
    SignerEnvironment,
    SignerFactory,
    SignerType,
    TestnetAccountSigner,
    TransactionSigner,
)
from app.services.blockchain.errors import (
    ArbitraryCalldataBlockedError,
    ChainMismatchError,
    ContractMismatchError,
    MainnetSubmissionBlockedError,
    SecurityConfigurationError,
    SignerNetworkMismatchError,
    TransactionRevertedError,
    UnauthorizedOperationError,
)
from app.services.blockchain.gas_estimator import GasEstimator
from app.services.blockchain.nonce_manager import NonceManager
from app.services.blockchain.rpc_client import BlockchainRpcClient

logger = logging.getLogger(__name__)


class BlockchainRelayer:
    """Manages transaction preparation, gas bumping, signing, broadcast, and receipt tracking."""

    def __init__(
        self,
        config: ChainConfig,
        rpc_client: BlockchainRpcClient,
        nonce_manager: NonceManager,
        gas_estimator: GasEstimator,
        signer: TransactionSigner,
    ) -> None:
        self.config = config
        self.chain_id = config.chain_id
        self.rpc_client = rpc_client
        self.nonce_manager = nonce_manager
        self.gas_estimator = gas_estimator
        self.signer = signer
        self._w3 = Web3()
        self._contract = self._w3.eth.contract(abi=ESCROW_ABI)
        self._notary_contract = self._w3.eth.contract(abi=RESULT_NOTARY_ABI)
        self._reputation_contract = self._w3.eth.contract(abi=REPUTATION_REGISTRY_ABI)

    async def verify_startup(self) -> None:
        """Performs pre-flight startup checks. Raises if any invariant fails.

        Checks (in order):
        1. RPC chain ID == configured chain ID (fail-closed on mismatch).
        2. Signer health_check passes.
        3. LocalAccountSigner is not used on Mainnet (defense-in-depth).
        4. Configured USDC address is in the chain allowlist.
        5. Configured Escrow contract address is non-zero.

        Raises:
            RuntimeError: On any failed check. Operation must not begin.
        """
        from app.services.blockchain.config import (
            BASE_MAINNET_USDC,
            BASE_SEPOLIA_USDC,
            CHAIN_ID_BASE_MAINNET,
            CHAIN_ID_BASE_SEPOLIA,
            CHAIN_ID_ANVIL,
        )

        # 1. Chain ID verification: RPC must report the expected chain ID
        try:
            actual = await self.rpc_client.verify_chain_id()
        except Exception as exc:
            raise RuntimeError(
                f"[PREFLIGHT FAIL] Chain ID verification failed: {exc}"
            ) from exc
        if actual != self.chain_id:
            raise RuntimeError(
                f"[PREFLIGHT FAIL] Chain ID mismatch: configured={self.chain_id}, "
                f"RPC reported={actual}. Startup aborted."
            )

        # 2. LocalAccountSigner must not operate on Mainnet (defense-in-depth)
        if isinstance(self.signer, LocalAccountSigner) and self.chain_id == CHAIN_ID_BASE_MAINNET:
            raise RuntimeError(
                "[PREFLIGHT FAIL] SECURITY VIOLATION: LocalAccountSigner cannot be used "
                "on Base Mainnet (chain_id=8453). Deploy a KMS/HSM/MPC production signer."
            )

        # 3. Hard-fail immediately if configured for Base Mainnet
        if self.chain_id == CHAIN_ID_BASE_MAINNET:
            raise RuntimeError(
                "[PREFLIGHT FAIL] SECURITY VIOLATION: Base Mainnet relayer startup is blocked."
            )

        # 4. Signer network compatibility & verification
        try:
            self.signer.verify_network_compatibility(self.chain_id)
        except Exception as exc:
            raise RuntimeError(
                f"[PREFLIGHT FAIL] Signer network compatibility check failed for chain {self.chain_id}: {exc}"
            ) from exc

        # 5. Signer health
        if hasattr(self.signer, "check_health_status"):
            status, reason = self.signer.check_health_status()
            status_val = getattr(status, "value", str(status))
            if status_val != "HEALTHY":
                raise RuntimeError(
                    f"[PREFLIGHT FAIL] Signer health_check() returned False on chain {self.chain_id} [{status_val}]: {reason}"
                )
        elif not self.signer.health_check():
            raise RuntimeError(
                f"[PREFLIGHT FAIL] Signer health_check() returned False on chain {self.chain_id}."
            )

        # 6. Hard block Anvil/Hardhat default accounts on non-local networks for testnet/prod signers
        if self.chain_id != CHAIN_ID_ANVIL and isinstance(self.signer, (TestnetAccountSigner, ProductionSigner)):
            signer_addr = self.signer.get_address().lower()
            if signer_addr in BLOCKED_DEVELOPMENT_ADDRESSES:
                raise RuntimeError(
                    f"[PREFLIGHT FAIL] SECURITY VIOLATION: Default test account {signer_addr} "
                    f"is forbidden on chain {self.chain_id}."
                )

        # 4. USDC allowlist verification
        usdc_allowlist: dict[int, str] = {
            CHAIN_ID_BASE_MAINNET: BASE_MAINNET_USDC.lower(),
            CHAIN_ID_BASE_SEPOLIA: BASE_SEPOLIA_USDC.lower(),
            # Anvil uses a mock; no canonical USDC address check
        }
        if self.chain_id in usdc_allowlist:
            configured_usdc = self.config.token_address.lower()
            expected_usdc = usdc_allowlist[self.chain_id]
            if configured_usdc != expected_usdc:
                raise RuntimeError(
                    f"[PREFLIGHT FAIL] USDC address mismatch on chain {self.chain_id}: "
                    f"configured='{configured_usdc}', expected='{expected_usdc}'. "
                    "Do not use an arbitrary ERC-20 token as payment."
                )

        # 5. Contract address non-zero
        escrow_addr = self.config.contract_addresses.get("Escrow", "")
        zero_addr = "0x" + "0" * 40
        if not escrow_addr or escrow_addr.lower() == zero_addr.lower():
            raise RuntimeError(
                f"[PREFLIGHT FAIL] Escrow contract address is zero/unset on chain {self.chain_id}. "
                "Configure ESCROW_CONTRACT_BASE_SEPOLIA or ESCROW_CONTRACT_BASE_MAINNET."
            )

        logger.info(
            "[PREFLIGHT OK] Relayer startup verified: chain=%d (%s), signer=%s, "
            "contract=%s, usdc=%s",
            self.chain_id,
            self.config.network_name,
            self.signer.get_address(),
            escrow_addr,
            self.config.token_address,
        )

    def _encode_calldata(self, operation: str, parameters: dict[str, Any]) -> str:
        """Encodes function calldata strictly from ABI. Arbitrary calldata is rejected."""
        if operation not in ALLOWED_OPERATIONS:
            raise UnauthorizedOperationError(f"Unauthorized operation: {operation}")

        if operation == "notarizeResult":
            contract_fn = getattr(self._notary_contract.functions, operation, None)
        elif operation == "registerReputationEvent":
            contract_fn = getattr(self._reputation_contract.functions, operation, None)
        else:
            contract_fn = getattr(self._contract.functions, operation, None)

        if not contract_fn:
            raise UnauthorizedOperationError(f"Operation {operation} not found in ABI")

        try:
            # Format arguments according to function requirements
            if operation in ("createEscrow", "createAndFundEscrow"):
                ref_id = bytes.fromhex(parameters["referenceId"].removeprefix("0x"))
                beneficiary = Web3.to_checksum_address(parameters["beneficiary"])
                amount = int(parameters["amount"])
                deadline = int(parameters["executionDeadline"])
                salt = int(parameters["salt"]) if isinstance(parameters["salt"], int) else int(parameters["salt"], 16)
                call_data = contract_fn(ref_id, beneficiary, amount, deadline, salt)._encode_transaction_data()
            elif operation in ("fundEscrow", "lockEscrow", "releaseEscrow", "refundEscrow"):
                escrow_id = bytes.fromhex(parameters["escrowId"].removeprefix("0x"))
                call_data = contract_fn(escrow_id)._encode_transaction_data()
            elif operation == "disputeEscrow":
                escrow_id = bytes.fromhex(parameters["escrowId"].removeprefix("0x"))
                reason_hash = bytes.fromhex(parameters["reasonHash"].removeprefix("0x"))
                call_data = contract_fn(escrow_id, reason_hash)._encode_transaction_data()
            elif operation == "resolveDispute":
                escrow_id = bytes.fromhex(parameters["escrowId"].removeprefix("0x"))
                ben_amt = int(parameters["beneficiaryAmount"])
                client_amt = int(parameters["clientRefundAmount"])
                call_data = contract_fn(escrow_id, ben_amt, client_amt)._encode_transaction_data()
            elif operation in ("pause", "unpause"):
                call_data = contract_fn()._encode_transaction_data()
            elif operation == "notarizeResult":
                exec_id = bytes.fromhex(parameters["executionId"].removeprefix("0x"))
                res_hash = bytes.fromhex(parameters["resultHash"].removeprefix("0x"))
                raw_artifact = parameters.get("artifactCommitment") or ("0x" + "00" * 32)
                artifact_commit = bytes.fromhex(raw_artifact.removeprefix("0x"))
                call_data = contract_fn(exec_id, res_hash, artifact_commit)._encode_transaction_data()
            elif operation == "registerReputationEvent":
                agent_id = bytes.fromhex(parameters["agentId"].removeprefix("0x"))
                exec_id = bytes.fromhex(parameters["executionId"].removeprefix("0x"))
                version_id = bytes.fromhex(parameters["agentVersionId"].removeprefix("0x"))
                outcome_type = int(parameters["outcomeType"])
                raw_res_hash = parameters.get("resultHash") or ("0x" + "00" * 32)
                res_hash = bytes.fromhex(raw_res_hash.removeprefix("0x"))
                raw_evidence_hash = parameters.get("evidenceHash") or ("0x" + "00" * 32)
                evidence_hash = bytes.fromhex(raw_evidence_hash.removeprefix("0x"))
                call_data = contract_fn(agent_id, exec_id, version_id, outcome_type, res_hash, evidence_hash)._encode_transaction_data()
            else:
                raise ArbitraryCalldataBlockedError(f"Unsupported function signature: {operation}")

            return call_data
        except (KeyError, ValueError) as exc:
            raise ArbitraryCalldataBlockedError(f"Invalid parameters for {operation}: {exc}") from exc

    async def submit_intent(
        self,
        session: AsyncSession,
        intent: BlockchainTransactionIntent,
    ) -> BlockchainTransaction:
        """Executes full transaction submission lifecycle for an intent."""
        # Hard security gating
        if self.chain_id == CHAIN_ID_BASE_MAINNET:
            raise MainnetSubmissionBlockedError(
                "HARD SECURITY GATING: Base Mainnet transaction submission is disabled in Phase 6.2A"
            )

        if not self.config.allow_transactions:
            raise MainnetSubmissionBlockedError(
                f"Transaction submission disabled for network {self.config.network_name}"
            )

        # Invariant: Intent chain binding must strictly match relayer chain
        if intent.chain_id != self.chain_id:
            raise ChainMismatchError(
                f"Intent chain_id ({intent.chain_id}) does not match relayer chain_id ({self.chain_id})"
            )

        # Signer/network compatibility invariant
        self.signer.verify_network_compatibility(self.chain_id)

        # Allowlist checks
        if intent.operation not in ALLOWED_OPERATIONS:
            raise UnauthorizedOperationError(f"Unauthorized operation: {intent.operation}")

        allowed_addrs = [a.lower() for a in self.config.contract_addresses.values()]
        if intent.target_contract.lower() not in allowed_addrs:
            raise ContractMismatchError(f"Contract {intent.target_contract} not in allowlist")

        from_address = self.signer.get_address()
        to_address = Web3.to_checksum_address(intent.target_contract)

        # Reserve persistent nonce
        nonce = await self.nonce_manager.reserve_nonce(session, from_address)

        # Estimate EIP-1559 fees
        max_fee, priority_fee = await self.gas_estimator.estimate_eip1559_fees()

        # Encode calldata
        calldata = self._encode_calldata(intent.operation, intent.parameters)

        tx_dict = {
            "chainId": self.chain_id,
            "from": from_address,
            "to": to_address,
            "nonce": nonce,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": priority_fee,
            "gas": self.gas_estimator.default_gas_limit,
            "data": calldata,
            "value": 0,
        }

        # Sign transaction
        signed_raw_hex = self.signer.sign_transaction(tx_dict)

        # Compute tx hash
        tx_hash = normalize_hex(Web3.keccak(hexstr=signed_raw_hex).hex())

        # Persist transaction record
        tx_record = BlockchainTransaction(
            intent_id=intent.id,
            chain_id=self.chain_id,
            from_address=from_address.lower(),
            to_address=to_address.lower(),
            nonce=nonce,
            status=TxLifecycleStatus.SUBMITTED,
            current_tx_hash=tx_hash,
            submitted_at=utc_now(),
            attempts_count=1,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(tx_record)
        await session.flush()

        # Persist attempt record
        attempt_record = BlockchainTransactionAttempt(
            transaction_id=tx_record.id,
            attempt_number=1,
            tx_hash=tx_hash,
            nonce=nonce,
            max_fee_per_gas=max_fee,
            max_priority_fee_per_gas=priority_fee,
            gas_limit=self.gas_estimator.default_gas_limit,
            raw_signed_tx=signed_raw_hex,
            status=AttemptStatus.SUBMITTED,
            submitted_at=utc_now(),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(attempt_record)

        # Advance intent status
        intent.status = IntentStatus.SUBMITTED
        intent.updated_at = utc_now()
        await session.flush()

        # Broadcast via RPC
        try:
            await self.rpc_client.send_raw_transaction(signed_raw_hex)
            logger.info(
                "Broadcasted tx %s (nonce=%d) for intent %s on chain %d",
                tx_hash,
                nonce,
                intent.id,
                self.chain_id,
            )
        except Exception as exc:
            logger.error("Failed to broadcast tx %s: %s", tx_hash, exc)
            attempt_record.status = AttemptStatus.FAILED
            attempt_record.error_message = str(exc)
            tx_record.status = TxLifecycleStatus.FAILED
            tx_record.error_message = str(exc)
            intent.status = IntentStatus.FAILED
            intent.error_message = str(exc)
            await session.flush()
            raise

        return tx_record

    async def bump_and_replace_transaction(
        self,
        session: AsyncSession,
        transaction: BlockchainTransaction,
    ) -> BlockchainTransactionAttempt:
        """Submits a replacement transaction with bumped gas fees for a stuck transaction."""
        latest_attempt_stmt = (
            sa.select(BlockchainTransactionAttempt)
            .where(BlockchainTransactionAttempt.transaction_id == transaction.id)
            .order_by(BlockchainTransactionAttempt.attempt_number.desc())
            .limit(1)
        )
        latest_attempt = (await session.execute(latest_attempt_stmt)).scalar_one_or_none()
        if not latest_attempt:
            raise RuntimeError(f"No previous attempt found for transaction {transaction.id}")

        new_max_fee, new_priority_fee = self.gas_estimator.bump_gas_fees(
            old_max_fee=latest_attempt.max_fee_per_gas,
            old_priority_fee=latest_attempt.max_priority_fee_per_gas,
            bump_percent=12.5,
        )

        intent = None
        if transaction.intent_id:
            intent_stmt = sa.select(BlockchainTransactionIntent).where(
                BlockchainTransactionIntent.id == transaction.intent_id
            )
            intent = (await session.execute(intent_stmt)).scalar_one_or_none()

        calldata = self._encode_calldata(intent.operation, intent.parameters) if intent else "0x"

        tx_dict = {
            "chainId": self.chain_id,
            "from": Web3.to_checksum_address(transaction.from_address),
            "to": Web3.to_checksum_address(transaction.to_address),
            "nonce": transaction.nonce,
            "maxFeePerGas": new_max_fee,
            "maxPriorityFeePerGas": new_priority_fee,
            "gas": latest_attempt.gas_limit,
            "data": calldata,
            "value": 0,
        }

        # Signer/network compatibility invariant
        self.signer.verify_network_compatibility(self.chain_id)

        signed_raw_hex = self.signer.sign_transaction(tx_dict)
        new_tx_hash = normalize_hex(Web3.keccak(hexstr=signed_raw_hex).hex())

        new_attempt_number = latest_attempt.attempt_number + 1
        new_attempt = BlockchainTransactionAttempt(
            transaction_id=transaction.id,
            attempt_number=new_attempt_number,
            tx_hash=new_tx_hash,
            nonce=transaction.nonce,
            max_fee_per_gas=new_max_fee,
            max_priority_fee_per_gas=new_priority_fee,
            gas_limit=latest_attempt.gas_limit,
            raw_signed_tx=signed_raw_hex,
            status=AttemptStatus.SUBMITTED,
            submitted_at=utc_now(),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(new_attempt)

        latest_attempt.status = AttemptStatus.REPLACED
        latest_attempt.updated_at = utc_now()

        transaction.current_tx_hash = new_tx_hash
        transaction.attempts_count = new_attempt_number
        transaction.submitted_at = new_attempt.submitted_at
        transaction.updated_at = utc_now()
        await session.flush()

        await self.rpc_client.send_raw_transaction(signed_raw_hex)
        logger.info(
            "Replacement tx %s submitted (attempt %d, nonce %d) bumping fee from %d to %d",
            new_tx_hash,
            new_attempt_number,
            transaction.nonce,
            latest_attempt.max_fee_per_gas,
            new_max_fee,
        )
        return new_attempt

    async def poll_and_update_receipt(
        self,
        session: AsyncSession,
        transaction: BlockchainTransaction,
    ) -> bool:
        """Checks for on-chain receipt across all attempts of the transaction and advances confirmation lifecycle."""
        attempts_stmt = (
            sa.select(BlockchainTransactionAttempt)
            .where(BlockchainTransactionAttempt.transaction_id == transaction.id)
            .order_by(BlockchainTransactionAttempt.attempt_number.desc())
        )
        attempts = (await session.execute(attempts_stmt)).scalars().all()

        winning_attempt: BlockchainTransactionAttempt | None = None
        winning_receipt: dict[str, Any] | None = None

        if attempts:
            for att in attempts:
                try:
                    receipt = await self.rpc_client.get_transaction_receipt(att.tx_hash)
                    if receipt:
                        winning_attempt = att
                        winning_receipt = receipt
                        break
                except Exception as exc:
                    logger.warning("Error querying receipt for attempt %s: %s", att.tx_hash, exc)
        elif transaction.current_tx_hash:
            try:
                winning_receipt = await self.rpc_client.get_transaction_receipt(transaction.current_tx_hash)
            except Exception as exc:
                logger.warning("Error querying receipt for tx %s: %s", transaction.current_tx_hash, exc)

        if not winning_receipt:
            return False  # Still pending or unmined

        block_number = int(winning_receipt["blockNumber"], 16) if isinstance(winning_receipt["blockNumber"], str) else int(winning_receipt["blockNumber"])
        block_hash = str(winning_receipt["blockHash"]).lower()
        gas_used = int(winning_receipt["gasUsed"], 16) if isinstance(winning_receipt["gasUsed"], str) else int(winning_receipt["gasUsed"])
        status = int(winning_receipt["status"], 16) if isinstance(winning_receipt["status"], str) else int(winning_receipt["status"])
        effective_gas_price = int(winning_receipt.get("effectiveGasPrice", 0), 16) if isinstance(winning_receipt.get("effectiveGasPrice", 0), str) else int(winning_receipt.get("effectiveGasPrice", 0))

        now = utc_now()
        confirmed_hash = winning_attempt.tx_hash if winning_attempt else transaction.current_tx_hash
        transaction.current_tx_hash = confirmed_hash

        # Async-safe intent loading
        intent = None
        if transaction.intent_id:
            intent_stmt = sa.select(BlockchainTransactionIntent).where(
                BlockchainTransactionIntent.id == transaction.intent_id
            )
            intent = (await session.execute(intent_stmt)).scalar_one_or_none()

        if status == 1:
            transaction.status = TxLifecycleStatus.CONFIRMED
            transaction.confirmed_at = now
            if intent:
                intent.status = IntentStatus.CONFIRMED
                intent.updated_at = now

            if winning_attempt:
                winning_attempt.status = AttemptStatus.CONFIRMED
                winning_attempt.block_number = block_number
                winning_attempt.block_hash = block_hash
                winning_attempt.gas_used = gas_used
                winning_attempt.effective_gas_price = effective_gas_price
                winning_attempt.receipt_status = status
                winning_attempt.confirmed_at = now
                winning_attempt.updated_at = now

            # Mark all other attempts as REPLACED/DROPPED
            for att in attempts:
                if winning_attempt and att.id != winning_attempt.id and att.status in (AttemptStatus.SUBMITTED, AttemptStatus.PENDING):
                    att.status = AttemptStatus.REPLACED
                    att.updated_at = now

            logger.info("Transaction %s CONFIRMED in block %d", confirmed_hash, block_number)
        else:
            transaction.status = TxLifecycleStatus.FAILED
            transaction.failed_at = now
            transaction.error_message = "Transaction execution reverted on-chain"
            if intent:
                intent.status = IntentStatus.FAILED
                intent.error_message = transaction.error_message
                intent.updated_at = now

            if winning_attempt:
                winning_attempt.status = AttemptStatus.FAILED
                winning_attempt.receipt_status = status
                winning_attempt.error_message = transaction.error_message
                winning_attempt.block_number = block_number
                winning_attempt.block_hash = block_hash
                winning_attempt.gas_used = gas_used
                winning_attempt.updated_at = now

            for att in attempts:
                if winning_attempt and att.id != winning_attempt.id and att.status in (AttemptStatus.SUBMITTED, AttemptStatus.PENDING):
                    att.status = AttemptStatus.DROPPED
                    att.updated_at = now

            logger.error("Transaction %s REVERTED in block %d", confirmed_hash, block_number)

        transaction.updated_at = now
        await session.flush()
        return True
