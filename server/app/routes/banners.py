"""Библиотека баннеров: загрузка, список, удаление."""

from __future__ import annotations

import re
import secrets
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..config import ALLOWED_BANNER_EXT, BANNERS_DIR
from ..db import execute, insert, query, query_one
from ..services import ffmpeg

router = APIRouter(prefix="/banners", tags=["banners"])

MAX_BANNER_BYTES = 15 * 1024 * 1024


def banner_dict(row) -> dict:
    data = dict(row)
    data["url"] = f"/media/banners/{Path(data['path']).name}"
    return data


@router.get("")
def list_banners() -> list[dict]:
    return [banner_dict(r) for r in query("SELECT * FROM banners ORDER BY id DESC")]


@router.post("")
async def upload_banner(file: UploadFile = File(...), title: str | None = None) -> dict:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_BANNER_EXT:
        raise HTTPException(
            400,
            "Поддерживаются PNG, WEBP и JPG. PNG и WEBP сохраняют прозрачность.",
        )

    data = await file.read()
    if not data:
        raise HTTPException(400, "Файл пустой")
    if len(data) > MAX_BANNER_BYTES:
        raise HTTPException(413, "Файл больше 15 МБ")

    safe_stem = re.sub(r"[^\w\-]+", "_", Path(file.filename or "banner").stem)[:40] or "banner"
    name = f"{safe_stem}_{secrets.token_hex(4)}{ext}"
    path = BANNERS_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

    width = height = None
    try:
        info = ffmpeg.summarize_probe(ffmpeg.probe(path))
        width, height = info["width"], info["height"]
    except Exception:
        # Размеры не критичны: ffmpeg отмасштабирует баннер при рендере.
        pass

    banner_id = insert(
        "INSERT INTO banners(title, filename, path, width, height) VALUES (?, ?, ?, ?, ?)",
        (title or Path(file.filename or name).stem, name, str(path), width, height),
    )
    return banner_dict(query_one("SELECT * FROM banners WHERE id=?", (banner_id,)))


@router.delete("/{banner_id}")
def delete_banner(banner_id: int) -> dict:
    row = query_one("SELECT * FROM banners WHERE id=?", (banner_id,))
    if row is None:
        raise HTTPException(404, "Баннер не найден")

    Path(row["path"]).unlink(missing_ok=True)
    execute("DELETE FROM banners WHERE id=?", (banner_id,))
    # Ссылки на удалённый баннер очищаются, иначе рендер будет искать пропавший файл.
    execute("UPDATE candidates SET banner_id=NULL WHERE banner_id=?", (banner_id,))
    current = query_one("SELECT value FROM settings WHERE key='banner_id'")
    if current and str(current["value"]) == str(banner_id):
        execute("UPDATE settings SET value='' WHERE key='banner_id'")
    return {"ok": True}
