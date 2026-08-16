"""Boensity Ingest service.

Turns scattered DEXA PDFs from any manufacturer into one internal T-score
schema. See engine/schema_mapper.py for why that normalisation is the part
that matters.
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

from app.engine.vision_client import get_vision_client
from app.routers import upload

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("boensity.ingest")

SERVICE_NAME = "ingest"
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
    title="Boensity Ingest",
    version=SERVICE_VERSION,
    description="DEXA PDF extraction and cross-manufacturer normalisation.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router)


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
    # An extraction traceback can contain fragments of the document text, which
    # is patient data. It goes to the log, never to the response.
    logger.exception("unhandled error in ingest service")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_envelope(error={"code": "internal_error", "message": "Internal server error"}),
    )


@app.get("/health")
async def health() -> dict:
    """Reports OCR availability rather than hiding it.

    The service is genuinely useful without Vision, since text-native PDFs are
    a large share of real traffic. Operators still need to know that scanned
    documents will be rejected.
    """
    return _envelope(
        data={
            "status": "ok",
            "service": SERVICE_NAME,
            "ocr_available": get_vision_client().available,
        }
    )


@app.get("/")
async def root() -> dict:
    return _envelope(
        data={
            "service": "Boensity Ingest",
            "supported_manufacturers": ["hologic", "ge_lunar"],
            "endpoints": ["/upload", "/extract", "/jobs/{job_id}", "/confirm"],
        }
    )
