"""Транскрипция: просмотр, поиск по диалогам, пробное распознавание."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..db import has_fts, query, query_one
from ..services import queue

router = APIRouter(prefix="/videos/{video_id}/transcript", tags=["transcript"])


@router.get("")
def get_transcript(video_id: int, with_words: bool = False) -> dict:
    video = query_one("SELECT id, duration FROM videos WHERE id=?", (video_id,))
    if video is None:
        raise HTTPException(404, "Видео не найдено")

    segments = [
        dict(r)
        for r in query(
            "SELECT id, idx, start, end, text, speaker FROM segments "
            "WHERE video_id=? ORDER BY start",
            (video_id,),
        )
    ]

    if with_words and segments:
        words_by_segment: dict[int, list[dict]] = {}
        for row in query(
            "SELECT segment_id, start, end, text FROM words WHERE video_id=? ORDER BY start",
            (video_id,),
        ):
            words_by_segment.setdefault(row["segment_id"], []).append(dict(row))
        for segment in segments:
            segment["words"] = words_by_segment.get(segment["id"], [])

    speakers = sorted({s["speaker"] for s in segments if s["speaker"]})
    return {
        "video_id": video_id,
        "duration": video["duration"],
        "segments": segments,
        "speakers": speakers,
        "count": len(segments),
    }


@router.get("/search")
def search_transcript(video_id: int, q: str, limit: int = 100) -> dict:
    """Полнотекстовый поиск по репликам."""
    text = (q or "").strip()
    if len(text) < 2:
        return {"query": text, "results": []}

    if has_fts():
        # Экранируем кавычки и ищем как фразу — иначе спецсимволы FTS ломают запрос.
        phrase = '"' + text.replace('"', '""') + '"'
        rows = query(
            "SELECT s.id, s.start, s.end, s.text, s.speaker FROM segments_fts f "
            "JOIN segments s ON s.id = f.rowid "
            "WHERE f.segments_fts MATCH ? AND s.video_id = ? "
            "ORDER BY s.start LIMIT ?",
            (phrase, video_id, limit),
        )
    else:
        rows = query(
            "SELECT id, start, end, text, speaker FROM segments "
            "WHERE video_id=? AND text LIKE ? ORDER BY start LIMIT ?",
            (video_id, f"%{text}%", limit),
        )

    return {"query": text, "results": [dict(r) for r in rows]}


@router.post("/sample")
def transcribe_sample(video_id: int, payload: dict) -> dict:
    """Пробное распознавание фрагмента — проверить качество до полной серии."""
    video = query_one("SELECT * FROM videos WHERE id=?", (video_id,))
    if video is None:
        raise HTTPException(404, "Видео не найдено")

    start = float(payload.get("start", 0))
    minutes = float(payload.get("minutes", 3))
    if minutes <= 0 or minutes > 15:
        raise HTTPException(400, "Длительность фрагмента: от 0 до 15 минут")

    duration = float(video["duration"] or 0)
    if duration and start >= duration:
        raise HTTPException(400, "Начало фрагмента выходит за пределы серии")
    end = min(start + minutes * 60, duration) if duration else start + minutes * 60

    running = query_one(
        "SELECT id FROM jobs WHERE video_id=? AND status IN ('queued','running')", (video_id,)
    )
    if running:
        raise HTTPException(409, "Для этой серии уже выполняется задача")

    job_id = queue.enqueue(
        "transcribe_sample", video_id=video_id, payload={"start": start, "end": end}
    )
    return {"job_id": job_id, "start": start, "end": end, "cost_estimate": round((end - start) / 60 * 2)}
