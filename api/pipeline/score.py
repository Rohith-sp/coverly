"""Video quality score q(s) (PRD §9.2). Pure computation, no I/O."""

import math

from schemas import Video

W_ENGAGEMENT = 0.4
W_POPULARITY = 0.3
W_RECENCY = 0.2
W_CAPTIONS = 0.1
RECENCY_V0 = 1.0  # constant in v0
CAPTIONS_REAL = 1.0
CAPTIONS_METADATA_ONLY = 0.7


def _percentiles(values: list[float]) -> list[float]:
    """Mid-rank percentile in [0, 1]. A single value or an all-equal pool maps to 1.0."""
    n = len(values)
    if n <= 1 or min(values) == max(values):
        return [1.0] * n
    out: list[float] = []
    for v in values:
        less = sum(1 for x in values if x < v)
        equal = sum(1 for x in values if x == v)
        out.append((less + (equal - 1) / 2) / (n - 1))
    return out


def _min_max(values: list[float]) -> list[float]:
    """Min-max scale to [0, 1]. A single value or an all-equal pool maps to 1.0."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [1.0] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def quality_scores(videos: list[Video]) -> dict[str, float]:
    """Return q per video id, in [0.5, 1.5], normalized within the given pool."""
    engagement = _percentiles([v.likes / max(v.views, 1) for v in videos])
    popularity = _min_max([math.log10(max(v.views, 0) + 1) for v in videos])
    scores: dict[str, float] = {}
    for video, e, p in zip(videos, engagement, popularity, strict=True):
        c = CAPTIONS_REAL if video.has_transcript else CAPTIONS_METADATA_ONLY
        scores[video.id] = 0.5 + (
            W_ENGAGEMENT * e + W_POPULARITY * p + W_RECENCY * RECENCY_V0 + W_CAPTIONS * c
        )
    return scores
