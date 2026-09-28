import enum
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.agent import Agent
    from app.models.audit import AuditLog
    from app.models.session import Session
    from app.models.task import Task



class UserRole(str, enum.Enum):
    CLIENT = "CLIENT"
    DEVELOPER = "DEVELOPER"
    AGENT_OPERATOR = "AGENT_OPERATOR"
    VERIFIER = "VERIFIER"
    ADMIN = "ADMIN"

    # Backward compatibility aliases
    REQUESTER = "CLIENT"
    VALIDATOR = "VERIFIER"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # primary_wallet_address kept for fast indexing & backward compatibility, nullable
    wallet_address: Mapped[str | None] = mapped_column(
        String(42), unique=True, index=True, nullable=True
    )
    primary_role: Mapped[str] = mapped_column(
        String(32), default=UserRole.CLIENT.value, nullable=False
    )
    additional_roles: Mapped[list[str]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    # Legacy role & nonce columns from initial schema
    legacy_role: Mapped[str | None] = mapped_column("role", String(32), nullable=True)
    nonce: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    # Relationships
    wallets: Mapped[list["Wallet"]] = relationship(
        "Wallet", back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    sessions: Mapped[list["Session"]] = relationship(
        "Session", back_populates="user", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        "AuditLog", back_populates="user"
    )
    tasks: Mapped[list["Task"]] = relationship("Task", back_populates="requester")
    owned_agents: Mapped[list["Agent"]] = relationship(
        "Agent", back_populates="owner", cascade="all, delete-orphan", foreign_keys="[Agent.owner_user_id]"
    )


    @property
    def role(self) -> UserRole:
        try:
            return UserRole(self.primary_role)
        except ValueError:
            return UserRole.CLIENT

    @property
    def roles(self) -> list[UserRole]:
        res: set[UserRole] = {self.role}
        for r in self.additional_roles or []:
            try:
                res.add(UserRole(r))
            except ValueError:
                pass
        return list(res)

    def has_role(self, role: UserRole) -> bool:
        if self.role == UserRole.ADMIN:
            return True
        return role in self.roles

    @property
    def primary_wallet(self) -> "Wallet | None":
        if "wallets" in self.__dict__ and self.wallets:
            for w in self.wallets:
                if w.is_primary:
                    return w
            return self.wallets[0]
        return None



class Wallet(Base):
    __tablename__ = "wallets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    chain_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="wallets")

    __table_args__ = (
        UniqueConstraint("address", "chain_id", name="uq_wallets_address_chain"),
        Index("idx_wallets_address_chain", "address", "chain_id"),
    )
