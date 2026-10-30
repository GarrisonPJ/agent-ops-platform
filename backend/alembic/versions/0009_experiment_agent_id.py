"""Add optional operator-selected agent identity to Experiments.

This migration adds only nullable identity storage; omitted values retain the
execution-mode fallback when a Run is created.  It is called by Alembic, and
its next file to read is ``app.agent_registry``.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0009_experiment_agent_id"
down_revision: str | None = "0008_regression_cases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("experiments", sa.Column("agent_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("experiments", "agent_id")
