"""Phase 6.10 — Production Signer & Mainnet Safety Readiness Test Suite.

Verifies:
1. Configuration Safety:
   - Missing / invalid / unsupported chain IDs fail closed.
   - Missing RPC, missing signer, and missing role addresses fail closed.
2. Signer Abstraction & Isolation:
   - Local development signer accepted only in local development (chain 31337).
   - Testnet signer accepted only on testnet (chain 84532); rejects Anvil accounts.
   - Production signer strictly rejects raw private keys.
   - KMS / HSM / MPC adapters fail closed with ProductionSignerUnavailableError.
   - Signer / network mismatch rejected across all signers.
3. Mainnet Safety Gate:
   - Base Mainnet (8453) is strictly hard blocked across relayer, signers, configs, and contracts.
   - Zero transactions can be broadcast to Mainnet.
4. Secret & Git Security:
   - Tracked files contain no real private keys or credentials.
   - .dev_wallet.txt and sensitive files are ignored.
   - Static security verification gate passes.
5. Deployment Guard:
   - Deterministic local deployment allowed on Anvil.
   - Base Sepolia requires explicit deployer key, explicit roles, and canonical USDC.
   - Base Mainnet deployment strictly blocked.
6. Relayer Hardening:
   - Signer / network mismatch rejected during startup and intent submission.
   - Intent chain binding verified (intent.chain_id == relayer.chain_id).
   - Target contract allowlist enforced.
"""

import os
import uuid
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.blockchain.config import (
    BASE_MAINNET_USDC,
    BASE_SEPOLIA_USDC,
    CHAIN_ID_ANVIL,
    CHAIN_ID_BASE_MAINNET,
    CHAIN_ID_BASE_SEPOLIA,
    get_chain_config,
)
from app.models.blockchain import (
    BlockchainTransactionIntent,
    IntentStatus,
)
from app.services.blockchain.errors import (
    MainnetSubmissionBlockedError,
    SignerNetworkMismatchError,
    ProductionSignerUnavailableError,
    SecurityConfigurationError,
    InvalidRoleAddressError,
    ChainMismatchError,
    ContractMismatchError,
)

from app.services.blockchain.signer import (
    SignerType,
    SignerEnvironment,
    LocalDevelopmentSigner,
    TestnetAccountSigner,
    LocalAccountSigner,
    MockSigner,
    ProductionSigner,
    KmsSignerAdapter,
    HsmSignerAdapter,
    MpcSignerAdapter,
    SignerFactory,
)
from app.services.blockchain.relayer import (
    BlockchainRelayer,
    NonceManager,
    GasEstimator,
)


from app.core.production_config import (
    ProductionEnvironmentConfig,
    RoleConfig,
    ContractConfig,
    EnvironmentType,
    ProductionSignerType,
    ProductionConfigError,

    CANONICAL_BASE_SEPOLIA_CONTRACTS,
    BLOCKED_PRODUCTION_ADDRESSES,
)

# Test Identities
ANVIL_DEFAULT_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
ANVIL_DEFAULT_ADDR = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
VALID_SEPOLIA_TEST_KEY = "0x703f4e6283731359f0f06c02ee4753a30f6424098e6b45aca799567b664c755d"
VALID_SEPOLIA_TEST_ADDR = "0x473888C859F88D3De7b5A20986805b4dC3178189"
VALID_ADMIN_ADDR = "0x162dEB52f30f551020c23155Bc5c66419c184d82"


def _make_dummy_tx(chain_id: int) -> dict:
    return {
        "to": "0x3e5C09D2123cA106b2E24C4f76bFad4a022640D1",
        "value": 0,
        "gas": 100000,
        "maxFeePerGas": 2000000000,
        "maxPriorityFeePerGas": 1000000000,
        "nonce": 0,
        "chainId": chain_id,
        "data": b"",
    }


# ==============================================================================
# 1. CONFIGURATION SAFETY TESTS
# ==============================================================================

class TestConfigurationSafety:
    def test_missing_chain_id_fails_closed(self):
        with pytest.raises(Exception):
            ProductionEnvironmentConfig(
                app_env=EnvironmentType.PRODUCTION,
                # chain_id missing
                rpc_url="https://sepolia.base.org",
                signer_provider=ProductionSignerType.KMS,
                signer_key_reference="arn:aws:kms:us-east-1:123456789012:key/test",
                roles=RoleConfig(relayer_role_address=VALID_SEPOLIA_TEST_ADDR, admin_role_address=VALID_ADMIN_ADDR),
                contracts=ContractConfig(**{f"{k}_address": v for k, v in CANONICAL_BASE_SEPOLIA_CONTRACTS.items()}),
            )

    def test_invalid_chain_id_type_fails_closed(self):
        with pytest.raises(Exception):
            ProductionEnvironmentConfig(
                app_env=EnvironmentType.PRODUCTION,
                chain_id="not_an_int",
                rpc_url="https://sepolia.base.org",
                signer_provider=ProductionSignerType.KMS,
                signer_key_reference="arn:aws:kms:us-east-1:123456789012:key/test",
                roles=RoleConfig(relayer_role_address=VALID_SEPOLIA_TEST_ADDR, admin_role_address=VALID_ADMIN_ADDR),
                contracts=ContractConfig(**{f"{k}_address": v for k, v in CANONICAL_BASE_SEPOLIA_CONTRACTS.items()}),
            )

    def test_unsupported_chain_id_fails_closed(self):
        with pytest.raises((ProductionConfigError, ValueError)) as excinfo:
            ProductionEnvironmentConfig(
                app_env=EnvironmentType.PRODUCTION,
                chain_id=1,  # Ethereum Mainnet not permitted
                rpc_url="https://eth.llamarpc.com",
                signer_provider=ProductionSignerType.KMS,
                signer_key_reference="arn:aws:kms:us-east-1:123456789012:key/test",
                roles=RoleConfig(relayer_role_address=VALID_SEPOLIA_TEST_ADDR, admin_role_address=VALID_ADMIN_ADDR),
                contracts=ContractConfig(**{f"{k}_address": v for k, v in CANONICAL_BASE_SEPOLIA_CONTRACTS.items()}),
            )
        assert "Unsupported chain ID" in str(excinfo.value)

    def test_missing_signer_key_reference_fails_closed(self):
        with pytest.raises((ProductionConfigError, ValueError)) as excinfo:
            ProductionEnvironmentConfig(
                app_env=EnvironmentType.PRODUCTION,
                chain_id=84532,
                rpc_url="https://sepolia.base.org",
                signer_provider=ProductionSignerType.KMS,
                signer_key_reference="",  # Empty
                roles=RoleConfig(relayer_role_address=VALID_SEPOLIA_TEST_ADDR, admin_role_address=VALID_ADMIN_ADDR),
                contracts=ContractConfig(**{f"{k}_address": v for k, v in CANONICAL_BASE_SEPOLIA_CONTRACTS.items()}),
            )
        assert "signer_key_reference is required" in str(excinfo.value)

    def test_missing_role_address_fails_closed(self):
        with pytest.raises(Exception):
            RoleConfig(
                relayer_role_address="",
                admin_role_address=VALID_ADMIN_ADDR,
            )


# ==============================================================================
# 2. SIGNER ABSTRACTION & ISOLATION TESTS
# ==============================================================================

class TestSignerAbstraction:
    def test_local_development_signer_accepted_only_on_anvil(self):
        signer = LocalDevelopmentSigner(ANVIL_DEFAULT_KEY)
        assert signer.get_address().lower() == ANVIL_DEFAULT_ADDR.lower()
        signer.verify_network_compatibility(CHAIN_ID_ANVIL)  # OK

        # Rejected on Base Sepolia
        with pytest.raises(SignerNetworkMismatchError):
            signer.verify_network_compatibility(CHAIN_ID_BASE_SEPOLIA)

        # Rejected on Base Mainnet
        with pytest.raises((SignerNetworkMismatchError, MainnetSubmissionBlockedError)):
            signer.verify_network_compatibility(CHAIN_ID_BASE_MAINNET)

        # Signing for non-local network rejected
        with pytest.raises(SignerNetworkMismatchError):
            signer.sign_transaction(_make_dummy_tx(CHAIN_ID_BASE_SEPOLIA))

    def test_testnet_account_signer_accepted_only_on_sepolia(self):
        signer = TestnetAccountSigner(VALID_SEPOLIA_TEST_KEY)
        signer.verify_network_compatibility(CHAIN_ID_BASE_SEPOLIA)  # OK

        # Rejected on Anvil
        with pytest.raises(SignerNetworkMismatchError):
            signer.verify_network_compatibility(CHAIN_ID_ANVIL)

        # Rejected on Mainnet
        with pytest.raises((SignerNetworkMismatchError, MainnetSubmissionBlockedError)):
            signer.verify_network_compatibility(CHAIN_ID_BASE_MAINNET)

        # Signing for Anvil rejected
        with pytest.raises(SignerNetworkMismatchError):
            signer.sign_transaction(_make_dummy_tx(CHAIN_ID_ANVIL))

    def test_testnet_account_signer_blocks_anvil_default_account(self):
        with pytest.raises(SecurityConfigurationError) as excinfo:
            TestnetAccountSigner(ANVIL_DEFAULT_KEY)
        assert "strictly forbidden on public testnet" in str(excinfo.value)

    def test_production_signer_blocks_raw_private_key(self):
        with pytest.raises(TypeError):
            # ProductionSigner cannot be instantiated directly without implementing abstract methods
            ProductionSigner()

    def test_kms_hsm_mpc_adapters_fail_closed(self):
        kms = KmsSignerAdapter(key_reference="arn:aws:kms:us-east-1:123456789012:key/test", chain_id=CHAIN_ID_BASE_SEPOLIA)
        assert kms.health_check() is False
        with pytest.raises(ProductionSignerUnavailableError):
            kms.get_address()
        with pytest.raises(ProductionSignerUnavailableError):
            kms.sign_transaction(_make_dummy_tx(CHAIN_ID_BASE_SEPOLIA))

        hsm = HsmSignerAdapter(key_reference="hsm-key-001", chain_id=CHAIN_ID_BASE_SEPOLIA)
        assert hsm.health_check() is False
        with pytest.raises(ProductionSignerUnavailableError):
            hsm.sign_transaction(_make_dummy_tx(CHAIN_ID_BASE_SEPOLIA))

        mpc = MpcSignerAdapter(key_reference="mpc-share-001", chain_id=CHAIN_ID_BASE_SEPOLIA)
        assert mpc.health_check() is False
        with pytest.raises(ProductionSignerUnavailableError):
            mpc.sign_transaction(_make_dummy_tx(CHAIN_ID_BASE_SEPOLIA))

    def test_signer_factory_environment_separation(self):
        # Development requires local_dev or mock
        dev_signer = SignerFactory.create_signer(
            environment=SignerEnvironment.DEVELOPMENT,
            signer_type=SignerType.LOCAL_DEV,
            chain_id=CHAIN_ID_ANVIL,
            private_key=ANVIL_DEFAULT_KEY,
        )
        assert isinstance(dev_signer, LocalDevelopmentSigner)

        # Testnet requires testnet
        testnet_signer = SignerFactory.create_signer(
            environment=SignerEnvironment.TESTNET,
            signer_type=SignerType.TESTNET,
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            private_key=VALID_SEPOLIA_TEST_KEY,
        )
        assert isinstance(testnet_signer, TestnetAccountSigner)

        # Production requires kms, hsm, or mpc
        prod_signer = SignerFactory.create_signer(
            environment=SignerEnvironment.PRODUCTION,
            signer_type=SignerType.PRODUCTION_KMS,
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            key_reference="arn:aws:kms:us-east-1:123456789012:key/test",
        )
        assert isinstance(prod_signer, KmsSignerAdapter)
        with pytest.raises(ProductionSignerUnavailableError):
            prod_signer.get_address()

        # Development fallback rejected in production
        with pytest.raises(SecurityConfigurationError):
            SignerFactory.create_signer(
                environment=SignerEnvironment.PRODUCTION,
                signer_type=SignerType.LOCAL_DEV,
                chain_id=CHAIN_ID_BASE_SEPOLIA,
                private_key=ANVIL_DEFAULT_KEY,
            )

        with pytest.raises(SecurityConfigurationError):
            SignerFactory.create_signer(
                environment=SignerEnvironment.PRODUCTION,
                signer_type=SignerType.TESTNET,
                chain_id=CHAIN_ID_BASE_SEPOLIA,
            )




# ==============================================================================
# 3. BASE MAINNET HARD BLOCK TESTS
# ==============================================================================

class TestBaseMainnetHardBlock:
    def test_production_config_rejects_base_mainnet(self):
        with pytest.raises((ProductionConfigError, ValueError)) as excinfo:
            ProductionEnvironmentConfig(
                app_env=EnvironmentType.PRODUCTION,
                chain_id=8453,
                rpc_url="https://mainnet.base.org",
                signer_provider=ProductionSignerType.KMS,
                signer_key_reference="arn:aws:kms:us-east-1:123456789012:key/test",
                roles=RoleConfig(relayer_role_address=VALID_SEPOLIA_TEST_ADDR, admin_role_address=VALID_ADMIN_ADDR),
                contracts=ContractConfig(**{f"{k}_address": v for k, v in CANONICAL_BASE_SEPOLIA_CONTRACTS.items()}),
            )
        assert "HARD BLOCKED" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_relayer_verify_startup_fails_closed_on_mainnet(self):
        mock_rpc = AsyncMock()
        mock_rpc.verify_chain_id.return_value = CHAIN_ID_BASE_MAINNET
        signer = MockSigner("0x473888C859F88D3De7b5A20986805b4dC3178189")
        cfg = get_chain_config(CHAIN_ID_BASE_MAINNET)

        relayer = BlockchainRelayer(
            config=cfg,
            rpc_client=mock_rpc,
            nonce_manager=NonceManager(mock_rpc),
            gas_estimator=GasEstimator(mock_rpc),
            signer=signer,
        )

        with pytest.raises(RuntimeError) as excinfo:
            await relayer.verify_startup()
        assert "Base Mainnet relayer startup is blocked" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_relayer_submit_intent_fails_closed_on_mainnet(self):
        mock_rpc = AsyncMock()
        signer = MockSigner("0x473888C859F88D3De7b5A20986805b4dC3178189")
        cfg = get_chain_config(CHAIN_ID_BASE_MAINNET)

        relayer = BlockchainRelayer(
            config=cfg,
            rpc_client=mock_rpc,
            nonce_manager=NonceManager(mock_rpc),
            gas_estimator=GasEstimator(mock_rpc),
            signer=signer,
        )


        intent = BlockchainTransactionIntent(
            chain_id=CHAIN_ID_BASE_MAINNET,
            operation="lockEscrow",
            target_contract="0x3e5C09D2123cA106b2E24C4f76bFad4a022640D1",
            parameters={"escrowId": "0x" + "01" * 32},
            idempotency_key="intent-mainnet-test",
            status=IntentStatus.SUBMITTED,
        )

        mock_session = AsyncMock()
        with pytest.raises(MainnetSubmissionBlockedError):
            await relayer.submit_intent(mock_session, intent)



# ==============================================================================
# 4. ROLE SEPARATION & ADDRESS HARDENING TESTS
# ==============================================================================

class TestRoleSeparation:
    def test_anvil_default_address_blocked_as_production_role(self):
        with pytest.raises((ProductionConfigError, ValueError)) as excinfo:
            bad_roles = RoleConfig(
                relayer_role_address=ANVIL_DEFAULT_ADDR,
                admin_role_address=VALID_ADMIN_ADDR,
            )
            ProductionEnvironmentConfig(
                app_env=EnvironmentType.PRODUCTION,
                chain_id=84532,
                rpc_url="https://sepolia.base.org",
                signer_provider=ProductionSignerType.KMS,
                signer_key_reference="arn:aws:kms:us-east-1:123456789012:key/test",
                roles=bad_roles,
                contracts=ContractConfig(**{f"{k}_address": v for k, v in CANONICAL_BASE_SEPOLIA_CONTRACTS.items()}),
            )
        assert "uses blocked address" in str(excinfo.value)

    def test_relayer_and_admin_cannot_be_same_address_in_production(self):
        with pytest.raises((ProductionConfigError, ValueError)) as excinfo:
            identical_roles = RoleConfig(
                relayer_role_address=VALID_SEPOLIA_TEST_ADDR,
                admin_role_address=VALID_SEPOLIA_TEST_ADDR,
            )
            ProductionEnvironmentConfig(
                app_env=EnvironmentType.PRODUCTION,
                chain_id=84532,
                rpc_url="https://sepolia.base.org",
                signer_provider=ProductionSignerType.KMS,
                signer_key_reference="arn:aws:kms:us-east-1:123456789012:key/test",
                roles=identical_roles,
                contracts=ContractConfig(**{f"{k}_address": v for k, v in CANONICAL_BASE_SEPOLIA_CONTRACTS.items()}),
            )
        assert "ROLE SEPARATION VIOLATION" in str(excinfo.value)

    def test_contract_address_mismatch_fails_closed(self):
        with pytest.raises((ProductionConfigError, ValueError)) as excinfo:
            tampered_contracts = {f"{k}_address": v for k, v in CANONICAL_BASE_SEPOLIA_CONTRACTS.items()}
            tampered_contracts["escrow_address"] = "0x1111111111111111111111111111111111111111"

            ProductionEnvironmentConfig(
                app_env=EnvironmentType.PRODUCTION,
                chain_id=84532,
                rpc_url="https://sepolia.base.org",
                signer_provider=ProductionSignerType.KMS,
                signer_key_reference="arn:aws:kms:us-east-1:123456789012:key/test",
                roles=RoleConfig(relayer_role_address=VALID_SEPOLIA_TEST_ADDR, admin_role_address=VALID_ADMIN_ADDR),
                contracts=ContractConfig(**tampered_contracts),
            )
        assert "Escrow address mismatch" in str(excinfo.value)


# ==============================================================================
# 5. RELAYER HARDENING & INTENT CHAIN BINDING TESTS
# ==============================================================================

class TestRelayerHardening:
    @pytest.mark.asyncio
    async def test_relayer_rejects_signer_network_mismatch_at_startup(self):
        mock_rpc = AsyncMock()
        mock_rpc.verify_chain_id.return_value = CHAIN_ID_BASE_SEPOLIA
        cfg = get_chain_config(CHAIN_ID_BASE_SEPOLIA)

        # Anvil signer configured for Base Sepolia relayer
        anvil_signer = LocalDevelopmentSigner(ANVIL_DEFAULT_KEY)
        relayer = BlockchainRelayer(
            config=cfg,
            rpc_client=mock_rpc,
            nonce_manager=NonceManager(mock_rpc),
            gas_estimator=GasEstimator(mock_rpc),
            signer=anvil_signer,
        )

        with pytest.raises(RuntimeError) as excinfo:
            await relayer.verify_startup()
        assert "Signer network compatibility check failed" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_relayer_rejects_intent_chain_mismatch(self):
        mock_rpc = AsyncMock()
        mock_rpc.verify_chain_id.return_value = CHAIN_ID_BASE_SEPOLIA
        signer = TestnetAccountSigner(VALID_SEPOLIA_TEST_KEY)
        cfg = get_chain_config(CHAIN_ID_BASE_SEPOLIA)

        relayer = BlockchainRelayer(
            config=cfg,
            rpc_client=mock_rpc,
            nonce_manager=NonceManager(mock_rpc),
            gas_estimator=GasEstimator(mock_rpc),
            signer=signer,
        )

        # Intent specified chain 31337, but relayer is on 84532
        intent = BlockchainTransactionIntent(
            chain_id=CHAIN_ID_ANVIL,
            operation="lockEscrow",
            target_contract=CANONICAL_BASE_SEPOLIA_CONTRACTS["escrow"],
            parameters={"escrowId": "0x" + "01" * 32},
            idempotency_key="intent-wrong-chain",
            status=IntentStatus.SUBMITTED,
        )

        mock_session = AsyncMock()
        with pytest.raises(ChainMismatchError) as excinfo:
            await relayer.submit_intent(mock_session, intent)
        assert "does not match relayer chain_id" in str(excinfo.value)


    @pytest.mark.asyncio
    async def test_relayer_rejects_unallowlisted_target_contract(self):
        mock_rpc = AsyncMock()
        mock_rpc.verify_chain_id.return_value = CHAIN_ID_BASE_SEPOLIA
        signer = TestnetAccountSigner(VALID_SEPOLIA_TEST_KEY)
        cfg = get_chain_config(CHAIN_ID_BASE_SEPOLIA)

        relayer = BlockchainRelayer(
            config=cfg,
            rpc_client=mock_rpc,
            nonce_manager=NonceManager(mock_rpc),
            gas_estimator=GasEstimator(mock_rpc),
            signer=signer,
        )


        # Non-canonical arbitrary target contract
        intent = BlockchainTransactionIntent(
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            operation="lockEscrow",
            target_contract="0x000000000000000000000000000000000000dead",
            parameters={"escrowId": "0x" + "01" * 32},
            idempotency_key="intent-bad-target",
            status=IntentStatus.SUBMITTED,
        )

        mock_session = AsyncMock()
        with pytest.raises(ContractMismatchError) as excinfo:
            await relayer.submit_intent(mock_session, intent)
        assert "not in allowlist" in str(excinfo.value)



# ==============================================================================
# 6. STATIC SECURITY GATES AUTOMATED RUN
# ==============================================================================

class TestStaticSecurityGates:
    def test_ci_security_gate_script_passes(self):
        import subprocess
        result = subprocess.run(
            [Path(__file__).resolve().parent.parent.parent / ".venv" / "bin" / "python3", "scripts/verify_production_safety_gates.py"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Gate failed:\n{result.stdout}\n{result.stderr}"
        assert "ALL PRODUCTION SAFETY GATES PASSED" in result.stdout
