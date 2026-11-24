import random

import pytest

from pipeline.score import quality_scores
from schemas import Video


def _video(vid: str, views: int, likes: int, has_transcript: bool = False) -> Video:
    return Video(
        id=vid,
        title=vid,
        channel_id="c",
        channel_title="C",
        duration_s=600,
        views=views,
        likes=likes,
        published_at=None,
        description="",
        has_transcript=has_transcript,
    )


def test_normalization_within_pool() -> None:
    q = quality_scores([_video("a", 9, 0), _video("b", 99, 10), _video("c", 999, 500)])
    # engagement percentiles 0, .5, 1; popularity log10 1,2,3 -> 0, .5, 1; captions 0.7
    assert q["a"] == pytest.approx(0.77)
    assert q["b"] == pytest.approx(1.12)
    assert q["c"] == pytest.approx(1.47)


def test_single_video_gets_full_normalized_terms() -> None:
    assert quality_scores([_video("a", 100, 1, True)]) == {"a": pytest.approx(1.5)}


def test_all_equal_pool_is_constant() -> None:
    q = quality_scores([_video("a", 50, 5), _video("b", 50, 5)])
    assert q["a"] == q["b"] == pytest.approx(1.47)


def test_captions_term() -> None:
    q = quality_scores([_video("a", 100, 10, True), _video("b", 100, 10, False)])
    assert q["a"] - q["b"] == pytest.approx(0.03)


def test_zero_views_and_empty_pool() -> None:
    assert quality_scores([]) == {}
    q = quality_scores([_video("a", 0, 0), _video("b", 10, 1)])
    assert q["a"] < q["b"]


def test_bounds() -> None:
    rng = random.Random(1)  # noqa: S311 - seeded test data
    pool = []
    for i in range(200):
        views = rng.randint(0, 10_000_000)
        pool.append(_video(f"v{i}", views, rng.randint(0, views), rng.random() < 0.5))
    for value in quality_scores(pool).values():
        assert 0.5 <= value <= 1.5
