"""Spaced repetition: when should you see a question again?

THE IDEA. Memory decays roughly exponentially, and every SUCCESSFUL retrieval makes the decay slower. So the optimal
schedule reviews a fact just before you would forget it, at intervals that grow after each success (1 day, ~3 days,
~a week, ~3 weeks, ...) and shrink back after a failure. Recalling under mild difficulty is what strengthens memory;
re-reading feels productive but is not.

THE ALGORITHM. A variant of SM-2 (SuperMemo 2, the ancestor of Anki's scheduler), with Anki's four buttons:

    grade 1 "again" : you could not recall it   -> lapse: interval resets to 1 day, ease drops 0.20
    grade 2 "hard"  : recalled with real effort -> interval x 1.2, ease drops 0.15
    grade 3 "good"  : recalled correctly        -> interval x ease
    grade 4 "easy"  : instant and certain       -> interval x ease x 1.3, ease rises 0.15

`ease` (default 2.5, floor 1.3) is the per-card growth factor: hard cards grow slowly, easy cards fast.
First successful reviews use fixed steps (1 day, then 3 days for "good") because ease is meaningless with no history.
Deterministic and pure (no clock, no DB), so it is trivially testable.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta

GRADES = {1: "again", 2: "hard", 3: "good", 4: "easy"}
MIN_EASE, MAX_INTERVAL = 1.3, 365.0


@dataclass(frozen=True)
class CardState:
    interval_days: float = 0.0
    ease: float = 2.5
    reps: int = 0  # consecutive successful reviews
    lapses: int = 0


def schedule(state: CardState, grade: int, today: date) -> tuple[CardState, date]:
    """Apply one review. Returns the new state and the next due date."""
    if grade not in GRADES:
        raise ValueError(f"grade must be 1-4, got {grade}")
    s = state
    if grade == 1:
        s = replace(s, interval_days=1.0, ease=max(MIN_EASE, s.ease - 0.20), reps=0, lapses=s.lapses + 1)
    else:
        if s.reps == 0:
            interval = {2: 1.0, 3: 1.0, 4: 4.0}[grade]
        elif s.reps == 1:
            interval = {2: 2.0, 3: 3.0, 4: 6.0}[grade]
        else:
            base = max(s.interval_days, 1.0)
            interval = {2: base * 1.2, 3: base * s.ease, 4: base * s.ease * 1.3}[grade]
        ease = s.ease + {2: -0.15, 3: 0.0, 4: 0.15}[grade]
        s = replace(s, interval_days=min(MAX_INTERVAL, round(interval, 2)), ease=round(max(MIN_EASE, ease), 3), reps=s.reps + 1)
    return s, today + timedelta(days=max(1, round(s.interval_days)))


def grade_from_verdict(verdict: str) -> int:
    """Map an AI recall check onto the four buttons. The learner can always override it."""
    return {"incorrect": 1, "partial": 2, "correct": 3}[verdict]
