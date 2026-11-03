"""Add trial groups: n Runs created together that share one EvaluationSpec.

This is the single H3 revision from ADR-0010. A group row records n, k and the
fixed Environment seed; member Runs point at it with a 1..n trial index.
Existing Runs keep null columns and stay outside any group. Read
``app.phase1_service.create_trial_group`` next.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0010_trial_groups"
down_revision: str | None = "0009_experiment_agent_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trial_groups",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("experiment_id", sa.String(length=36), nullable=False),
        sa.Column("policy_id", sa.String(length=36), nullable=True),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("k", sa.Integer(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["policy_id"], ["policies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("n >= 1 AND n <= 20", name="ck_trial_groups_n"),
        sa.CheckConstraint("k >= 1 AND k <= n", name="ck_trial_groups_k"),
    )
    op.create_index("ix_trial_groups_experiment_id", "trial_groups", ["experiment_id"])
    with op.batch_alter_table("runs") as batch_op:
        batch_op.add_column(sa.Column("trial_group_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("trial_index", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_runs_trial_group_id",
            "trial_groups",
            ["trial_group_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index("ix_runs_trial_group_id", ["trial_group_id"])
        batch_op.create_unique_constraint(
            "uq_runs_trial_group_index", ["trial_group_id", "trial_index"]
        )


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_constraint("uq_runs_trial_group_index", type_="unique")
        batch_op.drop_index("ix_runs_trial_group_id")
        batch_op.drop_constraint("fk_runs_trial_group_id", type_="foreignkey")
        batch_op.drop_column("trial_index")
        batch_op.drop_column("trial_group_id")
    op.drop_index("ix_trial_groups_experiment_id", table_name="trial_groups")
    op.drop_table("trial_groups")
