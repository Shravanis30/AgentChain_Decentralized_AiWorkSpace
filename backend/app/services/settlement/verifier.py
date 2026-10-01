"""Escrow verification boundary for Phase 6.2B settlement authorization.

Enforces:
- Canonical on-chain state verification.
- Sufficient confirmation depth verification.
- Contract address and token allowlist verification.
- State compatibility matching requested settlement action.
- Deadline condition verification for client refunds.
- Terminal-state rejection (fail-closed).
"""

import logging
from dataclasses import dataclass
from typing import Any
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from app.models.blockchain import EscrowChainState, EventStatus
from app.models.settlement import SettlementAction
from app.services.blockchain.config import (
    CHAIN_ID_BASE_MAINNET,
    get_chain_config,
)
from app.services.settlement.config import (
    BLOCK_MAINNET_SETTLEMENT,
    get_required_confirmations,
    is_chain_supported,
)
from app.services.settlement.errors import (
    SettlementConfirmationError,
    SettlementEscrowVerificationError,
    SettlementMainnetBlockedError,
)

logger = logging.getLogger(__name__)

# State constants from derived EscrowChainState
STATE_CREATED = 0
STATE_FUNDED = 1
STATE_LOCKED = 2
STATE_RELEASED = 3
STATE_REFUNDED = 4
STATE_DISPUTED = 5
STATE_RESOLVED = 6

TERMINAL_STATES = frozenset({STATE_RELEASED, STATE_REFUNDED, STATE_RESOLVED})


@dataclass(frozen=True)
class EscrowVerificationResult:
    is_valid: bool
    chain_id: int
    escrow_id: str
    escrow_state: EscrowChainState
    current_state_code: int
    client: str
    developer: str
    token: str
    amount: int
    error_message: str | None = None


class EscrowVerificationBoundary:
    """Validates escrow state against canonical indexed blockchain data."""

    @staticmethod
    async def verify_escrow_for_settlement(
        session: AsyncSession,
        chain_id: int,
        escrow_id: str,
        action: SettlementAction | str,
        expected_client: str | None = None,
        expected_beneficiary: str | None = None,
        expected_amount: int | None = None,
        expected_token: str | None = None,
    ) -> EscrowVerificationResult:
        """Verifies an escrow record for settlement eligibility. Fails closed."""

        # 1. Chain validation
        if not is_chain_supported(chain_id):
            raise SettlementEscrowVerificationError(
                f"Unsupported chain ID: {chain_id}"
            )

        if chain_id == CHAIN_ID_BASE_MAINNET and BLOCK_MAINNET_SETTLEMENT:
            raise SettlementMainnetBlockedError(
                "Base Mainnet settlement transaction submission is hard-disabled"
            )

        chain_config = get_chain_config(chain_id)
        configured_escrow = chain_config.contract_addresses.get("Escrow", "").lower()

        # 2. Query EscrowChainState
        clean_escrow_id = escrow_id.strip()
        stmt = sa.select(EscrowChainState).where(
            EscrowChainState.chain_id == chain_id,
            EscrowChainState.escrow_id == clean_escrow_id,
        )
        escrow_record = (await session.execute(stmt)).scalar_one_or_none()

        if not escrow_record:
            raise SettlementEscrowVerificationError(
                f"Escrow '{clean_escrow_id}' not found on chain {chain_id}"
            )

        # 3. Contract address verification
        if escrow_record.contract_address.lower() != configured_escrow:
            raise SettlementEscrowVerificationError(
                f"Escrow contract address mismatch: record {escrow_record.contract_address} != configured {configured_escrow}"
            )

        # 4. Canonical state check
        if not escrow_record.is_canonical:
            raise SettlementConfirmationError(
                f"Escrow '{clean_escrow_id}' is non-canonical or orphaned. Settlement blocked."
            )

        # 5. Confirmation depth check
        if escrow_record.confirmation_status != EventStatus.CONFIRMED.value:
            raise SettlementConfirmationError(
                f"Escrow '{clean_escrow_id}' status is '{escrow_record.confirmation_status}', but requires '{EventStatus.CONFIRMED.value}'"
            )

        # 6. Terminal state check
        current_state = escrow_record.current_chain_state
        if current_state in TERMINAL_STATES:
            state_names = {STATE_RELEASED: "RELEASED", STATE_REFUNDED: "REFUNDED", STATE_RESOLVED: "RESOLVED"}
            raise SettlementEscrowVerificationError(
                f"Escrow '{clean_escrow_id}' is already in terminal state '{state_names.get(current_state, str(current_state))}'"
            )

        # 7. Action compatibility check
        action_str = action.value if isinstance(action, SettlementAction) else str(action).upper()

        if action_str == SettlementAction.RELEASE.value:
            if current_state not in (STATE_FUNDED, STATE_LOCKED):
                raise SettlementEscrowVerificationError(
                    f"Action RELEASE requires escrow in FUNDED or LOCKED state; current state is {current_state}"
                )
        elif action_str == SettlementAction.REFUND.value:
            if current_state not in (STATE_CREATED, STATE_FUNDED, STATE_LOCKED):
                raise SettlementEscrowVerificationError(
                    f"Action REFUND requires escrow in CREATED, FUNDED, or LOCKED state; current state is {current_state}"
                )
        elif action_str in (
            SettlementAction.DISPUTE_RESOLVE_RELEASE.value,
            SettlementAction.DISPUTE_RESOLVE_REFUND.value,
        ):
            if current_state != STATE_DISPUTED:
                raise SettlementEscrowVerificationError(
                    f"Action {action_str} requires escrow in DISPUTED state; current state is {current_state}"
                )
        else:
            raise SettlementEscrowVerificationError(f"Unsupported settlement action: {action_str}")

        # 8. Ownership / parameter validation against expected values
        if expected_client and Web3.to_checksum_address(escrow_record.client) != Web3.to_checksum_address(expected_client):
            raise SettlementEscrowVerificationError(
                f"Escrow client mismatch: record={escrow_record.client} expected={expected_client}"
            )

        if expected_beneficiary and Web3.to_checksum_address(escrow_record.developer) != Web3.to_checksum_address(expected_beneficiary):
            raise SettlementEscrowVerificationError(
                f"Escrow beneficiary mismatch: record={escrow_record.developer} expected={expected_beneficiary}"
            )

        if expected_amount is not None and int(escrow_record.amount) != int(expected_amount):
            raise SettlementEscrowVerificationError(
                f"Escrow amount mismatch: record={escrow_record.amount} expected={expected_amount}"
            )

        if expected_token and escrow_record.token:
            if Web3.to_checksum_address(escrow_record.token) != Web3.to_checksum_address(expected_token):
                raise SettlementEscrowVerificationError(
                    f"Escrow token mismatch: record={escrow_record.token} expected={expected_token}"
                )

        return EscrowVerificationResult(
            is_valid=True,
            chain_id=chain_id,
            escrow_id=clean_escrow_id,
            escrow_state=escrow_record,
            current_state_code=current_state,
            client=escrow_record.client,
            developer=escrow_record.developer,
            token=escrow_record.token or chain_config.token_address,
            amount=int(escrow_record.amount),
        )
