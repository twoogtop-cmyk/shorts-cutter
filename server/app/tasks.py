"""Обработчики фоновых задач."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import AUDIO_DIR, PREVIEWS_DIR, RENDERS_DIR, STT_CHUNK_SECONDS, TMP_DIR
from .db import execute, get_settings, insert, query, query_one
from .services import action, analysis, ffmpeg, genapi, queue, render, stt
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


def run_scene_detection(video_id: int, job_id: int) -> list[float]:
    """Находит монтажные склейки — по ним уточняется начало шортса."""
    video = _video(video_id)
    source = Path(video["storage_path"])

    _set_status(video_id, "scene_detection")
    queue.set_progress(job_id, 0, "scene_detection")

    scenes = ffmpeg.detect_scenes(
        source,
        duration=video["duration"],
        on_progress=lambda frac: queue.set_progress(job_id, int(frac * 100), "scene_detection"),
        should_cancel=lambda: queue.is_cancelled(job_id),
    )

    execute("DELETE FROM scenes WHERE video_id=?", (video_id,))
    previous = 0.0
    for cut in scenes:
        insert("INSERT INTO scenes(video_id, start, end) VALUES (?, ?, ?)", (video_id, previous, cut))
        previous = cut
    duration = float(video["duration"] or 0)
    if duration > previous:
        insert("INSERT INTO scenes(video_id, start, end) VALUES (?, ?, ?)", (video_id, previous, duration))

    queue.log(job_id, f"найдено склеек: {len(scenes)}")
    return scenes


def run_ai_analysis(video_id: int, job_id: int) -> int:
    """Ищет интересные моменты и сохраняет их как кандидатов."""
    settings = get_settings()
    segments = [
        dict(r)
        for r in query(
            "SELECT id, start, end, text, speaker FROM segments WHERE video_id=? ORDER BY start",
            (video_id,),
        )
    ]
    if not segments:
        raise RuntimeError("Нет транскрипции — сначала нужно распознать речь")

    scenes = [r["end"] for r in query("SELECT end FROM scenes WHERE video_id=? ORDER BY start", (video_id,))]

    _set_status(video_id, "ai_analysis")
    queue.set_progress(job_id, 10, "ai_analysis")

    moments = analysis.find_moments(
        segments=segments,
        scenes=scenes,
        settings=settings,
        model=settings.get("llm_model", "claude-sonnet-4-5"),
        chat_fn=lambda messages, model: genapi.chat(messages, model=model),
        on_log=lambda msg: queue.log(job_id, msg),
    )

    queue.set_progress(job_id, 80, "ai_analysis")
    execute("DELETE FROM candidates WHERE video_id=? AND origin='ai' AND status='candidate'", (video_id,))

    # Одобренные и отрендеренные моменты не удаляются, поэтому новые кандидаты
    # сверяются и с ними: иначе один и тот же фрагмент появится дважды.
    kept = [
        {"start": r["start"], "end": r["end"]}
        for r in query("SELECT start, end FROM candidates WHERE video_id=?", (video_id,))
    ]
    fresh = []
    for moment in moments:
        overlaps = False
        for existing in kept:
            overlap = min(moment["end"], existing["end"]) - max(moment["start"], existing["start"])
            shorter = min(moment["end"] - moment["start"], existing["end"] - existing["start"])
            if overlap > 0 and shorter > 0 and overlap / shorter >= 0.5:
                overlaps = True
                break
        if overlaps:
            queue.log(job_id, f"пропущен дубль уже сохранённого момента на {moment['start']:.0f} с")
            continue
        fresh.append(moment)
    moments = fresh

    for moment in moments:
        insert(
            "INSERT INTO candidates(video_id, start, end, title, category, hook_score, "
            "retention_score, context_score, emotion_score, ending_score, total_score, "
            "ai_reason, transcript_text, status, origin) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate', 'ai')",
            (
                video_id, moment["start"], moment["end"], moment["title"], moment["category"],
                moment["hook_score"], moment["retention_score"], moment["context_score"],
                moment["emotion_score"], moment["ending_score"], moment["total_score"],
                moment["ai_reason"], moment["transcript_text"],
            ),
        )

    queue.log(job_id, f"отобрано моментов: {len(moments)}")
    return len(moments)


def run_action_search(video_id: int, job_id: int) -> int:
    """Ищет боевые и другие бессловесные сцены — их не видно по транскрипции."""
    settings = get_settings()
    if settings.get("find_action", "1") in ("0", "", "None"):
        return 0

    video = _video(video_id)
    audio_path = Path(video["audio_path"] or "")
    if not audio_path.exists():
        queue.log(job_id, "нет аудио — поиск экшена пропущен", "error")
        return 0

    scenes = [r["end"] for r in query("SELECT end FROM scenes WHERE video_id=? ORDER BY start", (video_id,))]
    segments = [
        dict(r)
        for r in query(
            "SELECT start, end, text, speaker FROM segments WHERE video_id=? ORDER BY start",
            (video_id,),
        )
    ]
    if not scenes:
        return 0

    queue.set_progress(job_id, 20, "ai_analysis")
    queue.log(job_id, "ищем экшен-сцены по монтажу и звуку")

    profile = action.loudness_profile(
        audio_path, should_cancel=lambda: queue.is_cancelled(job_id)
    )
    duration = float(video["duration"] or 0)

    windows = action.find_action_windows(
        scenes,
        profile,
        segments,
        duration=duration,
        min_duration=float(settings.get("min_duration", 20)),
        max_duration=float(settings.get("max_duration", 90)),
    )

    skip = float(settings.get("skip_service_seconds", 180))
    windows = [
        w for w in windows
        if not action.is_service_fragment(w, duration, segments, skip_intro=skip, skip_outro=skip)
    ]
    if not windows:
        queue.log(job_id, "экшен-сцены не найдены")
        return 0

    pad_start = float(settings.get("pad_start", 0.3))
    pad_end = float(settings.get("pad_end", 0.5))
    for window in windows:
        window["start"], window["end"] = action.snap_action_bounds(
            window, scenes, segments, pad_start, pad_end,
            max_duration=float(settings.get('max_duration', 90)),
        )

    windows = windows[:8]
    queue.log(job_id, f"отрезков-кандидатов на экшен: {len(windows)}")

    moments = analysis.evaluate_action_windows(
        windows=windows,
        segments=segments,
        settings=settings,
        model=settings.get("llm_model", "claude-sonnet-4-5"),
        chat_fn=lambda messages, model: genapi.chat(messages, model=model),
        on_log=lambda msg: queue.log(job_id, msg),
    )

    existing = [
        {"start": r["start"], "end": r["end"]}
        for r in query("SELECT start, end FROM candidates WHERE video_id=?", (video_id,))
    ]
    saved = 0
    for moment in moments:
        duplicate = False
        for other in existing:
            overlap = min(moment["end"], other["end"]) - max(moment["start"], other["start"])
            shorter = min(moment["end"] - moment["start"], other["end"] - other["start"])
            if overlap > 0 and shorter > 0 and overlap / shorter >= 0.5:
                duplicate = True
                break
        if duplicate:
            continue
        insert(
            "INSERT INTO candidates(video_id, start, end, title, category, hook_score, "
            "retention_score, context_score, emotion_score, ending_score, total_score, "
            "ai_reason, transcript_text, status, origin) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate', 'action')",
            (
                video_id, moment["start"], moment["end"], moment["title"], moment["category"],
                moment["hook_score"], moment["retention_score"], moment["context_score"],
                moment["emotion_score"], moment["ending_score"], moment["total_score"],
                moment["ai_reason"], moment["transcript_text"],
            ),
        )
        existing.append({"start": moment["start"], "end": moment["end"]})
        saved += 1

    queue.log(job_id, f"добавлено экшен-моментов: {saved}")
    return saved


@register("find_action")
def handle_find_action(job: dict[str, Any]) -> None:
    """Поиск экшена отдельной задачей — по уже готовой транскрипции и сценам."""
    video_id = job["video_id"]
    count = run_action_search(video_id, job["id"])
    if count:
        run_previews(video_id, job["id"])
    queue.set_progress(job["id"], 100, "ai_analysis")


@register("find_moments")
def handle_find_moments(job: dict[str, Any]) -> None:
    """Поиск моментов по уже готовой транскрипции — без повторной оплаты распознавания."""
    video_id = job["video_id"]
    job_id = job["id"]
    try:
        if not query_one("SELECT 1 FROM scenes WHERE video_id=? LIMIT 1", (video_id,)):
            run_scene_detection(video_id, job_id)
        run_ai_analysis(video_id, job_id)
        run_previews(video_id, job_id)
        _set_status(video_id, "completed")
    except Exception as exc:
        _set_status(video_id, "transcribed", str(exc)[:1000])
        raise


def _banner_for(candidate: dict[str, Any], settings: dict[str, str]) -> Path | None:
    banner_id = candidate.get("banner_id") or settings.get("banner_id") or ""
    if not banner_id:
        return None
    row = query_one("SELECT path FROM banners WHERE id=?", (int(banner_id),))
    if row is None:
        return None
    path = Path(row["path"])
    return path if path.exists() else None


def _build_request(candidate: dict[str, Any], video: dict[str, Any], kind: str) -> render.RenderRequest:
    """Собирает параметры рендера: настройки шортса важнее общих настроек."""
    settings = get_settings()

    def pick(key: str, default: str) -> str:
        value = candidate.get(key)
        if value in (None, ""):
            value = settings.get(key, default)
        return str(value)

    subtitles_on = pick("subtitles_enabled", "1") not in ("0", "", "None")
    words: list[dict[str, Any]] = []
    if subtitles_on:
        words = [
            dict(r)
            for r in query(
                "SELECT start, end, text FROM words WHERE video_id=? AND start >= ? AND end <= ? "
                "ORDER BY start",
                (video["id"], candidate["start"], candidate["end"]),
            )
        ]

    outro_on = pick("outro_enabled", "0") not in ("0", "", "None")
    outro_text = candidate.get("outro_text") or settings.get("outro_text", "")

    target_dir = PREVIEWS_DIR if kind == "preview" else RENDERS_DIR
    suffix = "preview" if kind == "preview" else "final"
    target = target_dir / f"short_{candidate['id']}_{suffix}.mp4"

    profile = "preview" if kind == "preview" else settings.get("quality_profile", "high")

    return render.RenderRequest(
        source=Path(video["storage_path"]),
        target=target,
        start=float(candidate["start"]),
        end=float(candidate["end"]),
        profile=profile,
        crop_mode=pick("crop_mode", "smart"),
        subtitle_words=words,
        subtitle_style=pick("subtitle_style", "dynamic"),
        banner_path=_banner_for(candidate, settings),
        banner_mode=settings.get("banner_mode", "separate_top"),
        banner_height_percent=float(settings.get("banner_height_percent", 18)),
        banner_opacity=float(settings.get("banner_opacity", 100)),
        outro_text=outro_text if outro_on else "",
        outro_duration=float(settings.get("outro_duration", 3)),
        outro_font_size=int(float(settings.get("outro_font_size", 64))),
        outro_bg_opacity=float(settings.get("outro_bg_opacity", 60)),
        outro_position=settings.get("outro_position", "auto"),
        work_dir=TMP_DIR,
    )


def run_render(candidate_id: int, job_id: int, kind: str, report_progress: bool = True) -> Path:
    """Рендерит один шортс: дешёвое превью или финал в полном качестве."""
    candidate = query_one("SELECT * FROM candidates WHERE id=?", (candidate_id,))
    if candidate is None:
        raise RuntimeError("Момент не найден")
    candidate = dict(candidate)

    video = _video(candidate["video_id"])
    if not video["storage_path"] or not Path(video["storage_path"]).exists():
        raise RuntimeError("Исходное видео удалено — рендер невозможен")

    request = _build_request(candidate, video, kind)
    if report_progress:
        queue.set_progress(job_id, 0, "rendering")
    queue.log(job_id, f"{'превью' if kind == 'preview' else 'финал'}: {candidate['title'] or candidate_id}")

    render_id = insert(
        "INSERT INTO renders(candidate_id, kind, resolution, crf, preset, crop_mode, "
        "subtitle_style, file_path, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running')",
        (
            candidate_id, kind,
            f"{render.PROFILES[request.profile]['width']}x{render.PROFILES[request.profile]['height']}",
            render.PROFILES[request.profile]["crf"], render.PROFILES[request.profile]["preset"],
            request.crop_mode, request.subtitle_style, str(request.target),
        ),
    )

    try:
        render.render_clip(
            request,
            on_progress=(
                (lambda frac: queue.set_progress(job_id, int(frac * 100), "rendering"))
                if report_progress
                else None
            ),
            on_log=lambda msg: queue.log(job_id, msg),
            should_cancel=lambda: queue.is_cancelled(job_id),
        )
    except Exception as exc:
        execute("UPDATE renders SET status='failed', error=? WHERE id=?", (str(exc)[:2000], render_id))
        execute("UPDATE candidates SET error=? WHERE id=?", (str(exc)[:1000], candidate_id))
        raise

    execute("UPDATE renders SET status='done' WHERE id=?", (render_id,))
    column = "preview_path" if kind == "preview" else "render_path"
    new_status = candidate["status"] if kind == "preview" else "ready"
    execute(
        f"UPDATE candidates SET {column}=?, error=NULL, status=?, updated_at=datetime('now') WHERE id=?",
        (str(request.target), new_status, candidate_id),
    )
    size_mb = request.target.stat().st_size / 1024**2
    queue.log(job_id, f"файл {request.target.name}: {size_mb:.1f} МБ")
    return request.target


@register("render_preview")
def handle_render_preview(job: dict[str, Any]) -> None:
    run_render(int(job["candidate_id"]), job["id"], "preview")
    queue.set_progress(job["id"], 100, "rendering")


@register("render_final")
def handle_render_final(job: dict[str, Any]) -> None:
    candidate_id = int(job["candidate_id"])
    execute("UPDATE candidates SET status='rendering' WHERE id=?", (candidate_id,))
    try:
        run_render(candidate_id, job["id"], "final")
    except Exception:
        execute("UPDATE candidates SET status='approved' WHERE id=?", (candidate_id,))
        raise
    queue.set_progress(job["id"], 100, "rendering")


def run_previews(video_id: int, job_id: int) -> int:
    """Делает превью для всех найденных моментов."""
    candidates = query(
        "SELECT id FROM candidates WHERE video_id=? AND status='candidate' AND preview_path IS NULL "
        "ORDER BY start",
        (video_id,),
    )
    if not candidates:
        return 0

    _set_status(video_id, "clips_generation")
    total = len(candidates)
    for i, row in enumerate(candidates):
        if queue.is_cancelled(job_id):
            break
        queue.set_progress(job_id, int(i / total * 100), "clips_generation")
        try:
            run_render(int(row["id"]), job_id, "preview", report_progress=False)
        except Exception as exc:
            # Один сбойный фрагмент не должен останавливать остальные.
            queue.log(job_id, f"превью для момента {row['id']} не создано: {exc}", "error")
    queue.set_progress(job_id, 100, "clips_generation")
    return total


@register("render_previews")
def handle_render_previews(job: dict[str, Any]) -> None:
    video_id = job["video_id"]
    count = run_previews(video_id, job["id"])
    queue.log(job["id"], f"обработано моментов: {count}")
    _set_status(video_id, "completed")


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

        # Склейки не зависят от транскрипции: если они уже посчитаны,
        # повторный проход по всей серии — впустую потраченные минуты.
        if query_one("SELECT 1 FROM scenes WHERE video_id=? LIMIT 1", (video_id,)):
            queue.log(job_id, "склейки уже найдены, пропускаем поиск сцен")
        else:
            run_scene_detection(video_id, job_id)
        if queue.is_cancelled(job_id):
            _set_status(video_id, "transcribed")
            return

        run_ai_analysis(video_id, job_id)
        if queue.is_cancelled(job_id):
            _set_status(video_id, "analyzed")
            return

        try:
            run_action_search(video_id, job_id)
        except Exception as exc:
            # Экшен — дополнение к основному поиску: его сбой не должен
            # обесценивать уже найденные диалоговые моменты.
            queue.log(job_id, f"поиск экшен-сцен не выполнен: {exc}", "error")

        run_previews(video_id, job_id)
        _set_status(video_id, "completed")
    except Exception as exc:
        _set_status(video_id, "failed", str(exc)[:1000])
        raise
