import enum
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.agent import Agent, AgentCapability, AgentStatus, AgentVersion
from app.models.agent_selection import SelectionDecision
from app.models.reputation_scoring import ReputationScore
from app.schemas.orchestration import TaskDefinitionInput
from app.services.hashing import compute_canonical_hash
from app.services.pricing import (
    extract_version_price,
    validate_budget_constraint,
    validate_runtime_requirements,
    validate_tool_requirements,
)

logger = logging.getLogger("agentchain.agent_selector")

# ──────────────────────────────────────────────────────────────────────────────
# Policy version constants
# ──────────────────────────────────────────────────────────────────────────────

SELECTION_POLICY_VERSION = "SelectionPolicyV1"
ECONOMIC_POLICY_VERSION = "EconomicConstraintPolicyV1"

# Cold-start score mirrors ReputationPolicyV1.COLD_START_SCORE intentionally.
# It is the score the policy assigns to agents with N=0 verified outcomes.
# Agents with NO reputation row are treated as REPUTATION_ABSENT — see below.
COLD_START_SCORE = 5000


# ──────────────────────────────────────────────────────────────────────────────
# Reputation State Enum
# ──────────────────────────────────────────────────────────────────────────────

class ReputationState(str, enum.Enum):
    """
    Explicitly distinguishes four reputation states for each candidate.

    CANONICAL  — A ReputationScore row exists and was produced by a canonical
                 ReputationPolicyV1 calculation. score_scaled comes from that row.

    COLD_START — A ReputationScore row exists with total_verified_executions == 0
                 (N=0 in the policy formula). The policy explicitly assigns 5000.
                 This is a valid, calculable state, not an absence of data.

    ABSENT     — No ReputationScore row exists for this agent. The selector
                 defaults to COLD_START_SCORE (5000) to allow new agents to
                 participate, but this state MUST be recorded explicitly so it
                 can never be confused with a canonical cold-start.

    UNAVAILABLE — Reputation data exists but is inconsistent or unreadable.
                 Agents in this state are excluded from selection.
    """
    CANONICAL = "CANONICAL"
    COLD_START = "COLD_START"
    ABSENT = "ABSENT"
    UNAVAILABLE = "UNAVAILABLE"


# ──────────────────────────────────────────────────────────────────────────────
# Candidate DTO
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class CandidateDTO:
    """Immutable per-candidate snapshot captured at selection time."""
    agent_id: str                        # str(UUID) for JSON serialisation
    agent_name: str
    reputation_state: str                # ReputationState value
    score_scaled: int                    # Always an integer in [0, 10000]
    total_verified_executions: int       # From canonical row; 0 for ABSENT
    evidence_hash: str | None            # SHA-256 hex or None for ABSENT
    policy_version: str | None           # e.g. "ReputationPolicyV1" or None
    price_atomic: int                    # Exact price in atomic units (e.g. 1000000 for 1 USDC)
    price_currency: str                  # Settlement currency (e.g. 'USDC')
    is_affordable: bool = True           # Meets client max_budget constraint
    meets_constraints: bool = True       # Meets all capability, tool, runtime, and budget constraints
    exclusion_reason: str | None = None  # Reason if excluded (e.g. 'OVER_BUDGET')

    def to_dict(self) -> dict[str, Any]:
        """Return canonical JSON-serialisable dict (keys sorted by name)."""
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "evidence_hash": self.evidence_hash,
            "exclusion_reason": self.exclusion_reason,
            "is_affordable": self.is_affordable,
            "meets_constraints": self.meets_constraints,
            "policy_version": self.policy_version,
            "price_atomic": self.price_atomic,
            "price_currency": self.price_currency,
            "reputation_state": self.reputation_state,
            "score_scaled": self.score_scaled,
            "total_verified_executions": self.total_verified_executions,
        }


# ──────────────────────────────────────────────────────────────────────────────
# Public types
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class AgentReference:
    agent_id: uuid.UUID
    agent_version: str
    agent_version_id: uuid.UUID
    name: str
    slug: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    price_atomic: int
    price_currency: str


@dataclass
class SelectionResult:
    agent_ref: AgentReference
    decision: SelectionDecision


class AgentSelector(Protocol):
    async def select_agent(
        self,
        db: AsyncSession,
        task: TaskDefinitionInput,
        orchestration_id: uuid.UUID | None = None,
        attempt_number: int = 1,
    ) -> SelectionResult:
        ...


# ──────────────────────────────────────────────────────────────────────────────
# DeterministicAgentSelector — SelectionPolicyV1 + EconomicConstraintPolicyV1
# ──────────────────────────────────────────────────────────────────────────────

class DeterministicAgentSelector:
    """
    Phase 6.7 — Cost-Aware & Constraint-Aware Deterministic Agent Selection.

    Combines:
      - SelectionPolicyV1 (Reputation ordering + agent_id tie-breaker)
      - EconomicConstraintPolicyV1 (Hard budget filtering + tool/runtime constraints)

    IMPORTANT ARCHITECTURAL RULE:
        Price is an ECONOMIC CONSTRAINT, NOT A REPUTATION SIGNAL.
        We do NOT create a composite 'reputation + price' score.
        Price acts strictly as a hard eligibility filter.

    ALGORITHM:
    STEP 1 — HARD ELIGIBILITY & CAPABILITY MATCH
        Filter PUBLISHED agents matching capability, slug, or explicit task.agent_id.
        Broad fallback to all PUBLISHED agents if no specific capability match is found.

    STEP 2 — VERSION, TOOL, RUNTIME & ECONOMIC CONSTRAINT EVALUATION
        For each candidate:
          - Resolve version (task.agent_version or agent.current_version).
          - Check tool requirements (required_tools).
          - Check runtime limits (max_timeout_seconds).
          - Check economic constraints (price_atomic <= max_budget_atomic).
          - Check currency compatibility (default USDC).
        Candidates failing any constraint are marked meets_constraints=False.

    STEP 3 — AFFORDABILITY FILTER
        Candidates with meets_constraints=False are excluded from ranking.
        If no candidates remain:
          - If candidates existed but exceeded budget: raise ValueError("NO_AFFORDABLE_AGENT")
          - Otherwise: raise ValueError("NO_ELIGIBLE_AGENT")

    STEP 4 — REPUTATION LOOKUP & STATE CLASSIFICATION
        Classify eligible candidates into CANONICAL, COLD_START, ABSENT.
        Default score for ABSENT is COLD_START_SCORE (5000).

    STEP 5 — DETERMINISTIC RANKING (no hidden weights)
        Sort eligible candidates by:
          1. score_scaled DESC   (reputation rank)
          2. agent_id     ASC    (deterministic tie-breaker)

    STEP 6 — CANONICAL HASH
        Payload includes constraints (budget, currency, tools), all candidates
        (with their prices, sorted by agent_id ASC), selected price, and policies.
        SHA-256(RFC-8785 JSON) -> 64-char hex digest.

    STEP 7 — SELECTION DECISION PERSISTENCE
        Persists SelectionDecision snapshot with selected_price_atomic and budget.
    """

    SELECTION_POLICY_VERSION: str = SELECTION_POLICY_VERSION
    ECONOMIC_POLICY_VERSION: str = ECONOMIC_POLICY_VERSION

    async def select_agent(
        self,
        db: AsyncSession,
        task: TaskDefinitionInput,
        orchestration_id: uuid.UUID | None = None,
        attempt_number: int = 1,
    ) -> SelectionResult:

        request_constraints: dict[str, Any] = {
            "task_key": task.task_key,
            "capability": task.capability,
            "agent_id": str(task.agent_id) if task.agent_id else None,
            "agent_version": task.agent_version,
            "max_budget_atomic": task.max_budget_atomic,
            "budget_currency": task.budget_currency,
            "required_tools": sorted(task.required_tools) if task.required_tools else [],
            "max_timeout_seconds": task.max_timeout_seconds,
        }

        # ── STEP 1 — Hard eligibility + capability match ──────────────────────

        if task.agent_id:
            query = (
                select(Agent)
                .options(selectinload(Agent.versions), selectinload(Agent.current_version))
                .where(
                    Agent.id == task.agent_id,
                    Agent.status == AgentStatus.PUBLISHED.value,
                )
            )
        else:
            capability_or_key = task.capability or task.task_key.lower()
            query = (
                select(Agent)
                .join(AgentCapability, Agent.id == AgentCapability.agent_id, isouter=True)
                .options(selectinload(Agent.current_version), selectinload(Agent.versions))
                .where(
                    Agent.status == AgentStatus.PUBLISHED.value,
                    (
                        (AgentCapability.capability == capability_or_key)
                        | (Agent.slug == capability_or_key)
                        | (Agent.slug.ilike(f"%{capability_or_key}%"))
                    ),
                )
            )

        res = await db.execute(query)
        candidates = res.scalars().unique().all()

        if not candidates and not task.agent_id:
            # Broad fallback: any published agent (preserves vertical-slice behaviour)
            fallback_query = (
                select(Agent)
                .options(selectinload(Agent.current_version), selectinload(Agent.versions))
                .where(Agent.status == AgentStatus.PUBLISHED.value)
            )
            fallback_res = await db.execute(fallback_query)
            candidates = fallback_res.scalars().unique().all()

        if not candidates:
            raise ValueError("NO_ELIGIBLE_AGENT")

        # ── STEP 2 — Version resolution & economic/constraint evaluation ─────

        # Map agent -> resolved target AgentVersion
        agent_target_versions: dict[uuid.UUID, AgentVersion] = {}
        candidate_evaluations: dict[uuid.UUID, dict[str, Any]] = {}

        for agent in candidates:
            # Version resolution
            target_version: AgentVersion | None = None
            if task.agent_version:
                for v in agent.versions:
                    if v.version == task.agent_version:
                        target_version = v
                        break
            else:
                target_version = agent.current_version

            if not target_version:
                if task.agent_id and task.agent_version:
                    raise ValueError(
                        f"Agent '{agent.name}' does not have published version '{task.agent_version}'"
                    )
                candidate_evaluations[agent.id] = {
                    "version": None,
                    "price_atomic": 0,
                    "price_currency": "USDC",
                    "is_affordable": False,
                    "meets_constraints": False,
                    "exclusion_reason": "NO_VALID_VERSION",
                }
                continue

            agent_target_versions[agent.id] = target_version
            version_manifest = target_version.manifest if isinstance(target_version.manifest, dict) else {}

            # Price extraction (integer atomic units)
            price_atomic, currency = extract_version_price(target_version)

            meets_constraints = True
            is_affordable = True
            exclusion_reason: str | None = None

            # 1. Currency check
            if currency != task.budget_currency:
                meets_constraints = False
                exclusion_reason = f"CURRENCY_MISMATCH_{currency}"

            # 2. Budget check (exact integer comparison)
            elif not validate_budget_constraint(price_atomic, task.max_budget_atomic):
                meets_constraints = False
                is_affordable = False
                exclusion_reason = f"OVER_BUDGET: price {price_atomic} > budget {task.max_budget_atomic}"

            # 3. Tool requirements check
            elif not validate_tool_requirements(version_manifest, task.required_tools):
                meets_constraints = False
                exclusion_reason = "MISSING_REQUIRED_TOOLS"

            # 4. Runtime limits check
            elif not validate_runtime_requirements(version_manifest, task.max_timeout_seconds):
                meets_constraints = False
                exclusion_reason = "EXCEEDS_MAX_TIMEOUT"

            candidate_evaluations[agent.id] = {
                "version": target_version,
                "price_atomic": price_atomic,
                "price_currency": currency,
                "is_affordable": is_affordable,
                "meets_constraints": meets_constraints,
                "exclusion_reason": exclusion_reason,
            }

        # ── STEP 3 — Affordability & constraint filtering ─────────────────────

        eligible_agents = [
            agent for agent in candidates
            if candidate_evaluations[agent.id]["meets_constraints"]
        ]

        if not eligible_agents:
            # Check if any candidate was rejected specifically for budget
            over_budget_count = sum(
                1 for eval_dict in candidate_evaluations.values()
                if not eval_dict["is_affordable"]
            )
            if over_budget_count > 0:
                raise ValueError("NO_AFFORDABLE_AGENT")
            raise ValueError("NO_ELIGIBLE_AGENT")

        # ── STEP 4 — Reputation lookup & state classification ─────────────────

        candidate_ids = [c.id for c in candidates]
        score_res = await db.execute(
            select(ReputationScore).where(ReputationScore.agent_id.in_(candidate_ids))
        )
        all_scores = score_res.scalars().all()

        score_map: dict[uuid.UUID, ReputationScore] = {}
        for s in all_scores:
            if s.agent_id not in score_map or s.score_scaled > score_map[s.agent_id].score_scaled:
                score_map[s.agent_id] = s

        all_candidate_dtos: list[CandidateDTO] = []
        for agent in candidates:
            eval_info = candidate_evaluations[agent.id]
            s = score_map.get(agent.id)

            if s is None:
                rep_state = ReputationState.ABSENT.value
                score_scaled = COLD_START_SCORE
                total_verified = 0
                evidence_hash = None
                policy_version = None
            elif s.total_verified_executions == 0:
                rep_state = ReputationState.COLD_START.value
                score_scaled = s.score_scaled
                total_verified = 0
                evidence_hash = s.evidence_set_hash
                policy_version = s.policy_version
            else:
                rep_state = ReputationState.CANONICAL.value
                score_scaled = s.score_scaled
                total_verified = s.total_verified_executions
                evidence_hash = s.evidence_set_hash
                policy_version = s.policy_version

            dto = CandidateDTO(
                agent_id=str(agent.id),
                agent_name=agent.name,
                reputation_state=rep_state,
                score_scaled=score_scaled,
                total_verified_executions=total_verified,
                evidence_hash=evidence_hash,
                policy_version=policy_version,
                price_atomic=eval_info["price_atomic"],
                price_currency=eval_info["price_currency"],
                is_affordable=eval_info["is_affordable"],
                meets_constraints=eval_info["meets_constraints"],
                exclusion_reason=eval_info["exclusion_reason"],
            )
            all_candidate_dtos.append(dto)

        # ── STEP 5 — Deterministic ranking of eligible candidates ─────────────
        #
        # Criteria:
        #   1. score_scaled DESC  — primary reputation criterion
        #   2. agent_id     ASC   — deterministic tie-breaker (UUID string sort)
        #
        # Price is strictly an eligibility constraint; it does NOT alter ranking
        # order among affordable candidates.

        eligible_dtos = [d for d in all_candidate_dtos if d.meets_constraints]
        eligible_dtos.sort(key=lambda d: (-d.score_scaled, d.agent_id))

        winner_dto = eligible_dtos[0]
        winner_agent_id = uuid.UUID(winner_dto.agent_id)
        winner_agent = next(a for a in candidates if a.id == winner_agent_id)
        target_version = agent_target_versions[winner_agent_id]

        # ── STEP 6 — Order-normalised canonical hash ───────────────────────────
        #
        # Evaluated candidates are sorted by agent_id ASC before hashing so the
        # hash is independent of discovery query or database iteration order.

        candidates_for_hash = sorted(
            [dto.to_dict() for dto in all_candidate_dtos],
            key=lambda d: d["agent_id"],
        )
        snapshot_payload: dict[str, Any] = {
            "candidates": candidates_for_hash,
            "constraints": request_constraints,
            "economic_policy": self.ECONOMIC_POLICY_VERSION,
            "policy": self.SELECTION_POLICY_VERSION,
            "selected_agent_id": str(winner_agent.id),
            "selected_price_atomic": winner_dto.price_atomic,
            "selected_version": target_version.version,
        }
        canonical_hash = compute_canonical_hash(snapshot_payload)

        # ── STEP 7 — Build SelectionDecision (not yet flushed) ────────────────

        has_reputation_row = winner_dto.reputation_state in (
            ReputationState.CANONICAL.value,
            ReputationState.COLD_START.value,
        )

        decision = SelectionDecision(
            orchestration_id=orchestration_id,
            attempt_number=attempt_number,
            selection_policy_version=self.SELECTION_POLICY_VERSION,
            economic_policy_version=self.ECONOMIC_POLICY_VERSION,
            reputation_policy_version=winner_dto.policy_version,
            reputation_state=winner_dto.reputation_state,
            selected_price_atomic=winner_dto.price_atomic,
            price_currency=winner_dto.price_currency,
            max_budget_atomic=task.max_budget_atomic,
            request_constraints=request_constraints,
            eligible_candidates_json=[dto.to_dict() for dto in all_candidate_dtos],
            selected_agent_id=winner_agent.id,
            selected_agent_version=target_version.version,
            reputation_score_scaled=winner_dto.score_scaled if has_reputation_row else None,
            reputation_evidence_count=winner_dto.total_verified_executions if has_reputation_row else None,
            reputation_evidence_hash=winner_dto.evidence_hash if has_reputation_row else None,
            canonical_selection_hash=canonical_hash,
            selection_reason=(
                f"SelectionPolicyV1 + EconomicConstraintPolicyV1: eligible under budget "
                f"({task.max_budget_atomic} atomic); highest score_scaled={winner_dto.score_scaled} "
                f"(state={winner_dto.reputation_state}); price={winner_dto.price_atomic} "
                f"{winner_dto.price_currency}; tie-break by agent_id ASC"
            ),
        )

        ref = AgentReference(
            agent_id=winner_agent.id,
            agent_version=target_version.version,
            agent_version_id=target_version.id,
            name=winner_agent.name,
            slug=winner_agent.slug,
            input_schema=target_version.input_schema,
            output_schema=target_version.output_schema,
            price_atomic=winner_dto.price_atomic,
            price_currency=winner_dto.price_currency,
        )

        logger.info(
            "Phase 6.7 selection: agent=%s version=%s score=%d price=%d %s hash=%s",
            winner_agent.id,
            target_version.version,
            winner_dto.score_scaled,
            winner_dto.price_atomic,
            winner_dto.price_currency,
            canonical_hash[:16],
        )

        return SelectionResult(agent_ref=ref, decision=decision)
