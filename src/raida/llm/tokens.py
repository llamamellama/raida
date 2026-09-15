"""Offline token estimation. Local servers report exact counts after the fact; planning uses
a character heuristic with a safety margin."""

from __future__ import annotations

import math

SAFETY_MARGIN = 1.15


def estimate_tokens(text: str, chars_per_token: float) -> int:
    if not text:
        return 0
    return math.ceil(len(text) / chars_per_token * SAFETY_MARGIN)
