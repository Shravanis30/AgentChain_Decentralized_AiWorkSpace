"""Pydantic schemas for the settlement subsystem (Phase 6.2B)."""

from datetime import datetime
from typing import Any
import uuid
from pydantic import BaseModel, Field


class SettlementCreateRequest(BaseModel):
    chain_id: int = Field(..., description="Target blockchain chain ID (e.g. 31337 or 84532)")
    escrow_id: str = Field(..., description="Deterministic escrow identifier (bytes32 hex)")
    action: str = Field(..., description="Action: RELEASE, REFUND, DISPUTE_RESOLVE_RELEASE, DISPUTE_RESOLVE_REFUND")
    orchestration_id: uuid.UUID | None = Field(None, description="Optional linked orchestration ID")
    execution_id: uuid.UUID | None = Field(None, description="Optional linked agent execution ID")
    authorization_version: int = Field(1, description="Authorization version for idempotency")
    beneficiary_amount: int | None = Field(None, description="Optional amount for dispute split")
    client_refund_amount: int | None = Field(None, description="Optional refund amount for dispute split")
    metadata_json: dict[str, Any] | None = Field(None, description="Arbitrary task context metadata")


class SettlementAuthorizeRequest(BaseModel):
    reason: str | None = Field(None, description="Authorization note or reason")


class SettlementCancelRequest(BaseModel):
    reason: str | None = Field(None, description="Cancellation reason")


class SettlementHistoryItem(BaseModel):
    id: uuid.UUID
    from_status: str
    to_status: str
    reason: str | None
    actor_id: uuid.UUID | None
    actor_role: str | None
    metadata_json: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class SettlementResponse(BaseModel):
    id: uuid.UUID
    idempotency_key: str
    chain_id: int
    escrow_contract: str
    escrow_id: str
    client_address: str
    beneficiary_address: str
    token_address: str
    amount: str
    action: str
    status: str
    authorization_version: int
    orchestration_id: uuid.UUID | None
    execution_id: uuid.UUID | None
    authorized_by: uuid.UUID | None
    authorized_at: datetime | None
    authorization_reason: str | None
    blocked_reason: str | None
    transaction_intent_id: uuid.UUID | None
    submitted_at: datetime | None
    confirmed_at: datetime | None
    failed_at: datetime | None
    cancelled_at: datetime | None
    settlement_tx_hash: str | None
    block_number: int | None
    block_hash: str | None
    confirmations: int
    error_message: str | None
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    history: list[SettlementHistoryItem] = []

    model_config = {"from_attributes": True}


class SettlementListResponse(BaseModel):
    settlements: list[SettlementResponse]
    total: int
