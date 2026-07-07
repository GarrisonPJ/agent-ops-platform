"""Embedding generation via an OpenAI-compatible /embeddings endpoint.

Uses the same connection details (``base_url``, ``api_key``) as the chat
completions provider but calls ``/embeddings`` instead.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class Embedder:
    """Generates text embeddings via an OpenAI-compatible embeddings API.

    Usage::

        embedder = Embedder("https://api.openai.com/v1", "sk-...")
        vec = await embedder.embed("some text")

    If no ``base_url`` is configured, ``embed()`` returns ``None`` so that
    callers can treat a missing LLM setup as a no-op.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "text-embedding-3-small",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._client = client or httpx.AsyncClient(timeout=30.0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self._client.aclose()

    async def aclose(self):
        await self._client.aclose()

    async def embed(self, text: str) -> list[float] | None:
        """Generate an embedding vector for *text*.

        Returns ``None`` when the LLM endpoint is not configured or on any
        API error (logged but not raised).
        """
        if not self.base_url:
            logger.debug("Embedder: no base_url configured, skipping")
            return None

        if not text.strip():
            return None

        body = {
            "model": self.model,
            "input": text,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = await self._client.post(
                f"{self.base_url}/embeddings",
                json=body,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
            return data["data"][0]["embedding"]
        except Exception:
            logger.exception("Failed to generate embedding for text (%.40r)", text)
            return None
