"""Очередь задач поверх SQLite.

Redis + Celery на этом сервере не помещаются (1 ГБ RAM), а нагрузка здесь
последовательная: одна серия за раз. Поэтому очередь — таблица jobs,
из которой единственный worker-процесс забирает задачи атомарным UPDATE.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ..db import execute, insert, query, query_one

# Человекочитаемые названия стадий для интерфейса.
STAGE_TITLES = {
    "uploaded": "Видео загружено",
    "probing": "Читаем параметры видео",
    "audio_extraction": "Извлекаем аудио",
    "transcribing": "Распознаём речь",
    "scene_detection": "Определяем сцены",
    "ai_analysis": "Анализируем интересные моменты",
    "clips_generation": "Формируем фрагменты",
    "rendering": "Создаём Shorts",
    "completed": "Готово",
    "failed": "Ошибка",
}


def enqueue(
    job_type: str,
    video_id: int | None = None,
    candidate_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    return insert(
        "INSERT INTO jobs(video_id, candidate_id, type, payload) VALUES (?, ?, ?, ?)",
        (video_id, candidate_id, job_type, json.dumps(payload or {}, ensure_ascii=False)),
    )


def claim_next_job() -> dict[str, Any] | None:
    """Атомарно забирает одну задачу из очереди."""
    row = query_one(
        "SELECT id FROM jobs WHERE status='queued' AND cancel_requested=0 ORDER BY id LIMIT 1"
    )
    if row is None:
        return None
    cur = execute(
        "UPDATE jobs SET status='running', started_at=datetime('now'), pid=? "
        "WHERE id=? AND status='queued'",
        (os.getpid(), row["id"]),
    )
    if cur.rowcount == 0:
        return None
    job = query_one("SELECT * FROM jobs WHERE id=?", (row["id"],))
    return dict(job) if job else None


def set_progress(job_id: int, progress: int, stage: str | None = None) -> None:
    progress = max(0, min(100, int(progress)))
    if stage:
        execute("UPDATE jobs SET progress=?, stage=? WHERE id=?", (progress, stage, job_id))
    else:
        execute("UPDATE jobs SET progress=? WHERE id=?", (progress, job_id))


def finish_job(job_id: int, status: str, error: str | None = None) -> None:
    execute(
        "UPDATE jobs SET status=?, error=?, finished_at=datetime('now'), "
        "progress=CASE WHEN ?='done' THEN 100 ELSE progress END WHERE id=?",
        (status, error, status, job_id),
    )


def is_cancelled(job_id: int) -> bool:
    row = query_one("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,))
    return bool(row and row["cancel_requested"])


def request_cancel(job_id: int) -> None:
    execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))


def log(job_id: int, message: str, level: str = "info") -> None:
    insert(
        "INSERT INTO job_logs(job_id, level, message) VALUES (?, ?, ?)",
        (job_id, level, message[:4000]),
    )


def job_logs(job_id: int, limit: int = 200) -> list[dict[str, Any]]:
    rows = query(
        "SELECT ts, level, message FROM job_logs WHERE job_id=? ORDER BY id DESC LIMIT ?",
        (job_id, limit),
    )
    return [dict(r) for r in reversed(rows)]
