"""Tests for tool_registry — Tool dataclass, tool_to_schema, and system prompt L3 rules."""
from __future__ import annotations

import pytest

from app.runtime import _REACT_SYSTEM_PROMPT
from app.tool_registry import Tool, tool_to_schema


def _make_tool(*, trigger_condition: str | None = None) -> Tool:
    return Tool(
        name="test_tool",
        description="A test tool.",
        parameters={"type": "object", "properties": {}},
        image="alpine:latest",
        command=["echo", "hello"],
        trigger_condition=trigger_condition,
    )


class TestL3SystemPrompt:
    """L3 tool-use rules section in _REACT_SYSTEM_PROMPT."""

    def test_l3_section_present(self):
        """_REACT_SYSTEM_PROMPT contains L3 tool-use guidelines."""
        assert "## Tool-use guidelines" in _REACT_SYSTEM_PROMPT

    def test_l3_dont_repeat_same_call(self):
        """L3 includes the 'same tool twice' rule."""
        assert "Don't call the same tool twice with identical arguments" in _REACT_SYSTEM_PROMPT

    def test_l3_error_recovery(self):
        """L3 includes the error recovery rule."""
        assert "try a different tool or approach before giving up" in _REACT_SYSTEM_PROMPT

    def test_l3_prefer_direct_tools(self):
        """L3 includes the 'prefer direct tools' rule."""
        assert "Prefer tools that directly answer" in _REACT_SYSTEM_PROMPT

    def test_l3_answer_directly_when_ready(self):
        """L3 includes the 'answer directly when ready' rule."""
        assert "already have the information needed" in _REACT_SYSTEM_PROMPT

    def test_l3_between_l2_and_l4(self):
        """L3 section is positioned between L2 and L4."""
        l2_marker = "You have access to the following tools"
        l3_marker = "## Tool-use guidelines"
        l4_marker = "Always respond in one of two ways:"
        idx_l2 = _REACT_SYSTEM_PROMPT.index(l2_marker)
        idx_l3 = _REACT_SYSTEM_PROMPT.index(l3_marker)
        idx_l4 = _REACT_SYSTEM_PROMPT.index(l4_marker)
        assert idx_l2 < idx_l3 < idx_l4, (
            f"Expected L2 ({l2_marker!r}) before L3 ({l3_marker!r}) before L4 ({l4_marker!r}), "
            f"but positions are L2={idx_l2}, L3={idx_l3}, L4={idx_l4}"
        )


class TestToolDataclass:
    """Tool dataclass — trigger_condition field."""

    def test_trigger_condition_default_none(self):
        """Default trigger_condition is None."""
        t = Tool(
            name="t",
            description="d",
            parameters={},
            image="i",
            command=["c"],
        )
        assert t.trigger_condition is None

    def test_trigger_condition_explicit(self):
        """trigger_condition can be set."""
        t = Tool(
            name="t",
            description="d",
            parameters={},
            image="i",
            command=["c"],
            trigger_condition="Use when foo.",
        )
        assert t.trigger_condition == "Use when foo."


class TestToolToSchema:
    """tool_to_schema — trigger condition inclusion."""

    def test_without_trigger(self):
        """tool_to_schema on a tool without trigger_condition returns description unchanged."""
        t = _make_tool(trigger_condition=None)
        schema = tool_to_schema(t)
        assert schema["function"]["description"] == "A test tool."
        assert "When to use:" not in schema["function"]["description"]

    def test_with_trigger(self):
        """tool_to_schema on a tool with trigger_condition appends it."""
        t = _make_tool(trigger_condition="Use when you need to test something.")
        schema = tool_to_schema(t)
        assert "When to use: Use when you need to test something." in schema["function"]["description"]

    def test_with_trigger_preserves_original(self):
        """tool_to_schema with trigger_condition preserves the original description prefix."""
        t = _make_tool(trigger_condition="Use when.")
        schema = tool_to_schema(t)
        assert schema["function"]["description"].startswith("A test tool.")

    def test_demo_tools_have_trigger_conditions(self):
        """register_demo_tools seeds all three tools with trigger conditions."""
        from app.tool_registry import ToolRegistry

        reg = ToolRegistry()
        reg._tools.clear()
        reg.register_demo_tools()
        for tool in reg.list_all():
            assert tool.trigger_condition is not None, f"{tool.name} missing trigger_condition"
            assert len(tool.trigger_condition) > 10
