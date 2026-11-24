"""Segmentation (PRD §6 F7): chapters, then ~window_s sentence-gap windows, then metadata."""

import math

from pipeline.transcripts import parse_chapters
from schemas import Segment, SegmentSource, Transcript, TranscriptLine, Video

PAUSE_S = 1.0  # a gap this long between lines counts as a sentence break
SENTENCE_END = (".", "?", "!")


def _seg(
    video_id: str, idx: int, start: int, end: int, text: str, source: SegmentSource
) -> Segment:
    return Segment(
        id=f"{video_id}_{idx:02d}",
        video_id=video_id,
        start_s=start,
        end_s=max(end, start + 1),
        text=text,
        source=source,
    )


def _join(lines: list[TranscriptLine]) -> str:
    return " ".join(ln.text for ln in lines)


def _cut(lines: list[TranscriptLine], a: int, window_s: int) -> int:
    """Exclusive end index of the window starting at line `a`."""
    n = len(lines)
    wstart = lines[a].start_s
    last = lines[-1]
    if last.start_s + last.duration_s - wstart <= 1.4 * window_s:
        return n
    cands = [
        j for j in range(a + 1, n) if 0.6 * window_s <= lines[j].start_s - wstart <= 1.4 * window_s
    ]
    if not cands:
        cands = list(range(a + 1, n))
        if not cands:
            return n

    def good(j: int) -> bool:
        prev = lines[j - 1]
        gap = lines[j].start_s - (prev.start_s + prev.duration_s)
        return prev.text.rstrip().endswith(SENTENCE_END) or gap >= PAUSE_S

    pool = [j for j in cands if good(j)] or cands
    return min(pool, key=lambda j: abs(lines[j].start_s - wstart - window_s))


def build_segments(video: Video, transcript: Transcript, window_s: int) -> list[Segment]:
    vid = video.id
    lines = transcript.lines
    chapters = parse_chapters(video.description, video.duration_s)
    if chapters:
        out: list[Segment] = []
        for i, (s, e, title) in enumerate(chapters):
            inside = [ln for ln in lines if s <= ln.start_s < e]
            text = f"{title}\n{_join(inside)}".strip()
            out.append(_seg(vid, i, s, e, text, "chapter"))
        return out
    if lines:
        segs: list[Segment] = []
        a = 0
        while a < len(lines):
            b = _cut(lines, a, window_s)
            start = int(lines[a].start_s)
            if b < len(lines):
                end = round(lines[b].start_s)
            else:
                tail = lines[-1].start_s + lines[-1].duration_s
                end = video.duration_s if video.duration_s > start else math.ceil(tail)
            segs.append(_seg(vid, len(segs), start, end, _join(lines[a:b]), "window"))
            a = b
        return segs
    text = f"{video.title}\n{video.description}".strip()
    return [_seg(vid, 0, 0, video.duration_s, text, "metadata")]
