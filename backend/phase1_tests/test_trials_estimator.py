"""H3-2 pass^k estimator and group aggregation on scripted outcomes; no model."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from math import comb

import pytest

from app.trials import GroupAggregate, aggregate, candidate_group_validates, pass_hat_k


@dataclass
class Member:
    status: str
    score: float | None = None
    metrics: dict = field(default_factory=dict)


def outcomes(pattern: str, score: float = 0.9, steps: int = 3) -> list[Member]:
    """'s' succeeded, 'f' failed, 't' timed_out, 'c' cancelled, 'q' queued, 'r' running."""
    names = {"s": "succeeded", "f": "failed", "t": "timed_out", "c": "cancelled", "q": "queued", "r": "running"}
    return [Member(names[ch], score if ch == "s" else 0.4, {"steps": steps}) for ch in pattern]


@pytest.mark.parametrize(("n", "c", "k", "expected"), [
    (5, 5, 5, 1.0), (5, 4, 5, 0.0), (5, 3, 2, 3 / 10), (5, 0, 1, 0.0), (1, 1, 1, 1.0), (1, 0, 1, 0.0),
    (20, 10, 3, comb(10, 3) / comb(20, 3)),
])
def test_estimator_values(n, c, k, expected) -> None:
    assert pass_hat_k(n, c, k) == pytest.approx(expected)


@pytest.mark.parametrize("n", range(1, 8))
def test_estimator_equals_mean_over_all_k_subsets(n) -> None:
    # The estimator is the share of k-subsets in which every trial succeeded.
    for c in range(n + 1):
        trials = [True] * c + [False] * (n - c)
        for k in range(1, n + 1):
            subsets = list(combinations(trials, k))
            assert pass_hat_k(n, c, k) == pytest.approx(sum(all(s) for s in subsets) / len(subsets))


def test_k_one_is_the_success_rate_and_larger_k_never_increases() -> None:
    for n in range(1, 10):
        for c in range(n + 1):
            assert pass_hat_k(n, c, 1) == pytest.approx(c / n)
            values = [pass_hat_k(n, c, k) for k in range(1, n + 1)]
            assert values == sorted(values, reverse=True)


@pytest.mark.parametrize(("n", "c", "k"), [(3, 1, 0), (3, 1, 4), (3, 4, 1), (3, -1, 1), (0, 0, 1)])
def test_estimator_rejects_out_of_range(n, c, k) -> None:
    with pytest.raises(ValueError):
        pass_hat_k(n, c, k)


@pytest.mark.parametrize("pattern", ["qss", "srs", "ssq"])
def test_incomplete_group_publishes_nothing(pattern) -> None:
    result = aggregate(3, 2, outcomes(pattern))
    assert result == GroupAggregate("running", terminal=pattern.count("s"))
    assert not result.published


def test_group_with_a_cancelled_member_is_never_published() -> None:
    result = aggregate(3, 1, outcomes("scs"))
    assert (result.state, result.pass_at_1, result.pass_at_k, result.published) == ("cancelled", None, None, False)


def test_complete_group_counts_failures_as_zero_score() -> None:
    result = aggregate(4, 2, outcomes("sfts", score=0.8, steps=5))
    assert result.published
    assert (result.successes, result.terminal) == (2, 4)
    assert result.pass_at_1 == pytest.approx(0.5)
    assert result.pass_at_k == pytest.approx(comb(2, 2) / comb(4, 2))
    assert result.mean_score == pytest.approx(2 * 0.8 / 4)  # failed/timed-out quality scores ignored
    assert result.mean_steps == pytest.approx(5)


def test_mean_steps_is_unknown_when_any_member_lacks_steps() -> None:
    members = outcomes("ss")
    members[1].metrics = {}
    assert aggregate(2, 1, members).mean_steps is None


def test_aggregate_requires_exactly_n_members() -> None:
    with pytest.raises(ValueError):
        aggregate(3, 1, outcomes("ss"))


def group(pattern: str, k: int, score: float = 0.9) -> GroupAggregate:
    return aggregate(len(pattern), k, outcomes(pattern, score=score))


@pytest.mark.parametrize(("baseline", "candidate", "k", "validated"), [
    ("sff", "sss", 2, True),       # pass^2 0 -> 1, mean up
    ("sff", "ssf", 1, True),       # pass^1 1/3 -> 2/3, mean up
    ("ssf", "sss", 3, True),       # pass^3 0 -> 1
    ("ssf", "sff", 2, False),      # pass^k down
    ("ssf", "ssf", 2, False),      # equal pass^k, zero mean delta
    ("sff", "ssf", 3, False),      # pass^3 stays 0: more successes alone is not enough at k = 3
    ("fff", "sff", 3, False),      # one lucky success no longer validates (tightened 2026-11-04)
    ("fffff", "sssss", 5, True),   # against 0/5 only 5/5 validates at k = 5
    ("fffff", "ssssf", 5, False),
    ("fff", "fff", 1, False),
])
def test_group_validation_rule(baseline, candidate, k, validated) -> None:
    assert candidate_group_validates(group(baseline, k), group(candidate, k), len(baseline)) is validated


@pytest.mark.parametrize(("baseline_score", "candidate_score", "validated"), [(0.8, 0.9, True), (0.9, 0.9, False)])
def test_group_validation_at_the_pass_k_ceiling(baseline_score, candidate_score, validated) -> None:
    # A baseline already at pass^k = 1 cannot be exceeded; staying at 1 with a higher mean score validates.
    baseline, candidate = group("sss", 2, score=baseline_score), group("sss", 2, score=candidate_score)
    assert candidate_group_validates(baseline, candidate, 3) is validated
    assert not candidate_group_validates(baseline, group("ssf", 2, score=1.0), 3)


def test_group_validation_needs_both_groups_published_and_n_above_one() -> None:
    complete = group("sss", 2)
    assert not candidate_group_validates(complete, aggregate(3, 2, outcomes("ssq")), 3)
    assert not candidate_group_validates(aggregate(3, 2, outcomes("scs")), complete, 3)
    with pytest.raises(ValueError):
        candidate_group_validates(group("s", 1), group("s", 1), 1)
