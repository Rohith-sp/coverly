import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends

import jobs
from db import get_conn
from schemas import HealthOut

router = APIRouter()


@router.get("/health")
def health(conn: Annotated[sqlite3.Connection, Depends(get_conn)]) -> HealthOut:
    return HealthOut(ok=True, quota_used_today=jobs.quota_used_today(conn))
