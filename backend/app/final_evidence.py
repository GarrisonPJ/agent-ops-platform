"""Strict, bounded Environment-owned final-check operands for durable handoff.

EnvironmentSession generates these facts; durable normalization and the CP validate them.
Operands preserve actual state, never expected values, assertion scores, or Run status.
Read env_server.py for generation and durable_events.py for the persisted projection.
"""

from __future__ import annotations

import json
import math
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.scenario_ids import (
    API_FAULT_ORCHESTRATION_SCENARIO_ID,
    CHECKOUT_SCENARIO_ID,
    MULTI_STEP_RESEARCH_SCENARIO_ID,
    ORDER_SUPPORT_SCENARIO_ID,
)

MAX_FINAL_OBSERVATION_CHARS = 4_000
MAX_FINAL_OBSERVATION_BYTES = 16_384
_TRAJECTORY_SCENARIO_IDS = frozenset(
    {
        CHECKOUT_SCENARIO_ID,
        MULTI_STEP_RESEARCH_SCENARIO_ID,
        API_FAULT_ORCHESTRATION_SCENARIO_ID,
    }
)


class _StrictOperand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class OrderRefundOperand(_StrictOperand):
    refund_id: str
    amount_cents: int = Field(ge=0)


class OrderOperand(_StrictOperand):
    order_id: str
    amount_cents: int = Field(ge=0)
    payment_status: Literal["pending", "authorized", "refunded"]
    shipment_status: Literal["pending", "failed"]
    refund_count: int = Field(ge=0)
    refunded_amount_cents: int = Field(ge=0)
    refunds: list[OrderRefundOperand]


class OrderFinalObservation(_StrictOperand):
    order: OrderOperand


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate final observation key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite final observation number")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite final observation number")
    return parsed


def _validate_observation_bounds(value: str) -> None:
    if len(value) > MAX_FINAL_OBSERVATION_CHARS:
        raise ValueError("final observation exceeds character limit")
    if len(value.encode("utf-8")) > MAX_FINAL_OBSERVATION_BYTES:
        raise ValueError("final observation exceeds byte limit")


def validate_order_final_observation(value: str | None) -> OrderFinalObservation:
    """Decode required ledger types/presence only; no task-success comparisons."""
    if not isinstance(value, str):
        raise ValueError("order final observation must be a string")
    _validate_observation_bounds(value)
    try:
        decoded = json.loads(
            value,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except (RecursionError, UnicodeError) as exc:
        raise ValueError("invalid final observation JSON") from exc
    return OrderFinalObservation.model_validate(decoded, strict=True)


class FinalCheck(_StrictOperand):
    scenario_id: str
    # Required even for trajectory-only suites: absence is not JSON null.
    final_observation: str | None = Field(max_length=MAX_FINAL_OBSERVATION_CHARS)

    @model_validator(mode="after")
    def validate_operands(self) -> Self:
        if self.scenario_id == ORDER_SUPPORT_SCENARIO_ID:
            validate_order_final_observation(self.final_observation)
        elif self.scenario_id in _TRAJECTORY_SCENARIO_IDS:
            if self.final_observation is not None:
                raise ValueError("trajectory-only final observation must be null")
        else:
            raise ValueError("unsupported final-check scenario")
        return self


def validate_final_check(
    value: object, *, scenario_id: str | None = None
) -> FinalCheck:
    """Validate exact operands, optionally against the immutable Run identity."""
    # Revalidate model instances too: do not trust model_construct or mutable
    # caller-owned input merely because it already has the public model type.
    if isinstance(value, FinalCheck):
        value = value.model_dump(mode="python")
    parsed = FinalCheck.model_validate(value, strict=True)
    if scenario_id is not None and parsed.scenario_id != scenario_id:
        raise ValueError("final-check scenario mismatch")
    return parsed
