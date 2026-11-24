import random
import time

from pipeline.cover import cover
from schemas import CoverResult, Depth, Level, Segment, Tag, Topic

MIN_CONF = 0.6


def _topics(n: int, depth: Depth = 1) -> list[Topic]:
    return [
        Topic(topic_id=f"t{i}", module="M", name=f"T{i}", position=i, required_depth=depth)
        for i in range(n)
    ]


def _seg(video: str, idx: int, start_s: int, end_s: int) -> Segment:
    return Segment(
        id=f"{video}_{idx:02d}",
        video_id=video,
        start_s=start_s,
        end_s=end_s,
        text="",
        source="window",
    )


def _tag(seg: Segment, topic: str, depth: Depth = 1, conf: float = 0.9) -> Tag:
    return Tag(segment_id=seg.id, topic_id=topic, depth=depth, confidence=conf)


def _run(
    topics: list[Topic],
    segs: list[Segment],
    tags: list[Tag],
    quality: dict[str, float] | None = None,
    level: Level = "interview",
    budget: float | None = None,
) -> CoverResult:
    return cover(topics, segs, tags, quality or {}, level, budget, MIN_CONF)


def _ranges(res: CoverResult) -> list[tuple[str, int, int]]:
    return [(r.video_id, r.start_s, r.end_s) for r in res.ranges]


def _reasons(res: CoverResult) -> dict[str, str]:
    return {u.topic_id: u.reason for u in res.uncovered}


def test_full_coverage() -> None:
    a0, a1 = _seg("a", 0, 0, 240), _seg("a", 1, 600, 840)
    res = _run(_topics(3), [a0, a1], [_tag(a0, "t0"), _tag(a0, "t1"), _tag(a1, "t2")])
    assert res.uncovered == []
    assert _ranges(res) == [("a", 0, 240), ("a", 600, 840)]
    assert [t.topic_id for t in res.ranges[0].topics] == ["t0", "t1"]


def test_no_candidates_found() -> None:
    a0 = _seg("a", 0, 0, 60)
    tags = [_tag(a0, "t0"), _tag(a0, "t1", conf=0.5)]  # t1 below confidence, t2 untagged
    res = _run(_topics(3), [a0], tags)
    assert _reasons(res) == {"t1": "no candidates found", "t2": "no candidates found"}


def test_below_required_depth() -> None:
    a0 = _seg("a", 0, 0, 60)
    res = _run(_topics(1, depth=2), [a0], [_tag(a0, "t0", depth=1)])
    assert res.ranges == []
    assert _reasons(res) == {"t0": "below required depth"}


def test_budget_skips_long_segment_and_reports_over_budget() -> None:
    long_seg, short_seg = _seg("a", 0, 0, 20 * 60), _seg("b", 0, 0, 5 * 60)
    tags = [_tag(long_seg, "t0"), _tag(long_seg, "t1"), _tag(short_seg, "t0")]
    res = _run(_topics(2), [long_seg, short_seg], tags, budget=10)
    assert _ranges(res) == [("b", 0, 300)]
    assert _reasons(res) == {"t1": "over budget"}
    # Without a budget the long segment wins (2 topics for one switch).
    assert _ranges(_run(_topics(2), [long_seg, short_seg], tags)) == [("a", 0, 1200)]


def test_prune_removes_redundant_pick() -> None:
    x, y, z = _seg("x", 0, 0, 60), _seg("y", 0, 0, 600), _seg("z", 0, 0, 600)
    tags = [
        _tag(x, "t1"),
        _tag(x, "t2"),
        _tag(y, "t0"),
        _tag(y, "t1"),
        _tag(z, "t2"),
        _tag(z, "t3"),
    ]
    res = _run(_topics(4), [x, y, z], tags)
    assert res.uncovered == []
    assert _ranges(res) == [("y", 0, 600), ("z", 0, 600)]


def test_merge_overlapping_keeps_gaps() -> None:
    a0, a1, a2 = _seg("a", 0, 0, 300), _seg("a", 1, 200, 500), _seg("a", 2, 900, 1000)
    tags = [_tag(a0, "t0"), _tag(a1, "t1"), _tag(a2, "t2")]
    res = _run(_topics(3), [a0, a1, a2], tags)
    assert _ranges(res) == [("a", 0, 500), ("a", 900, 1000)]


def test_merge_union_and_max_depth() -> None:
    a0, a1 = _seg("a", 0, 0, 240), _seg("a", 1, 240, 480)
    tags = [_tag(a0, "t0"), _tag(a0, "t1", depth=2), _tag(a1, "t1", depth=3), _tag(a1, "t2")]
    res = _run(_topics(3), [a0, a1], tags)
    assert _ranges(res) == [("a", 0, 480)]
    assert [(t.topic_id, t.depth) for t in res.ranges[0].topics] == [
        ("t0", 1),
        ("t1", 3),
        ("t2", 1),
    ]


def test_order_by_syllabus_position() -> None:
    a0, z0 = _seg("a", 0, 0, 60), _seg("z", 0, 0, 60)
    res = _run(_topics(2), [a0, z0], [_tag(a0, "t1"), _tag(z0, "t0")])
    assert [r.video_id for r in res.ranges] == ["z", "a"]


def test_order_ties_keep_video_together() -> None:
    a0, a1, b0 = _seg("a", 0, 0, 60), _seg("a", 1, 600, 660), _seg("b", 0, 0, 60)
    tags = [
        _tag(b0, "t0"),
        _tag(b0, "t1"),
        _tag(a0, "t0"),
        _tag(a0, "t2"),
        _tag(a1, "t0"),
        _tag(a1, "t3"),
    ]
    res = _run(_topics(4), [b0, a0, a1], tags)
    assert _ranges(res) == [("a", 0, 60), ("a", 600, 660), ("b", 0, 60)]


def test_beginner_penalizes_depth3() -> None:
    deep, plain = _seg("p", 0, 0, 600), _seg("q", 0, 0, 660)
    tags = [_tag(deep, "t0", depth=3), _tag(plain, "t0", depth=1)]
    topics = _topics(1, depth=1)
    # cost: deep 10+2=12 vs plain 11+2=13; beginner deep 12+2=14.
    assert _ranges(_run(topics, [deep, plain], tags, level="interview")) == [("p", 0, 600)]
    assert _ranges(_run(topics, [deep, plain], tags, level="beginner")) == [("q", 0, 660)]


def test_switch_penalty_prefers_picked_video() -> None:
    a0, a1, b0 = _seg("a", 0, 0, 60), _seg("a", 1, 600, 780), _seg("b", 0, 0, 120)
    tags = [_tag(a0, "t0"), _tag(a1, "t1"), _tag(b0, "t1")]
    res = _run(_topics(2), [a0, a1, b0], tags)
    assert _ranges(res) == [("a", 0, 60), ("a", 600, 780)]


def test_quality_prefers_better_video() -> None:
    a0, b0 = _seg("a", 0, 0, 600), _seg("b", 0, 0, 600)
    tags = [_tag(a0, "t0"), _tag(b0, "t0")]
    res = _run(_topics(1), [a0, b0], tags, quality={"a": 0.8, "b": 1.4})
    assert _ranges(res) == [("b", 0, 600)]


def test_unknown_ids_ignored() -> None:
    a0 = _seg("a", 0, 0, 60)
    tags = [
        _tag(a0, "t0"),
        _tag(a0, "nope"),
        Tag(segment_id="ghost_00", topic_id="t0", depth=1, confidence=1.0),
    ]
    res = _run(_topics(1), [a0], tags)
    assert _ranges(res) == [("a", 0, 60)]
    assert [t.topic_id for t in res.ranges[0].topics] == ["t0"]


def test_empty_input() -> None:
    res = _run([], [], [])
    assert res.ranges == [] and res.uncovered == []
    res = _run(_topics(2), [], [])
    assert _reasons(res) == {"t0": "no candidates found", "t1": "no candidates found"}


def test_performance() -> None:
    rng = random.Random(7)  # noqa: S311 - seeded test data
    topics = _topics(40)
    segs: list[Segment] = []
    tags: list[Tag] = []
    for v in range(200):
        for i in range(10):
            s = _seg(f"v{v:03d}", i, i * 240, (i + 1) * 240)
            segs.append(s)
            for t in rng.sample(range(40), 2):
                tags.append(_tag(s, f"t{t}", depth=rng.choice([1, 2, 3]), conf=rng.random()))
    quality = {f"v{v:03d}": rng.uniform(0.5, 1.5) for v in range(200)}
    start = time.perf_counter()
    res = cover(topics, segs, tags, quality, "interview", None, MIN_CONF)
    assert time.perf_counter() - start < 0.5
    assert len(res.ranges) > 0
