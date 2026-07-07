"""Unit tests for ``app.context_manager.ContextManager``."""
from __future__ import annotations

import pytest

from app.context_manager import ContextManager


class TestFormatObservation:
    """Tests for ``ContextManager.format_observation()``."""

    def _make_cm(self) -> ContextManager:
        return ContextManager(model="gpt-4o")

    def test_short_content_returned_unchanged(self) -> None:
        """Content under max_tokens is returned verbatim."""
        cm = self._make_cm()
        short = "Hello, world!"
        result = cm.format_observation(short, max_tokens=2000)
        assert result is short  # exact same object (no copy made)

    def test_500_token_content_returned_unchanged(self) -> None:
        """A 500-token string fits within default limit and is not truncated."""
        cm = self._make_cm()
        # Each "word " pair is roughly 1 token in o200k_base
        content = "token " * 500
        encoded = cm._encoding.encode(content)
        assert len(encoded) >= 500, f"Expected >=500 tokens, got {len(encoded)}"

        result = cm.format_observation(content)
        assert result is content  # returned unchanged
        assert "[... truncated to" not in result

    def test_10000_token_content_truncated_to_2000(self) -> None:
        """A 10000-token string is truncated to <= 2000 tokens + suffix."""
        cm = self._make_cm()
        # Build content with ~10000 tokens.  "token " is 2 tokens in
        # o200k_base, so ~20000 repetitions gives ~40000 tokens, but
        # we want exactly enough to exceed 2000.  Use 10000 repetitions
        # of "token" (no trailing space) — each "token" is 1 token.
        content = " ".join(["token"] * 10000)
        token_count = len(cm._encoding.encode(content))
        assert token_count > 2000, f"Expected >2000 tokens, got {token_count}"

        result = cm.format_observation(content)

        # Result should have the suffix
        assert "[... truncated to" in result

        # Result content tokens (without suffix) should be <= 2000
        result_tokens = cm._encoding.encode(result)
        # We allow a few extra tokens for the suffix
        assert len(result_tokens) <= 2010, (
            f"Expected <=2010 tokens, got {len(result_tokens)}"
        )

    def test_truncation_suffix_mentions_token_limit(self) -> None:
        """Truncated output includes ``[... truncated to N tokens]``."""
        cm = self._make_cm()
        content = "long " * 10000

        result = cm.format_observation(content, max_tokens=100)
        assert "[... truncated to 100 tokens]" in result

    def test_custom_default_via_constructor(self) -> None:
        """The default max_tokens can be configured via constructor."""
        cm = ContextManager(max_observation_tokens=500)
        content = " ".join(["token"] * 5000)

        result = cm.format_observation(content)  # no explicit max_tokens
        assert "[... truncated to 500 tokens]" in result

    def test_empty_content_unchanged(self) -> None:
        """Empty string is returned as-is."""
        cm = self._make_cm()
        assert cm.format_observation("") == ""

    def test_explicit_max_tokens_overrides_constructor_default(self) -> None:
        """Explicit max_tokens parameter takes precedence over constructor default."""
        cm = ContextManager(max_observation_tokens=500)
        content = " ".join(["token"] * 5000)

        result = cm.format_observation(content, max_tokens=100)
        assert "[... truncated to 100 tokens]" in result
