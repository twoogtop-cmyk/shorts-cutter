"""Конфигурация приложения. Значения берутся из окружения (/etc/shorts-cutter/env)."""

import os
from pathlib import Path

DATA_DIR = Path(os.getenv("SC_DATA_DIR", "/var/lib/shorts-cutter"))

SOURCES_DIR = DATA_DIR / "sources"
AUDIO_DIR = DATA_DIR / "audio"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
PREVIEWS_DIR = DATA_DIR / "previews"
RENDERS_DIR = DATA_DIR / "renders"
BANNERS_DIR = DATA_DIR / "banners"
TMP_DIR = DATA_DIR / "tmp"
LOGS_DIR = DATA_DIR / "logs"

ALL_DIRS = [
    SOURCES_DIR, AUDIO_DIR, TRANSCRIPTS_DIR, PREVIEWS_DIR,
    RENDERS_DIR, BANNERS_DIR, TMP_DIR, LOGS_DIR,
]

DB_PATH = DATA_DIR / "data.db"

GENAPI_TOKEN = os.getenv("GENAPI_TOKEN", "")
GENAPI_BASE = "https://api.gen-api.ru/api/v1"
GENAPI_PROXY = "https://proxy.gen-api.ru/v1"

# Модель распознавания речи: ElevenLabs Scribe v2 — 2 руб/мин, пословные
# таймкоды и диаризация. Аудио отправляется multipart, публичный URL не нужен.
STT_NETWORK = "speech-to-text"
STT_MODEL_VERSION = "v2"

LLM_MODEL = os.getenv("SC_LLM_MODEL", "claude-sonnet-4-5")

FFMPEG = os.getenv("SC_FFMPEG", "ffmpeg")
FFPROBE = os.getenv("SC_FFPROBE", "ffprobe")

ALLOWED_VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm"}
ALLOWED_BANNER_EXT = {".png", ".webp", ".jpg", ".jpeg"}

# Верхняя граница размера исходника. Реальный лимит считается от свободного
# места: под аудио, превью и финальные рендеры серии нужно оставить резерв.
MAX_SOURCE_BYTES = int(os.getenv("SC_MAX_SOURCE_BYTES", str(4 * 1024**3)))
DISK_RESERVE_BYTES = int(os.getenv("SC_DISK_RESERVE_BYTES", str(1024**3)))


def max_upload_bytes() -> int:
    """Сколько можно принять прямо сейчас, с учётом свободного места."""
    import shutil

    free = shutil.disk_usage(DATA_DIR).free
    return max(0, min(MAX_SOURCE_BYTES, free - DISK_RESERVE_BYTES))

# Аудио для распознавания: моно 16 кГц 64 kbps ≈ 28 МБ на час.
AUDIO_BITRATE = "64k"
AUDIO_SAMPLE_RATE = 16000

# Максимальная длительность одного куска аудио, отправляемого в STT (сек).
STT_CHUNK_SECONDS = 15 * 60


def ensure_dirs() -> None:
    for d in ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)
