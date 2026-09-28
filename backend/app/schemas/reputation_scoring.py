"""Pydantic schemas for Phase 6.5 — Reputation Scoring & Policy Engine."""

from datetime import datetime
from typing import Any
import uuid

from pydantic import BaseModel, ConfigDict, Field


class PolicyVersionResponse(BaseModel):
    """Read-only policy version specification."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    policy_name: str
    version_number: int
    description: str | None = None
    formula_json: dict[str, Any]
    is_active: bool
    activated_at: datetime
    deprecated_at: datetime | None = None
    created_at: datetime
    version_string: str


class ReputationScoreResponse(BaseModel):
    """Read-only current reputation score for an agent under a specific policy.

    Score is stored as score_scaled in [0, 10000].
    Divide by 10000 for normalized float in [0.0, 1.0].
    Example: score_scaled=7500 means score=0.75 (75th percentile).
    """
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID
    chain_id: int
    policy_version: str
    # Score (integer, [0, 10000])
    score_scaled: int
    score_min: int
    score_max: int
    # Convenience float (not stored; computed for display)
    score_float: float
    # Objective evidence metrics snapshot
    total_verified_executions: int
    verified_successes: int
    verified_failures: int
    verified_timeouts: int
    verified_cancellations: int
    # Integer rates ([0, 10000] or None when total==0)
    success_rate_scaled: int | None = None
    failure_rate_scaled: int | None = None
    timeout_rate_scaled: int | None = None
    cancellation_rate_scaled: int | None = None
    # Cold-start: separate from score
    experience_count: int
    # Evidence identification
    evidence_set_hash: str
    evidence_event_count: int
    # Calculation metadata
    calculation_reason: str
    explanation_json: dict[str, Any] = Field(default_factory=dict)
    calculated_at: datetime
    created_at: datetime
    updated_at: datetime


class ReputationMetricsResponse(BaseModel):
    """Objective evidence metrics, exposed separately from the score.

    A user must be able to inspect exactly why a score exists.
    Scores must never hide the underlying evidence.
    """
    agent_id: uuid.UUID
    chain_id: int
    policy_version: str
    # Objective counts
    total_verified_executions: int
    verified_successes: int
    verified_failures: int
    verified_timeouts: int
    verified_cancellations: int
    # Rate (integer scaled; None when total==0)
    success_rate_scaled: int | None = None
    failure_rate_scaled: int | None = None
    timeout_rate_scaled: int | None = None
    cancellation_rate_scaled: int | None = None
    # Rate (float for display; None when total==0)
    success_rate: float | None = None
    failure_rate: float | None = None
    timeout_rate: float | None = None
    cancellation_rate: float | None = None
    # Score
    reputation_score: int | None = None  # score_scaled [0, 10000]
    # Cold-start experience
    experience_count: int
    # Evidence identification
    evidence_set_hash: str | None = None
    evidence_event_count: int
    # Calculation metadata
    calculated_at: datetime | None = None


class ReputationScoreHistoryItem(BaseModel):
    """One immutable score history record."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reputation_score_id: uuid.UUID
    agent_id: uuid.UUID
    chain_id: int
    policy_version: str
    previous_score_scaled: int | None = None
    previous_evidence_set_hash: str | None = None
    new_score_scaled: int
    new_evidence_set_hash: str
    total_verified_executions: int
    verified_successes: int
    verified_failures: int
    verified_timeouts: int
    verified_cancellations: int
    calculation_reason: str
    explanation_json: dict[str, Any] = Field(default_factory=dict)
    calculated_at: datetime


class ReputationExplanationResponse(BaseModel):
    """Full auditable explanation of a score calculation.

    Contains sufficient information for an auditor to independently
    reproduce the score without reading the source code.
    """
    agent_id: uuid.UUID
    chain_id: int
    policy_version: str
    policy_formula_summary: str
    evidence_set_hash: str
    evidence_event_count: int
    score_scaled: int
    score_float: float
    is_cold_start: bool
    experience_count: int
    explanation_detail: dict[str, Any]
    calculated_at: datetime


class AdminRecalculateRequest(BaseModel):
    """Admin-triggered recalculation request.

    Note: Admins CANNOT set a score directly.
    This endpoint triggers a fresh computation from authoritative evidence.
    """
    chain_id: int | None = None
    reason: str = "MANUAL_RECALCULATION"
