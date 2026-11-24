import asyncio
import time
from types import SimpleNamespace

import pytest
import requests
from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled

from pipeline import transcripts
from pipeline.transcripts import fetch_transcript, languages_for, parse_chapters


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_languages_for() -> None:
    assert languages_for("en") == ["en"]
    assert languages_for("hi") == ["hi"]
    assert languages_for("both") == ["en", "hi"]


def test_chapters_valid() -> None:
    desc = "Intro text\n00:00 Intro\n02:30 - Tokens\n1:02:03 Refresh flow\nthanks"
    assert parse_chapters(desc, 4000) == [
        (0, 150, "Intro"),
        (150, 3723, "Tokens"),
        (3723, 4000, "Refresh flow"),
    ]


def test_chapters_must_start_at_zero() -> None:
    assert parse_chapters("00:10 A\n01:00 B", 300) == []


def test_chapters_single_line() -> None:
    assert parse_chapters("00:00 Only", 300) == []


def test_chapters_non_increasing() -> None:
    assert parse_chapters("00:00 A\n02:00 B\n01:00 C", 300) == []


_Langs = list[str]


class _Snippet(SimpleNamespace):
    pass


class _Found:
    def __init__(self, generated: bool) -> None:
        self.is_generated = generated

    def fetch(self) -> SimpleNamespace:
        return SimpleNamespace(
            language_code="en",
            is_generated=self.is_generated,
            snippets=[
                _Snippet(text=" hello ", start=0.0, duration=2.0),
                _Snippet(text="  ", start=2.0, duration=1.0),
                _Snippet(text="world.", start=3.0, duration=2.0),
            ],
        )


def _no_found() -> NoTranscriptFound:
    return NoTranscriptFound("v", ["en"], None)


class _FakeApi:
    manual = False
    auto = False
    disabled = False

    def list(self, video_id: str) -> "_FakeApi":
        if self.disabled:
            raise TranscriptsDisabled(video_id)
        return self

    def find_manually_created_transcript(self, langs: _Langs) -> _Found:
        if not self.manual:
            raise _no_found()
        return _Found(False)

    def find_generated_transcript(self, langs: _Langs) -> _Found:
        if not self.auto:
            raise _no_found()
        return _Found(True)


def _patch(monkeypatch: pytest.MonkeyPatch, **flags: bool) -> None:
    api = type("Api", (_FakeApi,), flags)
    monkeypatch.setattr(transcripts, "YouTubeTranscriptApi", api)


@pytest.mark.anyio
async def test_manual_preferred(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, manual=True, auto=True)
    t = await fetch_transcript("v", ["en"], asyncio.Semaphore(1))
    assert t.source == "manual"
    assert t.language == "en"
    assert [ln.text for ln in t.lines] == ["hello", "world."]


@pytest.mark.anyio
async def test_auto_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, auto=True)
    assert (await fetch_transcript("v", ["en"], asyncio.Semaphore(1))).source == "auto"


@pytest.mark.anyio
async def test_none_when_no_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    t = await fetch_transcript("v", ["en"], asyncio.Semaphore(1))
    assert (t.source, t.lines, t.language) == ("none", [], None)


@pytest.mark.anyio
async def test_none_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, disabled=True)
    assert (await fetch_transcript("v", ["en"], asyncio.Semaphore(1))).source == "none"


def _raising_api(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    class Api:
        def list(self, video_id: str) -> None:
            raise exc

    monkeypatch.setattr(transcripts, "YouTubeTranscriptApi", Api)


@pytest.mark.parametrize(
    "exc", [requests.ConnectionError("down"), RuntimeError("parser broke"), KeyError("x")]
)
@pytest.mark.anyio
async def test_none_on_request_or_unexpected_error(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    _raising_api(monkeypatch, exc)
    assert (await fetch_transcript("v", ["en"], asyncio.Semaphore(1))).source == "none"


@pytest.mark.anyio
async def test_none_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    class SlowApi(_FakeApi):
        def list(self, video_id: str) -> "_FakeApi":
            time.sleep(0.3)
            return self

    monkeypatch.setattr(transcripts, "YouTubeTranscriptApi", SlowApi)
    monkeypatch.setattr(transcripts, "FETCH_TIMEOUT_S", 0.05)
    t = await fetch_transcript("v", ["en"], asyncio.Semaphore(1))
    assert t.source == "none"
