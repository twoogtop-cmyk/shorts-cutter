"""Поиск экшен-сцен: боёв, погонь, драк.

Такие сцены почти не содержат речи, поэтому анализ транскрипции их не видит.
Зато у них есть два измеримых признака: рубленый монтаж (склейки идут в
несколько раз чаще обычного) и громкий звук при малом количестве реплик.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from . import ffmpeg

# Шаг профиля громкости. Мельче не нужно: сцены оцениваются окнами в секунды.
LOUDNESS_STEP = 0.5
WINDOW = 6.0
# Во сколько раз монтаж должен быть чаще среднего по серии.
CUT_RATE_FACTOR = 1.6
# Максимальная доля времени с речью — выше это уже диалог, а не экшен.
MAX_SPEECH_RATIO = 0.35


def loudness_profile(
    audio_path: Path,
    should_cancel: Callable[[], bool] | None = None,
) -> list[tuple[float, float]]:
    """Возвращает пары (время, громкость в дБ) с шагом LOUDNESS_STEP."""
    values: list[tuple[float, float]] = []
    time_re = re.compile(r"pts_time:([\d.]+)")
    rms_re = re.compile(r"RMS_level=(-?[\d.]+|-inf)")
    pending_time: float | None = None

    def collect(line: str) -> None:
        nonlocal pending_time
        time_match = time_re.search(line)
        if time_match:
            pending_time = float(time_match.group(1))
            return
        rms_match = rms_re.search(line)
        if rms_match and pending_time is not None:
            raw = rms_match.group(1)
            level = -90.0 if raw == "-inf" else float(raw)
            if not values or pending_time - values[-1][0] >= LOUDNESS_STEP:
                values.append((round(pending_time, 2), level))
            pending_time = None

    ffmpeg.run(
        [
            "-i", str(audio_path),
            "-af",
            "astats=metadata=1:reset=40,"
            "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=/dev/stderr",
            "-f", "null", "-",
        ],
        should_cancel=should_cancel,
        on_line=collect,
    )
    return values


def _speech_ratio(segments: list[dict[str, Any]], start: float, end: float) -> float:
    """Какая доля отрезка занята речью."""
    span = end - start
    if span <= 0:
        return 1.0
    spoken = 0.0
    for segment in segments:
        overlap = min(end, segment["end"]) - max(start, segment["start"])
        if overlap > 0:
            spoken += overlap
    return min(1.0, spoken / span)


def _average_loudness(profile: list[tuple[float, float]], start: float, end: float) -> float:
    inside = [v for t, v in profile if start <= t <= end]
    return sum(inside) / len(inside) if inside else -90.0


def find_action_windows(
    scenes: list[float],
    profile: list[tuple[float, float]],
    segments: list[dict[str, Any]],
    duration: float,
    min_duration: float,
    max_duration: float,
) -> list[dict[str, Any]]:
    """Ищет отрезки с частым монтажом, громким звуком и малым количеством речи."""
    if not scenes or duration <= 0:
        return []

    average_rate = len(scenes) / duration
    if average_rate <= 0:
        return []

    overall_loudness = (
        sum(v for _, v in profile) / len(profile) if profile else -90.0
    )

    # Оценка каждого окна по трём признакам.
    marks: list[tuple[float, float]] = []
    step = WINDOW / 2
    t = 0.0
    while t + WINDOW <= duration:
        window_end = t + WINDOW
        cuts = sum(1 for c in scenes if t <= c < window_end)
        rate = cuts / WINDOW
        loud = _average_loudness(profile, t, window_end)
        speech = _speech_ratio(segments, t, window_end)

        if (
            rate >= average_rate * CUT_RATE_FACTOR
            and loud >= overall_loudness
            and speech <= MAX_SPEECH_RATIO
        ):
            score = (rate / average_rate) + (loud - overall_loudness) / 6 + (1 - speech)
            marks.append((t, score))
        t += step

    if not marks:
        return []

    # Соседние окна склеиваются в один фрагмент.
    windows: list[dict[str, Any]] = []
    current_start = marks[0][0]
    current_end = marks[0][0] + WINDOW
    scores = [marks[0][1]]
    for start, score in marks[1:]:
        if start <= current_end + step:
            current_end = start + WINDOW
            scores.append(score)
        else:
            windows.append(
                {"start": current_start, "end": current_end, "score": sum(scores) / len(scores)}
            )
            current_start, current_end, scores = start, start + WINDOW, [score]
    windows.append({"start": current_start, "end": current_end, "score": sum(scores) / len(scores)})

    # Короткие всплески отбрасываем, длинные обрезаем до допустимой длительности.
    result = []
    for window in windows:
        span = window["end"] - window["start"]
        if span < min_duration:
            continue
        if span > max_duration:
            window["end"] = window["start"] + max_duration
        result.append(window)

    result.sort(key=lambda w: w["score"], reverse=True)
    return result


def is_service_fragment(
    window: dict[str, Any],
    duration: float,
    segments: list[dict[str, Any]],
    skip_intro: float,
    skip_outro: float,
    context_gap: float = 45.0,
) -> bool:
    """Отсекает заставку и финальные титры.

    У них те же признаки, что у боя — частый монтаж, громкая музыка, нет речи,
    и по картинке они не отличаются (в бою темноты даже больше, чем в титрах).
    Надёжно работает другое: служебные блоки стоят в начале и конце серии,
    и рядом с ними нет диалога.
    """
    if window["start"] < skip_intro:
        return True
    if window["end"] > duration - skip_outro:
        return True

    # Настоящая сцена окружена репликами: перед дракой говорят, после — реагируют.
    nearby = any(
        segment["end"] >= window["start"] - context_gap
        and segment["start"] <= window["end"] + context_gap
        for segment in segments
    )
    return not nearby


def snap_action_bounds(
    window: dict[str, Any],
    scenes: list[float],
    segments: list[dict[str, Any]],
    pad_start: float,
    pad_end: float,
    max_duration: float = 90.0,
    lead_in: float = 40.0,
    tail: float = 20.0,
) -> tuple[float, float]:
    """Границы экшена: сама драка плюс реплики, которые её объясняют.

    Без завязки и реакции голое действие не читается — зритель не понимает,
    из-за чего дерутся. Поэтому фрагмент расширяется до ближайших реплик,
    если они рядом и позволяет допустимая длительность.
    """
    start, end = window["start"], window["end"]

    lead = [s for s in segments if 0 < start - s["end"] <= lead_in]
    if lead:
        candidate_start = max(0.0, lead[-1]["start"] - pad_start)
        if end - candidate_start <= max_duration:
            start = candidate_start

    follow = [s for s in segments if 0 < s["start"] - end <= tail]
    if follow:
        candidate_end = follow[0]["end"] + pad_end
        if candidate_end - start <= max_duration:
            end = candidate_end

    # Если реплик рядом нет — держимся монтажных склеек.
    if not lead:
        before = [c for c in scenes if c <= start]
        if before and start - max(before) <= 2.5:
            start = max(before)
    if not follow:
        after = [c for c in scenes if c >= end]
        if after and min(after) - end <= 2.5:
            end = min(after)

    # Фраза не должна оборваться на полуслове.
    for segment in segments:
        if segment["start"] < start < segment["end"]:
            start = max(0.0, segment["start"] - pad_start)
        if segment["start"] < end < segment["end"]:
            end = segment["end"] + pad_end

    return max(0.0, start), end
