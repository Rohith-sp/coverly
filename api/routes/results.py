import json
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import TypeAdapter

from db import get_conn
from schemas import Coverage, ResultItem, ResultOut, UncoveredTopic

router = APIRouter()
_ITEMS = TypeAdapter(list[ResultItem])
_UNCOVERED = TypeAdapter(list[UncoveredTopic])


@router.get("/plans/{plan_id}/result")
async def get_result(
    plan_id: str, conn: Annotated[sqlite3.Connection, Depends(get_conn)]
) -> ResultOut:
    r = conn.execute("SELECT * FROM results WHERE plan_id=?", (plan_id,)).fetchone()
    if r is None:
        raise HTTPException(404, "No result yet")
    total = conn.execute("SELECT COUNT(*) FROM topics WHERE plan_id=?", (plan_id,)).fetchone()[0]
    uncovered = _UNCOVERED.validate_python(json.loads(r["uncovered_json"]))
    return ResultOut(
        items=_ITEMS.validate_python(json.loads(r["items_json"])),
        uncovered=uncovered,
        total_minutes=r["total_minutes"],
        coverage=Coverage(covered=total - len(uncovered), total=total),
    )
