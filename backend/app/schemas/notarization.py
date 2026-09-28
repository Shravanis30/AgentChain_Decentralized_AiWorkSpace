"""Pydantic schemas and canonical payload definition for Cryptographic Result Notarization."""

from datetime import datetime
import enum
from typing import Any
import uuid
from pydantic import BaseModel, Field

from app.services.hashing import canonicalize_json, compute_canonical_hash


class VerificationStatus(str, enum.Enum):
    VERIFIED = "VERIFIED"
    HASH_MISMATCH = "HASH_MISMATCH"
    NOT_CONFIRMED = "NOT_CONFIRMED"
    NOT_NOTARIZED = "NOT_NOTARIZED"
    REORGED = "REORGED"


class CanonicalNotarizationPayload(BaseModel):
    """
    Explicit, versioned canonical notarization envelope.
    Follows strict RFC 8785 canonicalization and deterministic SHA-256 identity.
    Excludes mutable database fields, volatile timestamps, or internal row IDs.
    """
    protocol_version: str = Field(default="1.0", description="Notarization protocol version")
    execution_id: str = Field(..., description="UUID of the agent execution")
    agent_id: str = Field(..., description="UUID of the parent agent")
    agent_version: str = Field(..., description="Semantic version string of the agent")
    result_hash: str = Field(..., description="Canonical SHA-256 hash of execution output")
    hash_algorithm: str = Field(default="SHA-256", description="Cryptographic hashing algorithm")
    canonicalization: str = Field(default="RFC-8785", description="Canonicalization scheme")
    artifact_reference: str | None = Field(default=None, description="Optional IPFS or URI artifact reference")
    orchestration_id: str | None = Field(default=None, description="Optional parent orchestration UUID")
    task_id: str | None = Field(default=None, description="Optional orchestration task UUID")
    completed_at: str | None = Field(default=None, description="ISO-8601 completion timestamp if fixed in envelope")

    def to_canonical_json(self) -> str:
        """Produce deterministic RFC-8785 JSON string."""
        return canonicalize_json(self.model_dump(exclude_none=False))

    def compute_commitment_hash(self) -> str:
        """Produce SHA-256 hex digest of the canonical envelope."""
        return compute_canonical_hash(self.model_dump(exclude_none=False))


class NotarizationCreateRequest(BaseModel):
    """Request to create an on-chain notarization proof for a succeeded execution."""
    execution_id: uuid.UUID = Field(..., description="ID of the terminal succeeded execution")
    chain_id: int | None = Field(None, description="Optional target blockchain network chain ID")
    artifact_reference: str | None = Field(None, description="Optional content-addressed artifact reference")
    idempotency_key: str | None = Field(None, description="Optional custom idempotency key")


class NotarizationHistoryItem(BaseModel):
    id: uuid.UUID
    from_status: str
    to_status: str
    reason: str | None
    metadata_json: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class NotarizationResponse(BaseModel):
    id: uuid.UUID
    idempotency_key: str
    notarization_key: str
    execution_id: uuid.UUID
    chain_id: int
    contract_address: str
    result_hash: str
    hash_algorithm: str
    canonicalization_version: str
    artifact_reference: str | None
    artifact_commitment: str | None
    status: str
    transaction_intent_id: uuid.UUID | None
    transaction_hash: str | None
    block_number: int | None
    block_hash: str | None
    confirmations: int
    is_canonical: bool
    error_message: str | None
    payload_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None
    reorged_at: datetime | None

    model_config = {"from_attributes": True}


class NotarizationVerifyRequest(BaseModel):
    """
    Verification request for checking an execution proof.
    Supports either passing raw result data to re-compute hash server-side,
    or verifying against an externally computed result hash.
    """
    execution_id: uuid.UUID = Field(..., description="Execution ID to verify")
    result_payload: dict[str, Any] | None = Field(None, description="Raw result payload to recompute canonical hash")
    result_hash: str | None = Field(None, description="Supplied result hash to verify")
    chain_id: int | None = Field(None, description="Target chain ID (defaults to active network)")


class NotarizationVerifyResponse(BaseModel):
    execution_id: uuid.UUID
    verification_status: VerificationStatus
    result_hash: str | None
    computed_hash: str | None
    onchain_hash: str | None
    hash_algorithm: str
    canonicalization_version: str
    chain_id: int
    contract_address: str
    transaction_hash: str | None
    block_number: int | None
    confirmations: int
    is_canonical: bool
    notarized_at: datetime | None
    verified_at: datetime
    details: str
