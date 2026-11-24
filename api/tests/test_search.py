import json
import logging
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from pipeline import search
from pipeline.search import QuotaExceededError
from schemas import Topic

FIX = Path(__file__).parent / "fixtures"


def _topic(i: int = 0) -> Topic:
    return Topic(topic_id=f"t{i}", module="Auth", name=f"JWT {i}", position=i, required_depth=2)


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search, "BACKOFF_BASE_S", 0.0)


def test_build_query() -> None:
    t = _topic()
    assert search.build_query(t, "en") == "JWT 0 Auth tutorial"
    assert search.build_query(t, "both") == "JWT 0 Auth tutorial"
    assert search.build_query(t, "hi") == "JWT 0 Auth tutorial hindi"


@pytest.mark.parametrize(
    ("iso", "secs"),
    [
        ("PT1H2M3S", 3723),
        ("P1DT2H", 93600),
        ("PT45S", 45),
        ("PT10M", 600),
        ("PT2H", 7200),
        ("P0D", 0),
        ("garbage", 0),
    ],
)
def test_parse_duration(iso: str, secs: int) -> None:
    assert search.parse_duration(iso) == secs


@pytest.mark.anyio
async def test_one_search_per_topic_and_quota() -> None:
    seen: list[httpx.Request] = []
    page = json.loads((FIX / "youtube_search_page.json").read_text())

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=page)

    async with _client(handler) as c:
        out = await search.search_topics(c, "K", [_topic(0), _topic(1), _topic(2)], "hi", 10)
    assert len(seen) == 3
    assert out.quota_units == 300
    assert not out.quota_exceeded
    assert out.video_ids["t1"] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]  # invalid id dropped
    p = seen[0].url.params
    assert "key" not in p
    assert seen[0].headers["X-Goog-Api-Key"] == "K"
    assert p["type"] == "video"
    assert p["maxResults"] == "10"
    assert p["relevanceLanguage"] == "hi"
    assert "videoCaption" not in p


@pytest.mark.anyio
async def test_both_language_omits_relevance_language() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"items": []})

    async with _client(handler) as c:
        await search.search_topic(c, "K", _topic(), "both", 5)
    assert "relevanceLanguage" not in seen[0].url.params


@pytest.mark.anyio
async def test_videos_batched_by_50() -> None:
    calls: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        ids = req.url.params["id"].split(",")
        calls.append(len(ids))
        return httpx.Response(200, json={"items": []})

    async with _client(handler) as c:
        out = await search.fetch_videos(
            c, "K", [f"v{i:010d}" for i in range(120)] + ["v0000000000"]
        )
    assert sorted(calls) == [20, 50, 50]
    assert out.quota_units == 3


@pytest.mark.anyio
async def test_video_mapping_and_missing_stats() -> None:
    page = json.loads((FIX / "youtube_videos_page.json").read_text())

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page)

    async with _client(handler) as c:
        out = await search.fetch_videos(c, "K", ["vidFull0001", "vidNoStat01"])
    full, bare = out.videos
    assert (full.duration_s, full.views, full.likes) == (3723, 1500, 90)
    assert full.channel_title == "Chan One"
    assert (bare.duration_s, bare.views, bare.likes) == (45, 0, 0)


VID = "abcdefghijk"


@pytest.mark.anyio
async def test_videos_retry_on_429_then_success_counts_each_attempt() -> None:
    n = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal n
        n += 1
        if n < 3:
            return httpx.Response(429, json={})
        return httpx.Response(200, json={"items": []})

    async with _client(handler) as c:
        out = await search.fetch_videos(c, "K", [VID])
    assert n == 3 and out.quota_units == 3


@pytest.mark.anyio
async def test_videos_give_up_after_three_tries() -> None:
    n = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal n
        n += 1
        return httpx.Response(503)

    async with _client(handler) as c:
        out = await search.fetch_videos(c, "K", [VID])
    assert n == 3 and out.videos == [] and out.quota_units == 3


@pytest.mark.anyio
async def test_videos_timeout_retried() -> None:
    n = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal n
        n += 1
        if n == 1:
            raise httpx.ReadTimeout("slow", request=req)
        return httpx.Response(200, json={"items": []})

    async with _client(handler) as c:
        await search.fetch_videos(c, "K", [VID])
    assert n == 2


@pytest.mark.parametrize("status", [429, 503])
@pytest.mark.anyio
async def test_search_single_attempt_no_retry(status: int) -> None:
    n = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal n
        n += 1
        return httpx.Response(status, json={})

    async with _client(handler) as c:
        with pytest.raises(httpx.HTTPStatusError):
            await search.search_topic(c, "K", _topic(), "en", 10)
    assert n == 1


@pytest.mark.anyio
async def test_search_timeout_not_retried() -> None:
    n = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal n
        n += 1
        raise httpx.ReadTimeout("slow", request=req)

    async with _client(handler) as c:
        with pytest.raises(httpx.ReadTimeout):
            await search.search_topic(c, "K", _topic(), "en", 10)
    assert n == 1


@pytest.mark.anyio
async def test_search_topics_http_error_is_partial_and_key_not_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    def handler(req: httpx.Request) -> httpx.Response:
        if "JWT 1" in req.url.params["q"]:
            return httpx.Response(500, json={})
        return httpx.Response(200, json={"items": [{"id": {"videoId": VID}}]})

    async with _client(handler) as c:
        out = await search.search_topics(c, "SECRETKEY", [_topic(0), _topic(1)], "en", 10)
    assert out.video_ids == {"t0": [VID]}
    assert out.quota_units == 200 and not out.quota_exceeded
    assert "SECRETKEY" not in caplog.text
    assert "HTTPStatusError status=500" in caplog.text


@pytest.mark.anyio
async def test_fetch_videos_batch_error_isolated() -> None:
    page = json.loads((FIX / "youtube_videos_page.json").read_text())

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.params["id"].startswith("x"):
            return httpx.Response(400, json={})
        return httpx.Response(200, json=page)

    ids = [f"v{i:010d}" for i in range(50)] + ["x0000000000"]
    async with _client(handler) as c:
        out = await search.fetch_videos(c, "K", ids)
    assert [v.id for v in out.videos] == ["vidFull0001", "vidNoStat01"]
    assert out.quota_units == 2


@pytest.mark.anyio
async def test_invalid_video_ids_dropped() -> None:
    seen: list[str] = []
    item = {"id": "bad", "snippet": {}, "contentDetails": {}, "statistics": {}}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.params["id"])
        return httpx.Response(200, json={"items": [item]})

    async with _client(handler) as c:
        out = await search.fetch_videos(c, "K", ["../evil", VID])
    assert seen == [VID] and out.videos == []


def _quota(_: httpx.Request) -> httpx.Response:
    body = {"error": {"code": 403, "errors": [{"reason": "quotaExceeded"}]}}
    return httpx.Response(403, json=body)


@pytest.mark.anyio
async def test_quota_exceeded_raises_single_call() -> None:
    async with _client(_quota) as c:
        with pytest.raises(QuotaExceededError):
            await search.search_topic(c, "K", _topic(), "en", 10)


@pytest.mark.anyio
async def test_quota_exceeded_partial_outcome() -> None:
    async with _client(_quota) as c:
        s = await search.search_topics(c, "K", [_topic(0)], "en", 10)
        v = await search.fetch_videos(c, "K", [VID])
    assert s.quota_exceeded and s.quota_units == 100 and s.video_ids == {}
    assert v.quota_exceeded and v.videos == [] and v.quota_units == 1


@pytest.mark.anyio
async def test_other_403_is_not_quota() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"errors": [{"reason": "forbidden"}]}})

    async with _client(handler) as c:
        with pytest.raises(httpx.HTTPStatusError):
            await search.search_topic(c, "K", _topic(), "en", 10)
