"""FastAPI-приложение сервиса нарезки Shorts."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import DATA_DIR, TMP_DIR, ensure_dirs, max_upload_bytes
from .db import execute, get_settings, init_db, query, set_settings
from .routes import banners as banners_routes
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


@api.post("/system/cleanup")
def cleanup(payload: dict | None = None) -> dict:
    """Удаляет временные файлы и, по запросу, отклонённые шортсы.

    Полная очистка (серии и все шортсы) делается через DELETE /api/videos.
    """
    options = payload or {}
    freed = 0
    removed = {"temp": 0, "rejected": 0, "previews": 0}

    for leftover in TMP_DIR.glob("*"):
        try:
            if leftover.is_file():
                freed += leftover.stat().st_size
                leftover.unlink()
                removed["temp"] += 1
            else:
                shutil.rmtree(leftover, ignore_errors=True)
        except OSError:
            continue

    if options.get("rejected"):
        for row in query("SELECT * FROM candidates WHERE status='rejected'"):
            for key in ("preview_path", "render_path"):
                path = Path(row[key] or "")
                if path.is_file():
                    freed += path.stat().st_size
                    path.unlink(missing_ok=True)
            execute("DELETE FROM candidates WHERE id=?", (row["id"],))
            removed["rejected"] += 1

    if options.get("previews"):
        # Превью не нужны после того, как ролик отрендерен в полном качестве.
        for row in query(
            "SELECT * FROM candidates WHERE preview_path IS NOT NULL AND render_path IS NOT NULL"
        ):
            path = Path(row["preview_path"])
            if path.is_file():
                freed += path.stat().st_size
                path.unlink(missing_ok=True)
            execute("UPDATE candidates SET preview_path=NULL WHERE id=?", (row["id"],))
            removed["previews"] += 1

    return {"ok": True, "freed_bytes": freed, "removed": removed}


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
api.include_router(banners_routes.router)

app.include_router(api)
