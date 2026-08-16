"""Multi-step research — search, fetch, and answer with deliberate baseline loops."""

from __future__ import annotations

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


def _topic(spec: EvaluationSpec) -> str:
    return spec.scenario_params.get("topic", "checkout latency")


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
        return self.run_fixture(spec, emit)

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
