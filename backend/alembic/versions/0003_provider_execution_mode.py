"""Persist the selected Phase 1 execution mode for every Experiment."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003_provider_execution_mode"
down_revision: str | None = "0002_runner_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "experiments",
        sa.Column(
            "execution_mode",
            sa.String(length=20),
            nullable=False,
            server_default="fixture",
        ),
    )


def downgrade() -> None:
    op.drop_column("experiments", "execution_mode")
