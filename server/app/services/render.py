"""Сборка вертикального ролика 9:16.

Всё оформление — кадрирование, субтитры, баннер, финальная плашка — делается
за один проход ffmpeg. Промежуточных перекодирований нет: каждое лишнее
кодирование ухудшает картинку.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import faces, ffmpeg, subtitles

OUT_WIDTH = 1080
OUT_HEIGHT = 1920

PROFILES = {
    "preview": {"width": 540, "height": 960, "crf": 30, "preset": "veryfast", "audio": "96k"},
    "high": {"width": 1080, "height": 1920, "crf": 18, "preset": "medium", "audio": "192k"},
    "max": {"width": 1080, "height": 1920, "crf": 16, "preset": "slow", "audio": "192k"},
}


@dataclass
class RenderRequest:
    source: Path
    target: Path
    start: float
    end: float
    profile: str = "high"
    crop_mode: str = "smart"
    subtitle_words: list[dict[str, Any]] = field(default_factory=list)
    subtitle_style: str = "dynamic"
    banner_path: Path | None = None
    banner_mode: str = "separate_top"
    banner_height_percent: float = 18.0
    banner_opacity: float = 100.0
    outro_text: str = ""
    outro_duration: float = 3.0
    outro_font_size: int = 64
    outro_bg_opacity: float = 60.0
    work_dir: Path | None = None


def _escape_path(path: Path) -> str:
    """Экранирование пути внутри значения фильтра ffmpeg."""
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _escape_expr(expression: str) -> str:
    """Экранирует выражение внутри значения параметра фильтра.

    Запятая и точка с запятой в filter_complex разделяют фильтры, поэтому
    внутри выражения их нужно закрыть слэшем.
    """
    return expression.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;")


def _escape_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "’")
        .replace("%", "\\%").replace(",", "\\,")
    )


def build_video_chain(
    request: RenderRequest,
    source_width: int,
    source_height: int,
    crop_expression: str | None,
    out_width: int,
    out_height: int,
    banner_height: int,
) -> list[str]:
    """Строит цепочку фильтров для картинки."""
    chain: list[str] = []
    video_height = out_height - banner_height

    if request.crop_mode == "blur":
        # Размытая увеличенная копия кадра как фон, поверх — всё видео целиком.
        return [
            f"[0:v]scale={out_width}:{video_height}:force_original_aspect_ratio=increase,"
            f"crop={out_width}:{video_height},boxblur=luma_radius=40:luma_power=2[bg]",
            f"[0:v]scale={out_width}:{video_height}:force_original_aspect_ratio=decrease[fg]",
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[vid]",
        ]

    crop_width = int(source_height * out_width / video_height)
    crop_width = min(crop_width, source_width)
    crop_width -= crop_width % 2

    if request.crop_mode == "smart" and crop_expression:
        x_expr = _escape_expr(crop_expression)
    else:
        x_expr = str(int((source_width - crop_width) / 2))

    chain.append(
        f"[0:v]crop={crop_width}:{source_height}:{x_expr}:0,"
        f"scale={out_width}:{video_height}:flags=lanczos[vid]"
    )
    return chain


def build_filter_complex(
    request: RenderRequest,
    source_width: int,
    source_height: int,
    crop_expression: str | None,
    subtitle_file: Path | None,
    out_width: int,
    out_height: int,
) -> tuple[str, str]:
    """Возвращает filter_complex и метку итогового видеопотока."""
    banner_height = 0
    has_banner = request.banner_path is not None and request.banner_path.exists()
    if has_banner and request.banner_mode == "separate_top":
        banner_height = int(out_height * request.banner_height_percent / 100)
        banner_height -= banner_height % 2

    parts = build_video_chain(
        request, source_width, source_height, crop_expression, out_width, out_height, banner_height
    )
    label = "vid"

    if subtitle_file is not None:
        parts.append(f"[{label}]subtitles='{_escape_path(subtitle_file)}'[subbed]")
        label = "subbed"

    if banner_height:
        # Видео прижато вниз, сверху отдельная полоса под баннер.
        parts.append(
            f"color=c=black:s={out_width}x{out_height}:d=1[canvas]"
        )
        parts.append(f"[canvas][{label}]overlay=0:{banner_height}:shortest=1[stacked]")
        label = "stacked"

    if has_banner:
        opacity = max(0.0, min(1.0, request.banner_opacity / 100))
        target_h = banner_height if banner_height else -1
        scale = f"scale={out_width}:{target_h}" if banner_height else f"scale={out_width}:-1"
        parts.append(f"[1:v]{scale},format=rgba,colorchannelmixer=aa={opacity:.2f}[banner]")
        parts.append(f"[{label}][banner]overlay=0:0[banned]")
        label = "banned"

    if request.outro_text.strip():
        duration = request.end - request.start
        outro_start = max(0.0, duration - request.outro_duration)
        lines = [l.strip() for l in request.outro_text.strip().splitlines() if l.strip()]
        text = _escape_drawtext("\n".join(lines))
        box_alpha = max(0.0, min(1.0, request.outro_bg_opacity / 100))
        font_size = int(request.outro_font_size * out_width / OUT_WIDTH)
        draw = (
            f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
            f"text='{text}':fontcolor=white:fontsize={font_size}:"
            f"x=(w-text_w)/2:y=(h-text_h)/2:line_spacing=12:"
            f"box=1:boxcolor=black@{box_alpha:.2f}:boxborderw=28:"
            f"enable='gte(t,{outro_start:.2f})'"
        )
        parts.append(f"[{label}]{draw}[outro]")
        label = "outro"

    return ";".join(parts), label


def render_clip(
    request: RenderRequest,
    on_progress: Callable[[float], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> Path:
    """Готовит и запускает единственный проход ffmpeg."""
    profile = PROFILES.get(request.profile, PROFILES["high"])
    out_width, out_height = profile["width"], profile["height"]

    info = ffmpeg.summarize_probe(ffmpeg.probe(request.source))
    source_width = int(info["width"] or 1920)
    source_height = int(info["height"] or 1080)
    duration = request.end - request.start

    work_dir = request.work_dir or request.target.parent
    work_dir.mkdir(parents=True, exist_ok=True)

    crop_expression = None
    effective_mode = request.crop_mode
    if request.crop_mode == "smart":
        banner_height = 0
        if request.banner_path and request.banner_mode == "separate_top":
            banner_height = int(out_height * request.banner_height_percent / 100)
        video_height = out_height - banner_height
        crop_width = min(int(source_height * out_width / video_height), source_width)

        track, faces_too_wide = faces.detect_face_track(
            request.source,
            request.start,
            request.end,
            crop_width=crop_width,
            frame_width=source_width,
            should_cancel=should_cancel,
        )
        if faces_too_wide:
            # Говорящие не помещаются в вертикальное окно — обрезка потеряет
            # кого-то из них, поэтому показываем кадр целиком с размытым фоном.
            effective_mode = "blur"
            request.crop_mode = "blur"
            if on_log:
                on_log("лица не помещаются в кадр 9:16 — используем размытый фон")
        elif track:
            crop_expression = faces.build_crop_expression(track, crop_width, source_width)
            if on_log:
                on_log(f"слежение за лицами: {faces.describe_track(track)}")
        elif on_log:
            on_log("лица не найдены — кадрируем по центру")

    subtitle_file = None
    if request.subtitle_words:
        shifted = [
            {**w, "start": w["start"] - request.start, "end": w["end"] - request.start}
            for w in request.subtitle_words
        ]
        content = subtitles.build_ass(
            shifted,
            style_name=request.subtitle_style,
            bottom_margin=subtitles.SAFE_BOTTOM_MARGIN,
        )
        subtitle_file = subtitles.write_ass(work_dir / f"{request.target.stem}.ass", content)

    filter_complex, label = build_filter_complex(
        request, source_width, source_height, crop_expression, subtitle_file, out_width, out_height
    )

    args = ["-ss", f"{request.start:.3f}", "-t", f"{duration:.3f}", "-i", str(request.source)]
    if request.banner_path and request.banner_path.exists():
        args += ["-i", str(request.banner_path)]

    args += [
        "-filter_complex", filter_complex,
        "-map", f"[{label}]",
        "-map", "0:a:0?",
        "-c:v", "libx264",
        "-crf", str(profile["crf"]),
        "-preset", profile["preset"],
        "-pix_fmt", "yuv420p",
        "-profile:v", "high",
        "-c:a", "aac",
        "-b:a", profile["audio"],
        "-ac", "2",
        "-movflags", "+faststart",
        "-r", "30",
        str(request.target),
    ]

    log = ffmpeg.run(
        args,
        total_duration=duration,
        on_progress=on_progress,
        should_cancel=should_cancel,
    )
    if on_log:
        on_log(f"готово: {request.target.name} ({effective_mode})")
    return request.target
