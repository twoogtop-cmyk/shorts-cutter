"""Найденные моменты: список, модерация, ручное создание."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..db import execute, insert, query, query_one
from ..services import analysis, queue

router = APIRouter(prefix="/candidates", tags=["candidates"])

ALLOWED_STATUSES = {"candidate", "approved", "rejected", "editing", "rendering", "ready", "downloaded"}


def candidate_dict(row) -> dict:
    data = dict(row)
    data["duration"] = round(data["end"] - data["start"], 2)
    for key, folder in (("preview_path", "previews"), ("render_path", "renders")):
        path = data.get(key)
        data[key.replace("_path", "_url")] = f"/media/{folder}/{Path(path).name}" if path else None
    return data


@router.get("")
def list_candidates(video_id: int | None = None, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM candidates"
    params: list = []
    where = []
    if video_id is not None:
        where.append("video_id=?")
        params.append(video_id)
    if status:
        where.append("status=?")
        params.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY start"
    return [candidate_dict(r) for r in query(sql, params)]


@router.get("/{candidate_id}")
def get_candidate(candidate_id: int) -> dict:
    row = query_one("SELECT * FROM candidates WHERE id=?", (candidate_id,))
    if row is None:
        raise HTTPException(404, "Момент не найден")
    return candidate_dict(row)


@router.patch("/{candidate_id}")
def update_candidate(candidate_id: int, payload: dict) -> dict:
    """Меняет статус, границы и настройки оформления одного момента."""
    row = query_one("SELECT * FROM candidates WHERE id=?", (candidate_id,))
    if row is None:
        raise HTTPException(404, "Момент не найден")

    fields: dict[str, object] = {}

    if "status" in payload:
        status = str(payload["status"])
        if status not in ALLOWED_STATUSES:
            raise HTTPException(400, f"Недопустимый статус: {status}")
        fields["status"] = status

    for key in ("title", "category", "crop_mode", "subtitle_style", "outro_text"):
        if key in payload:
            fields[key] = payload[key]

    for key in ("subtitles_enabled", "banner_id", "outro_enabled"):
        if key in payload:
            value = payload[key]
            fields[key] = None if value in (None, "") else int(value)

    if "start" in payload or "end" in payload:
        start = float(payload.get("start", row["start"]))
        end = float(payload.get("end", row["end"]))
        if end <= start:
            raise HTTPException(400, "Конец должен быть позже начала")
        fields["start"] = round(start, 3)
        fields["end"] = round(end, 3)

    if not fields:
        return candidate_dict(row)

    assignments = ", ".join(f"{k}=?" for k in fields)
    execute(
        f"UPDATE candidates SET {assignments}, updated_at=datetime('now') WHERE id=?",
        (*fields.values(), candidate_id),
    )
    return candidate_dict(query_one("SELECT * FROM candidates WHERE id=?", (candidate_id,)))


@router.post("/bulk")
def bulk_update(payload: dict) -> dict:
    """Массовое действие над выбранными моментами."""
    ids = [int(i) for i in payload.get("ids", [])]
    status = str(payload.get("status", ""))
    if not ids:
        raise HTTPException(400, "Не выбрано ни одного момента")
    if status not in ALLOWED_STATUSES:
        raise HTTPException(400, f"Недопустимый статус: {status}")
    placeholders = ",".join("?" * len(ids))
    execute(
        f"UPDATE candidates SET status=?, updated_at=datetime('now') WHERE id IN ({placeholders})",
        (status, *ids),
    )
    return {"updated": len(ids), "status": status}


@router.delete("/{candidate_id}")
def delete_candidate(candidate_id: int) -> dict:
    row = query_one("SELECT * FROM candidates WHERE id=?", (candidate_id,))
    if row is None:
        raise HTTPException(404, "Момент не найден")
    for key in ("preview_path", "render_path"):
        if row[key]:
            Path(row[key]).unlink(missing_ok=True)
    execute("DELETE FROM candidates WHERE id=?", (candidate_id,))
    return {"ok": True}


@router.post("/manual")
def create_manual(payload: dict) -> dict:
    """Создаёт момент вручную — например из выделенных реплик транскрипции."""
    video_id = int(payload.get("video_id", 0))
    video = query_one("SELECT id FROM videos WHERE id=?", (video_id,))
    if video is None:
        raise HTTPException(404, "Видео не найдено")

    start = float(payload.get("start", 0))
    end = float(payload.get("end", 0))
    if end <= start:
        raise HTTPException(400, "Конец должен быть позже начала")

    segments = query(
        "SELECT start, end, text, speaker FROM segments "
        "WHERE video_id=? AND start >= ? AND end <= ? ORDER BY start",
        (video_id, start, end),
    )
    transcript_text = "\n".join(
        f"[{r['start']:.1f}-{r['end']:.1f}] {analysis.speaker_label(r['speaker'])}: {r['text']}"
        for r in segments
    )

    candidate_id = insert(
        "INSERT INTO candidates(video_id, start, end, title, category, transcript_text, "
        "status, origin) VALUES (?, ?, ?, ?, 'Вручную', ?, 'candidate', 'manual')",
        (video_id, start, end, str(payload.get("title") or "Выбранный фрагмент")[:120], transcript_text),
    )
    return candidate_dict(query_one("SELECT * FROM candidates WHERE id=?", (candidate_id,)))


@router.post("/find/{video_id}")
def find_moments(video_id: int) -> dict:
    """Ищет моменты по уже готовой транскрипции, не оплачивая распознавание заново."""
    video = query_one("SELECT * FROM videos WHERE id=?", (video_id,))
    if video is None:
        raise HTTPException(404, "Видео не найдено")
    if not query_one("SELECT 1 FROM segments WHERE video_id=? LIMIT 1", (video_id,)):
        raise HTTPException(400, "Сначала нужно распознать речь")
    if query_one("SELECT id FROM jobs WHERE video_id=? AND status IN ('queued','running')", (video_id,)):
        raise HTTPException(409, "Для этой серии уже выполняется задача")

    job_id = queue.enqueue("find_moments", video_id=video_id)
    return {"job_id": job_id}
