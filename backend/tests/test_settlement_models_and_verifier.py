"""Tests for EscrowVerificationBoundary, Mainnet hard block, and settlement domain invariants."""

import uuid
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.blockchain import EscrowChainState, EventStatus
from app.models.settlement import Settlement, SettlementAction, SettlementStatus
from app.services.blockchain.config import (
    CHAIN_ID_ANVIL,
    CHAIN_ID_BASE_MAINNET,
    CHAIN_ID_BASE_SEPOLIA,
    get_chain_config,
)
from app.services.settlement.config import BLOCK_MAINNET_SETTLEMENT
from app.services.settlement.errors import (
    SettlementConfirmationError,
    SettlementEscrowVerificationError,
    SettlementMainnetBlockedError,
)
from app.services.settlement.idempotency import generate_settlement_idempotency_key
from app.services.settlement.verifier import (
    EscrowVerificationBoundary,
    STATE_CREATED,
    STATE_FUNDED,
    STATE_LOCKED,
    STATE_RELEASED,
    STATE_REFUNDED,
    STATE_DISPUTED,
    STATE_RESOLVED,
)


@pytest.mark.asyncio
async def test_idempotency_key_generation():
    """Validates deterministic idempotency key format and normalization."""
    key1 = generate_settlement_idempotency_key(31337, "0xABC123", "RELEASE", 1)
    key2 = generate_settlement_idempotency_key(31337, "0xabc123", "release", 1)
    assert key1 == key2
    assert key1 == "settlement:31337:0xabc123:RELEASE:1"


@pytest.mark.asyncio
async def test_mainnet_hard_block_verifier(db_session: AsyncSession):
    """Verifies that Base Mainnet settlement is strictly blocked and fails closed."""
    assert BLOCK_MAINNET_SETTLEMENT is True
    with pytest.raises(SettlementMainnetBlockedError) as exc_info:
        await EscrowVerificationBoundary.verify_escrow_for_settlement(
            session=db_session,
            chain_id=CHAIN_ID_BASE_MAINNET,
            escrow_id="0x" + "11" * 32,
            action=SettlementAction.RELEASE,
        )
    assert "hard-disabled" in str(exc_info.value)


@pytest.mark.asyncio
async def test_verifier_unknown_escrow(db_session: AsyncSession):
    """Rejects non-existent escrow IDs."""
    with pytest.raises(SettlementEscrowVerificationError) as exc_info:
        await EscrowVerificationBoundary.verify_escrow_for_settlement(
            session=db_session,
            chain_id=CHAIN_ID_ANVIL,
            escrow_id="0x" + "99" * 32,
            action=SettlementAction.RELEASE,
        )
    assert "not found" in str(exc_info.value)


@pytest.mark.asyncio
async def test_verifier_contract_mismatch(db_session: AsyncSession):
    """Rejects escrow records with malicious or mismatched contract address."""
    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address="0x" + "de" * 20,  # Rogue address
        client="0x" + "11" * 20,
        developer="0x" + "22" * 20,
        amount=10_000_000,
        reference_id="0x" + "33" * 32,
        current_chain_state=STATE_LOCKED,
        creation_tx_hash="0x" + "44" * 32,
        latest_tx_hash="0x" + "44" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(record)
    await db_session.flush()

    with pytest.raises(SettlementEscrowVerificationError) as exc_info:
        await EscrowVerificationBoundary.verify_escrow_for_settlement(
            session=db_session,
            chain_id=CHAIN_ID_ANVIL,
            escrow_id=escrow_id,
            action=SettlementAction.RELEASE,
        )
    assert "contract address mismatch" in str(exc_info.value)


@pytest.mark.asyncio
async def test_verifier_non_canonical_orphaned_escrow(db_session: AsyncSession):
    """Rejects escrows whose on-chain projection is marked non-canonical / orphaned."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_contract = cfg.contract_addresses["Escrow"]

    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=escrow_contract,
        client="0x" + "11" * 20,
        developer="0x" + "22" * 20,
        amount=10_000_000,
        reference_id="0x" + "33" * 32,
        current_chain_state=STATE_LOCKED,
        creation_tx_hash="0x" + "44" * 32,
        latest_tx_hash="0x" + "44" * 32,
        is_canonical=False,  # Orphaned
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(record)
    await db_session.flush()

    with pytest.raises(SettlementConfirmationError) as exc_info:
        await EscrowVerificationBoundary.verify_escrow_for_settlement(
            session=db_session,
            chain_id=CHAIN_ID_ANVIL,
            escrow_id=escrow_id,
            action=SettlementAction.RELEASE,
        )
    assert "non-canonical or orphaned" in str(exc_info.value)


@pytest.mark.asyncio
async def test_verifier_unconfirmed_escrow(db_session: AsyncSession):
    """Rejects escrows that have not reached required confirmation threshold."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_contract = cfg.contract_addresses["Escrow"]

    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=escrow_contract,
        client="0x" + "11" * 20,
        developer="0x" + "22" * 20,
        amount=10_000_000,
        reference_id="0x" + "33" * 32,
        current_chain_state=STATE_LOCKED,
        creation_tx_hash="0x" + "44" * 32,
        latest_tx_hash="0x" + "44" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.SEEN.value,  # Only SEEN, not CONFIRMED
    )
    db_session.add(record)
    await db_session.flush()

    with pytest.raises(SettlementConfirmationError) as exc_info:
        await EscrowVerificationBoundary.verify_escrow_for_settlement(
            session=db_session,
            chain_id=CHAIN_ID_ANVIL,
            escrow_id=escrow_id,
            action=SettlementAction.RELEASE,
        )
    assert "requires 'CONFIRMED'" in str(exc_info.value)


@pytest.mark.asyncio
async def test_verifier_terminal_escrow_rejection(db_session: AsyncSession):
    """Rejects settlement authorization on escrows already in terminal states."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_contract = cfg.contract_addresses["Escrow"]

    for term_state in (STATE_RELEASED, STATE_REFUNDED, STATE_RESOLVED):
        escrow_id = "0x" + uuid.uuid4().hex
        record = EscrowChainState(
            chain_id=CHAIN_ID_ANVIL,
            escrow_id=escrow_id,
            contract_address=escrow_contract,
            client="0x" + "11" * 20,
            developer="0x" + "22" * 20,
            amount=10_000_000,
            reference_id="0x" + "33" * 32,
            current_chain_state=term_state,
            creation_tx_hash="0x" + "44" * 32,
            latest_tx_hash="0x" + "44" * 32,
            is_canonical=True,
            confirmation_status=EventStatus.CONFIRMED.value,
        )
        db_session.add(record)
        await db_session.flush()

        with pytest.raises(SettlementEscrowVerificationError) as exc_info:
            await EscrowVerificationBoundary.verify_escrow_for_settlement(
                session=db_session,
                chain_id=CHAIN_ID_ANVIL,
                escrow_id=escrow_id,
                action=SettlementAction.RELEASE,
            )
        assert "already in terminal state" in str(exc_info.value)


@pytest.mark.asyncio
async def test_verifier_mismatched_parameters(db_session: AsyncSession):
    """Rejects settlement when client, beneficiary, or amount differs from expected."""
    cfg = get_chain_config(CHAIN_ID_ANVIL)
    escrow_contract = cfg.contract_addresses["Escrow"]

    escrow_id = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    record = EscrowChainState(
        chain_id=CHAIN_ID_ANVIL,
        escrow_id=escrow_id,
        contract_address=escrow_contract,
        client="0x" + "11" * 20,
        developer="0x" + "22" * 20,
        amount=10_000_000,
        reference_id="0x" + "33" * 32,
        current_chain_state=STATE_LOCKED,
        creation_tx_hash="0x" + "44" * 32,
        latest_tx_hash="0x" + "44" * 32,
        is_canonical=True,
        confirmation_status=EventStatus.CONFIRMED.value,
    )
    db_session.add(record)
    await db_session.flush()

    # Wrong client
    with pytest.raises(SettlementEscrowVerificationError) as exc:
        await EscrowVerificationBoundary.verify_escrow_for_settlement(
            session=db_session,
            chain_id=CHAIN_ID_ANVIL,
            escrow_id=escrow_id,
            action=SettlementAction.RELEASE,
            expected_client="0x" + "99" * 20,
        )
    assert "client mismatch" in str(exc.value)

    # Wrong beneficiary
    with pytest.raises(SettlementEscrowVerificationError) as exc:
        await EscrowVerificationBoundary.verify_escrow_for_settlement(
            session=db_session,
            chain_id=CHAIN_ID_ANVIL,
            escrow_id=escrow_id,
            action=SettlementAction.RELEASE,
            expected_beneficiary="0x" + "99" * 20,
        )
    assert "beneficiary mismatch" in str(exc.value)

    # Wrong amount
    with pytest.raises(SettlementEscrowVerificationError) as exc:
        await EscrowVerificationBoundary.verify_escrow_for_settlement(
            session=db_session,
            chain_id=CHAIN_ID_ANVIL,
            escrow_id=escrow_id,
            action=SettlementAction.RELEASE,
            expected_amount=99_000_000,
        )
    assert "amount mismatch" in str(exc.value)
