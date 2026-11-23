"""FastAPI app: CORS, schema init, router registration."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import db
from config import get_settings
from routes import health

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    conn = db.connect()
    try:
        db.init_schema(conn)
    finally:
        conn.close()
    yield


app = FastAPI(title="Coverly API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().cors_origin],
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Content-Type"],
)
app.include_router(health.router)
