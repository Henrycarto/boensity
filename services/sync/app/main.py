"""Boensity Sync service.

Medication adherence tracking and physician to pharmacy referral routing.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.engine import adherence_scorer, referral_router
from app.engine.pharmacy_client import get_pharmacy_client
from app.routers import adherence

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("boensity.sync")

SERVICE_NAME = "sync"
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


app = FastAPI(
    title="Boensity Sync",
    version=SERVICE_VERSION,
    description="Medication adherence and pharmacy referral routing.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(adherence.router)


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
            }
        ),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error in sync service")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_envelope(error={"code": "internal_error", "message": "Internal server error"}),
    )


@app.get("/health")
async def health() -> dict:
    """Reports which capabilities are actually wired up.

    Sync degrades in three independent ways: no live pharmacy feed, no private
    scoring package, no private routing package. An operator needs to see all
    three separately, because the service still does useful work with any
    combination of them missing.
    """
    return _envelope(
        data={
            "status": "ok",
            "service": SERVICE_NAME,
            "live_pharmacy_connection": get_pharmacy_client().live,
            "private_scoring_available": adherence_scorer.private_scoring_available(),
            "private_routing_available": referral_router.private_routing_available(),
        }
    )


@app.get("/")
async def root() -> dict:
    return _envelope(
        data={
            "service": "Boensity Sync",
            "endpoints": [
                "/adherence",
                "/adherence/{patient_id}",
                "/referrals",
                "/referrals/evaluate",
            ],
        }
    )
