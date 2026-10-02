"""Phase 6.5.1 — PostgreSQL Integration, Concurrency, Reorg, and API Hardening Tests.

Connects to the real PostgreSQL database and verifies:
1. Database-level append-only immutability trigger on reputation_score_history
2. Concurrency: At least 10 concurrent calculations on the same agent/chain/policy
3. Reorg handling: Orphaned events excluded from score, history preserved
4. API endpoints & Admin security authorization matrix
"""

import asyncio
from datetime import datetime, timezone
import uuid
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory
from app.models.agent import Agent
from app.models.reputation import ReputationEvent, ReputationOutcomeType, ReputationStatus
from app.models.reputation_scoring import (
    ReputationPolicyVersion,
    ReputationScore,
    ReputationScoreHistory,
)
from app.models.user import User, UserRole
from app.services.blockchain.config import CHAIN_ID_ANVIL
from app.services.reputation_policy.policy_v1 import (
    COLD_START_SCORE,
    get_policy_v1,
)
from app.services.reputation_policy.scoring_service import (
    CalculationReason,
    ReputationScoringService,
)


@pytest.fixture
async def test_agent_and_user():
    """Create a test user and agent in PostgreSQL."""
    async with async_session_factory() as session:
        user_id = uuid.uuid4()
        user = User(
            id=user_id,
            wallet_address=f"0x{user_id.hex[:40]}",
            primary_role="ADMIN",
            legacy_role="ADMIN",
            additional_roles=[],
        )
        session.add(user)

        agent_id = uuid.uuid4()
        agent = Agent(
            id=agent_id,
            owner_user_id=user_id,
            name=f"Agent-{agent_id.hex[:8]}",
            slug=f"agent-{agent_id.hex[:8]}",
            description="Integration test agent",
            status="ACTIVE",
            is_active=True,
        )
        session.add(agent)
        await session.commit()

        yield agent, user


class TestPostgresImmutabilityTrigger:
    """Verifies PostgreSQL trigger blocks UPDATE and DELETE on reputation_score_history."""

    @pytest.mark.asyncio
    async def test_update_and_delete_blocked_by_trigger(self, test_agent_and_user):
        agent, user = test_agent_and_user

        async with async_session_factory() as session:
            score = await ReputationScoringService.calculate_and_persist_score(
                session=session,
                agent_id=agent.id,
                chain_id=CHAIN_ID_ANVIL,
                reason=CalculationReason.INITIAL_CALCULATION,
                actor_user=user,
            )
            await session.commit()

        # Retrieve history row
        async with async_session_factory() as session:
            stmt = sa.select(ReputationScoreHistory).where(ReputationScoreHistory.agent_id == agent.id)
            hist = (await session.execute(stmt)).scalars().first()
            assert hist is not None
            hist_id = hist.id

            # Attempt 1: UPDATE via SQL
            with pytest.raises(Exception) as exc_info:
                await session.execute(
                    sa.text("UPDATE reputation_score_history SET new_score_scaled = 9999 WHERE id = :hid"),
                    {"hid": hist_id},
                )
                await session.commit()
            assert "reputation_score_history is append-only and cannot be mutated or deleted" in str(exc_info.value)
            await session.rollback()

            # Attempt 2: DELETE via SQL
            with pytest.raises(Exception) as exc_info:
                await session.execute(
                    sa.text("DELETE FROM reputation_score_history WHERE id = :hid"),
                    {"hid": hist_id},
                )
                await session.commit()
            assert "reputation_score_history is append-only and cannot be mutated or deleted" in str(exc_info.value)
            await session.rollback()


class TestConcurrentScoring:
    """Verifies at least 10 concurrent score calculations for same agent/chain/policy on real PostgreSQL."""

    @pytest.mark.asyncio
    async def test_10_concurrent_score_calculations(self, test_agent_and_user):
        agent, user = test_agent_and_user

        async def calculate_task(idx: int):
            async with async_session_factory() as session:
                try:
                    score = await ReputationScoringService.calculate_and_persist_score(
                        session=session,
                        agent_id=agent.id,
                        chain_id=CHAIN_ID_ANVIL,
                        reason=CalculationReason.MANUAL_RECALCULATION,
                        actor_user=user,
                    )
                    await session.commit()
                    return score.score_scaled
                except Exception as e:
                    await session.rollback()
                    # If serialized transaction retry or lock wait occurs
                    return None

        # Execute 10 concurrent calculations
        tasks = [calculate_task(i) for i in range(10)]
        results = await asyncio.gather(*tasks)

        # Inspect resulting database state
        async with async_session_factory() as session:
            # Exactly 1 active score record must exist for this agent x chain x policy
            score_stmt = sa.select(ReputationScore).where(
                ReputationScore.agent_id == agent.id,
                ReputationScore.chain_id == CHAIN_ID_ANVIL,
            )
            scores = (await session.execute(score_stmt)).scalars().all()
            assert len(scores) == 1
            assert scores[0].score_scaled == 5000  # Cold start score
            assert scores[0].experience_count == 0

            # History rows must be intact and valid
            hist_stmt = sa.select(ReputationScoreHistory).where(
                ReputationScoreHistory.agent_id == agent.id,
                ReputationScoreHistory.chain_id == CHAIN_ID_ANVIL,
            )
            history_rows = (await session.execute(hist_stmt)).scalars().all()
            assert len(history_rows) >= 1
            for row in history_rows:
                assert row.new_score_scaled == 5000


class TestReorgRecalculation:
    """Verifies that blockchain reorgs properly recalculate reputation score and preserve history."""

    @pytest.mark.asyncio
    async def test_reorg_orphaning_and_score_recalculation(self, test_agent_and_user):
        agent, user = test_agent_and_user

        # 1. Create agent version, execution, and confirmed canonical success event
        ev_id = uuid.uuid4()
        async with async_session_factory() as session:
            from app.models.agent import AgentExecution, AgentStatus, AgentVersion, ExecutionStatus
            version = AgentVersion(
                agent_id=agent.id,
                version="1.0.0",
                manifest={"entrypoint": "main:run"},
                input_schema={},
                output_schema={},
            )
            session.add(version)
            await session.flush()

            execution = AgentExecution(
                agent_id=agent.id,
                agent_version_id=version.id,
                requested_by=user.id,
                status=ExecutionStatus.SUCCEEDED.value,
                input_data={},
                input_hash="00" * 32,
                output_data={"ok": True},
                output_hash="11" * 32,
            )
            session.add(execution)
            await session.flush()

            ev = ReputationEvent(
                id=ev_id,
                idempotency_key=f"idemp_{ev_id.hex[:16]}",
                agent_id=agent.id,
                chain_id=CHAIN_ID_ANVIL,
                execution_id=execution.id,
                agent_version_id=version.id,
                outcome_type=ReputationOutcomeType.VERIFIED_SUCCESS.value,
                reputation_key=f"repkey_{ev_id.hex[:16]}",
                evidence_hash="ev_1",
                result_hash="res_1",
                contract_address="0x" + "11" * 20,
                status=ReputationStatus.CONFIRMED.value,
                is_canonical=True,
                confirmations=32,
                confirmed_at=datetime.now(timezone.utc),
            )
            session.add(ev)
            await session.commit()

        # 2. Calculate score with event A -> score = 10000
        async with async_session_factory() as session:
            score = await ReputationScoringService.calculate_and_persist_score(
                session=session,
                agent_id=agent.id,
                chain_id=CHAIN_ID_ANVIL,
                reason=CalculationReason.NEW_VERIFIED_EVENT,
                actor_user=user,
            )
            await session.commit()
            assert score.score_scaled == 10000
            assert score.total_verified_executions == 1
            assert score.verified_successes == 1

        # 3. Reorg occurs: event A orphaned
        async with async_session_factory() as session:
            stmt = sa.select(ReputationEvent).where(ReputationEvent.id == ev_id)
            ev = (await session.execute(stmt)).scalar_one()
            ev.is_canonical = False
            ev.status = ReputationStatus.REORGED.value
            await session.commit()

        # 4. Trigger reorg recalculation
        async with async_session_factory() as session:
            reorg_score = await ReputationScoringService.handle_reorg_recalculation(
                session=session,
                agent_id=agent.id,
                chain_id=CHAIN_ID_ANVIL,
            )
            await session.commit()
            # Since event A is now non-canonical and REORGED, it is excluded!
            # Active evidence count is 0 -> score reverts to cold start (5000)
            assert reorg_score.score_scaled == 5000
            assert reorg_score.total_verified_executions == 0
            assert reorg_score.verified_successes == 0
            assert reorg_score.calculation_reason == CalculationReason.REORG_RECALCULATION

        # 5. Verify history log preserved both calculations
        async with async_session_factory() as session:
            hist_stmt = (
                sa.select(ReputationScoreHistory)
                .where(ReputationScoreHistory.agent_id == agent.id)
                .order_by(ReputationScoreHistory.calculated_at.asc())
            )
            history = (await session.execute(hist_stmt)).scalars().all()
            assert len(history) >= 2
            # First calculation: 10000
            assert history[0].new_score_scaled == 10000
            assert history[0].total_verified_executions == 1
            # Second calculation (reorg): reverted to 5000
            assert history[-1].new_score_scaled == 5000
            assert history[-1].total_verified_executions == 0
            assert history[-1].previous_score_scaled == 10000
            assert history[-1].calculation_reason == CalculationReason.REORG_RECALCULATION


class TestApiAuthorizationAndHardening:
    """Verifies API endpoints and admin authorization boundaries."""

    @pytest.mark.asyncio
    async def test_reputation_api_flow(self, async_client, test_agent_and_user):
        agent, user = test_agent_and_user

        # 1. GET score (public)
        res = await async_client.get(f"/api/v1/agents/{agent.id}/reputation/score")
        assert res.status_code == 200
        data = res.json()
        assert data["agent_id"] == str(agent.id)
        assert data["score_scaled"] == 5000
        assert data["score_float"] == 0.5
        assert data["policy_version"] == "ReputationPolicyV1"

        # 2. GET metrics (public)
        res_m = await async_client.get(f"/api/v1/agents/{agent.id}/reputation/metrics")
        assert res_m.status_code == 200
        data_m = res_m.json()
        assert data_m["agent_id"] == str(agent.id)
        assert data_m["total_verified_executions"] == 0

        # 3. GET explanation (public)
        res_e = await async_client.get(f"/api/v1/agents/{agent.id}/reputation/explanation")
        assert res_e.status_code == 200
        data_e = res_e.json()
        assert data_e["is_cold_start"] is True

        # 4. GET policy (public)
        res_p = await async_client.get(f"/api/v1/agents/{agent.id}/reputation/policy")
        assert res_p.status_code == 200
        data_p = res_p.json()
        assert data_p["version_string"] == "ReputationPolicyV1"

        # 5. GET evidence (public)
        res_ev = await async_client.get(f"/api/v1/agents/{agent.id}/reputation/evidence")
        assert res_ev.status_code == 200
        data_ev = res_ev.json()
        assert "evidence_set_hash" in data_ev

        # 6. POST recalculate as anonymous -> 401 Unauthorized
        res_anon = await async_client.post(
            f"/api/v1/agents/{agent.id}/reputation/recalculate",
            json={"chain_id": CHAIN_ID_ANVIL},
        )
        assert res_anon.status_code == 401

        # 7. POST recalculate with injected malicious payload:
        # Attempt to set score=9999 as ADMIN.
        # Should succeed because user is admin, BUT score must be derived from evidence (5000), not 9999!
        from app.core.dependencies import require_admin_user
        from app.main import app

        app.dependency_overrides[require_admin_user] = lambda: user
        try:
            res_admin_inj = await async_client.post(
                f"/api/v1/agents/{agent.id}/reputation/recalculate",
                json={
                    "chain_id": CHAIN_ID_ANVIL,
                    "score": 9999,
                    "score_scaled": 9999,
                    "evidence_set_hash": "malicious_hash",
                },
            )
            assert res_admin_inj.status_code == 200
            inj_data = res_admin_inj.json()
            # Verified: Injected score was COMPLETELY IGNORED. Derived score is 5000!
            assert inj_data["score_scaled"] == 5000
            assert inj_data["score_scaled"] != 9999
        finally:
            app.dependency_overrides.pop(require_admin_user, None)
