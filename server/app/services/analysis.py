"""Поиск интересных моментов по транскрипции.

Модель получает диалог с таймкодами и метками говорящих и возвращает
фрагменты-кандидаты. Границы, которые она называет, затем уточняются по
репликам и монтажным склейкам — чтобы шортс не начинался и не заканчивался
посреди фразы.
"""

from __future__ import annotations

import json
import re
from typing import Any

CATEGORIES = [
    "Конфликт", "Юмор", "Неожиданный поворот", "Интрига", "Романтический момент",
    "Эмоциональный момент", "Ссора", "Разоблачение", "Напряжённый диалог",
    "Экшен", "Сильная цитата", "Абсурдная ситуация",
]

SYSTEM_PROMPT = """Ты — редактор коротких вертикальных видео для YouTube Shorts.
Ты получаешь транскрипцию серии сериала с таймкодами и метками говорящих.
Твоя задача — найти фрагменты, которые сработают как самостоятельные короткие ролики.

Хороший фрагмент:
- понятен зрителю, который не смотрел серию;
- содержит законченную мини-историю: завязку и развязку;
- цепляет в первые 2-3 секунды;
- держит внимание до конца;
- заканчивается сильной репликой, а не обрывом.

Категории: {categories}

Плохой фрагмент (такие не предлагай):
- начинается или заканчивается посреди мысли;
- непонятен без знания сюжета;
- почти без речи и без событий;
- длинное вступление перед сутью;
- кульминация осталась за границей фрагмента;
- почти повторяет другой предложенный фрагмент.

Не нарезай механически подряд. Лучше вернуть меньше фрагментов, но сильных.
Если в материале нет ничего стоящего — верни пустой список."""

USER_PROMPT = """Транскрипция фрагмента серии. Формат строки: [начало-конец] Голос: реплика

{transcript}

Найди фрагменты длительностью от {min_dur} до {max_dur} секунд (оптимально {target_min}-{target_max}).
Верни ТОЛЬКО JSON без пояснений и без markdown-разметки:

{{"moments": [
  {{
    "start": 1234.5,
    "end": 1278.0,
    "title": "короткий заголовок до 60 символов",
    "category": "одна из категорий",
    "hook": 0-100,
    "retention": 0-100,
    "clarity": 0-100,
    "emotion": 0-100,
    "ending": 0-100,
    "reason": "1-2 предложения: почему это сработает как Shorts"
  }}
]}}

Оценки:
- hook — цепляет ли начало;
- retention — удержит ли внимание;
- clarity — понятно ли без контекста серии;
- emotion — эмоциональная сила;
- ending — есть ли внятная концовка.

start и end — секунды из таймкодов транскрипции. Начинай фрагмент с начала реплики,
заканчивай концом реплики."""


def speaker_label(speaker: str | None) -> str:
    """speaker_0 → «Голос 1»: нумерация с единицы, как в интерфейсе."""
    if not speaker:
        return "—"
    match = re.search(r"(\d+)", speaker)
    return f"Голос {int(match.group(1)) + 1}" if match else speaker


def build_transcript_text(segments: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"[{seg['start']:.1f}-{seg['end']:.1f}] {speaker_label(seg.get('speaker'))}: {seg['text']}"
        for seg in segments
    )


def parse_response(raw: str) -> list[dict[str, Any]]:
    """Достаёт JSON из ответа модели, даже если он обёрнут в текст или ```json."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith("{"):
        brace = text.find("{")
        if brace == -1:
            return []
        text = text[brace:]
        depth = 0
        for i, ch in enumerate(text):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    text = text[: i + 1]
                    break
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    moments = data.get("moments") if isinstance(data, dict) else data
    return moments if isinstance(moments, list) else []


def score_moment(moment: dict[str, Any]) -> int:
    """Общая оценка. Понятность и hook весомее прочего: без них шортс не работает."""
    weights = {"hook": 0.3, "retention": 0.2, "clarity": 0.25, "emotion": 0.15, "ending": 0.1}
    total = 0.0
    for key, weight in weights.items():
        try:
            total += float(moment.get(key) or 0) * weight
        except (TypeError, ValueError):
            continue
    return int(round(max(0, min(100, total))))


def snap_to_speech(
    start: float,
    end: float,
    segments: list[dict[str, Any]],
    scenes: list[float],
    pad_start: float,
    pad_end: float,
    max_shift: float = 3.0,
) -> tuple[float, float]:
    """Двигает границы на ближайшее начало и конец реплики.

    Начало дополнительно подтягивается к монтажной склейке, если она рядом —
    так ролик стартует с нового кадра, а не с середины плана.
    """
    if not segments:
        return max(0.0, start - pad_start), end + pad_end

    starts = [s["start"] for s in segments]
    nearest_start = min(starts, key=lambda v: abs(v - start))
    if abs(nearest_start - start) <= max_shift:
        start = nearest_start

    ends = [s["end"] for s in segments]
    nearest_end = min(ends, key=lambda v: abs(v - end))
    if abs(nearest_end - end) <= max_shift:
        end = nearest_end

    result_start = max(0.0, start - pad_start)
    if scenes:
        cuts_before = [c for c in scenes if result_start - 1.2 <= c <= start]
        if cuts_before:
            # Склейка ближе к реплике, чем отступ, — начинаем прямо с неё.
            result_start = max(cuts_before)

    return result_start, end + pad_end


def deduplicate(moments: list[dict[str, Any]], overlap_ratio: float = 0.5) -> list[dict[str, Any]]:
    """Убирает пересекающиеся фрагменты, оставляя те, что оценены выше."""
    ordered = sorted(moments, key=lambda m: m.get("total_score", 0), reverse=True)
    kept: list[dict[str, Any]] = []
    for moment in ordered:
        duplicate = False
        for chosen in kept:
            overlap = min(moment["end"], chosen["end"]) - max(moment["start"], chosen["start"])
            if overlap <= 0:
                continue
            shorter = min(moment["end"] - moment["start"], chosen["end"] - chosen["start"])
            if shorter > 0 and overlap / shorter >= overlap_ratio:
                duplicate = True
                break
        if not duplicate:
            kept.append(moment)
    return sorted(kept, key=lambda m: m["start"])


def find_moments(
    segments: list[dict[str, Any]],
    scenes: list[float],
    settings: dict[str, str],
    model: str,
    chat_fn,
    on_log=None,
) -> list[dict[str, Any]]:
    """Полный проход: запрос к модели, разбор, фильтрация, границы, дедуп."""
    if not segments:
        return []

    min_dur = float(settings.get("min_duration", 20))
    max_dur = float(settings.get("max_duration", 90))
    target_min = float(settings.get("target_min_duration", 30))
    target_max = float(settings.get("target_max_duration", 55))
    min_score = float(settings.get("min_score", 70))
    pad_start = float(settings.get("pad_start", 0.3))
    pad_end = float(settings.get("pad_end", 0.5))

    prompt = USER_PROMPT.format(
        transcript=build_transcript_text(segments),
        min_dur=int(min_dur),
        max_dur=int(max_dur),
        target_min=int(target_min),
        target_max=int(target_max),
    )
    raw = chat_fn(
        [
            {"role": "system", "content": SYSTEM_PROMPT.format(categories=", ".join(CATEGORIES))},
            {"role": "user", "content": prompt},
        ],
        model=model,
    )

    parsed = parse_response(raw)
    if on_log:
        on_log(f"модель предложила фрагментов: {len(parsed)}")

    results: list[dict[str, Any]] = []
    for moment in parsed:
        try:
            start = float(moment["start"])
            end = float(moment["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue

        start, end = snap_to_speech(start, end, segments, scenes, pad_start, pad_end)
        duration = end - start
        if duration < min_dur or duration > max_dur:
            if on_log:
                on_log(f"пропущен фрагмент {start:.0f}–{end:.0f} с: длительность {duration:.0f} с")
            continue

        total = score_moment(moment)
        if total < min_score:
            if on_log:
                on_log(f"пропущен фрагмент {start:.0f} с: оценка {total} ниже порога {int(min_score)}")
            continue

        inner = [s for s in segments if s["start"] >= start and s["end"] <= end]
        results.append(
            {
                "start": round(start, 2),
                "end": round(end, 2),
                "title": str(moment.get("title") or "")[:120],
                "category": str(moment.get("category") or "")[:60],
                "hook_score": int(moment.get("hook") or 0),
                "retention_score": int(moment.get("retention") or 0),
                "context_score": int(moment.get("clarity") or 0),
                "emotion_score": int(moment.get("emotion") or 0),
                "ending_score": int(moment.get("ending") or 0),
                "total_score": total,
                "ai_reason": str(moment.get("reason") or "")[:1000],
                "transcript_text": build_transcript_text(inner),
            }
        )

    deduped = deduplicate(results)
    if on_log and len(deduped) != len(results):
        on_log(f"убрано пересекающихся фрагментов: {len(results) - len(deduped)}")

    limit = settings.get("max_shorts", "auto")
    if limit != "auto":
        try:
            deduped = sorted(deduped, key=lambda m: m["total_score"], reverse=True)[: int(limit)]
            deduped.sort(key=lambda m: m["start"])
        except ValueError:
            pass

    return deduped
