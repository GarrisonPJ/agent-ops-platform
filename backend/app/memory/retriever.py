"""Similarity search over stored trajectory embeddings.

Provides:

* ``MemoryRetriever.query()`` — find top-K similar trajectory summaries
  given an embedding vector.
* ``store_trajectory_memory()`` — persist a new ``TrajectoryEmbedding`` row.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.models import TrajectoryEmbedding

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def store_trajectory_memory(
    session: AsyncSession,
    trajectory_id: str,
    embedding: list[float],
    summary: str,
) -> None:
    """Insert a ``TrajectoryEmbedding`` row.

    Args:
        session: Active database session.
        trajectory_id: FK to ``trajectories.id`` (also the PK).
        embedding: 1536-dimensional pgvector embedding.
        summary: Human-readable summary of the trajectory.
    """
    traj_emb = TrajectoryEmbedding(
        id=trajectory_id,
        embedding=embedding,
        summary=summary,
    )
    session.add(traj_emb)


class MemoryRetriever:
    """Retrieve similar past trajectories via pgvector cosine similarity."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def query(
        self,
        query_embedding: list[float],
        k: int = 3,
        min_similarity: float = 0.7,
    ) -> list[str]:
        """Return summaries of the top-K most similar trajectories.

        Results are sorted by descending cosine similarity.  Only trajectories
        whose cosine similarity *exceeds* ``min_similarity`` are returned.

        Returns an empty list when no matches are found (cold start).
        """
        from pgvector.sqlalchemy import Vector  # noqa: F401 (type registration)

        distance_col = TrajectoryEmbedding.embedding.cosine_distance(
            query_embedding
        ).label("distance")

        stmt = (
            select(TrajectoryEmbedding.summary, distance_col)
            .order_by(distance_col)
            .limit(k)
        )

        result = await self.session.execute(stmt)
        rows = result.all()

        summaries: list[str] = []
        for row in rows:
            similarity = 1.0 - row.distance
            if similarity > min_similarity:
                summaries.append(row.summary)

        return summaries
