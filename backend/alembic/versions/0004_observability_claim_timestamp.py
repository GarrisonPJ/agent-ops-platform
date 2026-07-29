"""Persist when a queued Run is claimed by a Runner."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0004_observability_claim_timestamp"
down_revision: str | None = "0003_provider_execution_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "claimed_at")
