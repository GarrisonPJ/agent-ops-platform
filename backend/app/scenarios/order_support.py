"""Stateful order-support world with a retained, order-only fixture handler.

The registry creates each world; the compatibility fixture calls its tools.
Refund evidence comes from the local ledger, never from tool names or policy.
Read test_order_support.py for the contract and environment.py for the boundary.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from app.durable_events import MAX_EVENT_CONTENT
from app.environment import EnvironmentCheck, InjectedFault, ToolDefinition, UnknownToolError
from app.phase1_schemas import EvaluationSpec, PolicyPatch
from app.scenario_assertions import (
    AssertionCombination,
    AssertionSuite,
    ScenarioAssertion,
    evaluate_assertion,
)
from app.scenario_ids import ORDER_SUPPORT_SCENARIO_ID as SCENARIO_ID
from app.scenario_registry import EmitFn, ScenarioMetadata, create_environment, register_scenario
from app.scenarios._helpers import TrajectoryEnvironment, emit_step, require_exact_arguments


ORDER_ID = "order-1001"
AMOUNT_CENTS = 4200
REFUND_ID = "refund-order-1001"
TOOLS = ("inspect_order", "authorize_payment", "ship_order", "request_refund")
PROVIDER_UNAVAILABLE = (
    "order-support.v1 provider execution is unavailable until the independent "
    "agent/Environment integration ships."
)

ORDER_TRAJECTORY_ASSERTIONS = (
    ScenarioAssertion(
        type="tool-sequence",
        sequence=("inspect_order", "authorize_payment", "ship_order"),
    ),
    ScenarioAssertion(type="step-count", max_steps=6),
)
ORDER_OUTCOME_ASSERTIONS = (
    ScenarioAssertion(type="json-match", json_path="order.refund_count", json_expected=1),
    ScenarioAssertion(
        type="json-match", json_path="order.payment_status", json_expected="refunded"
    ),
    ScenarioAssertion(
        type="json-match", json_path="order.shipment_status", json_expected="failed"
    ),
    ScenarioAssertion(
        type="json-match", json_path="order.refunded_amount_cents", json_expected=AMOUNT_CENTS
    ),
    ScenarioAssertion(
        type="json-match",
        json_path="order.refunds",
        json_expected=[{"refund_id": REFUND_ID, "amount_cents": AMOUNT_CENTS}],
    ),
)
ORDER_ASSERTIONS = (*ORDER_TRAJECTORY_ASSERTIONS, *ORDER_OUTCOME_ASSERTIONS)


@dataclass(frozen=True)
class Refund:
    refund_id: str
    amount_cents: int


@dataclass
class Order:
    order_id: str = ORDER_ID
    amount_cents: int = AMOUNT_CENTS
    payment_status: str = "pending"
    shipment_status: str = "pending"
    refunds: list[Refund] = field(default_factory=list)


def _order_snapshot(order: Order) -> dict[str, object]:
    return {
        "order": {
            **asdict(order),
            "refund_count": len(order.refunds),
            "refunded_amount_cents": sum(refund.amount_cents for refund in order.refunds),
        }
    }


def _canonical_observation(payload: dict[str, object]) -> str:
    observation = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    # Only fixed world-owned values enter observations. Fail rather than emit
    # truncated JSON that the persisted-event scorer could no longer inspect.
    if len(observation) > MAX_EVENT_CONTENT:
        raise ValueError("order observation exceeds the durable observation limit")
    return observation


class OrderSupportEnvironment(TrajectoryEnvironment):
    def __init__(self, params: dict[str, str]) -> None:
        super().__init__(AssertionSuite(AssertionCombination.ALL, ORDER_TRAJECTORY_ASSERTIONS))

    def reset(self, seed: int) -> None:
        super().reset(seed)
        self._orders = {ORDER_ID: Order()}

    def _snapshot(self) -> dict[str, object]:
        return _order_snapshot(self._orders[ORDER_ID])

    def _observation(self, result: str) -> str:
        return _canonical_observation({"result": result, **self._snapshot()})

    @property
    def fault_schedule(self) -> tuple[InjectedFault, ...]:
        failed_order = _order_snapshot(Order(
            payment_status="authorized", shipment_status="failed"
        ))
        refunded_order = _order_snapshot(Order(
            payment_status="refunded",
            shipment_status="failed",
            refunds=[Refund(REFUND_ID, AMOUNT_CENTS)],
        ))
        return (
            InjectedFault(
                tool="ship_order",
                condition="first authorized shipment attempt fails before dispatch",
                observation=_canonical_observation({"result": "shipping_failed", **failed_order}),
            ),
            InjectedFault(
                tool="request_refund",
                condition="first eligible refund commits, then its acknowledgement times out",
                observation=_canonical_observation({
                    "result": "refund_timeout_after_commit", **refunded_order
                }),
            ),
        )

    def tool_definitions(self) -> list[ToolDefinition]:
        descriptions = (
            "Inspect order-1001 and its payment, shipment, and refund ledger.",
            "Authorize order-1001 payment once before attempting shipment.",
            "Attempt shipment; a declared carrier fault prevents dispatch.",
            "Refund a paid order after shipping fails; retries return the same receipt.",
        )
        definitions: list[ToolDefinition] = []
        for name, description in zip(TOOLS, descriptions):
            properties = {"order_id": {"type": "string", "const": ORDER_ID}}
            if name == "request_refund":
                properties["reason"] = {"type": "string", "const": "shipping_failed"}
            definitions.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": list(properties),
                        "additionalProperties": False,
                    },
                },
            })
        return definitions

    def call(self, name: str, args: dict[str, object]) -> str:
        if name not in TOOLS:
            raise UnknownToolError(name)
        expected = {"order_id": ORDER_ID}
        if name == "request_refund":
            expected["reason"] = "shipping_failed"
        # Validation precedes both history and mutations, including fault state.
        require_exact_arguments(name, args, expected)
        order = self._orders[ORDER_ID]
        if name == "inspect_order":
            result = "order_inspected"
        elif name == "authorize_payment":
            if order.payment_status == "pending":
                order.payment_status = "authorized"
                result = "payment_authorized"
            elif order.payment_status == "authorized":
                result = "payment_already_authorized"
            else:
                result = "payment_already_refunded"
        elif name == "ship_order":
            if order.shipment_status == "failed":
                result = "shipping_already_failed"
            elif order.payment_status != "authorized":
                result = "payment_required"
            else:
                order.shipment_status = "failed"
                result = "shipping_failed"
        elif order.refunds:
            result = "refund_already_recorded"
        elif order.payment_status != "authorized" or order.shipment_status != "failed":
            result = "refund_not_allowed"
        else:
            # The ledger is the idempotency boundary: the simulated lost ACK is
            # an observation, not an exception/rollback or a real network timeout.
            order.refunds.append(Refund(REFUND_ID, order.amount_cents))
            order.payment_status = "refunded"
            result = "refund_timeout_after_commit"
        return self._record(name, args, self._observation(result))

    def check(self) -> EnvironmentCheck:
        trajectory = super().check()
        final_observation = _canonical_observation(self._snapshot())
        # Local outcome adapter: evaluate the same vocabulary on current state,
        # not a fabricated persisted step or agent-provided final response.
        outcome_input = {"steps": [{"observation": final_observation}]}
        return EnvironmentCheck(
            combination=AssertionCombination.ALL,
            trajectory=trajectory.trajectory,
            outcome=tuple(
                evaluate_assertion(assertion, outcome_input)
                for assertion in ORDER_OUTCOME_ASSERTIONS
            ),
            final_observation=final_observation,
        )


class OrderSupportScenario:
    def run_fixture(self, spec: EvaluationSpec, emit: EmitFn) -> int:
        environment = create_environment(spec)
        order_args = {"order_id": ORDER_ID}
        refund_args = {**order_args, "reason": "shipping_failed"}
        if spec.policy is None:
            actions = [("inspect_order", order_args, "Inspect again without resolving the order.")] * 6
        else:
            actions = [
                ("inspect_order", order_args, "Inspect the current order before changing it."),
                ("authorize_payment", order_args, "Authorize payment before shipping."),
                ("ship_order", order_args, "Attempt shipment and observe the carrier fault."),
                ("request_refund", refund_args, "Refund the order after failed shipment."),
                ("request_refund", refund_args, "Retry the lost acknowledgement idempotently."),
                ("inspect_order", order_args, "Verify exactly one refund in the final ledger."),
            ]
        max_steps = spec.policy.max_steps if spec.policy is not None else 6
        for index, (name, args, summary) in enumerate(actions[:max_steps]):
            observation = environment.call(name, dict(args))
            emit_step(emit, index, summary, name, dict(args), observation, 20 + index)
        checked = environment.check()
        # This world's declared combination is ALL; other suites are unchanged.
        return 0 if all(item.passed for item in (*checked.trajectory, *checked.outcome)) else 1

    async def run_provider(self, spec: EvaluationSpec, emit: EmitFn) -> int:
        emit("process_output", {"stream": "stderr", "content": PROVIDER_UNAVAILABLE})
        return 1

    def candidate_policy_patch(self) -> PolicyPatch:
        return PolicyPatch(
            instruction_patch=[
                "Inspect order-1001, authorize payment, then attempt shipment.",
                "After shipping fails, request a shipping_failed refund; retry the lost acknowledgement once.",
                "Inspect the final ledger and verify exactly one refund for the full order amount.",
            ],
            max_steps=6,
        )


register_scenario(
    ScenarioMetadata(
        id=SCENARIO_ID,
        name="Order Support",
        description=(
            "Resolve a deterministic shipping failure with an exactly-once refund in an "
            "instance-local order ledger. Fixture compatibility only; provider is unavailable."
        ),
        default_task=(
            "Resolve order-1001: inspect it, authorize payment, attempt shipment, then refund "
            "the failed shipment exactly once and verify the final ledger."
        ),
        allowed_tools=TOOLS,
        assertions=ORDER_ASSERTIONS,
        assertion_combination=AssertionCombination.ALL,
    ),
    OrderSupportScenario(),
    OrderSupportEnvironment,
)
