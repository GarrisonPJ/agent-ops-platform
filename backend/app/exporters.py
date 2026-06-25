"""Export functions — pure functions for building training-data formats.

Functions take trajectory detail dicts (as returned by the API) and return
formatted structures suitable for RL / fine-tuning pipelines.
"""

from __future__ import annotations

import json
from typing import Any


def build_openai_sft(trajectory_detail: dict[str, Any]) -> dict[str, Any]:
    """Build an OpenAI fine-tuning (SFT) message format from a trajectory.

    Extracts the ``task`` from the trajectory and the ``final_answer`` from
    the last step whose ``action`` is ``None`` (or the very last step's
    observation as fallback).

    Returns
    -------
    dict
        ``{"messages": [{"role": "system", "content": ""},
                        {"role": "user", "content": "<task>"},
                        {"role": "assistant", "content": "<final_answer>"}]}``
    """
    task = trajectory_detail.get("task", "")
    steps = trajectory_detail.get("steps", [])

    final_answer = ""
    for step in reversed(steps):
        if step.get("action") is None:
            final_answer = step.get("observation", "")
            break
    if not final_answer and steps:
        final_answer = steps[-1].get("observation", "")

    return {
        "messages": [
            {"role": "system", "content": ""},
            {"role": "user", "content": task},
            {"role": "assistant", "content": final_answer},
        ]
    }


def build_rlhf_pair(
    best_trajectory: dict[str, Any],
    worst_trajectory: dict[str, Any],
) -> dict[str, Any]:
    """Build an RLHF preference pair from two trajectories.

    Parameters
    ----------
    best_trajectory:
        Trajectory detail dict for the highest-scoring run.
    worst_trajectory:
        Trajectory detail dict for the lowest-scoring run.

    Returns
    -------
    dict
        ``{"chosen": <best_trajectory>, "rejected": <worst_trajectory>}``
    """
    return {
        "chosen": best_trajectory,
        "rejected": worst_trajectory,
    }


def build_jsonl(trajectories: list[dict[str, Any]]) -> str:
    """Build newline-delimited JSON from a list of trajectory detail dicts.

    Parameters
    ----------
    trajectories:
        List of trajectory detail dicts.

    Returns
    -------
    str
        Newline-delimited JSON string, with each line being a JSON
        representation of one trajectory.  Trailing newline is included.
    """
    lines = [json.dumps(t, default=str) for t in trajectories]
    return "\n".join(lines) + "\n"
