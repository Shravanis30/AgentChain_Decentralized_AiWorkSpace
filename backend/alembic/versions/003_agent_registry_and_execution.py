"""003_agent_registry_and_execution

Revision ID: 003_agent_registry_and_execution
Revises: 002_identity_and_sessions
Create Date: 2026-09-26 19:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003_agent_registry_and_execution"
down_revision: str | None = "002_identity_and_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Update agents table
    # Drop old index and FK
    op.drop_index("idx_agents_operator", table_name="agents")
    op.drop_constraint("agents_operator_id_fkey", "agents", type_="foreignkey")
    op.alter_column("agents", "operator_id", new_column_name="owner_user_id")
    op.create_foreign_key(
        "fk_agents_owner_user",
        "agents",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("idx_agents_owner", "agents", ["owner_user_id"])

    # Add slug, status, current_version_id, published_at
    op.add_column("agents", sa.Column("slug", sa.String(length=128), nullable=False))
    op.create_unique_constraint("uq_agents_slug", "agents", ["slug"])
    op.create_index("idx_agents_slug", "agents", ["slug"])

    op.add_column(
        "agents",
        sa.Column("status", sa.String(length=32), server_default="DRAFT", nullable=False),
    )
    op.create_index("idx_agents_status", "agents", ["status"])

    op.add_column(
        "agents",
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "agents",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column("agents", "reputation_score", existing_type=sa.Numeric(10, 4), nullable=True)
    op.alter_column("agents", "is_active", existing_type=sa.Boolean(), nullable=True)

    # 2. Create agent_versions table
    op.create_table(
        "agent_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "runtime_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "pricing_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "verification_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "version", name="uq_agent_version"),
    )
    op.create_index("idx_agent_versions_agent", "agent_versions", ["agent_id"])
    op.create_index("idx_agent_versions_lookup", "agent_versions", ["agent_id", "version"])

    # Link agents.current_version_id FK
    op.create_foreign_key(
        "fk_agents_current_version",
        "agents",
        "agent_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
        use_alter=True,
    )

    # 3. Create agent_capabilities table
    op.create_table(
        "agent_capabilities",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "capability", name="uq_agent_capability"),
    )
    op.create_index("idx_agent_capabilities_agent", "agent_capabilities", ["agent_id"])
    op.create_index("idx_agent_capabilities_capability", "agent_capabilities", ["capability"])

    # 4. Create agent_tools table
    op.create_table(
        "agent_tools",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column(
            "configuration",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_agent_tools_agent", "agent_tools", ["agent_id"])

    # 5. Create agent_executions table
    op.create_table(
        "agent_executions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="QUEUED",
            nullable=False,
        ),
        sa.Column("input_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["agent_version_id"], ["agent_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_agent_executions_agent", "agent_executions", ["agent_id"])
    op.create_index("idx_agent_executions_version", "agent_executions", ["agent_version_id"])
    op.create_index("idx_agent_executions_requester", "agent_executions", ["requested_by"])
    op.create_index("idx_agent_executions_status", "agent_executions", ["status"])
    op.create_index("idx_agent_executions_created", "agent_executions", ["created_at"])


def downgrade() -> None:
    # 5. Drop agent_executions
    op.drop_index("idx_agent_executions_created", table_name="agent_executions")
    op.drop_index("idx_agent_executions_status", table_name="agent_executions")
    op.drop_index("idx_agent_executions_requester", table_name="agent_executions")
    op.drop_index("idx_agent_executions_version", table_name="agent_executions")
    op.drop_index("idx_agent_executions_agent", table_name="agent_executions")
    op.drop_table("agent_executions")

    # 4. Drop agent_tools
    op.drop_index("idx_agent_tools_agent", table_name="agent_tools")
    op.drop_table("agent_tools")

    # 3. Drop agent_capabilities
    op.drop_index("idx_agent_capabilities_capability", table_name="agent_capabilities")
    op.drop_index("idx_agent_capabilities_agent", table_name="agent_capabilities")
    op.drop_table("agent_capabilities")

    # 2. Drop current_version FK from agents, then drop agent_versions
    op.drop_constraint("fk_agents_current_version", "agents", type_="foreignkey")
    op.drop_index("idx_agent_versions_lookup", table_name="agent_versions")
    op.drop_index("idx_agent_versions_agent", table_name="agent_versions")
    op.drop_table("agent_versions")

    # 1. Revert agents table
    op.drop_index("idx_agents_status", table_name="agents")
    op.drop_column("agents", "published_at")
    op.drop_column("agents", "current_version_id")
    op.drop_column("agents", "status")
    op.drop_index("idx_agents_slug", table_name="agents")
    op.drop_constraint("uq_agents_slug", "agents", type_="unique")
    op.drop_column("agents", "slug")

    op.drop_index("idx_agents_owner", table_name="agents")
    op.drop_constraint("fk_agents_owner_user", "agents", type_="foreignkey")
    op.alter_column("agents", "owner_user_id", new_column_name="operator_id")
    op.create_foreign_key(
        "agents_operator_id_fkey",
        "agents",
        "users",
        ["operator_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("idx_agents_operator", "agents", ["operator_id"])
