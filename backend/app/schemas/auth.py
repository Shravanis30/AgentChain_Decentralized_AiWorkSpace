import re
import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator
from web3 import Web3

from app.schemas.user import UserResponse, WalletResponse

ETH_ADDRESS_REGEX = re.compile(r"^0x[a-fA-F0-9]{40}$")


class NonceRequest(BaseModel):
    wallet_address: str = Field(..., description="EVM wallet address (0x...)")
    chain_id: int | None = Field(default=None, description="EVM Chain ID")

    @field_validator("wallet_address")
    @classmethod
    def validate_wallet_address(cls, v: str) -> str:
        if not ETH_ADDRESS_REGEX.match(v):
            raise ValueError("Invalid EVM wallet address format")
        return Web3.to_checksum_address(v)


class NonceResponse(BaseModel):
    nonce: str
    wallet_address: str
    expires_at: datetime
    chain_id: int | None = None


class VerifyRequest(BaseModel):
    message: str = Field(..., description="Full EIP-4361 SIWE formatted message string")
    signature: str = Field(..., description="Hex encoded signature (0x...)")

    @field_validator("signature")
    @classmethod
    def validate_signature(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("0x"):
            v = "0x" + v
        if len(v) < 132:
            raise ValueError("Invalid signature format; expected 0x-prefixed hex string")
        return v


class VerifyResponse(BaseModel):
    authenticated: bool
    user: UserResponse
    wallet: WalletResponse
    session_expires_at: datetime
    token: str | None = Field(default=None, description="Session token for API clients")


class LogoutResponse(BaseModel):
    success: bool
    message: str


class WalletLinkRequest(BaseModel):
    message: str = Field(..., description="Full EIP-4361 SIWE message proving ownership of the new wallet")
    signature: str = Field(..., description="Hex encoded signature")

    @field_validator("signature")
    @classmethod
    def validate_signature(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("0x"):
            v = "0x" + v
        if len(v) < 132:
            raise ValueError("Invalid signature format")
        return v

