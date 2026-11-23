"""Skill-matrix update rule. Deterministic code, deliberately NOT an LLM judgement.

Why not ask the LLM "how much did the learner improve"? It can't observe the learner, its answer would drift
run to run, and a progress bar that moves arbitrarily destroys trust. A transparent formula can be audited
and tuned. It is a *motivation heuristic*, not a measurement of competence - for real signal, add eval
exercises (quizzes, tests that pass) and weight those higher.

    delta = BASE[kind] * DIFFICULTY[difficulty] * headroom_factor(level)

Learning has diminishing returns: the first hours on a topic move the needle most. headroom_factor falls
linearly from 1.0 at level 0 to 0.25 at level 100, so 20 -> 25 takes about a week of daily work, while
90 -> 95 takes much longer.
"""
from __future__ import annotations

BASE_POINTS = {"reading": 0.15, "hands_on": 0.60, "insight": 0.05, "math": 0.12}  # building teaches more than reading; math = per first-try correct drill problem
DIFFICULTY_MULTIPLIER = {1: 1.0, 2: 1.25, 3: 1.5}


def headroom_factor(level: float) -> float:
    return 0.25 + 0.75 * max(0.0, 1.0 - level / 100.0)


def compute_delta(kind: str, difficulty: int, level: float) -> float:
    return round(BASE_POINTS[kind] * DIFFICULTY_MULTIPLIER[difficulty] * headroom_factor(level), 3)


def apply_delta(level: float, delta: float) -> float:
    return round(min(100.0, max(0.0, level + delta)), 3)
