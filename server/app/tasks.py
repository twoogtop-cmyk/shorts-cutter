"""Обработчики фоновых задач."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import AUDIO_DIR, STT_CHUNK_SECONDS, TMP_DIR
from .db import execute, get_settings, insert, query_one
from .services import ffmpeg, queue, stt
from .registry import register


def _video(video_id: int) -> dict[str, Any]:
    row = query_one("SELECT * FROM videos WHERE id=?", (video_id,))
    if row is None:
        raise RuntimeError(f"Видео {video_id} не найдено")
    return dict(row)


def _set_status(video_id: int, status: str, error: str | None = None) -> None:
    execute(
        "UPDATE videos SET status=?, error=?, updated_at=datetime('now') WHERE id=?",
        (status, error, video_id),
    )


def run_probe(video_id: int, job_id: int) -> dict[str, Any]:
    """Читает параметры файла и сохраняет их."""
    video = _video(video_id)
    path = Path(video["storage_path"])
    if not path.exists():
        raise RuntimeError("Файл видео не найден на диске")

    queue.set_progress(job_id, 10, "probing")
    info = ffmpeg.summarize_probe(ffmpeg.probe(path))

    if not info["audio_tracks"]:
        raise RuntimeError("В файле нет аудиодорожки — распознавать нечего")

    execute(
        "UPDATE videos SET duration=?, width=?, height=?, fps=?, video_codec=?, "
        "size_bytes=?, audio_tracks_json=?, updated_at=datetime('now') WHERE id=?",
        (
            info["duration"], info["width"], info["height"], info["fps"],
            info["video_codec"], info["size_bytes"] or video["size_bytes"],
            json.dumps(info["audio_tracks"], ensure_ascii=False), video_id,
        ),
    )
    queue.log(
        job_id,
        f"{info['width']}×{info['height']}, {info['fps']} fps, "
        f"{(info['duration'] or 0) / 60:.1f} мин, дорожек звука: {len(info['audio_tracks'])}",
    )
    return info


@register("probe")
def handle_probe(job: dict[str, Any]) -> None:
    video_id = job["video_id"]
    run_probe(video_id, job["id"])
    _set_status(video_id, "uploaded")
    queue.set_progress(job["id"], 100, "uploaded")


def run_extract_audio(video_id: int, job_id: int) -> Path:
    """Извлекает аудиодорожку для распознавания речи."""
    video = _video(video_id)
    source = Path(video["storage_path"])
    target = AUDIO_DIR / f"{video_id}.mp3"

    _set_status(video_id, "audio_extraction")
    queue.set_progress(job_id, 0, "audio_extraction")
    queue.log(job_id, f"извлекаем аудиодорожку №{video['audio_track_index']}")

    ffmpeg.extract_audio(
        source,
        target,
        audio_track=int(video["audio_track_index"] or 0),
        duration=video["duration"],
        on_progress=lambda frac: queue.set_progress(job_id, int(frac * 100), "audio_extraction"),
        should_cancel=lambda: queue.is_cancelled(job_id),
    )

    size_mb = target.stat().st_size / 1024**2
    execute("UPDATE videos SET audio_path=? WHERE id=?", (str(target), video_id))
    queue.log(job_id, f"аудио готово: {size_mb:.1f} МБ")
    return target


def _save_segments(video_id: int, segments: list[dict[str, Any]], replace_range: tuple[float, float] | None) -> None:
    """Сохраняет реплики и слова.

    Для пробного прогона перезаписывается только его диапазон, чтобы
    результаты разных фрагментов не затирали друг друга.
    """
    if replace_range is None:
        execute("DELETE FROM segments WHERE video_id=?", (video_id,))
        execute("DELETE FROM words WHERE video_id=?", (video_id,))
    else:
        start, end = replace_range
        execute(
            "DELETE FROM segments WHERE video_id=? AND start >= ? AND start < ?",
            (video_id, start, end),
        )
        execute(
            "DELETE FROM words WHERE video_id=? AND start >= ? AND start < ?",
            (video_id, start, end),
        )

    base_idx = query_one(
        "SELECT COALESCE(MAX(idx), -1) AS last FROM segments WHERE video_id=?", (video_id,)
    )
    idx = int(base_idx["last"]) + 1 if base_idx else 0

    for segment in segments:
        segment_id = insert(
            "INSERT INTO segments(video_id, idx, start, end, text, speaker) VALUES (?, ?, ?, ?, ?, ?)",
            (video_id, idx, segment["start"], segment["end"], segment["text"], segment["speaker"]),
        )
        idx += 1
        for word in segment["words"]:
            insert(
                "INSERT INTO words(video_id, segment_id, start, end, text, speaker) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (video_id, segment_id, word["start"], word["end"], word["text"], word["speaker"]),
            )


def run_transcribe(
    video_id: int,
    job_id: int,
    sample: tuple[float, float] | None = None,
) -> int:
    """Распознаёт речь целиком или на фрагменте (sample = начало и конец в секундах)."""
    video = _video(video_id)
    audio_path = Path(video["audio_path"] or "")
    if not audio_path.exists():
        audio_path = run_extract_audio(video_id, job_id)

    settings = get_settings()
    language = settings.get("language", "ru")

    _set_status(video_id, "transcribing")
    queue.set_progress(job_id, 0, "transcribing")

    target = audio_path
    if sample is not None:
        start, end = sample
        target = TMP_DIR / f"sample_{video_id}_{int(start)}_{int(end)}.mp3"
        ffmpeg.run([
            "-ss", str(start), "-t", str(end - start),
            "-i", str(audio_path), "-c", "copy", str(target),
        ])
        queue.log(job_id, f"пробный фрагмент {start / 60:.1f}–{end / 60:.1f} мин")

    segments, cost = stt.transcribe_audio(
        target,
        language=language,
        chunk_seconds=STT_CHUNK_SECONDS,
        tmp_dir=TMP_DIR,
        on_progress=lambda frac: queue.set_progress(job_id, int(frac * 100), "transcribing"),
        on_log=lambda msg: queue.log(job_id, msg),
        should_cancel=lambda: queue.is_cancelled(job_id),
    )

    if sample is not None:
        offset = sample[0]
        for segment in segments:
            segment["start"] += offset
            segment["end"] += offset
            for word in segment["words"]:
                word["start"] += offset
                word["end"] += offset
        target.unlink(missing_ok=True)

    _save_segments(video_id, segments, sample)
    words_count = sum(len(s["words"]) for s in segments)
    queue.log(job_id, f"распознано реплик: {len(segments)}, слов: {words_count}, стоимость: {cost:.0f} ₽")
    return len(segments)


@register("transcribe_sample")
def handle_transcribe_sample(job: dict[str, Any]) -> None:
    """Пробное распознавание фрагмента — проверить качество, не оплачивая серию целиком."""
    payload = job.get("payload") or {}
    start = float(payload.get("start", 0))
    end = float(payload.get("end", start + 180))
    video_id = job["video_id"]
    try:
        run_transcribe(video_id, job["id"], sample=(start, end))
        _set_status(video_id, "audio_ready")
        queue.set_progress(job["id"], 100, "transcribing")
    except Exception as exc:
        _set_status(video_id, "audio_ready", str(exc)[:1000])
        raise


@register("analyze")
def handle_analyze(job: dict[str, Any]) -> None:
    """Полный разбор серии. Стадии добавляются по мере готовности этапов."""
    video_id = job["video_id"]
    job_id = job["id"]

    try:
        video = _video(video_id)
        if not video.get("duration"):
            run_probe(video_id, job_id)

        run_extract_audio(video_id, job_id)
        if queue.is_cancelled(job_id):
            _set_status(video_id, "uploaded")
            return

        run_transcribe(video_id, job_id)
        if queue.is_cancelled(job_id):
            _set_status(video_id, "audio_ready")
            return

        # Поиск сцен и анализ интересных моментов подключаются на этапе 4.
        _set_status(video_id, "transcribed")
        queue.set_progress(job_id, 100, "transcribing")
    except Exception as exc:
        _set_status(video_id, "failed", str(exc)[:1000])
        raise
