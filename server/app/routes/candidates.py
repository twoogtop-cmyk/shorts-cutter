"""Найденные моменты: список, модерация, ручное создание."""

from __future__ import annotations

import re
import secrets
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..config import TMP_DIR
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


@router.post("/{candidate_id}/render")
def render_final(candidate_id: int) -> dict:
    """Ставит в очередь финальный рендер в полном качестве."""
    row = query_one("SELECT * FROM candidates WHERE id=?", (candidate_id,))
    if row is None:
        raise HTTPException(404, "Момент не найден")
    if query_one(
        "SELECT id FROM jobs WHERE candidate_id=? AND type='render_final' "
        "AND status IN ('queued','running')",
        (candidate_id,),
    ):
        raise HTTPException(409, "Рендер этого момента уже идёт")

    job_id = queue.enqueue("render_final", video_id=row["video_id"], candidate_id=candidate_id)
    return {"job_id": job_id}


@router.post("/render-bulk")
def render_bulk(payload: dict) -> dict:
    """Финальный рендер нескольких моментов — очередь выполнит их по очереди."""
    ids = [int(i) for i in payload.get("ids", [])]
    if not ids:
        raise HTTPException(400, "Не выбрано ни одного момента")

    queued = []
    for candidate_id in ids:
        row = query_one("SELECT * FROM candidates WHERE id=?", (candidate_id,))
        if row is None or row["render_path"]:
            continue
        if query_one(
            "SELECT id FROM jobs WHERE candidate_id=? AND type='render_final' "
            "AND status IN ('queued','running')",
            (candidate_id,),
        ):
            continue
        queued.append(queue.enqueue("render_final", video_id=row["video_id"], candidate_id=candidate_id))
    return {"queued": len(queued), "job_ids": queued}


@router.get("/{candidate_id}/download")
def download(candidate_id: int) -> FileResponse:
    """Отдаёт готовый файл шортса."""
    row = query_one("SELECT * FROM candidates WHERE id=?", (candidate_id,))
    if row is None:
        raise HTTPException(404, "Момент не найден")

    path = Path(row["render_path"] or "")
    if not path.exists():
        raise HTTPException(400, "Финальный файл ещё не готов — сначала запустите рендер")

    execute("UPDATE candidates SET status='downloaded' WHERE id=? AND status='ready'", (candidate_id,))
    return FileResponse(path, media_type="video/mp4", filename=_download_name(row))


def _download_name(row) -> str:
    """Имя файла для скачивания: номер и название момента латиницей."""
    title = (row["title"] or "short").strip()
    translit = str.maketrans(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        "abvgdeejziyklmnoprstufhccss_y_eua",
    )
    slug = title.lower().translate(translit)
    slug = re.sub(r"[^a-z0-9]+", "_", slug).strip("_")[:50] or "short"
    return f"short_{row['id']:03d}_{slug}.mp4"


@router.post("/download-zip")
def download_zip(payload: dict) -> FileResponse:
    """Собирает архив из выбранных готовых шортсов."""
    ids = [int(i) for i in payload.get("ids", [])]
    if not ids:
        raise HTTPException(400, "Не выбрано ни одного момента")

    placeholders = ",".join("?" * len(ids))
    rows = query(
        f"SELECT * FROM candidates WHERE id IN ({placeholders}) AND render_path IS NOT NULL",
        ids,
    )
    ready = [r for r in rows if Path(r["render_path"]).exists()]
    if not ready:
        raise HTTPException(400, "Среди выбранных нет готовых файлов")

    archive = TMP_DIR / f"shorts_{len(ready)}_{secrets.token_hex(4)}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as zf:
        for row in ready:
            zf.write(row["render_path"], arcname=_download_name(row))
            execute("UPDATE candidates SET status='downloaded' WHERE id=? AND status='ready'", (row["id"],))

    return FileResponse(archive, media_type="application/zip", filename="shorts.zip")


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
