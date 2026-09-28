"""Pydantic schemas for the 85/10/5 revenue distribution subsystem (Phase 6.2C)."""

from datetime import datetime
from typing import Any
import uuid
from pydantic import BaseModel, Field


class DistributionCreateRequest(BaseModel):
    """Request to create an economic distribution for an authorized settlement.
    
    Security note: Callers CANNOT choose percentages, tokens, or recipients.
    All values are derived strictly from authoritative backend and chain state.
    """
    settlement_id: uuid.UUID = Field(..., description="ID of the authorized settlement")
    idempotency_key: str | None = Field(None, description="Optional custom idempotency key")
    metadata_json: dict[str, Any] | None = Field(None, description="Optional metadata context")


class DistributionHistoryItem(BaseModel):
    id: uuid.UUID
    from_status: str
    to_status: str
    reason: str | None
    metadata_json: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class DistributionResponse(BaseModel):
    id: uuid.UUID
    idempotency_key: str
    distribution_key: str
    chain_id: int
    escrow_contract: str
    escrow_id: str
    settlement_id: uuid.UUID
    distributor_contract: str
    token_address: str
    gross_amount: str
    developer_amount: str
    developer_recipient: str
    staker_amount: str
    staker_recipient: str
    dao_amount: str
    dao_recipient: str
    distribution_version: int
    status: str
    transaction_intent_id: uuid.UUID | None
    distribution_tx_hash: str | None
    onchain_distribution_id: str | None
    block_number: int | None
    block_hash: str | None
    confirmations: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None

    model_config = {"from_attributes": True}


class DistributionDetailResponse(DistributionResponse):
    history: list[DistributionHistoryItem] = Field(default_factory=list)


class DistributionListResponse(BaseModel):
    items: list[DistributionResponse]
    total: int
