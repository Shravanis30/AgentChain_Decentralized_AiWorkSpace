"""Pydantic schemas for Marketplace execution and escrow lifecycle."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MarketplaceOrderCreate(BaseModel):
    goal: str = Field(..., min_length=3, description="Client objective or task goal")
    task_input: dict[str, Any] = Field(default_factory=dict, description="Structured task input payload")
    chain_id: int = Field(default=31337, description="Target EVM chain ID for escrow")
    idempotency_key: str = Field(..., min_length=8, max_length=128, description="Client idempotency key")
    max_budget_atomic: int | None = Field(default=None, ge=0, description="Max budget in token base units")
    price_currency: str = Field(default="USDC", description="Payment token currency")
    required_capabilities: list[str] | None = Field(default=None, description="Required agent capabilities")
    preferred_agent_id: uuid.UUID | None = Field(default=None, description="Optional pinned agent candidate")
    preferred_agent_version: str | None = Field(default=None, description="Optional pinned agent version")


class MarketplaceOrderResponse(BaseModel):
    id: uuid.UUID
    order_number: str
    client_user_id: uuid.UUID
    client_address: str
    goal: str
    task_input: dict[str, Any]
    max_budget_atomic: int | None
    price_currency: str

    selection_decision_id: uuid.UUID
    selected_agent_id: uuid.UUID
    selected_agent_version: str
    pinned_price_atomic: int
    platform_fee_atomic: int
    total_escrow_atomic: int

    escrow_chain_id: int
    escrow_contract: str
    escrow_reference_id: str
    escrow_salt: str
    escrow_id: str | None

    execution_id: uuid.UUID | None
    orchestration_id: uuid.UUID | None
    orchestration_task_id: uuid.UUID | None
    settlement_id: uuid.UUID | None
    distribution_id: uuid.UUID | None

    status: str
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class MarketplaceDisputeCreate(BaseModel):
    dispute_reason: str = Field(..., min_length=10, description="Detailed reason for opening escrow dispute")


class MarketplaceDisputeResolve(BaseModel):
    beneficiary_amount: int = Field(..., ge=0, description="Amount awarded to agent developer in base units")
    client_refund_amount: int = Field(..., ge=0, description="Amount refunded to client in base units")
    reason: str | None = Field(default=None, description="Arbitration ruling explanation")
