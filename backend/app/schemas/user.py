import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class WalletResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    address: str
    chain_id: int
    is_primary: bool
    verified_at: datetime
    created_at: datetime


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    primary_role: str
    roles: list[str]
    primary_wallet: WalletResponse | None = None
    created_at: datetime


class UserProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    primary_role: str
    roles: list[str]
    wallets: list[WalletResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
