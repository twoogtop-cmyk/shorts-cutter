"""Обёртки над ffmpeg/ffprobe.

Все вызовы идут списком аргументов (никакого shell), поэтому имя файла
не может превратиться в команду.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from ..config import AUDIO_BITRATE, AUDIO_SAMPLE_RATE, FFMPEG, FFPROBE


class FFmpegError(RuntimeError):
    pass


def probe(path: Path) -> dict[str, Any]:
    """Метаданные файла через ffprobe."""
    cmd = [
        FFPROBE, "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe завершился с кодом {proc.returncode}: {proc.stderr[:500]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise FFmpegError("ffprobe вернул неразбираемый ответ") from exc


def _parse_fps(value: str | None) -> float | None:
    if not value or "/" not in value:
        return None
    num, den = value.split("/", 1)
    try:
        num_f, den_f = float(num), float(den)
    except ValueError:
        return None
    return round(num_f / den_f, 3) if den_f else None


def summarize_probe(data: dict[str, Any]) -> dict[str, Any]:
    """Достаёт из ffprobe только то, что нужно интерфейсу и обработке."""
    streams = data.get("streams", [])
    fmt = data.get("format", {})

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    duration = fmt.get("duration") or (video or {}).get("duration")
    tracks = []
    for i, stream in enumerate(audio_streams):
        tags = stream.get("tags") or {}
        tracks.append(
            {
                "index": i,
                "stream_index": stream.get("index"),
                "language": tags.get("language") or "und",
                "title": tags.get("title") or "",
                "codec": stream.get("codec_name"),
                "channels": stream.get("channels"),
            }
        )

    return {
        "duration": float(duration) if duration else None,
        "width": (video or {}).get("width"),
        "height": (video or {}).get("height"),
        "fps": _parse_fps((video or {}).get("avg_frame_rate")),
        "video_codec": (video or {}).get("codec_name"),
        "size_bytes": int(fmt.get("size", 0) or 0),
        "audio_tracks": tracks,
    }


TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def run(
    args: list[str],
    total_duration: float | None = None,
    on_progress: Callable[[float], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    timeout: float | None = None,
    on_line: Callable[[str], None] | None = None,
) -> str:
    """Запускает ffmpeg, отдавая прогресс по разобранному stderr.

    Возвращает последние строки stderr — их сохраняем в логи задачи для
    диагностики. Полный вывод может быть огромным, поэтому вызывающий код,
    которому нужны все строки (например, список склеек), передаёт on_line.
    """
    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-y", *args]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, bufsize=1
    )
    tail: list[str] = []
    try:
        assert proc.stderr is not None
        for line in proc.stderr:
            if on_line:
                on_line(line)
            tail.append(line)
            if len(tail) > 200:
                del tail[0]
            if should_cancel and should_cancel():
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise FFmpegError("Обработка отменена пользователем")
            if on_progress and total_duration:
                match = TIME_RE.search(line)
                if match:
                    h, m, s = match.groups()
                    done = int(h) * 3600 + int(m) * 60 + float(s)
                    on_progress(min(1.0, done / total_duration))
        proc.wait(timeout=timeout)
    finally:
        if proc.poll() is None:
            proc.kill()

    log = "".join(tail)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg завершился с кодом {proc.returncode}:\n{log[-1500:]}")
    return log


def extract_audio(
    source: Path,
    target: Path,
    audio_track: int = 0,
    duration: float | None = None,
    on_progress: Callable[[float], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> str:
    """Извлекает моно-аудио для распознавания речи (≈28 МБ на час)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "-i", str(source),
        "-map", f"0:a:{audio_track}",
        "-vn",
        "-ac", "1",
        "-ar", str(AUDIO_SAMPLE_RATE),
        "-b:a", AUDIO_BITRATE,
        str(target),
    ]
    return run(args, total_duration=duration, on_progress=on_progress, should_cancel=should_cancel)


def split_audio(source: Path, target_dir: Path, chunk_seconds: int) -> list[tuple[Path, float]]:
    """Режет аудио на куски. Возвращает пары (файл, смещение от начала).

    Копирование потока без перекодирования — на 1 ядре это важно.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    pattern = target_dir / "chunk_%04d.mp3"
    run([
        "-i", str(source),
        "-f", "segment",
        "-segment_time", str(chunk_seconds),
        "-c", "copy",
        "-reset_timestamps", "1",
        str(pattern),
    ])
    chunks = sorted(target_dir.glob("chunk_*.mp3"))
    result: list[tuple[Path, float]] = []
    offset = 0.0
    for chunk in chunks:
        result.append((chunk, offset))
        info = summarize_probe(probe(chunk))
        offset += info["duration"] or chunk_seconds
    return result


def detect_scenes(
    source: Path,
    threshold: float = 0.4,
    duration: float | None = None,
    on_progress: Callable[[float], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[float]:
    """Возвращает моменты монтажных склеек (в секундах).

    Анализ идёт по уменьшенному кадру: на одном ядре полное разрешение
    обрабатывалось бы кратно дольше без пользы для точности.
    """
    times: list[float] = []
    scene_re = re.compile(r"lavfi\.scd\.time: ([\d.]+)")

    def collect(line: str) -> None:
        match = scene_re.search(line)
        if match:
            times.append(float(match.group(1)))

    run(
        [
            "-i", str(source),
            "-filter_complex", f"[0:v]scale=320:-2,scdet=threshold={threshold * 100}",
            "-f", "null", "-",
        ],
        total_duration=duration,
        on_progress=on_progress,
        should_cancel=should_cancel,
        on_line=collect,
    )
    return sorted(set(times))
