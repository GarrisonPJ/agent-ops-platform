"""Persist the latest authenticated Runner availability signal."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0005_runner_presence"
down_revision: str | None = "0004_observability_claim"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runner_presence",
        sa.Column("runner_id", sa.String(length=100), primary_key=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_runner_presence_last_seen_at", "runner_presence", ["last_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_runner_presence_last_seen_at", table_name="runner_presence")
    op.drop_table("runner_presence")
