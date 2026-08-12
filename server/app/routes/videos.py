"""Загрузка исходных видео и запуск обработки."""

from __future__ import annotations

import json
import re
import secrets
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile

from ..config import (
    ALLOWED_VIDEO_EXT,
    SOURCES_DIR,
    TMP_DIR,
    max_upload_bytes,
)
from ..db import execute, insert, query, query_one
from ..services import queue

router = APIRouter(prefix="/videos", tags=["videos"])

SAFE_NAME_RE = re.compile(r"[^\w.\- ]+", re.UNICODE)


def safe_filename(name: str) -> str:
    """Имя для файловой системы: без разделителей пути и спецсимволов."""
    cleaned = SAFE_NAME_RE.sub("_", Path(name).name).strip() or "video"
    return cleaned[:150]


def video_dict(row) -> dict:
    data = dict(row)
    tracks = data.pop("audio_tracks_json", None)
    data["audio_tracks"] = json.loads(tracks) if tracks else []
    data.pop("probe_json", None)
    # Исходник раздаётся nginx напрямую из /media — плееру в браузере
    # нужен URL, а не путь на диске.
    storage = data.get("storage_path")
    data["media_url"] = f"/media/sources/{Path(storage).name}" if storage else None
    data["segments_count"] = (
        query_one("SELECT COUNT(*) AS n FROM segments WHERE video_id=?", (data["id"],)) or {"n": 0}
    )["n"]
    return data


@router.get("")
def list_videos() -> list[dict]:
    rows = query("SELECT * FROM videos ORDER BY id DESC")
    return [video_dict(r) for r in rows]


@router.get("/{video_id}")
def get_video(video_id: int) -> dict:
    row = query_one("SELECT * FROM videos WHERE id=?", (video_id,))
    if row is None:
        raise HTTPException(404, "Видео не найдено")
    return video_dict(row)


@router.post("/upload/init")
def init_upload(payload: dict) -> dict:
    """Создаёт сессию загрузки. Файл заливается кусками и может докачиваться."""
    filename = str(payload.get("filename") or "").strip()
    total_size = int(payload.get("total_size") or 0)

    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_VIDEO_EXT:
        raise HTTPException(
            400, f"Неподдерживаемый формат {ext or '—'}. Разрешены: "
            + ", ".join(sorted(e.lstrip('.') for e in ALLOWED_VIDEO_EXT))
        )
    if total_size <= 0:
        raise HTTPException(400, "Не указан размер файла")

    limit = max_upload_bytes()
    if total_size > limit:
        raise HTTPException(
            413,
            f"Файл {total_size / 1024**3:.1f} ГБ не поместится: свободно "
            f"{limit / 1024**3:.1f} ГБ. Удалите предыдущую серию и шортсы.",
        )

    video_id = insert(
        "INSERT INTO videos(original_filename, status, size_bytes) VALUES (?, 'uploading', ?)",
        (safe_filename(filename), total_size),
    )
    session_id = secrets.token_urlsafe(16)
    temp_path = TMP_DIR / f"upload_{session_id}{ext}"
    temp_path.touch()
    insert(
        "INSERT INTO upload_sessions(id, video_id, total_size, temp_path) VALUES (?, ?, ?, ?)",
        (session_id, video_id, total_size, str(temp_path)),
    )
    return {"upload_id": session_id, "video_id": video_id, "received": 0}


@router.get("/upload/{upload_id}/status")
def upload_status(upload_id: str) -> dict:
    row = query_one("SELECT * FROM upload_sessions WHERE id=?", (upload_id,))
    if row is None:
        raise HTTPException(404, "Сессия загрузки не найдена")
    received = Path(row["temp_path"]).stat().st_size if Path(row["temp_path"]).exists() else 0
    return {
        "upload_id": upload_id,
        "video_id": row["video_id"],
        "received": received,
        "total_size": row["total_size"],
    }


@router.put("/upload/{upload_id}/chunk")
async def upload_chunk(upload_id: str, request: Request, offset: int = 0) -> dict:
    """Дописывает кусок файла начиная с указанного смещения."""
    row = query_one("SELECT * FROM upload_sessions WHERE id=?", (upload_id,))
    if row is None:
        raise HTTPException(404, "Сессия загрузки не найдена")

    temp_path = Path(row["temp_path"])
    current = temp_path.stat().st_size if temp_path.exists() else 0
    if offset > current:
        raise HTTPException(
            409, f"Разрыв в данных: сервер принял {current} байт, прислан кусок с {offset}"
        )

    # Повторная отправка уже принятого куска (после обрыва) не должна портить файл.
    written = 0
    with temp_path.open("r+b") as fh:
        fh.seek(offset)
        async for chunk in request.stream():
            fh.write(chunk)
            written += len(chunk)

    received = temp_path.stat().st_size
    execute("UPDATE upload_sessions SET received=? WHERE id=?", (received, upload_id))
    execute(
        "UPDATE videos SET updated_at=datetime('now') WHERE id=?", (row["video_id"],)
    )
    return {"received": received, "total_size": row["total_size"], "written": written}


@router.post("/upload/{upload_id}/complete")
def complete_upload(upload_id: str) -> dict:
    row = query_one("SELECT * FROM upload_sessions WHERE id=?", (upload_id,))
    if row is None:
        raise HTTPException(404, "Сессия загрузки не найдена")

    temp_path = Path(row["temp_path"])
    if not temp_path.exists():
        raise HTTPException(400, "Временный файл потерян, загрузите заново")

    received = temp_path.stat().st_size
    if received != row["total_size"]:
        raise HTTPException(
            400,
            f"Файл принят не полностью: {received} из {row['total_size']} байт. "
            "Продолжите загрузку.",
        )

    video = query_one("SELECT * FROM videos WHERE id=?", (row["video_id"],))
    if video is None:
        raise HTTPException(404, "Видео не найдено")

    target = SOURCES_DIR / f"{video['id']}_{video['original_filename']}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(temp_path), str(target))

    execute(
        "UPDATE videos SET storage_path=?, status='uploaded', size_bytes=?, "
        "updated_at=datetime('now') WHERE id=?",
        (str(target), received, video["id"]),
    )
    execute("DELETE FROM upload_sessions WHERE id=?", (upload_id,))

    job_id = queue.enqueue("probe", video_id=video["id"])
    return {"video_id": video["id"], "job_id": job_id, "path": str(target)}


@router.post("/{video_id}/analyze")
def start_analysis(video_id: int, payload: dict | None = None) -> dict:
    """Запускает полный разбор серии."""
    video = query_one("SELECT * FROM videos WHERE id=?", (video_id,))
    if video is None:
        raise HTTPException(404, "Видео не найдено")
    if not video["storage_path"] or not Path(video["storage_path"]).exists():
        raise HTTPException(400, "Файл видео отсутствует на диске")

    running = query_one(
        "SELECT id FROM jobs WHERE video_id=? AND type='analyze' AND status IN ('queued','running')",
        (video_id,),
    )
    if running:
        raise HTTPException(409, "Анализ этой серии уже запущен")

    track = (payload or {}).get("audio_track")
    if track is not None:
        execute("UPDATE videos SET audio_track_index=? WHERE id=?", (int(track), video_id))

    execute("UPDATE videos SET status='queued', error=NULL WHERE id=?", (video_id,))
    job_id = queue.enqueue("analyze", video_id=video_id)
    return {"job_id": job_id}


def _remove_files(paths) -> int:
    """Удаляет файлы, игнорируя уже отсутствующие. Возвращает освобождённые байты."""
    freed = 0
    for raw in paths:
        if not raw:
            continue
        path = Path(raw)
        try:
            if path.is_file():
                freed += path.stat().st_size
                path.unlink()
        except OSError:
            continue
    return freed


def _delete_video(video_id: int) -> int:
    """Удаляет исходник, аудио, превью и рендеры одной серии."""
    video = query_one("SELECT * FROM videos WHERE id=?", (video_id,))
    if video is None:
        raise HTTPException(404, "Видео не найдено")

    for job in query(
        "SELECT id FROM jobs WHERE video_id=? AND status IN ('queued','running')", (video_id,)
    ):
        queue.request_cancel(job["id"])

    paths = [video["storage_path"], video["audio_path"]]
    for cand in query(
        "SELECT preview_path, render_path FROM candidates WHERE video_id=?", (video_id,)
    ):
        paths.extend([cand["preview_path"], cand["render_path"]])

    freed = _remove_files(paths)
    # Остальные таблицы очистятся каскадом по внешним ключам.
    execute("DELETE FROM videos WHERE id=?", (video_id,))
    return freed


@router.delete("/{video_id}")
def delete_video(video_id: int) -> dict:
    freed = _delete_video(video_id)
    return {"ok": True, "freed_bytes": freed}


@router.delete("")
def delete_all_videos() -> dict:
    """Полная очистка: все серии, шортсы и промежуточные файлы."""
    ids = [r["id"] for r in query("SELECT id FROM videos")]
    freed = sum(_delete_video(vid) for vid in ids)

    for leftover in list(TMP_DIR.glob("*")):
        try:
            if leftover.is_file():
                freed += leftover.stat().st_size
                leftover.unlink()
            else:
                shutil.rmtree(leftover, ignore_errors=True)
        except OSError:
            continue

    execute("DELETE FROM upload_sessions")
    return {"ok": True, "deleted_videos": len(ids), "freed_bytes": freed}


@router.post("/{video_id}/audio-track")
def set_audio_track(video_id: int, payload: dict) -> dict:
    track = int(payload.get("audio_track", 0))
    execute("UPDATE videos SET audio_track_index=? WHERE id=?", (track, video_id))
    return {"audio_track": track}
