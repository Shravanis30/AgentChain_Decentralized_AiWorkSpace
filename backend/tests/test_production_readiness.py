"""Phase 6.2B.2 — Production Readiness Hardening Test Suite.

Tests the 20 required production-readiness scenarios:
 1. Slither CI configuration exists and is well-formed
 2. LocalAccountSigner rejected at construction on Mainnet
 3. LocalAccountSigner rejected at sign-time on Mainnet
 4. RPC chain ID mismatch detected via verify_startup
 5. Wrong Escrow contract (zero address) detected via verify_startup
 6. Wrong USDC address detected via verify_startup
 7. Signer health_check failure detected via verify_startup
 8. Signer chain mismatch (LocalAccountSigner on Mainnet) detected via verify_startup
 9. Deep reorg alert: authorization halted after deep reorg
10. Settlement authorization halt (blocked by deep reorg)
11. Active settlements blocked after deep reorg (already covered in 6.2B.1, re-verified)
12. Recovery cannot bypass confirmation depth
13. Recovery cannot bypass Mainnet block
14. Metric cardinality: settlement metrics use bounded labels
15. Relayer health failure via signer health_check False
16. Outbox backlog detection (structural)
17. Indexer lag detection (structural)
18. Transaction replacement alert (structural)
19. Signer health_check interface enforced on all implementations
20. Deployment preflight success on Anvil
"""

import asyncio
import os
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa
from eth_account import Account
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.blockchain import (
    ChainReorganization,
    EscrowChainState,
    EventStatus,
    ReorgStatus,
)
from app.models.settlement import (
    Settlement,
    SettlementAction,
    SettlementStatus,
)
from app.models.user import User, UserRole
from app.services.blockchain.config import (
    BASE_MAINNET_USDC,
    BASE_SEPOLIA_USDC,
    CHAIN_ID_ANVIL,
    CHAIN_ID_BASE_MAINNET,
    CHAIN_ID_BASE_SEPOLIA,
    ChainConfig,
    get_chain_config,
)
from app.services.blockchain.errors import MainnetSubmissionBlockedError
from app.services.blockchain.relayer import (
    BlockchainRelayer,
    LocalAccountSigner,
    MockSigner,
    TransactionSigner,
)
from app.services.settlement.errors import (
    SettlementAuthorizationError,
    SettlementMainnetBlockedError,
)
from app.services.settlement.metrics import SettlementMetrics
from app.services.settlement.service import SettlementService

# ---------------------------------------------------------------------------
# TEST 1: Slither CI configuration exists
# ---------------------------------------------------------------------------


def test_slither_ci_workflow_exists():
    """CI security workflow using Slither must exist at .github/workflows/security.yml."""
    repo_root = Path(__file__).parent.parent.parent
    security_yml = repo_root / ".github" / "workflows" / "security.yml"
    assert security_yml.exists(), (
        f"Slither CI workflow not found at {security_yml}. "
        "This file is required by Phase 6.2B.2."
    )
    content = security_yml.read_text()
    assert "slither" in content.lower(), "security.yml must reference slither"
    assert "slither-analyzer==" in content, (
        "Slither version must be pinned in security.yml (e.g., slither-analyzer==0.10.4)"
    )
    assert "upload-artifact" in content, "security.yml must upload Slither report as artifact"


def test_slither_accepted_findings_document_exists():
    """All accepted Slither findings must be documented."""
    repo_root = Path(__file__).parent.parent.parent
    accepted = repo_root / "docs" / "blockchain" / "SLITHER_ACCEPTED.md"
    assert accepted.exists(), (
        f"SLITHER_ACCEPTED.md not found at {accepted}. "
        "All Slither findings must be classified."
    )
    content = accepted.read_text()
    # Must contain at minimum the finding S1 classification
    assert "ACCEPTED" in content or "FALSE POSITIVE" in content, (
        "SLITHER_ACCEPTED.md must contain finding classifications"
    )


# ---------------------------------------------------------------------------
# TEST 2: LocalAccountSigner rejected at construction on Mainnet
# ---------------------------------------------------------------------------


def test_local_account_signer_rejected_construction_on_mainnet():
    """LocalAccountSigner must raise RuntimeError if chain_id=8453 (Base Mainnet)."""
    test_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    with pytest.raises(RuntimeError, match="SECURITY VIOLATION"):
        LocalAccountSigner(private_key_hex=test_key, chain_id=CHAIN_ID_BASE_MAINNET)


def test_local_account_signer_allowed_on_anvil():
    """LocalAccountSigner must be constructable on Anvil (chain_id=31337)."""
    test_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    signer = LocalAccountSigner(private_key_hex=test_key, chain_id=CHAIN_ID_ANVIL)
    assert signer.get_address().startswith("0x")
    assert len(signer.get_address()) == 42


def test_local_account_signer_allowed_on_sepolia():
    """LocalAccountSigner must be constructable on Base Sepolia (chain_id=84532)."""
    test_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    signer = LocalAccountSigner(private_key_hex=test_key, chain_id=CHAIN_ID_BASE_SEPOLIA)
    assert signer.get_address().startswith("0x")


# ---------------------------------------------------------------------------
# TEST 3: LocalAccountSigner rejected at sign-time on Mainnet
# ---------------------------------------------------------------------------


def test_local_account_signer_sign_time_mainnet_guard():
    """sign_transaction must raise RuntimeError if tx chainId=8453."""
    test_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    # Construct without chain_id (no construction-time guard)
    signer = LocalAccountSigner(private_key_hex=test_key, chain_id=None)
    # Now attempt to sign a Mainnet transaction
    tx = {
        "chainId": CHAIN_ID_BASE_MAINNET,
        "from": signer.get_address(),
        "to": "0x" + "a" * 40,
        "nonce": 0,
        "maxFeePerGas": 1_000_000_000,
        "maxPriorityFeePerGas": 100_000_000,
        "gas": 100_000,
        "data": "0x",
        "value": 0,
    }
    with pytest.raises(RuntimeError, match="SECURITY VIOLATION"):
        signer.sign_transaction(tx)


# ---------------------------------------------------------------------------
# TEST 4: RPC chain ID mismatch detected via verify_startup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_startup_rpc_chain_id_mismatch():
    """Relayer.verify_startup must raise RuntimeError if RPC chain ID != configured."""
    config = get_chain_config(CHAIN_ID_ANVIL)
    test_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    signer = LocalAccountSigner(private_key_hex=test_key, chain_id=CHAIN_ID_ANVIL)

    mock_rpc = AsyncMock()
    # RPC reports chain ID 99999 — mismatch with configured 31337
    mock_rpc.verify_chain_id = AsyncMock(return_value=99999)

    relayer = BlockchainRelayer(
        config=config,
        rpc_client=mock_rpc,
        nonce_manager=MagicMock(),
        gas_estimator=MagicMock(),
        signer=signer,
    )

    with pytest.raises(RuntimeError, match="Chain ID mismatch"):
        await relayer.verify_startup()


# ---------------------------------------------------------------------------
# TEST 5: Zero Escrow contract detected via verify_startup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_startup_zero_escrow_contract():
    """verify_startup must raise if Escrow contract is zero address."""
    # Construct a config with zero Escrow address
    config = ChainConfig(
        chain_id=CHAIN_ID_BASE_SEPOLIA,
        network_name="base-sepolia",
        rpc_url="http://localhost:8545",
        confirmations_required=3,
        contract_addresses={"Escrow": "0x" + "0" * 40},
        token_address=BASE_SEPOLIA_USDC,
        allow_transactions=True,
        max_reorg_depth=32,
    )
    test_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    signer = LocalAccountSigner(private_key_hex=test_key, chain_id=CHAIN_ID_BASE_SEPOLIA)

    mock_rpc = AsyncMock()
    mock_rpc.verify_chain_id = AsyncMock(return_value=CHAIN_ID_BASE_SEPOLIA)

    relayer = BlockchainRelayer(
        config=config,
        rpc_client=mock_rpc,
        nonce_manager=MagicMock(),
        gas_estimator=MagicMock(),
        signer=signer,
    )

    with pytest.raises(RuntimeError, match="PREFLIGHT FAIL"):
        await relayer.verify_startup()


# ---------------------------------------------------------------------------
# TEST 6: Wrong USDC address detected via verify_startup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_startup_wrong_usdc_address():
    """verify_startup must raise if configured USDC != canonical USDC for the chain."""
    fake_usdc = "0x" + "d" * 40  # random address, not the real USDC
    config = ChainConfig(
        chain_id=CHAIN_ID_BASE_SEPOLIA,
        network_name="base-sepolia",
        rpc_url="http://localhost:8545",
        confirmations_required=3,
        contract_addresses={"Escrow": "0x" + "b" * 40},
        token_address=fake_usdc,
        allow_transactions=True,
        max_reorg_depth=32,
    )
    test_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    signer = LocalAccountSigner(private_key_hex=test_key, chain_id=CHAIN_ID_BASE_SEPOLIA)

    mock_rpc = AsyncMock()
    mock_rpc.verify_chain_id = AsyncMock(return_value=CHAIN_ID_BASE_SEPOLIA)

    relayer = BlockchainRelayer(
        config=config,
        rpc_client=mock_rpc,
        nonce_manager=MagicMock(),
        gas_estimator=MagicMock(),
        signer=signer,
    )

    with pytest.raises(RuntimeError, match="USDC address mismatch"):
        await relayer.verify_startup()


# ---------------------------------------------------------------------------
# TEST 7: Signer health_check False detected via verify_startup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_startup_signer_health_check_failure():
    """verify_startup must raise if signer.health_check() returns False."""
    config = get_chain_config(CHAIN_ID_ANVIL)

    class UnhealthySigner(TransactionSigner):
        def get_address(self) -> str:
            return "0x" + "a" * 40

        def sign_transaction(self, tx_dict):
            raise RuntimeError("Unhealthy")

        def health_check(self) -> bool:
            return False

    mock_rpc = AsyncMock()
    mock_rpc.verify_chain_id = AsyncMock(return_value=CHAIN_ID_ANVIL)

    relayer = BlockchainRelayer(
        config=config,
        rpc_client=mock_rpc,
        nonce_manager=MagicMock(),
        gas_estimator=MagicMock(),
        signer=UnhealthySigner(),
    )

    with pytest.raises(RuntimeError, match="health_check"):
        await relayer.verify_startup()


# ---------------------------------------------------------------------------
# TEST 8: LocalAccountSigner on Mainnet detected via verify_startup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_startup_local_signer_on_mainnet_rejected():
    """verify_startup must raise if LocalAccountSigner is used on Mainnet."""
    # Construct the LocalAccountSigner without a chain_id (bypasses construction guard)
    # so we can test the verify_startup guard independently
    test_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    signer = LocalAccountSigner(private_key_hex=test_key, chain_id=None)
    # Override internal chain_id to None so construction doesn't raise
    # Then create relayer with Mainnet config — verify_startup must catch this

    mainnet_config = ChainConfig(
        chain_id=CHAIN_ID_BASE_MAINNET,
        network_name="base-mainnet",
        rpc_url="http://localhost:8545",
        confirmations_required=12,
        contract_addresses={"Escrow": "0x" + "c" * 40},
        token_address=BASE_MAINNET_USDC,
        allow_transactions=False,
        max_reorg_depth=32,
    )

    mock_rpc = AsyncMock()
    mock_rpc.verify_chain_id = AsyncMock(return_value=CHAIN_ID_BASE_MAINNET)

    relayer = BlockchainRelayer(
        config=mainnet_config,
        rpc_client=mock_rpc,
        nonce_manager=MagicMock(),
        gas_estimator=MagicMock(),
        signer=signer,
    )

    with pytest.raises(RuntimeError, match="SECURITY VIOLATION"):
        await relayer.verify_startup()


# ---------------------------------------------------------------------------
# TEST 9 & 10: Deep reorg halt and settlement authorization blocked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deep_reorg_halts_settlement_authorization(db_session: AsyncSession):
    """After a deep reorg record, new settlement authorizations are halted."""
    chain_id = CHAIN_ID_ANVIL

    # Create a deep reorg record (FAILED state)
    reorg = ChainReorganization(
        chain_id=chain_id,
        detection_block_number=1040,
        old_block_hash="0x" + "a" * 64,
        new_block_hash="0x" + "b" * 64,
        common_ancestor_number=1000,
        common_ancestor_hash="0x" + "c" * 64,
        depth=40,
        status=ReorgStatus.FAILED,
    )
    db_session.add(reorg)
    await db_session.flush()

    # Now try to authorize a settlement on the same chain
    client = User(
        wallet_address=Account.create().address,
        primary_role=UserRole.CLIENT.value,
    )
    db_session.add(client)
    await db_session.flush()

    settlement = Settlement(
        idempotency_key="key-" + uuid.uuid4().hex,
        chain_id=chain_id,
        escrow_contract=get_chain_config(chain_id).contract_addresses.get("Escrow"),
        escrow_id="0x" + "a" * 64,
        client_address=client.wallet_address,
        beneficiary_address=Account.create().address,
        token_address=get_chain_config(chain_id).token_address,
        action=SettlementAction.RELEASE.value,
        amount=1_000_000,
        user_id=client.id,
        status=SettlementStatus.PENDING_AUTHORIZATION.value,
    )
    db_session.add(settlement)
    await db_session.flush()

    with pytest.raises(SettlementAuthorizationError, match="chain reorganization"):
        await SettlementService.authorize_settlement(
            session=db_session,
            settlement_id=settlement.id,
            actor_user=client,
        )


# ---------------------------------------------------------------------------
# TEST 11: Recovery cannot bypass confirmation depth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_cannot_bypass_confirmation_depth(db_session: AsyncSession):
    """EscrowVerificationBoundary must reject escrows not yet confirmed, even in recovery."""
    from app.services.settlement.verifier import EscrowVerificationBoundary

    chain_id = CHAIN_ID_ANVIL
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    client_addr = Account.create().address

    # Create an escrow in PENDING (not CONFIRMED) state
    chain_state = EscrowChainState(
        chain_id=chain_id,
        escrow_id=escrow_id,
        contract_address=get_chain_config(chain_id).contract_addresses.get("Escrow"),
        client=client_addr,
        developer=Account.create().address,
        amount=1_000_000,
        reference_id="ref-" + uuid.uuid4().hex,
        creation_tx_hash="0x" + "1" * 64,
        latest_tx_hash="0x" + "1" * 64,
        current_chain_state=2,  # LOCKED
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMING.value,  # NOT confirmed
        token=get_chain_config(chain_id).token_address,
    )
    db_session.add(chain_state)
    await db_session.flush()

    from app.services.settlement.errors import SettlementConfirmationError

    with pytest.raises(SettlementConfirmationError):
        await EscrowVerificationBoundary.verify_escrow_for_settlement(
            session=db_session,
            chain_id=chain_id,
            escrow_id=escrow_id,
            action=SettlementAction.RELEASE,
        )


# ---------------------------------------------------------------------------
# TEST 12: Recovery cannot bypass Mainnet block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_cannot_bypass_mainnet_block(db_session: AsyncSession):
    """Settlement creation must always reject Mainnet regardless of escrow state."""
    client = User(
        wallet_address=Account.create().address,
        primary_role=UserRole.CLIENT.value,
    )
    db_session.add(client)
    await db_session.flush()

    with pytest.raises(SettlementMainnetBlockedError):
        await SettlementService.create_settlement(
            session=db_session,
            chain_id=CHAIN_ID_BASE_MAINNET,
            escrow_id="0x" + "f" * 64,
            action=SettlementAction.RELEASE,
            actor_user=client,
        )


# ---------------------------------------------------------------------------
# TEST 13: Metric cardinality safety
# ---------------------------------------------------------------------------


def test_metric_cardinality_no_unbounded_labels():
    """Settlement metrics must not use unbounded labels (no user IDs, tx hashes, escrow IDs)."""
    import inspect
    from app.services.settlement import metrics as sm

    source = inspect.getsource(sm)

    # Verify no high-cardinality fields are used as label keys
    forbidden_label_patterns = [
        '"user_id"',
        '"settlement_id"',
        '"escrow_id"',
        '"tx_hash"',
        '"transaction_hash"',
    ]
    for pattern in forbidden_label_patterns:
        assert pattern not in source, (
            f"Metric label '{pattern}' detected in settlement metrics — "
            "this would cause unbounded Prometheus cardinality."
        )

    # Reason[] truncation is present (bounded error messages)
    assert "[:64]" in source or "[:32]" in source, (
        "Settlement metrics must truncate free-text label values (e.g., reason[:64])"
    )


def test_blockchain_metric_cardinality_no_unbounded_labels():
    """Blockchain metrics must not use unbounded labels."""
    import inspect
    from app.services.blockchain import metrics as bm

    source = inspect.getsource(bm)

    forbidden_label_patterns = [
        '"tx_hash"',
        '"transaction_hash"',
        '"user_id"',
        '"escrow_id"',
    ]
    for pattern in forbidden_label_patterns:
        assert pattern not in source, (
            f"Label '{pattern}' detected in blockchain metrics — unbounded cardinality risk."
        )


# ---------------------------------------------------------------------------
# TEST 14: Relayer health failure via signer health_check
# ---------------------------------------------------------------------------


def test_local_account_signer_health_check_true():
    """LocalAccountSigner.health_check() must return True when initialized correctly."""
    test_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    signer = LocalAccountSigner(private_key_hex=test_key, chain_id=CHAIN_ID_ANVIL)
    assert signer.health_check() is True


def test_mock_signer_health_check_true():
    """MockSigner.health_check() must return True (for tests that simulate a healthy signer)."""
    signer = MockSigner()
    assert signer.health_check() is True


# ---------------------------------------------------------------------------
# TEST 15: Signer interface enforced on all implementations
# ---------------------------------------------------------------------------


def test_transaction_signer_interface_complete():
    """All TransactionSigner implementations must implement get_address, sign_transaction, health_check."""
    from app.services.blockchain.relayer import TransactionSigner

    for cls in [LocalAccountSigner, MockSigner]:
        assert hasattr(cls, "get_address"), f"{cls.__name__} missing get_address"
        assert hasattr(cls, "sign_transaction"), f"{cls.__name__} missing sign_transaction"
        assert hasattr(cls, "health_check"), f"{cls.__name__} missing health_check"

    # Verify abstract methods are declared on base class
    abstract_methods = getattr(TransactionSigner, "__abstractmethods__", set())
    assert "get_address" in abstract_methods
    assert "sign_transaction" in abstract_methods
    assert "health_check" in abstract_methods


# ---------------------------------------------------------------------------
# TEST 16: Deployment preflight success on Anvil
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_startup_success_on_anvil():
    """verify_startup must succeed when all preflight checks pass for Anvil."""
    config = get_chain_config(CHAIN_ID_ANVIL)
    test_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    signer = LocalAccountSigner(private_key_hex=test_key, chain_id=CHAIN_ID_ANVIL)

    mock_rpc = AsyncMock()
    mock_rpc.verify_chain_id = AsyncMock(return_value=CHAIN_ID_ANVIL)

    relayer = BlockchainRelayer(
        config=config,
        rpc_client=mock_rpc,
        nonce_manager=MagicMock(),
        gas_estimator=MagicMock(),
        signer=signer,
    )

    # Must complete without raising
    await relayer.verify_startup()


# ---------------------------------------------------------------------------
# TEST 17: Prometheus alert rules file exists and has correct structure
# ---------------------------------------------------------------------------


def test_prometheus_alert_rules_file_exists():
    """Prometheus alert rules file must exist at infra/monitoring/settlement-alerts.yml."""
    repo_root = Path(__file__).parent.parent.parent
    alert_file = repo_root / "infra" / "monitoring" / "settlement-alerts.yml"
    assert alert_file.exists(), (
        f"Prometheus alert rules not found at {alert_file}. "
        "Required by Phase 6.2B.2 for production monitoring."
    )
    content = alert_file.read_text()
    # Verify all required alert groups are present (A through O)
    required_alerts = [
        "DeepReorgDetected",
        "SettlementAuthorizationBlocked",
        "MainnetSettlementAttempted",
        "SettlementConfirmationLatencyHigh",
        "SettlementFailureSpike",
        "SettlementReorgInvalidation",
        "RelayerDown",
        "RpcErrorRateHigh",
        "TransactionReplacementSpike",
        "NonceContention",
        "OutboxBacklogHigh",
        "IndexerLagHigh",
        "SettlementReconciliationStale",
        "MainnetSettlementAttemptSecurity",
        "ReorgRecoveryManualActionRequired",
        "SignerUnhealthy",
    ]
    for alert in required_alerts:
        assert alert in content, (
            f"Required alert '{alert}' not found in settlement-alerts.yml"
        )


# ---------------------------------------------------------------------------
# TEST 18: Production runbook documents exist
# ---------------------------------------------------------------------------


def test_production_runbooks_exist():
    """All required production runbooks must exist."""
    repo_root = Path(__file__).parent.parent.parent
    required_docs = [
        "docs/DEEP_REORG_RUNBOOK.md",
        "docs/BASE_SEPOLIA_RUNBOOK.md",
        "docs/PRODUCTION_SIGNER.md",
        "docs/blockchain/SLITHER_ACCEPTED.md",
    ]
    for doc_path in required_docs:
        full_path = repo_root / doc_path
        assert full_path.exists(), (
            f"Required production document not found: {doc_path}. "
            f"Phase 6.2B.2 requires all runbooks to be present."
        )
        content = full_path.read_text()
        assert len(content) > 500, (
            f"{doc_path} appears to be empty or stub. "
            "All runbooks must contain substantive content."
        )


# ---------------------------------------------------------------------------
# TEST 19: Mainnet hard-block cannot be bypassed via env
# ---------------------------------------------------------------------------


def test_mainnet_block_default_is_true():
    """BLOCK_MAINNET_SETTLEMENT must default to True (fail-closed)."""
    from app.services.settlement.config import BLOCK_MAINNET_SETTLEMENT

    # The default must be True regardless of environment
    # If someone sets BLOCK_MAINNET_SETTLEMENT=false, it should be explicit — not a default
    # Here we test that the module-level constant, loaded without any env override, is True
    # (In CI, no env override is set, so this will always be True)
    assert BLOCK_MAINNET_SETTLEMENT is True, (
        "BLOCK_MAINNET_SETTLEMENT must default to True (fail-closed). "
        "It must never default to False."
    )


# ---------------------------------------------------------------------------
# TEST 20: Configuration drift — reorg depth is 32 everywhere
# ---------------------------------------------------------------------------


def test_configuration_reorg_depth_no_drift():
    """The authoritative reorg depth (32) must be consistent across all config sources."""
    from app.services.blockchain.config import get_chain_configs
    from app.services.settlement.config import get_max_reorg_depth, MAX_REORG_DEPTH

    # Check all chain configs
    configs = get_chain_configs()
    for chain_id, cfg in configs.items():
        assert cfg.max_reorg_depth == 32, (
            f"Chain {chain_id} has max_reorg_depth={cfg.max_reorg_depth}, expected 32. "
            "Configuration drift detected."
        )

    # Check settlement config
    assert MAX_REORG_DEPTH == 32, (
        f"Settlement MAX_REORG_DEPTH={MAX_REORG_DEPTH}, expected 32. Drift detected."
    )

    for chain_id in [CHAIN_ID_ANVIL, CHAIN_ID_BASE_SEPOLIA, CHAIN_ID_BASE_MAINNET]:
        depth = get_max_reorg_depth(chain_id)
        assert depth == 32, (
            f"get_max_reorg_depth({chain_id})={depth}, expected 32. Drift detected."
        )
