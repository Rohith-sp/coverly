import itertools

from pipeline.segment import build_segments
from schemas import Transcript, TranscriptLine, Video


def _video(desc: str = "", duration: int = 600) -> Video:
    return Video(
        id="vid",
        title="Title",
        channel_id="c",
        channel_title="C",
        duration_s=duration,
        views=1,
        likes=1,
        published_at=None,
        description=desc,
    )


def _tr(lines: list[tuple[float, float, str]], source: str = "manual") -> Transcript:
    return Transcript(
        video_id="vid",
        language="en",
        source=source,
        lines=[TranscriptLine(start_s=s, duration_s=d, text=t) for s, d, t in lines],
    )


NONE = Transcript(video_id="vid", language=None, source="none", lines=[])


def test_chapter_segments_with_transcript_text() -> None:
    v = _video("00:00 Intro\n01:00 Tokens", duration=180)
    tr = _tr([(5, 5, "welcome"), (70, 5, "jwt is a token")])
    segs = build_segments(v, tr, 240)
    assert [(s.id, s.start_s, s.end_s, s.source) for s in segs] == [
        ("vid_00", 0, 60, "chapter"),
        ("vid_01", 60, 180, "chapter"),
    ]
    assert segs[0].text == "Intro\nwelcome"
    assert "jwt is a token" in segs[1].text


def test_chapters_without_transcript_use_titles() -> None:
    segs = build_segments(_video("00:00 Intro\n01:00 Tokens"), NONE, 240)
    assert [s.text for s in segs] == ["Intro", "Tokens"]


def test_windows_cut_at_sentence_ends() -> None:
    # a line every 20 s; only the line ending at 100 s and 220 s end sentences
    lines = []
    for i in range(30):
        t = i * 20.0
        text = f"line {i}." if i in (4, 10) else f"line {i}"
        lines.append((t, 20.0, text))
    v = _video(duration=600)
    segs = build_segments(v, _tr(lines), 120)
    assert segs[0].source == "window"
    assert segs[0].start_s == 0
    # target 120 s; boundary at 120 s follows "line 5" (no period), so the cut goes to a
    # sentence end within the tolerance band: after line 4 (100 s) or line 10 (220 s)
    assert segs[0].end_s == 100
    assert segs[1].start_s == 100
    assert segs[-1].end_s == 600
    for a, b in itertools.pairwise(segs):
        assert a.end_s == b.start_s
        assert a.end_s > a.start_s
    assert [s.id for s in segs] == [f"vid_{i:02d}" for i in range(len(segs))]


def test_windows_cut_at_pause() -> None:
    lines = [(i * 10.0, 5.0, "word") for i in range(30)]  # 5 s gaps between every line
    segs = build_segments(_video(duration=300), _tr(lines), 100)
    assert segs[0].end_s == 100


def test_short_transcript_single_window() -> None:
    segs = build_segments(_video(duration=90), _tr([(0, 5, "a."), (5, 5, "b.")]), 240)
    assert len(segs) == 1
    assert (segs[0].start_s, segs[0].end_s) == (0, 90)
    assert segs[0].text == "a. b."


def test_metadata_only() -> None:
    v = _video("just a blurb", duration=300)
    segs = build_segments(v, NONE, 240)
    assert len(segs) == 1
    s = segs[0]
    assert (s.id, s.start_s, s.end_s, s.source) == ("vid_00", 0, 300, "metadata")
    assert s.text == "Title\njust a blurb"


def test_zero_duration_never_zero_length() -> None:
    s = build_segments(_video(duration=0), NONE, 240)[0]
    assert s.end_s > s.start_s
