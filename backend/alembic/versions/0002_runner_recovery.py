"""Persist the reason for the latest Runner lease recovery."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002_runner_recovery"
down_revision: str | None = "0001_phase1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runner_jobs",
        sa.Column("recovery_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runner_jobs", "recovery_reason")
