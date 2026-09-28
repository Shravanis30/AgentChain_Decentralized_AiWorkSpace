"""Pydantic schemas for verified reputation evidence layer."""

from datetime import datetime
from decimal import Decimal
from typing import Any
import uuid
from pydantic import BaseModel, ConfigDict, Field


class ReputationProfileResponse(BaseModel):
    """Authoritative reputation read-model for an agent."""
    model_config = ConfigDict(from_attributes=True)

    agent_id: uuid.UUID
    chain_id: int
    total_verified_executions: int
    verified_successes: int
    verified_failures: int
    verified_timeouts: int
    verified_cancellations: int
    canonical_event_count: int
    first_verified_execution_id: uuid.UUID | None = None
    latest_verified_execution_id: uuid.UUID | None = None
    latest_verified_outcome: str | None = None
    latest_verified_at: datetime | None = None
    success_rate: float | None = None
    last_recalculated_at: datetime
    created_at: datetime
    updated_at: datetime


class ReputationEventResponse(BaseModel):
    """Detailed verified reputation event representation."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    idempotency_key: str
    reputation_key: str
    agent_id: uuid.UUID
    agent_version_id: uuid.UUID
    execution_id: uuid.UUID
    outcome_type: str
    result_hash: str | None = None
    notarization_id: uuid.UUID | None = None
    evidence_hash: str
    chain_id: int
    contract_address: str
    status: str
    transaction_intent_id: uuid.UUID | None = None
    transaction_hash: str | None = None
    block_number: int | None = None
    block_hash: str | None = None
    confirmations: int
    is_canonical: bool
    error_message: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None = None
    reorged_at: datetime | None = None


class ReputationVerifyResponse(BaseModel):
    """Comprehensive server-side cryptographic and chain verification status."""
    reputation_event_id: uuid.UUID
    verification_status: str  # PENDING, CONFIRMED, REORGED, INVALIDATED
    is_verified: bool
    agent_id: uuid.UUID
    agent_version_id: uuid.UUID
    execution_id: uuid.UUID
    outcome_type: str
    result_hash: str | None = None
    has_valid_notarization: bool
    notarization_status: str | None = None
    chain_id: int
    contract_address: str
    transaction_hash: str | None = None
    confirmations: int
    confirmations_required: int
    is_canonical: bool
    details: dict[str, Any] = Field(default_factory=dict)


class CreateReputationEventRequest(BaseModel):
    """Internal authenticated request to create reputation evidence."""
    execution_id: uuid.UUID
    chain_id: int | None = None
    idempotency_key: str | None = None
    evidence_payload: dict[str, Any] | None = None
