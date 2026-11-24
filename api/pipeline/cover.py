"""Greedy weighted set cover over segments (PRD §9.3-9.5). Pure function: no I/O, DB or LLM.

Rules not fixed by the PRD:
- A segment's depth for a topic is the max depth among its covering tags for that topic.
- Beginner +20%: if any still-uncovered topic the segment would cover is covered via depth 3,
  its `duration_min / q` term is multiplied by 1.2 (the switch penalty is not scaled).
- A video missing from `quality` gets q = 1.0.
- Ties on gain/cost go to the lowest segment id.
"""

from collections import defaultdict
from dataclasses import dataclass

from schemas import (
    CoverResult,
    Depth,
    Level,
    PlannedRange,
    Segment,
    Tag,
    Topic,
    TopicRef,
    UncoveredReason,
    UncoveredTopic,
)

SWITCH_PENALTY_MIN = 2.0
BEGINNER_DEEP_MULTIPLIER = 1.2
DEFAULT_QUALITY = 1.0
_MIN_COST = 1e-9


@dataclass
class _Cand:
    seg: Segment
    minutes: float
    covers: dict[str, Depth]  # topic_id -> max covering depth


def _candidates(
    topics: dict[str, Topic], segments: list[Segment], tags: list[Tag], confidence_min: float
) -> tuple[list[_Cand], set[str]]:
    """Build covering candidates and the set of topics with any confident tag."""
    seg_by_id = {s.id: s for s in segments}
    covers: dict[str, dict[str, Depth]] = defaultdict(dict)
    tagged: set[str] = set()
    for tag in tags:
        topic = topics.get(tag.topic_id)
        if topic is None or tag.segment_id not in seg_by_id or tag.confidence < confidence_min:
            continue
        tagged.add(tag.topic_id)
        if tag.depth >= topic.required_depth:
            prev = covers[tag.segment_id].get(tag.topic_id, 0)
            if tag.depth > prev:
                covers[tag.segment_id][tag.topic_id] = tag.depth
    cands = [
        _Cand(seg_by_id[sid], max(seg_by_id[sid].end_s - seg_by_id[sid].start_s, 0) / 60, cov)
        for sid, cov in sorted(covers.items())
    ]
    return cands, tagged


def _greedy(
    cands: list[_Cand],
    uncovered: set[str],
    quality: dict[str, float],
    beginner: bool,
    budget_minutes: float | None,
) -> list[_Cand]:
    """Pick segments by max new_covered / cost until nothing that fits gains anything."""
    picked: list[_Cand] = []
    picked_ids: set[str] = set()
    picked_videos: set[str] = set()
    total = 0.0
    while uncovered:
        best: _Cand | None = None
        best_ratio = 0.0
        for c in cands:
            if c.seg.id in picked_ids:
                continue
            new = uncovered.intersection(c.covers)
            if not new:
                continue
            if budget_minutes is not None and total + c.minutes > budget_minutes:
                continue
            base = c.minutes / quality.get(c.seg.video_id, DEFAULT_QUALITY)
            if beginner and any(c.covers[t] == 3 for t in new):
                base *= BEGINNER_DEEP_MULTIPLIER
            switch = 0.0 if c.seg.video_id in picked_videos else SWITCH_PENALTY_MIN
            ratio = len(new) / max(base + switch, _MIN_COST)
            if ratio > best_ratio:
                best, best_ratio = c, ratio
        if best is None:
            break
        picked.append(best)
        picked_ids.add(best.seg.id)
        picked_videos.add(best.seg.video_id)
        uncovered -= best.covers.keys()
        total += best.minutes
    return picked


def _prune(picked: list[_Cand]) -> list[_Cand]:
    """Reverse pass: drop picks whose topics are all covered by other kept picks."""
    count: dict[str, int] = defaultdict(int)
    for c in picked:
        for t in c.covers:
            count[t] += 1
    keep = list(picked)
    for c in reversed(picked):
        if all(count[t] >= 2 for t in c.covers):
            keep.remove(c)
            for t in c.covers:
                count[t] -= 1
    return keep


def _merge(picked: list[_Cand], topics: dict[str, Topic]) -> list[PlannedRange]:
    """Merge adjacent/overlapping picks of the same video and order by syllabus position."""
    by_video: dict[str, list[_Cand]] = defaultdict(list)
    for c in picked:
        by_video[c.seg.video_id].append(c)
    groups: list[tuple[str, int, int, dict[str, Depth]]] = []
    for vid, cs in by_video.items():
        cs.sort(key=lambda c: (c.seg.start_s, c.seg.end_s))
        cur: tuple[str, int, int, dict[str, Depth]] | None = None
        for c in cs:
            if cur is not None and c.seg.start_s <= cur[2]:
                merged = dict(cur[3])
                for t, d in c.covers.items():
                    merged[t] = max(merged.get(t, d), d)
                cur = (vid, cur[1], max(cur[2], c.seg.end_s), merged)
            else:
                if cur is not None:
                    groups.append(cur)
                cur = (vid, c.seg.start_s, c.seg.end_s, dict(c.covers))
        if cur is not None:
            groups.append(cur)
    groups.sort(key=lambda g: (min(topics[t].position for t in g[3]), g[0], g[1]))
    return [
        PlannedRange(
            video_id=vid,
            start_s=start,
            end_s=end,
            topics=[
                TopicRef(topic_id=t, name=topics[t].name, depth=d)
                for t, d in sorted(cov.items(), key=lambda kv: topics[kv[0]].position)
            ],
        )
        for vid, start, end, cov in groups
    ]


def cover(
    topics: list[Topic],
    segments: list[Segment],
    tags: list[Tag],
    quality: dict[str, float],
    level: Level,
    budget_minutes: float | None,
    confidence_min: float,
) -> CoverResult:
    """Smallest-cost set of ranges covering the plan topics, in syllabus order.

    `Topic.required_depth` decides coverage; `level` only toggles the beginner +20% rule.
    `budget_minutes=None` means unlimited.
    """
    by_id = {t.topic_id: t for t in topics}
    cands, tagged = _candidates(by_id, segments, tags, confidence_min)
    picked = _greedy(cands, set(by_id), quality, level == "beginner", budget_minutes)
    ranges = _merge(_prune(picked), by_id)

    covered = {t for c in picked for t in c.covers}
    coverable = {t for c in cands for t in c.covers}
    uncovered: list[UncoveredTopic] = []
    for t in sorted(topics, key=lambda t: t.position):
        if t.topic_id in covered:
            continue
        reason: UncoveredReason
        if t.topic_id in coverable:
            reason = "over budget"
        elif t.topic_id in tagged:
            reason = "below required depth"
        else:
            reason = "no candidates found"
        uncovered.append(
            UncoveredTopic(topic_id=t.topic_id, name=t.name, module=t.module, reason=reason)
        )
    return CoverResult(ranges=ranges, uncovered=uncovered)
