import sqlite3
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException

import jobs
from db import get_conn
from schemas import JobOut, JobStatus

router = APIRouter()


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, conn: Annotated[sqlite3.Connection, Depends(get_conn)]) -> JobOut:
    r = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if r is None:
        raise HTTPException(404, "Job not found")
    status, message = r["status"], r["message"]
    stale = conn.execute(
        "SELECT 1 FROM jobs WHERE id=? AND status NOT IN ('done','failed')"
        " AND started_at < datetime('now', ?)",
        (job_id, f"-{jobs.STALE_JOB_MINUTES} minutes"),
    ).fetchone()
    if stale is not None:
        status, message = "failed", jobs.STALE_MESSAGE
        conn.execute(
            "UPDATE jobs SET status=?, message=?, finished_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, message, job_id),
        )
        conn.commit()
    return JobOut(
        job_id=r["id"],
        plan_id=r["plan_id"],
        status=cast(JobStatus, status),
        progress=r["progress"],
        message=message,
        quota_units=r["quota_units"],
        llm_tokens=r["llm_tokens"],
    )
