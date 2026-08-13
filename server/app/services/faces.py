"""Слежение за лицами для вертикального кадрирования.

Кадры разбираются с шагом в полсекунды, а не покадрово: на одном ядре
полный проход был бы в 12 раз дороже, а траектория кадрирования всё равно
сглаживается — разницы в результате нет.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

MODEL_DIR = Path("/opt/shorts-cutter/models")
PROTO = MODEL_DIR / "deploy.prototxt"
WEIGHTS = MODEL_DIR / "res10.caffemodel"

CONFIDENCE = 0.4
SAMPLE_FPS = 2.0
# Насколько кадр может смещаться за секунду (в долях ширины окна): выше —
# резче реакция на смену говорящего, ниже — плавнее картинка.
MAX_SHIFT_PER_SECOND = 0.6
SMOOTH_WINDOW = 5


def model_available() -> bool:
    return PROTO.exists() and WEIGHTS.exists()


def _load_net() -> cv2.dnn.Net:
    return cv2.dnn.readNetFromCaffe(str(PROTO), str(WEIGHTS))


def detect_face_track(
    source: Path,
    start: float,
    end: float,
    crop_width: int,
    frame_width: int,
    should_cancel=None,
) -> tuple[list[tuple[float, float]], bool, list[tuple[float, float, float]]]:
    """Возвращает траекторию кадрирования, признак «лица не помещаются» и полосы лиц.

    Траектория — список пар (время от начала фрагмента, центр по X).
    Второй элемент равен True, когда лица в кадре разнесены шире окна
    кадрирования: тогда обрезка неизбежно потеряет кого-то из говорящих.
    """
    if not model_available():
        return [], False, []

    net = _load_net()
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        return [], False, []

    step = 1.0 / SAMPLE_FPS
    duration = end - start
    track: list[tuple[float, float]] = []
    too_wide_frames = 0
    frames_with_faces = 0
    last_center = frame_width / 2
    # Вертикальные границы лиц нужны, чтобы финальная плашка не легла на лицо.
    bands: list[tuple[float, float, float]] = []

    try:
        t = 0.0
        while t < duration:
            if should_cancel and should_cancel():
                break
            capture.set(cv2.CAP_PROP_POS_MSEC, (start + t) * 1000)
            ok, frame = capture.read()
            if not ok:
                break

            height, width = frame.shape[:2]
            blob = cv2.dnn.blobFromImage(
                cv2.resize(frame, (300, 300)), 1.0, (300, 300), (104.0, 177.0, 123.0)
            )
            net.setInput(blob)
            detections = net.forward()

            faces: list[tuple[float, float, float]] = []  # центр X, вес, ширина
            tops: list[float] = []
            bottoms: list[float] = []
            for i in range(detections.shape[2]):
                confidence = float(detections[0, 0, i, 2])
                if confidence < CONFIDENCE:
                    continue
                box = detections[0, 0, i, 3:7] * np.array([width, height, width, height])
                x1, y1, x2, y2 = box
                face_width = max(1.0, float(x2 - x1))
                faces.append(((float(x1) + float(x2)) / 2, confidence * face_width, face_width))
                tops.append(float(y1) / height)
                bottoms.append(float(y2) / height)

            if faces:
                frames_with_faces += 1
                weight_sum = sum(f[1] for f in faces)
                center = sum(f[0] * f[1] for f in faces) / weight_sum
                left = min(f[0] - f[2] / 2 for f in faces)
                right = max(f[0] + f[2] / 2 for f in faces)
                if right - left > crop_width:
                    too_wide_frames += 1
                last_center = center
                bands.append((round(t, 2), round(min(tops), 3), round(max(bottoms), 3)))
            else:
                center = last_center

            track.append((round(t, 2), round(center, 1)))
            t += step
    finally:
        capture.release()

    faces_too_wide = frames_with_faces > 0 and too_wide_frames / frames_with_faces > 0.5
    return smooth_track(track, crop_width, frame_width), faces_too_wide, bands


def smooth_track(
    track: list[tuple[float, float]],
    crop_width: int,
    frame_width: int,
) -> list[tuple[float, float]]:
    """Сглаживает траекторию и ограничивает скорость — без рывков кадра."""
    if not track:
        return []

    values = [c for _, c in track]
    smoothed: list[float] = []
    for i in range(len(values)):
        lo = max(0, i - SMOOTH_WINDOW // 2)
        hi = min(len(values), i + SMOOTH_WINDOW // 2 + 1)
        smoothed.append(sum(values[lo:hi]) / (hi - lo))

    half = crop_width / 2
    limit_per_step = MAX_SHIFT_PER_SECOND * crop_width / SAMPLE_FPS
    result: list[tuple[float, float]] = []
    previous = smoothed[0]
    for (t, _), value in zip(track, smoothed):
        delta = value - previous
        if abs(delta) > limit_per_step:
            value = previous + limit_per_step * (1 if delta > 0 else -1)
        value = max(half, min(frame_width - half, value))
        result.append((t, round(value, 1)))
        previous = value
    return result


def simplify_track(
    track: list[tuple[float, float]], tolerance: float = 8.0, max_points: int = 40
) -> list[tuple[float, float]]:
    """Оставляет опорные точки: выражение для ffmpeg не должно быть огромным."""
    if len(track) <= 2:
        return track

    kept = [track[0]]
    for point in track[1:-1]:
        if abs(point[1] - kept[-1][1]) >= tolerance:
            kept.append(point)
    kept.append(track[-1])

    if len(kept) > max_points:
        stride = len(kept) / max_points
        thinned = [kept[int(i * stride)] for i in range(max_points)]
        thinned[-1] = kept[-1]
        kept = thinned
    return kept


def build_crop_expression(track: list[tuple[float, float]], crop_width: int, frame_width: int) -> str:
    """Строит выражение x(t) для фильтра crop — кусочно-линейное по опорным точкам."""
    half = crop_width / 2
    max_x = max(0, frame_width - crop_width)

    if not track:
        return str(int(max_x / 2))

    points = simplify_track(track)
    if len(points) == 1:
        return str(int(max(0, min(max_x, points[0][1] - half))))

    def x_at(index: int) -> float:
        return max(0.0, min(max_x, points[index][1] - half))

    expression = f"{x_at(len(points) - 1):.0f}"
    for i in range(len(points) - 2, -1, -1):
        t0, t1 = points[i][0], points[i + 1][0]
        x0, x1 = x_at(i), x_at(i + 1)
        span = max(0.001, t1 - t0)
        segment = f"({x0:.0f}+({x1 - x0:.0f})*(t-{t0:.2f})/{span:.2f})"
        expression = f"if(lt(t,{t1:.2f}),{segment},{expression})"
    return expression


def describe_track(track: list[tuple[float, float]]) -> dict[str, Any]:
    if not track:
        return {"points": 0}
    values = [c for _, c in track]
    return {
        "points": len(track),
        "min": round(min(values)),
        "max": round(max(values)),
        "span": round(max(values) - min(values)),
    }


def free_vertical_zone(
    bands: list[tuple[float, float, float]],
    since: float,
) -> tuple[float, float] | None:
    """Ищет свободную от лиц полосу кадра начиная с момента since.

    Возвращает долю высоты (от, до) — куда можно положить плашку, не закрыв лицо.
    Если лиц в этом отрезке нет, возвращает None: место любое.
    """
    relevant = [b for b in bands if b[0] >= since]
    if not relevant:
        return None

    top = min(b[1] for b in relevant)
    bottom = max(b[2] for b in relevant)
    above = top
    below = 1.0 - bottom
    if below >= above:
        return (bottom, 1.0)
    return (0.0, top)
