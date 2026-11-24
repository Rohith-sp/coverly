"""FastAPI app: CORS, schema init, router registration."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import db
from config import get_settings
from routes import health, jobs, plans, results

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
MAX_BODY_BYTES = 100_000


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    conn = db.connect()
    try:
        db.init_schema(conn)
    finally:
        conn.close()
    yield


app = FastAPI(title="Coverly API", lifespan=lifespan)


@app.middleware("http")
async def limit_body(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
        return JSONResponse({"detail": "Request body too large"}, status_code=413)
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().cors_origin],
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Content-Type"],
)
app.include_router(health.router)
app.include_router(plans.router)
app.include_router(jobs.router)
app.include_router(results.router)
