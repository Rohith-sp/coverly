import sqlite3
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException

from db import get_conn
from schemas import JobOut, JobStatus

router = APIRouter()


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, conn: Annotated[sqlite3.Connection, Depends(get_conn)]) -> JobOut:
    r = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if r is None:
        raise HTTPException(404, "Job not found")
    return JobOut(
        job_id=r["id"],
        plan_id=r["plan_id"],
        status=cast(JobStatus, r["status"]),
        progress=r["progress"],
        message=r["message"],
        quota_units=r["quota_units"],
        llm_tokens=r["llm_tokens"],
    )
