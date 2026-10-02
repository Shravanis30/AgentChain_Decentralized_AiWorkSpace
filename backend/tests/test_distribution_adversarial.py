import asyncio
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from eth_account import Account
from web3 import Web3

from app.db.session import async_session_factory
from app.models.user import User, UserRole
from app.models.settlement import Settlement, SettlementStatus, SettlementAction
from app.models.distribution import Distribution, DistributionStatus, DistributionHistory
from app.models.blockchain import (
    EscrowChainState,
    EventStatus,
    BlockchainEvent,
    BlockchainTransaction,
)
from app.services.settlement.verifier import STATE_LOCKED, STATE_RELEASED
from app.services.blockchain.config import (
    get_chain_config,
    CHAIN_ID_ANVIL,
    CHAIN_ID_BASE_SEPOLIA,
    CHAIN_ID_BASE_MAINNET,
)
from app.services.distribution.config import (
    compute_85_10_5_split,
    validate_distribution_recipients,
    get_platform_recipients,
)
from app.services.distribution.service import DistributionService
from app.services.distribution.reconciliation import DistributionReconciliationService
from app.services.distribution.errors import (
    DistributionStateConflictError,
    DistributionNotFoundError,
    DistributionAuthorizationError,
    DistributionDuplicateRecipientError,
    DistributionMainnetBlockedError,
)
from app.services.distribution.metrics import DistributionMetrics


@pytest.fixture
def keypair():
    return {
        "client": Account.create().address,
        "beneficiary": Account.create().address,
        "arbitrator": Account.create().address,
        "stranger": Account.create().address,
    }


# ==============================================================================
# 1. MIGRATION 012 VERIFICATION & CONCURRENCY
# ==============================================================================

def test_migration_012_upgrade_downgrade_upgrade():
    """Proves Alembic migration 012 downgrades and upgrades cleanly without loss of schema integrity."""
    backend_dir = Path(__file__).resolve().parent.parent
    venv_alembic = backend_dir.parent / ".venv" / "bin" / "alembic"

    # Downgrade to 011
    cmd_down = [str(venv_alembic), "downgrade", "-1"]
    res_down = subprocess.run(cmd_down, cwd=str(backend_dir), capture_output=True, text=True)
    assert res_down.returncode == 0, f"Downgrade failed: {res_down.stderr}"

    # Upgrade back to 012 (head)
    cmd_up = [str(venv_alembic), "upgrade", "head"]
    res_up = subprocess.run(cmd_up, cwd=str(backend_dir), capture_output=True, text=True)
    assert res_up.returncode == 0, f"Upgrade failed: {res_up.stderr}"


@pytest.mark.asyncio
async def test_concurrent_distribution_creation_single_logical_record(db_session: AsyncSession, keypair):
    """Proves 10 concurrent creation attempts against the same settlement produce exactly one record."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex

    client_user = User(wallet_address=keypair["client"], primary_role=UserRole.CLIENT.value)
    db_session.add(client_user)
    await db_session.flush()

    settlement = Settlement(
        idempotency_key=f"conc:dist:{uuid.uuid4().hex}",
        chain_id=CHAIN_ID_ANVIL,
        escrow_contract=cfg.contract_addresses["Escrow"].lower(),
        escrow_id=escrow_id,
        client_address=keypair["client"].lower(),
        beneficiary_address=keypair["beneficiary"].lower(),
        token_address=cfg.token_address.lower(),
        amount=10_000_000,
        action=SettlementAction.RELEASE.value,
        status=SettlementStatus.AUTHORIZED.value,
    )
    db_session.add(settlement)
    await db_session.commit()

    # Worker task using its own isolated database session
    async def create_attempt(worker_idx: int):
        async with async_session_factory() as session:
            try:
                dist, created = await DistributionService.create_distribution(
                    session=session,
                    settlement_id=settlement.id,
                    actor_user=client_user,
                    idempotency_key=f"idemp:worker:{worker_idx}",
                )
                await session.commit()
                return dist.id, created
            except Exception as e:
                await session.rollback()
                return None, e

    # 10 concurrent tasks
    tasks = [create_attempt(i) for i in range(10)]
    results = await asyncio.gather(*tasks)

    successful = [r for r in results if r[0] is not None]
    assert len(successful) >= 1, "At least one creation must succeed"

    # All successful responses must return the SAME distribution ID
    dist_ids = {r[0] for r in successful}
    assert len(dist_ids) == 1, "All tasks must resolve to the identical distribution record"

    # Exactly one task created it, other tasks returned existing record
    created_count = sum(1 for r in successful if r[1] is True)
    assert created_count == 1, "Exactly one task should perform the initial creation"

    # Verify at database level
    async with async_session_factory() as session:
        dists = (
            await session.execute(
                sa.select(Distribution).where(Distribution.settlement_id == settlement.id)
            )
        ).scalars().all()
        assert len(dists) == 1, "Exactly one distribution row must exist in DB"


# ==============================================================================
# 2. RECIPIENT MANIPULATION & BOUNDARY ATTACKS
# ==============================================================================

def test_recipient_manipulation_zero_address_rejected():
    with pytest.raises(ValueError, match="Developer recipient must be a valid non-zero address"):
        validate_distribution_recipients("0x" + "0" * 40, "0x" + "1" * 40, "0x" + "2" * 40)


def test_recipient_manipulation_duplicate_rejected():
    addr1 = Account.create().address
    addr2 = Account.create().address
    with pytest.raises(DistributionDuplicateRecipientError, match="Developer recipient cannot match staker recipient"):
        validate_distribution_recipients(addr1, addr1, addr2)

    with pytest.raises(DistributionDuplicateRecipientError, match="Developer recipient cannot match DAO recipient"):
        validate_distribution_recipients(addr1, addr2, addr1)


def test_recipient_address_casing_normalization():
    recipients = get_platform_recipients(CHAIN_ID_ANVIL)
    assert Web3.is_checksum_address(recipients.staker_recipient)
    assert Web3.is_checksum_address(recipients.dao_recipient)


@pytest.mark.asyncio
async def test_distribution_service_rejects_non_release_action(db_session: AsyncSession, keypair):
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex

    client_user = User(wallet_address=keypair["client"], primary_role=UserRole.CLIENT.value)
    db_session.add(client_user)
    await db_session.flush()

    settlement = Settlement(
        idempotency_key=f"rej:action:{uuid.uuid4().hex}",
        chain_id=CHAIN_ID_ANVIL,
        escrow_contract=cfg.contract_addresses["Escrow"].lower(),
        escrow_id=escrow_id,
        client_address=keypair["client"].lower(),
        beneficiary_address=keypair["beneficiary"].lower(),
        token_address=cfg.token_address.lower(),
        amount=10_000_000,
        action=SettlementAction.REFUND.value, # Not RELEASE
        status=SettlementStatus.AUTHORIZED.value,
    )
    db_session.add(settlement)
    await db_session.flush()

    with pytest.raises(DistributionStateConflictError, match="Only RELEASE triggers 85/10/5 split"):
        await DistributionService.create_distribution(db_session, settlement.id, client_user)


@pytest.mark.asyncio
async def test_distribution_service_rejects_pending_settlement(db_session: AsyncSession, keypair):
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex

    client_user = User(wallet_address=keypair["client"], primary_role=UserRole.CLIENT.value)
    db_session.add(client_user)
    await db_session.flush()

    settlement = Settlement(
        idempotency_key=f"rej:pending:{uuid.uuid4().hex}",
        chain_id=CHAIN_ID_ANVIL,
        escrow_contract=cfg.contract_addresses["Escrow"].lower(),
        escrow_id=escrow_id,
        client_address=keypair["client"].lower(),
        beneficiary_address=keypair["beneficiary"].lower(),
        token_address=cfg.token_address.lower(),
        amount=10_000_000,
        action=SettlementAction.RELEASE.value,
        status=SettlementStatus.PENDING_AUTHORIZATION.value, # Not authorized
    )
    db_session.add(settlement)
    await db_session.flush()

    with pytest.raises(DistributionAuthorizationError, match="Cannot distribute prior to authorization"):
        await DistributionService.create_distribution(db_session, settlement.id, client_user)


@pytest.mark.asyncio
async def test_distribution_service_mainnet_hard_block(db_session: AsyncSession, keypair):
    client_user = User(wallet_address=keypair["client"], primary_role=UserRole.CLIENT.value)
    db_session.add(client_user)
    await db_session.flush()

    settlement = Settlement(
        idempotency_key=f"mainnet:blk:{uuid.uuid4().hex}",
        chain_id=CHAIN_ID_BASE_MAINNET, # 8453
        escrow_contract="0x" + "88" * 20,
        escrow_id="0x" + "99" * 32,
        client_address=keypair["client"].lower(),
        beneficiary_address=keypair["beneficiary"].lower(),
        token_address="0x" + "11" * 20,
        amount=10_000_000,
        action=SettlementAction.RELEASE.value,
        status=SettlementStatus.AUTHORIZED.value,
    )
    db_session.add(settlement)
    await db_session.flush()

    with pytest.raises(DistributionMainnetBlockedError, match="Base Mainnet economic distribution is hard-disabled"):
        await DistributionService.create_distribution(db_session, settlement.id, client_user)


# ==============================================================================
# 3. REORGANIZATION & RECONCILIATION HARDENING
# ==============================================================================

@pytest.mark.asyncio
async def test_distribution_reconciliation_advances_to_confirmed(db_session: AsyncSession, keypair):
    """Proves reconciliation advances SUBMITTED distribution to CONFIRMED when canonical event exists with depth."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    block_hash = "0x" + "33" * 32
    tx_hash = "0x" + "44" * 32

    settlement = Settlement(
        idempotency_key=f"reconcile:{uuid.uuid4().hex}",
        chain_id=CHAIN_ID_ANVIL,
        escrow_contract=cfg.contract_addresses["Escrow"].lower(),
        escrow_id=escrow_id,
        client_address=keypair["client"].lower(),
        beneficiary_address=keypair["beneficiary"].lower(),
        token_address=cfg.token_address.lower(),
        amount=10_000_000,
        action=SettlementAction.RELEASE.value,
        status=SettlementStatus.CONFIRMED.value,
    )
    db_session.add(settlement)
    await db_session.flush()

    dist = Distribution(
        settlement_id=settlement.id,
        chain_id=CHAIN_ID_ANVIL,
        escrow_contract=cfg.contract_addresses["Escrow"].lower(),
        distributor_contract=cfg.contract_addresses["RevenueDistributor"].lower(),
        escrow_id=escrow_id,
        distribution_key=f"dist:key:{uuid.uuid4().hex}",
        idempotency_key=f"idemp:key:{uuid.uuid4().hex}",
        gross_amount=10_000_000,
        developer_recipient=keypair["beneficiary"].lower(),
        developer_amount=8_500_000,
        staker_recipient="0x" + "51" * 20,
        staker_amount=1_000_000,
        dao_recipient="0x" + "da" * 20,
        dao_amount=500_000,
        token_address=cfg.token_address.lower(),
        status=DistributionStatus.SUBMITTED.value,
    )
    db_session.add(dist)

    event = BlockchainEvent(
        chain_id=CHAIN_ID_ANVIL,
        contract_address=cfg.contract_addresses["RevenueDistributor"].lower(),
        event_name="DistributionExecuted",
        block_number=200,
        block_hash=block_hash,
        transaction_hash=tx_hash,
        transaction_index=0,
        log_index=0,
        decoded_data={

            "escrowId": escrow_id,
            "grossAmount": 10_000_000,
            "developerAmount": 8_500_000,
            "stakerAmount": 1_000_000,
            "daoAmount": 500_000,
            "distributionVersion": 1,
        },
        status=EventStatus.CONFIRMED.value,
        is_canonical=True,
    )
    db_session.add(event)
    await db_session.flush()

    await DistributionReconciliationService.reconcile_distribution(db_session, dist.id)
    await db_session.refresh(dist)

    assert dist.status == DistributionStatus.CONFIRMED.value
    assert dist.distribution_tx_hash == tx_hash
    assert dist.block_number == 200
    assert dist.block_hash == block_hash
    assert dist.confirmed_at is not None


@pytest.mark.asyncio
async def test_distribution_reorg_invalidates_confirmed_distribution(db_session: AsyncSession, keypair):
    """Proves reorg invalidation transitions a CONFIRMED distribution to REORGED if event is orphaned."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    block_hash = "0x" + "55" * 32
    tx_hash = "0x" + "66" * 32

    settlement = Settlement(
        idempotency_key=f"reorg:dist:{uuid.uuid4().hex}",
        chain_id=CHAIN_ID_ANVIL,
        escrow_contract=cfg.contract_addresses["Escrow"].lower(),
        escrow_id=escrow_id,
        client_address=keypair["client"].lower(),
        beneficiary_address=keypair["beneficiary"].lower(),
        token_address=cfg.token_address.lower(),
        amount=10_000_000,
        action=SettlementAction.RELEASE.value,
        status=SettlementStatus.CONFIRMED.value,
    )
    db_session.add(settlement)
    await db_session.flush()

    event = BlockchainEvent(
        chain_id=CHAIN_ID_ANVIL,
        contract_address=cfg.contract_addresses["RevenueDistributor"].lower(),
        event_name="DistributionExecuted",
        block_number=300,
        block_hash=block_hash,
        transaction_hash=tx_hash,
        transaction_index=0,
        log_index=0,
        decoded_data={"escrowId": escrow_id},
        status=EventStatus.ORPHANED.value,
        is_canonical=False,
    )
    db_session.add(event)

    dist = Distribution(
        settlement_id=settlement.id,
        chain_id=CHAIN_ID_ANVIL,
        escrow_contract=cfg.contract_addresses["Escrow"].lower(),
        distributor_contract=cfg.contract_addresses["RevenueDistributor"].lower(),
        escrow_id=escrow_id,
        distribution_key=f"dist:key:{uuid.uuid4().hex}",
        idempotency_key=f"idemp:key:{uuid.uuid4().hex}",
        gross_amount=10_000_000,
        developer_recipient=keypair["beneficiary"].lower(),
        developer_amount=8_500_000,
        staker_recipient="0x" + "51" * 20,
        staker_amount=1_000_000,
        dao_recipient="0x" + "da" * 20,
        dao_amount=500_000,
        token_address=cfg.token_address.lower(),
        status=DistributionStatus.CONFIRMED.value,
        distribution_tx_hash=tx_hash,
        block_number=300,
        block_hash=block_hash,
        confirmed_at=datetime.now(timezone.utc),
    )
    db_session.add(dist)
    await db_session.flush()

    await DistributionReconciliationService.reconcile_distribution(db_session, dist.id)
    await db_session.refresh(dist)

    assert dist.status == DistributionStatus.REORGED.value
    assert "Reorganized" in (dist.error_message or "")


# ==============================================================================
# 4. OBSERVABILITY & METRICS BOUNDED CARDINALITY
# ==============================================================================

def test_metrics_bounded_cardinality():
    """Proves Prometheus metrics use strictly bounded label keys and no unbounded identifiers."""
    from app.core.metrics import metrics_registry

    # Record various distribution metrics
    DistributionMetrics.record_created(CHAIN_ID_ANVIL, 1)
    DistributionMetrics.record_submitted(CHAIN_ID_ANVIL, 1)
    DistributionMetrics.record_confirmed(CHAIN_ID_ANVIL, 1)
    DistributionMetrics.record_failed(CHAIN_ID_ANVIL, "err_insufficient_gas")
    DistributionMetrics.record_reorged(CHAIN_ID_ANVIL)
    DistributionMetrics.record_blocked(CHAIN_ID_ANVIL, "deep_reorg")
    DistributionMetrics.record_reconciliation(CHAIN_ID_ANVIL, "confirmed")

    # Verify no labels contain high-cardinality keys like addresses or tx hashes
    forbidden_keys = {"wallet", "address", "tx_hash", "escrow_id", "distribution_id", "user_id"}
    for key in metrics_registry.counters.keys():
        if "distribution" in key:
            for forbidden in forbidden_keys:
                assert f"{forbidden}=" not in key, f"Forbidden label key '{forbidden}' found in {key}"

