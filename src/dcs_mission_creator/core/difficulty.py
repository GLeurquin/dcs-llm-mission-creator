"""The mission difficulty label, and how a free-text label maps onto it.

Difficulty is a mission-design concept, not a drawing one: it sets how much of
the enemy picture the F10 plan reveals (`core/map_draw.py`) *and* how the enemy
AI fights (`core/tasking.py`). It used to live in `map_draw.py`, which meant
asking for an ROE dial pulled the whole `dcs.drawing` stack in behind it.
"""

from __future__ import annotations

from enum import Enum

import structlog

log = structlog.get_logger(__name__)


class Difficulty(Enum):
    """Mission difficulty labels, ordered easiest → hardest."""

    RECRUIT = "recruit"
    TRAINED = "trained"
    VETERAN = "veteran"
    ACE = "ace"

    @classmethod
    def parse(cls, label: str) -> Difficulty:
        """Map a free-text label to a member, defaulting to TRAINED.

        The fallback is logged: an unrecognised label silently downgrades both
        the map reveal and the AI's ROE, which looks like the mission being too
        easy rather than like a typo.
        """
        try:
            return cls(label.strip().lower())
        except ValueError:
            log.warning(
                "unknown difficulty label, falling back to trained",
                label=label,
                known=[member.value for member in cls],
            )
            return cls.TRAINED

    @staticmethod
    def coerce(difficulty: Difficulty | str) -> Difficulty:
        """Accept either a member or a label — the two call sites both need it."""
        if isinstance(difficulty, Difficulty):
            return difficulty
        return Difficulty.parse(difficulty)
