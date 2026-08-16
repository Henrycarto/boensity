"""Boensity Timeline service.

Owns the longitudinal T-score store and every number derived from it: decline
velocity, forward projection, threshold crossings, and FRAX probability.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.db import close_pool, init_pool
from app.routers import frax, scans, trajectory

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("boensity.timeline")

SERVICE_NAME = "timeline"
SERVICE_VERSION = "0.1.0"


def _envelope(data=None, error=None, **meta) -> dict:
    return {
        "data": data,
        "error": error,
        "meta": {
            "service": SERVICE_NAME,
            "version": SERVICE_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            **meta,
        },
    }


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # The service answers trajectory and FRAX calls from the request body
    # alone, so a database outage degrades stored history rather than taking
    # down calculation. Startup logs the failure and carries on.
    try:
        await init_pool()
        logger.info("timescaledb pool ready")
    except Exception as exc:
        logger.warning("timescaledb unavailable at startup: %s", exc)
    yield
    await close_pool()


app = FastAPI(
    title="Boensity Timeline",
    version=SERVICE_VERSION,
    description="T-score trajectory modelling and ten year fracture probability.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(trajectory.router)
app.include_router(frax.router)
app.include_router(scans.router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(error={"code": f"http_{exc.status_code}", "message": str(exc.detail)}),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(part) for part in first.get("loc", []) if part != "body")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_envelope(
            error={
                "code": "validation_error",
                "message": first.get("msg", "invalid request"),
                "field": field or None,
            },
            error_count=len(exc.errors()),
        ),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    # Exception text can carry patient values. Log it, never return it.
    logger.exception("unhandled error in timeline service")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_envelope(error={"code": "internal_error", "message": "Internal server error"}),
    )


@app.get("/health")
async def health() -> dict:
    return _envelope(data={"status": "ok", "service": SERVICE_NAME})


@app.get("/")
async def root() -> dict:
    return _envelope(
        data={
            "service": "Boensity Timeline",
            "endpoints": [
                "/trajectory",
                "/trajectory/velocity",
                "/frax",
                "/frax/projected",
                "/scans",
            ],
        }
    )
