import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends

from db import get_conn
from schemas import HealthOut

router = APIRouter()


@router.get("/health")
def health(conn: Annotated[sqlite3.Connection, Depends(get_conn)]) -> HealthOut:
    row = conn.execute(
        "SELECT COALESCE(SUM(quota_units), 0) FROM jobs WHERE date(started_at) = date('now')"
    ).fetchone()
    return HealthOut(ok=True, quota_used_today=int(row[0]))
