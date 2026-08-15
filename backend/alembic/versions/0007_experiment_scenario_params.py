"""Persist bounded scenario_params on Experiments."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0007_experiment_scenario_params"
down_revision: str | None = "0006_runner_attempts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "experiments",
        sa.Column("scenario_params", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("experiments", "scenario_params")
