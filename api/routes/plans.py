import sqlite3
import uuid
from typing import Annotated, cast

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

import jobs
import llm
from config import get_settings
from db import get_conn
from pipeline import parse
from schemas import (
    REQUIRED_DEPTH,
    Language,
    Level,
    ModuleOut,
    Ok,
    PlanCreate,
    PlanOut,
    RunOut,
    TopicOut,
    TopicsUpdate,
)

router = APIRouter()
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
STALE_JOB_MINUTES = 30  # a job older than this with no end is treated as dead (process crashed)


def _plan_row(conn: sqlite3.Connection, plan_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Plan not found")
    return cast(sqlite3.Row, row)


def _modules(conn: sqlite3.Connection, plan_id: str) -> list[ModuleOut]:
    rows = conn.execute(
        "SELECT id, module, name FROM topics WHERE plan_id=? ORDER BY position", (plan_id,)
    )
    out: dict[str, ModuleOut] = {}
    for r in rows:
        mod = out.setdefault(r["module"], ModuleOut(name=r["module"], topics=[]))
        mod.topics.append(TopicOut(topic_id=r["id"], name=r["name"]))
    return list(out.values())


def _plan_out(conn: sqlite3.Connection, row: sqlite3.Row) -> PlanOut:
    minutes = row["budget_minutes"]
    return PlanOut(
        plan_id=row["id"],
        title=row["title"],
        level=cast(Level, row["level"]),
        language=cast(Language, row["language"]),
        budget_hours=None if minutes is None else minutes / 60,
        modules=_modules(conn, row["id"]),
    )


@router.post("/plans")
async def create_plan(body: PlanCreate, conn: Conn) -> PlanOut:
    try:
        async with httpx.AsyncClient(timeout=jobs.HTTP_TIMEOUT) as client:
            title, modules, _tokens = await parse.parse_plan(client, body.text)
    except parse.TooManyTopicsError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (llm.LLMError, httpx.HTTPError) as exc:
        raise HTTPException(502, f"Plan parsing failed: {exc}") from exc
    plan_id = uuid.uuid4().hex
    budget = None if body.budget_hours is None else round(body.budget_hours * 60)
    conn.execute(
        "INSERT INTO plans (id, title, raw_input, level, language, budget_minutes)"
        " VALUES (?,?,?,?,?,?)",
        (plan_id, title, body.text, body.level, body.language, budget),
    )
    depth = REQUIRED_DEPTH[body.level]
    pos = 0
    for m in modules:
        for t in m.topics:
            conn.execute(
                "INSERT INTO topics (id, plan_id, module, name, position, required_depth)"
                " VALUES (?,?,?,?,?,?)",
                (t.topic_id, plan_id, m.name, t.name, pos, depth),
            )
            pos += 1
    conn.commit()
    return _plan_out(conn, _plan_row(conn, plan_id))


@router.get("/plans/{plan_id}")
async def get_plan(plan_id: str, conn: Conn) -> PlanOut:
    return _plan_out(conn, _plan_row(conn, plan_id))


@router.put("/plans/{plan_id}/topics")
async def update_topics(plan_id: str, body: TopicsUpdate, conn: Conn) -> Ok:
    plan = _plan_row(conn, plan_id)
    total = sum(len(m.topics) for m in body.modules)
    limit = get_settings().max_topics
    if total > limit:
        raise HTTPException(422, f"{total} topics; the limit is {limit}. Trim or split the plan.")
    depth = REQUIRED_DEPTH[cast(Level, plan["level"])]
    conn.execute("DELETE FROM topics WHERE plan_id=?", (plan_id,))
    conn.execute("DELETE FROM results WHERE plan_id=?", (plan_id,))
    used: set[str] = set()
    pos = 0
    for m in body.modules:
        for t in m.topics:
            tid = t.topic_id or parse.topic_id(m.name, t.name)
            base, n = tid, 2
            while tid in used:
                tid, n = f"{base}-{n}", n + 1
            used.add(tid)
            conn.execute(
                "INSERT INTO topics (id, plan_id, module, name, position, required_depth)"
                " VALUES (?,?,?,?,?,?)",
                (tid, plan_id, m.name, t.name, pos, depth),
            )
            pos += 1
    conn.commit()
    return Ok()


@router.post("/plans/{plan_id}/run")
async def run_plan(plan_id: str, tasks: BackgroundTasks, conn: Conn) -> RunOut:
    _plan_row(conn, plan_id)
    busy = conn.execute(
        "SELECT 1 FROM jobs WHERE plan_id=? AND status NOT IN ('done','failed')"
        " AND started_at >= datetime('now', ?)",
        (plan_id, f"-{STALE_JOB_MINUTES} minutes"),
    ).fetchone()
    if busy is not None:
        raise HTTPException(409, "A run for this plan is already in progress")
    job_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO jobs (id, plan_id, status) VALUES (?, ?, 'queued')", (job_id, plan_id)
    )
    conn.commit()
    tasks.add_task(jobs.run_job, job_id, plan_id)
    return RunOut(job_id=job_id)
