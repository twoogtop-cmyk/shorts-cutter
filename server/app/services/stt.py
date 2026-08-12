"""Распознавание речи и сборка транскрипции.

Сервис возвращает поток слов с таймкодами и меткой говорящего. Из него
собираются реплики: по смене говорящего, по паузе и по концу предложения —
именно на границы реплик потом опираются начало и конец шортса.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable

# Пауза, с которой начинается новая реплика того же говорящего.
# Внутри незаконченной фразы порог выше: драматичная пауза посреди
# предложения не должна разрывать реплику.
PAUSE_SPLIT = 0.8
PAUSE_SPLIT_MID_SENTENCE = 2.5
# Реплику длиннее этого стараемся закрыть на ближайшем конце предложения.
MAX_SEGMENT_SECONDS = 12.0
SENTENCE_END = (".", "!", "?", "…")


def extract_words(full_response: dict[str, Any], offset: float = 0.0) -> list[dict[str, Any]]:
    """Оставляет только произнесённые слова, сдвигая таймкоды на offset.

    Ответ содержит также элементы spacing (пробелы) и audio_event
    (звуки вроде [музыка]) — в текст диалога они не идут.
    """
    words: list[dict[str, Any]] = []
    for item in full_response.get("words", []) or []:
        if item.get("type") != "word":
            continue
        text = (item.get("text") or "").strip()
        if not text:
            continue
        start = item.get("start")
        end = item.get("end")
        if start is None or end is None:
            continue
        words.append(
            {
                "text": text,
                "start": round(float(start) + offset, 3),
                "end": round(float(end) + offset, 3),
                "speaker": item.get("speaker_id") or None,
            }
        )
    return words


def group_into_segments(words: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Собирает слова в реплики."""
    segments: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        if not current:
            return
        segments.append(
            {
                "start": current[0]["start"],
                "end": current[-1]["end"],
                "text": " ".join(w["text"] for w in current),
                "speaker": current[0]["speaker"],
                "words": list(current),
            }
        )
        current.clear()

    for word in words:
        if current:
            prev = current[-1]
            ends_sentence = prev["text"].endswith(SENTENCE_END)
            pause_limit = PAUSE_SPLIT if ends_sentence else PAUSE_SPLIT_MID_SENTENCE
            speaker_changed = word["speaker"] != prev["speaker"]
            long_pause = word["start"] - prev["end"] >= pause_limit
            too_long = word["end"] - current[0]["start"] >= MAX_SEGMENT_SECONDS
            if speaker_changed or long_pause or (too_long and ends_sentence):
                flush()
        current.append(word)

    flush()
    return segments


def transcribe_audio(
    audio_path: Path,
    language: str,
    chunk_seconds: int,
    tmp_dir: Path,
    on_progress: Callable[[float], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[list[dict[str, Any]], float]:
    """Распознаёт файл целиком, при необходимости разбивая его на куски.

    Возвращает (реплики, суммарная стоимость в рублях).
    """
    from . import ffmpeg, genapi

    info = ffmpeg.summarize_probe(ffmpeg.probe(audio_path))
    duration = info["duration"] or 0.0

    if duration <= chunk_seconds:
        pieces: list[tuple[Path, float]] = [(audio_path, 0.0)]
        work_dir = None
    else:
        work_dir = tmp_dir / f"stt_{audio_path.stem}"
        pieces = ffmpeg.split_audio(audio_path, work_dir, chunk_seconds)
        if on_log:
            on_log(f"аудио разбито на {len(pieces)} частей по {chunk_seconds // 60} мин")

    lang_code = {"ru": "rus", "en": "eng"}.get(language, language if language != "auto" else "")

    all_words: list[dict[str, Any]] = []
    total_cost = 0.0
    for i, (piece, offset) in enumerate(pieces):
        if should_cancel and should_cancel():
            raise RuntimeError("Распознавание отменено пользователем")
        if on_log:
            on_log(f"распознаём часть {i + 1} из {len(pieces)}")
        result = genapi.transcribe_file(
            piece, language=lang_code or "rus", diarize=True, should_cancel=should_cancel
        )
        total_cost += float(result.get("_cost") or 0)
        all_words.extend(extract_words(result, offset=offset))
        if on_progress:
            on_progress((i + 1) / len(pieces))

    if work_dir is not None:
        for piece, _ in pieces:
            piece.unlink(missing_ok=True)
        work_dir.rmdir()

    all_words.sort(key=lambda w: w["start"])
    return group_into_segments(all_words), total_cost
