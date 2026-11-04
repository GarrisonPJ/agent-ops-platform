"""Trial-group aggregation: the pass^k estimator and group policy validation.

The Control Plane calls these pure functions when it renders an Experiment and
when the last member of a candidate group reaches a terminal status. They read
member Runs only and are never stored, so they cannot drift from the Runs.

Invariant (ADR-0010, CONTEXT 26-27): an incomplete group publishes no pass^k; a
group with a cancelled member is never published; success means terminal status
``succeeded``, which already requires the registered aggregate >= 1.0.

Read ``app.phase1_service.create_trial_group`` for how groups are created and
``docs/learn/06-pass-k.md`` for the estimator.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import comb
from typing import Iterable, Literal, Protocol

TERMINAL = frozenset({"succeeded", "failed", "timed_out", "cancelled"})


class Member(Protocol):
    status: str
    score: float | None
    metrics: dict


def pass_hat_k(n: int, c: int, k: int) -> float:
    """Unbiased estimator C(c, k) / C(n, k) of all k trials succeeding (tau-bench)."""
    if not 1 <= k <= n or not 0 <= c <= n:
        raise ValueError("require 1 <= k <= n and 0 <= c <= n")
    return comb(c, k) / comb(n, k)


@dataclass(frozen=True)
class GroupAggregate:
    state: Literal["running", "complete", "cancelled"]
    terminal: int
    successes: int | None = None
    pass_at_1: float | None = None
    pass_at_k: float | None = None
    mean_score: float | None = None
    mean_steps: float | None = None

    @property
    def published(self) -> bool:
        return self.state == "complete"


def aggregate(n: int, k: int, members: Iterable[Member]) -> GroupAggregate:
    members = list(members)
    if len(members) != n:
        raise ValueError("group must have exactly n members")
    terminal = sum(member.status in TERMINAL for member in members)
    if terminal < n:
        return GroupAggregate("running", terminal)
    if any(member.status == "cancelled" for member in members):
        # pass^k assumes n attempts by the Agent; an operator cancellation is
        # not a failed attempt, so the group has nothing to publish.
        return GroupAggregate("cancelled", terminal)
    succeeded = [member for member in members if member.status == "succeeded"]
    c = len(succeeded)
    steps = [member.metrics.get("steps") for member in members]
    return GroupAggregate(
        "complete",
        terminal,
        successes=c,
        pass_at_1=c / n,
        pass_at_k=pass_hat_k(n, c, k),
        # ADR-0010: failures count as score 0 in the mean.
        mean_score=sum(member.score or 0.0 for member in succeeded) / n,
        mean_steps=(
            sum(steps) / n
            if all(type(value) is int and value >= 0 for value in steps)
            else None
        ),
    )


def candidate_group_validates(baseline: GroupAggregate, candidate: GroupAggregate, n: int) -> bool:
    """n > 1: positive mean score delta and a strictly higher pass^k (ADR-0010 H3-2).

    "Not lower" would always hold against a pass^k = 0 baseline, so one lucky
    candidate success could validate. Only a baseline already at pass^k = 1,
    which cannot rise, is matched by a candidate that also stays at 1.
    """
    if n < 2:
        raise ValueError("single trials use CONTEXT invariant 7")
    if not (baseline.published and candidate.published):
        return False
    assert baseline.pass_at_k is not None and candidate.pass_at_k is not None
    assert baseline.mean_score is not None and candidate.mean_score is not None
    improved = candidate.pass_at_k > baseline.pass_at_k or candidate.pass_at_k == baseline.pass_at_k == 1.0
    return improved and candidate.mean_score - baseline.mean_score > 0
