"""Persist failed runs as regression cases and their policy replays."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0008_regression_cases"
down_revision: str | None = "0007_experiment_scenario_params"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "regression_cases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("experiment_id", sa.String(length=36), nullable=False),
        sa.Column("source_run_id", sa.String(length=36), nullable=False),
        sa.Column("scenario_id", sa.String(length=100), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("scenario_params", sa.JSON(), nullable=False),
        sa.Column("execution_mode", sa.String(length=20), nullable=False),
        sa.Column("reproducibility", sa.String(length=30), nullable=False),
        sa.Column("reproducibility_note", sa.Text(), nullable=False),
        sa.Column("eligible_for_replay", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("replay_run_id", sa.String(length=36), nullable=True),
        sa.Column("replay_policy_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["replay_run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["replay_policy_id"], ["policies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_run_id", name="uq_regression_cases_source_run"),
    )
    op.create_index("ix_regression_cases_experiment_id", "regression_cases", ["experiment_id"])
    op.create_index("ix_regression_cases_source_run_id", "regression_cases", ["source_run_id"])
    op.create_index("ix_regression_cases_eligible_for_replay", "regression_cases", ["eligible_for_replay"])
    with op.batch_alter_table("runs") as batch_op:
        batch_op.add_column(
            sa.Column("regression_case_id", sa.String(length=36), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_runs_regression_case_id",
            "regression_cases",
            ["regression_case_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_runs_regression_case_id", ["regression_case_id"])


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_index("ix_runs_regression_case_id")
        batch_op.drop_constraint("fk_runs_regression_case_id", type_="foreignkey")
        batch_op.drop_column("regression_case_id")
    op.drop_index("ix_regression_cases_eligible_for_replay", table_name="regression_cases")
    op.drop_index("ix_regression_cases_source_run_id", table_name="regression_cases")
    op.drop_index("ix_regression_cases_experiment_id", table_name="regression_cases")
    op.drop_table("regression_cases")
