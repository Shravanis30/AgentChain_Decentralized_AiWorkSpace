"""002_identity_and_sessions

Revision ID: 002_identity_and_sessions
Revises: 001_initial_foundation
Create Date: 2026-09-26 18:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002_identity_and_sessions"
down_revision: str | None = "001_initial_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Update users table with primary_role, additional_roles, and make wallet_address/nonce nullable
    op.add_column(
        "users",
        sa.Column(
            "primary_role",
            sa.String(length=32),
            server_default="CLIENT",
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "additional_roles",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.alter_column("users", "wallet_address", existing_type=sa.String(length=42), nullable=True)
    op.alter_column("users", "nonce", existing_type=sa.String(length=64), nullable=True)
    op.alter_column(
        "users",
        "role",
        type_=sa.String(length=32),
        postgresql_using="role::text",
        nullable=True,
    )

    # 2. Create wallets table
    op.create_table(
        "wallets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("address", sa.String(length=42), nullable=False),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("address", "chain_id", name="uq_wallets_address_chain"),
    )
    op.create_index("idx_wallets_user_id", "wallets", ["user_id"])
    op.create_index("idx_wallets_address", "wallets", ["address"])
    op.create_index("idx_wallets_address_chain", "wallets", ["address", "chain_id"])

    # 3. Create sessions table
    op.create_table(
        "sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("idx_sessions_user_id", "sessions", ["user_id"])
    op.create_index("idx_sessions_token_hash", "sessions", ["token_hash"])
    op.create_index("idx_sessions_expires_at", "sessions", ["expires_at"])
    op.create_index("idx_sessions_revoked_at", "sessions", ["revoked_at"])

    # 4. Create auth_nonces table
    op.create_table(
        "auth_nonces",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("wallet_address", sa.String(length=42), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("chain_id", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("nonce"),
    )
    op.create_index("idx_auth_nonces_wallet", "auth_nonces", ["wallet_address"])
    op.create_index("idx_auth_nonces_nonce", "auth_nonces", ["nonce"])
    op.create_index("idx_auth_nonces_expires", "auth_nonces", ["expires_at"])
    op.create_index("idx_auth_nonces_consumed", "auth_nonces", ["consumed_at"])

    # 5. Create audit_logs table
    op.create_table(
        "audit_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("wallet_address", sa.String(length=42), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=256), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("idx_audit_logs_wallet", "audit_logs", ["wallet_address"])
    op.create_index("idx_audit_logs_event_type", "audit_logs", ["event_type"])
    op.create_index("idx_audit_logs_timestamp", "audit_logs", ["timestamp"])
    op.create_index("idx_audit_logs_request_id", "audit_logs", ["request_id"])


def downgrade() -> None:
    # 5. Drop audit_logs
    op.drop_index("idx_audit_logs_request_id", table_name="audit_logs")
    op.drop_index("idx_audit_logs_timestamp", table_name="audit_logs")
    op.drop_index("idx_audit_logs_event_type", table_name="audit_logs")
    op.drop_index("idx_audit_logs_wallet", table_name="audit_logs")
    op.drop_index("idx_audit_logs_user_id", table_name="audit_logs")
    op.drop_table("audit_logs")

    # 4. Drop auth_nonces
    op.drop_index("idx_auth_nonces_consumed", table_name="auth_nonces")
    op.drop_index("idx_auth_nonces_expires", table_name="auth_nonces")
    op.drop_index("idx_auth_nonces_nonce", table_name="auth_nonces")
    op.drop_index("idx_auth_nonces_wallet", table_name="auth_nonces")
    op.drop_table("auth_nonces")

    # 3. Drop sessions
    op.drop_index("idx_sessions_revoked_at", table_name="sessions")
    op.drop_index("idx_sessions_expires_at", table_name="sessions")
    op.drop_index("idx_sessions_token_hash", table_name="sessions")
    op.drop_index("idx_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")

    # 2. Drop wallets
    op.drop_index("idx_wallets_address_chain", table_name="wallets")
    op.drop_index("idx_wallets_address", table_name="wallets")
    op.drop_index("idx_wallets_user_id", table_name="wallets")
    op.drop_table("wallets")

    # 1. Revert users table changes
    op.alter_column(
        "users",
        "role",
        existing_type=postgresql.ENUM(
            "REQUESTER", "AGENT_OPERATOR", "VALIDATOR", "ADMIN", name="user_role"
        ),
        nullable=False,
    )
    op.alter_column("users", "nonce", existing_type=sa.String(length=64), nullable=False)
    op.alter_column("users", "wallet_address", existing_type=sa.String(length=42), nullable=False)
    op.drop_column("users", "additional_roles")
    op.drop_column("users", "primary_role")

