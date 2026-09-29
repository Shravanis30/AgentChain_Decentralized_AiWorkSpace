"""Derived on-chain Escrow state machine projector.

Translates canonical BlockchainEvents into queryable EscrowChainState projections:
- EscrowCreated  -> CREATED (0)
- EscrowFunded   -> FUNDED (1)
- EscrowLocked   -> LOCKED (2)
- EscrowReleased -> RELEASED (3)
- EscrowRefunded -> REFUNDED (4)
- DisputeOpened  -> DISPUTED (5)
- DisputeResolved-> RESOLVED (6)

Note: The blockchain remains authoritative; this table is an operational read model.
"""

import logging
from typing import Any
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utc_now
from app.models.blockchain import BlockchainEvent, EscrowChainState, EventStatus

logger = logging.getLogger(__name__)

EVENT_TO_STATE: dict[str, int] = {
    "EscrowCreated": 0,
    "EscrowFunded": 1,
    "EscrowLocked": 2,
    "EscrowReleased": 3,
    "EscrowRefunded": 4,
    "DisputeOpened": 5,
    "DisputeResolved": 6,
}


class EscrowStateProjector:
    """Projects canonical events onto derived EscrowChainState records."""

    def __init__(self, chain_id: int) -> None:
        self.chain_id = chain_id

    async def apply_event(
        self,
        session: AsyncSession,
        event: BlockchainEvent,
    ) -> EscrowChainState | None:
        """Applies a single canonical blockchain event to the derived escrow state."""
        if not event.is_canonical:
            return None

        decoded = event.decoded_data or {}
        escrow_id = decoded.get("escrowId")
        if not escrow_id:
            # Non-escrow-specific event (e.g. Paused, Unpaused)
            return None

        stmt = sa.select(EscrowChainState).where(
            EscrowChainState.chain_id == self.chain_id,
            EscrowChainState.escrow_id == escrow_id,
        )
        state_record = (await session.execute(stmt)).scalar_one_or_none()

        new_state = EVENT_TO_STATE.get(event.event_name)
        if new_state is None:
            return None

        if event.event_name == "EscrowCreated":
            if not state_record:
                state_record = EscrowChainState(
                    chain_id=self.chain_id,
                    escrow_id=escrow_id,
                    contract_address=event.contract_address,
                    client=decoded.get("client", ""),
                    developer=decoded.get("beneficiary", ""),
                    token="",
                    amount=int(decoded.get("amount", 0)),
                    reference_id=decoded.get("referenceId", ""),
                    salt=str(decoded.get("salt", "0")),
                    current_chain_state=new_state,
                    creation_tx_hash=event.transaction_hash,
                    latest_tx_hash=event.transaction_hash,
                    latest_block_number=event.block_number,
                    latest_block_hash=event.block_hash,
                    is_canonical=True,
                    confirmation_status=event.status,
                    last_reconciliation_at=utc_now(),
                )
                session.add(state_record)
            else:
                state_record.current_chain_state = new_state
                state_record.latest_tx_hash = event.transaction_hash
                state_record.latest_block_number = event.block_number
                state_record.latest_block_hash = event.block_hash
                state_record.is_canonical = True
                state_record.confirmation_status = event.status
                state_record.last_reconciliation_at = utc_now()
                state_record.updated_at = utc_now()
        else:
            if state_record:
                state_record.current_chain_state = new_state
                state_record.latest_tx_hash = event.transaction_hash
                state_record.latest_block_number = event.block_number
                state_record.latest_block_hash = event.block_hash
                state_record.is_canonical = True
                state_record.confirmation_status = event.status
                state_record.last_reconciliation_at = utc_now()
                state_record.updated_at = utc_now()

        await session.flush()
        return state_record

    async def sync_confirmation_status(
        self,
        session: AsyncSession,
        event: BlockchainEvent,
    ) -> None:
        """Propagates updated confirmation status from event to derived state."""
        decoded = event.decoded_data or {}
        escrow_id = decoded.get("escrowId")
        if not escrow_id:
            return

        stmt = sa.select(EscrowChainState).where(
            EscrowChainState.chain_id == self.chain_id,
            EscrowChainState.escrow_id == escrow_id,
        )
        state_record = (await session.execute(stmt)).scalar_one_or_none()
        if state_record and state_record.latest_tx_hash == event.transaction_hash:
            state_record.confirmation_status = event.status
            state_record.updated_at = utc_now()
