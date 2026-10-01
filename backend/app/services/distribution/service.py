import logging
from typing import Any
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from app.models.audit import AuditEventType, AuditLog
from app.models.base import utc_now
from app.models.distribution import (
    Distribution,
    DistributionHistory,
    DistributionStatus,
)
from app.models.settlement import (
    Settlement,
    SettlementAction,
    SettlementStatus,
)
from app.models.user import User
from app.services.blockchain.config import (
    CHAIN_ID_BASE_MAINNET,
    get_chain_config,
)
from app.services.distribution.config import (
    DISTRIBUTION_VERSION,
    compute_85_10_5_split,
    get_platform_recipients,
    validate_distribution_recipients,
)
from app.services.distribution.errors import (
    DistributionAuthorizationError,
    DistributionMainnetBlockedError,
    DistributionNotFoundError,
    DistributionStateConflictError,
)
from app.services.distribution.metrics import DistributionMetrics
from app.services.settlement.config import BLOCK_MAINNET_SETTLEMENT

logger = logging.getLogger(__name__)


class DistributionService:
    """Manages the creation, querying, and state lifecycle of 85/10/5 economic distributions."""

    @staticmethod
    async def create_distribution(
        session: AsyncSession,
        settlement_id: uuid.UUID,
        idempotency_key: str | None = None,
        metadata_json: dict[str, Any] | None = None,
        actor_user: User | None = None,
        client_ip: str | None = None,
    ) -> tuple[Distribution, bool]:
        """Creates an 85/10/5 distribution record idempotently for an authorized settlement release.
        
        Returns:
            (distribution, was_created)
        """
        # 1. Fetch settlement with lock
        stmt = (
            sa.select(Settlement)
            .where(Settlement.id == settlement_id)
            .with_for_update()
        )
        settlement = (await session.execute(stmt)).scalar_one_or_none()
        if not settlement:
            raise DistributionNotFoundError(f"Settlement {settlement_id} not found")

        # 2. Mainnet guard check
        if settlement.chain_id == CHAIN_ID_BASE_MAINNET and BLOCK_MAINNET_SETTLEMENT:
            DistributionMetrics.record_blocked(settlement.chain_id, "mainnet_blocked")
            raise DistributionMainnetBlockedError(
                "Base Mainnet economic distribution is hard-disabled"
            )

        # 3. Action validation: only RELEASE actions trigger 85/10/5 splits
        if settlement.action != SettlementAction.RELEASE.value:
            raise DistributionStateConflictError(
                f"Action '{settlement.action}' does not trigger revenue distribution. Only RELEASE triggers 85/10/5 split."
            )

        # 4. State validation: settlement must have been authorized by the settlement authorizer
        if settlement.status == SettlementStatus.PENDING_AUTHORIZATION.value:
            raise DistributionAuthorizationError(
                f"Settlement {settlement_id} is still in PENDING_AUTHORIZATION state. Cannot distribute prior to authorization."
            )
        if settlement.status in (
            SettlementStatus.FAILED.value,
            SettlementStatus.CANCELLED.value,
            SettlementStatus.BLOCKED.value,
        ):
            raise DistributionStateConflictError(
                f"Cannot create distribution for settlement in terminal/blocked status '{settlement.status}'"
            )

        # 5. Deterministic identity keys
        distribution_key = (
            f"distribution:{settlement.chain_id}:{settlement.escrow_id}:{settlement.id}:{DISTRIBUTION_VERSION}"
        )
        idemp_key = (
            idempotency_key
            or f"dist:{settlement.chain_id}:{settlement.id}:{DISTRIBUTION_VERSION}"
        )

        # 6. Idempotency check: see if distribution already exists
        existing_stmt = sa.select(Distribution).where(
            sa.or_(
                Distribution.distribution_key == distribution_key,
                sa.and_(
                    Distribution.chain_id == settlement.chain_id,
                    Distribution.idempotency_key == idemp_key,
                ),
            )
        )
        existing = (await session.execute(existing_stmt)).scalar_one_or_none()
        if existing:
            logger.info("Distribution %s already exists for settlement %s", existing.id, settlement_id)
            return existing, False

        # 7. Resolve recipients
        developer_recipient = Web3.to_checksum_address(settlement.beneficiary_address)
        platform_recipients = get_platform_recipients(settlement.chain_id)
        staker_recipient = platform_recipients.staker_recipient
        dao_recipient = platform_recipients.dao_recipient

        # Validate zero addresses and mutual exclusivity
        validate_distribution_recipients(developer_recipient, staker_recipient, dao_recipient)

        # 8. Compute deterministic 85/10/5 integer split
        gross_amount = int(settlement.amount)
        dev_amt, stk_amt, dao_amt = compute_85_10_5_split(gross_amount)

        # 9. Resolve distributor contract address
        chain_config = get_chain_config(settlement.chain_id)
        distributor_contract = (
            chain_config.contract_addresses.get("RevenueDistributor")
            or "0x0000000000000000000000000000000000000000"
        ).lower()

        # 10. Initial status based on settlement lifecycle
        now = utc_now()
        initial_status = DistributionStatus.PENDING.value
        confirmed_at = None
        if settlement.status == SettlementStatus.CONFIRMED.value:
            initial_status = DistributionStatus.CONFIRMED.value
            confirmed_at = settlement.confirmed_at or now
        elif settlement.status == SettlementStatus.SUBMITTED.value:
            initial_status = DistributionStatus.SUBMITTED.value
        elif settlement.status == SettlementStatus.AUTHORIZED.value:
            initial_status = DistributionStatus.AUTHORIZED.value

        distribution = Distribution(
            idempotency_key=idemp_key,
            distribution_key=distribution_key,
            chain_id=settlement.chain_id,
            escrow_contract=settlement.escrow_contract,
            escrow_id=settlement.escrow_id,
            settlement_id=settlement.id,
            distributor_contract=distributor_contract,
            token_address=settlement.token_address,
            gross_amount=gross_amount,
            developer_amount=dev_amt,
            developer_recipient=developer_recipient,
            staker_amount=stk_amt,
            staker_recipient=staker_recipient,
            dao_amount=dao_amt,
            dao_recipient=dao_recipient,
            distribution_version=DISTRIBUTION_VERSION,
            status=initial_status,
            transaction_intent_id=settlement.transaction_intent_id,
            distribution_tx_hash=settlement.settlement_tx_hash,
            block_number=settlement.block_number,
            block_hash=settlement.block_hash,
            confirmations=settlement.confirmations,
            metadata_json=metadata_json or {},
            created_at=now,
            updated_at=now,
            confirmed_at=confirmed_at,
        )
        session.add(distribution)
        await session.flush()

        # 11. History record
        history = DistributionHistory(
            distribution_id=distribution.id,
            from_status=DistributionStatus.PENDING.value,
            to_status=initial_status,
            reason="Distribution record created from authorized settlement release",
            metadata_json={
                "gross_amount": str(gross_amount),
                "dev_amount": str(dev_amt),
                "stk_amount": str(stk_amt),
                "dao_amount": str(dao_amt),
            },
            created_at=now,
        )
        session.add(history)

        # 12. Audit log
        audit = AuditLog(
            user_id=actor_user.id if actor_user else settlement.user_id,
            wallet_address=actor_user.wallet_address if actor_user else None,
            event_type=AuditEventType.DISTRIBUTION_CREATED.value,
            timestamp=now,
            ip_address=client_ip,
            metadata_json={
                "distribution_id": str(distribution.id),
                "settlement_id": str(settlement.id),
                "chain_id": settlement.chain_id,
                "gross_amount": str(gross_amount),
                "developer_recipient": developer_recipient,
                "developer_amount": str(dev_amt),
                "staker_recipient": staker_recipient,
                "staker_amount": str(stk_amt),
                "dao_recipient": dao_recipient,
                "dao_amount": str(dao_amt),
            },
        )
        session.add(audit)
        await session.flush()

        DistributionMetrics.record_created(settlement.chain_id, DISTRIBUTION_VERSION)
        logger.info(
            "Created distribution %s for settlement %s (gross: %d, dev: %d, stk: %d, dao: %d)",
            distribution.id,
            settlement.id,
            gross_amount,
            dev_amt,
            stk_amt,
            dao_amt,
        )
        return distribution, True

    @staticmethod
    async def get_distribution(
        session: AsyncSession,
        distribution_id: uuid.UUID,
    ) -> Distribution:
        stmt = sa.select(Distribution).where(Distribution.id == distribution_id)
        dist = (await session.execute(stmt)).scalar_one_or_none()
        if not dist:
            raise DistributionNotFoundError(f"Distribution {distribution_id} not found")
        return dist

    @staticmethod
    async def get_distribution_by_key(
        session: AsyncSession,
        distribution_key: str,
    ) -> Distribution | None:
        stmt = sa.select(Distribution).where(Distribution.distribution_key == distribution_key)
        return (await session.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def list_distributions(
        session: AsyncSession,
        chain_id: int | None = None,
        status: str | None = None,
        escrow_id: str | None = None,
        settlement_id: uuid.UUID | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[Distribution], int]:
        query = sa.select(Distribution)
        count_query = sa.select(sa.func.count(Distribution.id))

        filters = []
        if chain_id is not None:
            filters.append(Distribution.chain_id == chain_id)
        if status is not None:
            filters.append(Distribution.status == status)
        if escrow_id is not None:
            filters.append(Distribution.escrow_id == escrow_id)
        if settlement_id is not None:
            filters.append(Distribution.settlement_id == settlement_id)

        if filters:
            query = query.where(sa.and_(*filters))
            count_query = count_query.where(sa.and_(*filters))

        total = (await session.execute(count_query)).scalar_one() or 0
        distributions = (
            await session.execute(
                query.order_by(Distribution.created_at.desc()).offset(skip).limit(limit)
            )
        ).scalars().all()

        return list(distributions), total
