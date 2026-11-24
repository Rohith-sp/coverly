import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import llm
from pipeline import parse, search, tag, transcripts
from schemas import (
    ModuleOut,
    Segment,
    Tag,
    TopicOut,
    Transcript,
    TranscriptLine,
    Video,
)

SEARCH_CALLS: list[list[str]] = []


def _video(vid: str) -> Video:
    return Video(
        id=vid,
        title=f"Title {vid}",
        channel_id="c",
        channel_title="Chan",
        duration_s=600,
        views=1000,
        likes=50,
        published_at=None,
        description="",
    )


@pytest.fixture(autouse=True)
def mocks(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    monkeypatch.setenv("GROQ_API_KEY_1", "k")
    monkeypatch.setenv("YOUTUBE_API_KEY", "yt")
    from config import get_settings

    get_settings.cache_clear()
    SEARCH_CALLS.clear()

    async def fake_parse(_c: object, text: str) -> tuple[str, list[ModuleOut], int]:
        n = int(text) if text.isdigit() else 2
        if n > 40:
            raise parse.TooManyTopicsError(n, 40)
        topics = [TopicOut(topic_id=f"t{i}", name=f"Topic {i}") for i in range(n)]
        return "My plan", [ModuleOut(name="Authentication", topics=topics)], 5

    async def fake_search(
        _c: object, _k: str, topics: list[object], _l: str, _m: int
    ) -> search.SearchOutcome:
        SEARCH_CALLS.append([getattr(t, "topic_id") for t in topics])  # noqa: B009
        ids = {getattr(t, "topic_id"): ["v1"] for t in topics}  # noqa: B009
        return search.SearchOutcome(ids, 100 * len(topics), False)

    async def fake_videos(_c: object, _k: str, ids: list[str]) -> search.VideosOutcome:
        return search.VideosOutcome([_video(i) for i in ids], 1, False)

    async def fake_tr(vid: str, _l: list[str], _s: object = None) -> Transcript:
        lines = [TranscriptLine(start_s=0, duration_s=5, text="authentication basics.")]
        return Transcript(video_id=vid, language="en", source="manual", lines=lines)

    async def fake_tag(
        _c: object, topics: list[object], segs: list[Segment], _s: object = None
    ) -> tag.TagOutcome:
        tags = [
            Tag(segment_id=segs[0].id, topic_id=getattr(t, "topic_id"), depth=3, confidence=0.9)  # noqa: B009
            for t in topics
        ]
        return tag.TagOutcome(tags, 42, 0)

    monkeypatch.setattr(parse, "parse_plan", fake_parse)
    monkeypatch.setattr(search, "search_topics", fake_search)
    monkeypatch.setattr(search, "fetch_videos", fake_videos)
    monkeypatch.setattr(transcripts, "fetch_transcript", fake_tr)
    monkeypatch.setattr(tag, "tag_segments", fake_tag)


def _plan(client: TestClient, text: str = "2", **kw: object) -> str:
    body = {"text": text, "level": "interview", "language": "en", **kw}
    r = client.post("/plans", json=body)
    assert r.status_code == 200, r.text
    return str(r.json()["plan_id"])


def _run(client: TestClient, plan_id: str) -> dict[str, object]:
    r = client.post(f"/plans/{plan_id}/run")
    assert r.status_code == 200, r.text
    job: dict[str, object] = client.get(f"/jobs/{r.json()['job_id']}").json()
    return job


def test_create_and_edit_plan(client: TestClient, conn: sqlite3.Connection) -> None:
    pid = _plan(client, budget_hours=2)
    plan = client.get(f"/plans/{pid}").json()
    assert plan["budget_hours"] == 2 and len(plan["modules"][0]["topics"]) == 2
    body = {
        "modules": [
            {"name": "A", "topics": [{"topic_id": "t1", "name": "Kept"}, {"name": "New"}]},
            {"name": "B", "topics": [{"name": "New"}]},
        ]
    }
    assert client.put(f"/plans/{pid}/topics", json=body).status_code == 200
    rows = conn.execute(
        "SELECT id, position, required_depth FROM topics WHERE plan_id=? ORDER BY position", (pid,)
    ).fetchall()
    assert [r["position"] for r in rows] == [0, 1, 2]
    assert rows[0]["id"] == "t1" and rows[0]["required_depth"] == 2
    assert len({r["id"] for r in rows}) == 3


def test_topic_cap_422(client: TestClient) -> None:
    assert (
        client.post("/plans", json={"text": "41", "level": "deep", "language": "en"}).status_code
        == 422
    )
    pid = _plan(client)
    too_many = {"modules": [{"name": "A", "topics": [{"name": f"n{i}"} for i in range(41)]}]}
    assert client.put(f"/plans/{pid}/topics", json=too_many).status_code == 422


def test_parse_llm_error_502(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_a: object) -> tuple[str, list[ModuleOut], int]:
        raise llm.LLMRequestError("down")

    monkeypatch.setattr(parse, "parse_plan", boom)
    r = client.post("/plans", json={"text": "x", "level": "deep", "language": "en"})
    assert r.status_code == 502


def test_run_end_to_end_and_cached_rerun(client: TestClient) -> None:
    pid = _plan(client, "3")
    job = _run(client, pid)
    assert job["status"] == "done" and job["progress"] == 100
    assert job["quota_units"] == 301 and job["llm_tokens"] == 42
    res = client.get(f"/plans/{pid}/result").json()
    assert res["coverage"] == {"covered": 3, "total": 3} and res["uncovered"] == []
    item = res["items"][0]
    assert item["url"] == "https://www.youtube.com/watch?v=v1&t=0s"
    assert item["thumbnail"] == "https://i.ytimg.com/vi/v1/mqdefault.jpg"
    assert [t["name"] for t in item["topics"]] == ["Topic 0", "Topic 1", "Topic 2"]
    assert item["reason"].startswith("Covers Topic 0, Topic 1 and Topic 2 at interview depth in ")
    rerun = _run(client, pid)
    assert rerun["status"] == "done" and rerun["quota_units"] == 0
    assert len(SEARCH_CALLS) == 1


def test_stage_error_gives_partial_result(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_a: object) -> tag.TagOutcome:
        raise llm.LLMRequestError("groq down")

    monkeypatch.setattr(tag, "tag_segments", boom)
    pid = _plan(client)
    job = _run(client, pid)
    assert job["status"] == "done" and "tagging failed" in str(job["message"])
    res = client.get(f"/plans/{pid}/result").json()
    assert res["items"] == [] and len(res["uncovered"]) == 2


def test_missing_groq_key_message(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from config import get_settings

    monkeypatch.setenv("GROQ_API_KEY_1", "")
    get_settings.cache_clear()
    job = _run(client, _plan(client))
    assert job["status"] == "done" and "GROQ_API_KEY" in str(job["message"])


def test_bug_sets_failed(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def bug(*_a: object) -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr(tag, "select_segments", bug)
    job = _run(client, _plan(client))
    assert job["status"] == "failed"


def test_unknown_ids_404(client: TestClient) -> None:
    assert client.get("/plans/nope").status_code == 404
    assert client.post("/plans/nope/run").status_code == 404
    assert (
        client.put(
            "/plans/nope/topics", json={"modules": [{"name": "A", "topics": [{"name": "x"}]}]}
        ).status_code
        == 404
    )
    assert client.get("/jobs/nope").status_code == 404
    assert client.get("/plans/nope/result").status_code == 404


def test_no_result_yet_404_and_duplicate_run_409(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    pid = _plan(client)
    assert client.get(f"/plans/{pid}/result").status_code == 404
    conn.execute("INSERT INTO jobs (id, plan_id, status) VALUES ('j', ?, 'tagging')", (pid,))
    conn.commit()
    assert client.post(f"/plans/{pid}/run").status_code == 409


def test_failed_transcript_not_served_from_cache(conn: sqlite3.Connection) -> None:
    import jobs

    jobs._store_videos(conn, [_video("v1"), _video("v2")])
    jobs._store_transcript(conn, Transcript(video_id="v1", language=None, source="none", lines=[]))
    line = TranscriptLine(start_s=0, duration_s=1, text="hi")
    jobs._store_transcript(
        conn, Transcript(video_id="v2", language="en", source="auto", lines=[line])
    )
    assert set(jobs._load_transcripts(conn, ["v1", "v2"])) == {"v2"}


def test_sync_route_uses_shared_conn(client: TestClient) -> None:
    assert client.get("/health").status_code == 200


def _job(conn: sqlite3.Connection, pid: str, status: str, age: str = "0 minutes") -> None:
    conn.execute(
        "INSERT INTO jobs (id, plan_id, status, started_at) VALUES (?, ?, ?, datetime('now', ?))",
        (f"j-{pid}-{status}-{age}", pid, status, f"-{age}"),
    )
    conn.commit()


def test_edit_topics_409_while_job_active(client: TestClient, conn: sqlite3.Connection) -> None:
    pid = _plan(client)
    _job(conn, pid, "tagging")
    body = {"modules": [{"name": "A", "topics": [{"name": "x"}]}]}
    assert client.put(f"/plans/{pid}/topics", json=body).status_code == 409


def test_stale_job_reported_failed(client: TestClient, conn: sqlite3.Connection) -> None:
    pid = _plan(client)
    _job(conn, pid, "tagging", "45 minutes")
    r = client.get(f"/jobs/j-{pid}-tagging-45 minutes").json()
    assert r["status"] == "failed" and r["message"] == "Job interrupted (server restarted?)"
    row = conn.execute("SELECT status FROM jobs").fetchone()
    assert row["status"] == "failed"


def test_other_plans_tags_survive_run(client: TestClient, conn: sqlite3.Connection) -> None:
    a, b = _plan(client), _plan(client)
    assert _run(client, a)["status"] == "done"
    before = conn.execute("SELECT COUNT(*) FROM segment_tags WHERE plan_id=?", (a,)).fetchone()[0]
    assert before > 0
    assert _run(client, b)["status"] == "done"
    after = conn.execute("SELECT COUNT(*) FROM segment_tags WHERE plan_id=?", (a,)).fetchone()[0]
    assert after == before


def test_global_run_guard_and_daily_quota(
    client: TestClient, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = _plan(client), _plan(client)
    _job(conn, a, "searching")
    r = client.post(f"/plans/{b}/run")
    assert r.status_code == 429 and "Another plan" in r.text
    conn.execute("DELETE FROM jobs")
    conn.execute(
        "INSERT INTO jobs (id, plan_id, status, quota_units) VALUES ('old', ?, 'done', 9900)", (a,)
    )
    conn.commit()
    assert client.post(f"/plans/{b}/run").status_code == 429  # 9900 + 2*100 > 10000
    monkeypatch.setenv("DAILY_QUOTA_LIMIT", "20000")
    from config import get_settings

    get_settings.cache_clear()
    assert client.post(f"/plans/{b}/run").status_code == 200


def test_tiny_budget_rounds_up(client: TestClient) -> None:
    pid = _plan(client, budget_hours=0.001)
    assert client.get(f"/plans/{pid}").json()["budget_hours"] == 1 / 60


def test_parse_502_is_generic(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_a: object) -> tuple[str, list[ModuleOut], int]:
        raise llm.LLMRequestError("secret-body http://x")

    monkeypatch.setattr(parse, "parse_plan", boom)
    r = client.post("/plans", json={"text": "x", "level": "deep", "language": "en"})
    assert r.status_code == 502 and r.json()["detail"] == "Plan parsing failed, try again"


def test_edit_validation_and_body_limit(client: TestClient) -> None:
    pid = _plan(client)
    for mod in (
        {"name": "A\nB", "topics": [{"name": "x"}]},
        {"name": "A", "topics": [{"name": "x\x00"}]},
        {"name": "A", "topics": [{"name": "x", "topic_id": "Bad_ID"}]},
        {"name": "A", "topics": [{"name": "x" * 201}]},
    ):
        assert client.put(f"/plans/{pid}/topics", json={"modules": [mod]}).status_code == 422
    big = {"text": "x" * 120_000, "level": "deep", "language": "en"}
    assert client.post("/plans", json=big).status_code == 413
