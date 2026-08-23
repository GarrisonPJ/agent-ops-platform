"""Multi-step research — search, fetch, and answer with deliberate baseline loops."""

from __future__ import annotations

from app.phase1_provider import ProviderError, run_provider_agent
from app.phase1_schemas import EvaluationSpec, PolicyPatch
from app.scenario_assertions import AssertionCombination, ScenarioAssertion
from app.scenario_registry import (
    EmitFn,
    ScenarioHandler,
    ScenarioMetadata,
    ScenarioParamDef,
    register_scenario,
)
from app.scenarios._helpers import emit_step

from app.scenario_ids import MULTI_STEP_RESEARCH_SCENARIO_ID as SCENARIO_ID
TOOLS = ("search_documents", "fetch_document", "submit_answer")
RESEARCH_PROVIDER_INSTRUCTIONS = (
    "You answer research questions using only the provided document tools.",
    "Use at most one tool call per response.",
    "Do not request shell commands, network access, or arbitrary code execution.",
)


def _provider_tool_definitions() -> list[dict[str, object]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "search_documents",
                "description": "Search the deterministic document index for the research topic.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "fetch_document",
                "description": "Fetch the highest-ranked document from the deterministic index.",
                "parameters": {
                    "type": "object",
                    "properties": {"document_id": {"type": "string"}},
                    "required": ["document_id"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "submit_answer",
                "description": "Submit an evidence-backed answer to the research question.",
                "parameters": {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                    "required": ["answer"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def _topic(spec: EvaluationSpec) -> str:
    return spec.scenario_params.get("topic", "checkout latency")


def _provider_tool_result(spec: EvaluationSpec, name: str) -> tuple[dict[str, str], str]:
    if name == "search_documents":
        return (
            {"query": _topic(spec)},
            "Found two relevant documents about dependency latency.",
        )
    if name == "fetch_document":
        return (
            {"document_id": "doc-dependency-latency"},
            "The document explains payment-gateway pool saturation.",
        )
    if name == "submit_answer":
        return (
            {"answer": "Checkout latency is caused by payment-gateway connection pool saturation."},
            "Answer accepted with supporting citations.",
        )
    raise ProviderError(
        "PROVIDER_UNSUPPORTED_TOOL",
        "Provider selected a tool outside the allowlist",
        retryable=False,
        attempts=1,
    )


def _baseline(spec: EvaluationSpec, emit: EmitFn) -> int:
    topic = _topic(spec)
    emit("process_output", {"stream": "stdout", "content": "Starting research baseline"})
    for index in range(6):
        emit_step(
            emit,
            index,
            "Search again without fetching or answering.",
            "search_documents",
            {"query": topic},
            "The same result list is returned; no new evidence is gathered.",
            70 + index * 5,
        )
    return 1


def _replay(spec: EvaluationSpec, emit: EmitFn) -> int:
    topic = _topic(spec)
    emit("process_output", {"stream": "stdout", "content": "Applying research policy"})
    emit_step(
        emit,
        0,
        "Search once for the topic.",
        "search_documents",
        {"query": topic},
        "Found two relevant documents about dependency latency.",
        36,
    )
    emit_step(
        emit,
        1,
        "Fetch the highest-ranked document.",
        "fetch_document",
        {"document_id": "doc-dependency-latency"},
        "The document explains payment-gateway pool saturation.",
        44,
    )
    emit_step(
        emit,
        2,
        "Submit the evidence-backed answer.",
        "submit_answer",
        {"answer": "Checkout latency is caused by payment-gateway connection pool saturation."},
        "Answer accepted with supporting citations.",
        28,
    )
    return 0


class MultiStepResearchScenario:
    def run_fixture(self, spec: EvaluationSpec, emit: EmitFn) -> int:
        return _replay(spec, emit) if spec.policy is not None else _baseline(spec, emit)

    async def run_provider(self, spec: EvaluationSpec, emit: EmitFn) -> int:
        return await run_provider_agent(
            spec,
            emit,
            tool_definitions=_provider_tool_definitions,
            tool_result=_provider_tool_result,
            instructions=RESEARCH_PROVIDER_INSTRUCTIONS,
            scenario_label="research",
        )

    def candidate_policy_patch(self) -> PolicyPatch:
        return PolicyPatch(
            instruction_patch=[
                "Search once, fetch the best document, then submit an answer.",
                "Do not repeat the same search query without fetching.",
            ],
            tool_priority={
                "search_documents": 1.0,
                "fetch_document": 0.9,
                "submit_answer": 0.8,
            },
            max_steps=6,
        )


RESEARCH_ASSERTIONS = (
    ScenarioAssertion(type="tool-used", tool="search_documents", weight=1.0),
    ScenarioAssertion(type="tool-used", tool="fetch_document", weight=1.0),
    ScenarioAssertion(type="tool-used", tool="submit_answer", weight=1.0),
    ScenarioAssertion(
        type="tool-sequence",
        sequence=("search_documents", "fetch_document", "submit_answer"),
        weight=2.0,
    ),
    ScenarioAssertion(type="step-count", max_steps=4, weight=1.0),
)


register_scenario(
    ScenarioMetadata(
        id=SCENARIO_ID,
        name="Multi-step Research",
        description=(
            "Answer a research question by searching documents, fetching evidence, "
            "and submitting a final answer."
        ),
        default_task="Answer a research question using search and fetch tools",
        allowed_tools=TOOLS,
        params=(
            ScenarioParamDef(
                name="topic",
                description="Research topic used by search tools.",
                required=True,
            ),
        ),
        assertions=RESEARCH_ASSERTIONS,
        assertion_combination=AssertionCombination.WEIGHTED,
    ),
    MultiStepResearchScenario(),
)
