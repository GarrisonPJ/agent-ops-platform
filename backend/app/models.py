"""SQLAlchemy ORM models.

Tables
------
- ``trajectories`` — Agent trajectories (session-level).
- ``steps``       — Individual steps within a trajectory.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

try:
    from pgvector.sqlalchemy import Vector as PgVector

    HAS_PGVECTOR = True
except ImportError:
    HAS_PGVECTOR = False
    PgVector = None


class Base(DeclarativeBase):
    pass


class Trajectory(Base):
    __tablename__ = "trajectories"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default="running")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    score: Mapped[float | None] = mapped_column(nullable=True)
    score_breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    steps: Mapped[list[Step]] = relationship(back_populates="trajectory")
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_window_peak: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Step(Base):
    __tablename__ = "steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trajectory_id: Mapped[str] = mapped_column(
        String, ForeignKey("trajectories.id"), nullable=False
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    thought: Mapped[str] = mapped_column(Text)
    action: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    observation: Mapped[str] = mapped_column(Text)
    latency_ms: Mapped[int] = mapped_column(Integer)
    container_id: Mapped[str | None] = mapped_column(String, nullable=True)
    context_window: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    token_prompt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_completion: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    trajectory: Mapped[Trajectory] = relationship(back_populates="steps")


class PolicyVersion(Base):
    __tablename__ = "policy_versions"

    version_id: Mapped[str] = mapped_column(String, primary_key=True)
    version_display: Mapped[str] = mapped_column(String, unique=True)
    parent_version: Mapped[str | None] = mapped_column(String, ForeignKey("policy_versions.version_id"), nullable=True)
    patch: Mapped[dict] = mapped_column(JSONB)
    rationale: Mapped[str] = mapped_column(Text)
    expected_impact: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending_review")
    score_delta: Mapped[float | None] = mapped_column(nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class TrajectoryPolicyMap(Base):
    __tablename__ = "trajectory_policy_map"

    trajectory_id: Mapped[str] = mapped_column(
        String, ForeignKey("trajectories.id"), primary_key=True
    )
    policy_version_id: Mapped[str] = mapped_column(
        String, ForeignKey("policy_versions.version_id"), primary_key=True
    )


if HAS_PGVECTOR:

    class TrajectoryEmbedding(Base):
        """pgvector embedding of a completed trajectory summary.

        One-to-one with ``trajectories`` — ``id`` is both PK and FK.
        """

        __tablename__ = "trajectory_embeddings"

        id: Mapped[str] = mapped_column(
            String, ForeignKey("trajectories.id"), primary_key=True
        )
        embedding = mapped_column(PgVector(1536))  # type: ignore[var-annotated]
        summary: Mapped[str] = mapped_column(Text)
        created_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
        )

