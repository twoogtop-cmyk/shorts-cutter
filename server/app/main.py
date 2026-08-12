"""FastAPI-приложение сервиса нарезки Shorts."""

from __future__ import annotations

import shutil

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import DATA_DIR, ensure_dirs, max_upload_bytes
from .db import get_settings, init_db, set_settings
from .routes import candidates as candidates_routes
from .routes import jobs as jobs_routes
from .routes import transcript as transcript_routes
from .routes import videos as videos_routes
from .services import genapi

app = FastAPI(title="Shorts Cutter", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

api = APIRouter(prefix="/api")


@app.on_event("startup")
def on_startup() -> None:
    ensure_dirs()
    init_db()


@api.get("/health")
def health() -> dict[str, object]:
    return {"ok": True}


@api.get("/system/status")
def system_status() -> dict[str, object]:
    usage = shutil.disk_usage(DATA_DIR)
    balance: float | None
    balance_error: str | None = None
    try:
        balance = genapi.get_balance()
    except Exception as exc:  # сервис внешний — падать из-за него нельзя
        balance = None
        balance_error = str(exc)[:200]
    return {
        "disk": {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
        },
        "max_source_bytes": max_upload_bytes(),
        "balance": balance,
        "balance_error": balance_error,
    }


@api.get("/settings")
def read_settings() -> dict[str, str]:
    return get_settings()


@api.put("/settings")
def update_settings(values: dict[str, str]) -> dict[str, str]:
    set_settings(values)
    return get_settings()


api.include_router(videos_routes.router)
api.include_router(jobs_routes.router)
api.include_router(transcript_routes.router)
api.include_router(candidates_routes.router)

app.include_router(api)
