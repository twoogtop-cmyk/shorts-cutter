"""Состояние фоновых задач: прогресс, логи, отмена, повтор."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..db import execute, insert, query, query_one
from ..services import queue

router = APIRouter(prefix="/jobs", tags=["jobs"])


def job_dict(row) -> dict:
    data = dict(row)
    data["stage_title"] = queue.STAGE_TITLES.get(data.get("stage") or "", data.get("stage"))
    payload = data.pop("payload", None)
    data["payload"] = json.loads(payload) if payload else {}
    return data


@router.get("")
def list_jobs(video_id: int | None = None, limit: int = 50) -> list[dict]:
    if video_id is not None:
        rows = query(
            "SELECT * FROM jobs WHERE video_id=? ORDER BY id DESC LIMIT ?", (video_id, limit)
        )
    else:
        rows = query("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))
    return [job_dict(r) for r in rows]


@router.get("/active")
def active_jobs() -> list[dict]:
    rows = query("SELECT * FROM jobs WHERE status IN ('queued','running') ORDER BY id")
    return [job_dict(r) for r in rows]


@router.get("/{job_id}")
def get_job(job_id: int) -> dict:
    row = query_one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if row is None:
        raise HTTPException(404, "Задача не найдена")
    data = job_dict(row)
    data["logs"] = queue.job_logs(job_id)
    return data


@router.post("/{job_id}/cancel")
def cancel_job(job_id: int) -> dict:
    row = query_one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if row is None:
        raise HTTPException(404, "Задача не найдена")
    if row["status"] not in ("queued", "running"):
        raise HTTPException(409, "Задача уже завершена")
    queue.request_cancel(job_id)
    if row["status"] == "queued":
        # Ожидающую задачу можно закрыть сразу — worker её ещё не взял.
        queue.finish_job(job_id, "canceled")
        # Иначе видео осталось бы висеть в статусе «в очереди» навсегда.
        if row["video_id"]:
            execute(
                "UPDATE videos SET status='uploaded', updated_at=datetime('now') "
                "WHERE id=? AND status='queued'",
                (row["video_id"],),
            )
    return {"ok": True}


@router.post("/{job_id}/retry")
def retry_job(job_id: int) -> dict:
    row = query_one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if row is None:
        raise HTTPException(404, "Задача не найдена")
    if row["status"] in ("queued", "running"):
        raise HTTPException(409, "Задача ещё выполняется")
    new_id = insert(
        "INSERT INTO jobs(video_id, candidate_id, type, payload) VALUES (?, ?, ?, ?)",
        (row["video_id"], row["candidate_id"], row["type"], row["payload"]),
    )
    if row["video_id"]:
        execute("UPDATE videos SET error=NULL WHERE id=?", (row["video_id"],))
    return {"job_id": new_id}


@router.get("/{job_id}/stream")
async def stream_job(job_id: int) -> StreamingResponse:
    """Прогресс задачи через SSE — интерфейс не опрашивает сервер по таймеру."""

    async def events():
        last = None
        idle = 0
        while idle < 3600:
            row = query_one("SELECT * FROM jobs WHERE id=?", (job_id,))
            if row is None:
                yield f"data: {json.dumps({'error': 'not found'})}\n\n"
                return
            data = job_dict(row)
            snapshot = (data["status"], data["progress"], data["stage"])
            if snapshot != last:
                last = snapshot
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            if data["status"] in ("done", "failed", "canceled"):
                return
            idle += 1
            await asyncio.sleep(1)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
