"""SQLite-хранилище: подключение, схема, миграции."""

import sqlite3
import threading
from typing import Any, Iterable

from .config import DB_PATH, ensure_dirs

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS videos (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    original_filename  TEXT NOT NULL,
    storage_path       TEXT,
    audio_path         TEXT,
    status             TEXT NOT NULL DEFAULT 'uploading',
    size_bytes         INTEGER DEFAULT 0,
    duration           REAL,
    width              INTEGER,
    height             INTEGER,
    fps                REAL,
    video_codec        TEXT,
    audio_track_index  INTEGER DEFAULT 0,
    audio_tracks_json  TEXT,
    probe_json         TEXT,
    error              TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS upload_sessions (
    id            TEXT PRIMARY KEY,
    video_id      INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    total_size    INTEGER NOT NULL,
    received      INTEGER NOT NULL DEFAULT 0,
    temp_path     TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     INTEGER REFERENCES videos(id) ON DELETE CASCADE,
    candidate_id INTEGER,
    type         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'queued',
    stage        TEXT,
    progress     INTEGER NOT NULL DEFAULT 0,
    payload      TEXT,
    error        TEXT,
    pid          INTEGER,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    started_at   TEXT,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, id);

CREATE TABLE IF NOT EXISTS job_logs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id  INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    ts      TEXT NOT NULL DEFAULT (datetime('now')),
    level   TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_logs_job ON job_logs(job_id, id);

CREATE TABLE IF NOT EXISTS segments (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    idx      INTEGER NOT NULL,
    start    REAL NOT NULL,
    end      REAL NOT NULL,
    text     TEXT NOT NULL,
    speaker  TEXT
);
CREATE INDEX IF NOT EXISTS idx_segments_video ON segments(video_id, start);

CREATE TABLE IF NOT EXISTS words (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id   INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    segment_id INTEGER REFERENCES segments(id) ON DELETE CASCADE,
    start      REAL NOT NULL,
    end        REAL NOT NULL,
    text       TEXT NOT NULL,
    speaker    TEXT
);
CREATE INDEX IF NOT EXISTS idx_words_video ON words(video_id, start);

CREATE TABLE IF NOT EXISTS scenes (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    start    REAL NOT NULL,
    end      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scenes_video ON scenes(video_id, start);

CREATE TABLE IF NOT EXISTS candidates (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id          INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    start             REAL NOT NULL,
    end               REAL NOT NULL,
    title             TEXT,
    category          TEXT,
    hook_score        INTEGER,
    retention_score   INTEGER,
    context_score     INTEGER,
    emotion_score     INTEGER,
    ending_score      INTEGER,
    total_score       INTEGER,
    ai_reason         TEXT,
    transcript_text   TEXT,
    status            TEXT NOT NULL DEFAULT 'candidate',
    origin            TEXT NOT NULL DEFAULT 'ai',
    crop_mode         TEXT,
    banner_id         INTEGER REFERENCES banners(id) ON DELETE SET NULL,
    subtitles_enabled INTEGER,
    subtitle_style    TEXT,
    preview_path      TEXT,
    render_path       TEXT,
    error             TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_candidates_video ON candidates(video_id, total_score DESC);

CREATE TABLE IF NOT EXISTS renders (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id   INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL,
    resolution     TEXT,
    crf            INTEGER,
    preset         TEXT,
    crop_mode      TEXT,
    banner_id      INTEGER,
    subtitle_style TEXT,
    file_path      TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',
    error          TEXT,
    ffmpeg_log     TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS banners (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    filename   TEXT NOT NULL,
    path       TEXT NOT NULL,
    width      INTEGER,
    height     INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts
USING fts5(text, content='segments', content_rowid='id', tokenize='unicode61');

CREATE TRIGGER IF NOT EXISTS segments_ai AFTER INSERT ON segments BEGIN
    INSERT INTO segments_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS segments_ad AFTER DELETE ON segments BEGIN
    INSERT INTO segments_fts(segments_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS segments_au AFTER UPDATE ON segments BEGIN
    INSERT INTO segments_fts(segments_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO segments_fts(rowid, text) VALUES (new.id, new.text);
END;
"""

DEFAULT_SETTINGS = {
    "language": "ru",
    "min_duration": "20",
    "target_min_duration": "30",
    "target_max_duration": "55",
    "max_duration": "90",
    "min_score": "70",
    "max_shorts": "auto",
    "crop_mode": "smart",
    "subtitles_enabled": "1",
    "subtitle_style": "dynamic",
    "banner_id": "",
    "banner_mode": "separate_top",
    "banner_height_percent": "18",
    "banner_opacity": "100",
    "banner_duration": "full",
    "pad_start": "0.3",
    "pad_end": "0.5",
    "quality_profile": "high",
    "llm_model": "claude-sonnet-4-5",
    # Финальная плашка: компактный текст по центру в последние секунды шортса.
    "outro_enabled": "0",
    "outro_text": "",
    "outro_duration": "3",
    "outro_font_size": "64",
    "outro_bg_opacity": "60",
    "outro_position": "auto",
}

# Колонки, добавленные после первого выпуска схемы.
MIGRATIONS = [
    ("candidates", "outro_text", "TEXT"),
    ("candidates", "outro_enabled", "INTEGER"),
]


def get_conn() -> sqlite3.Connection:
    """Соединение на поток: SQLite-объекты нельзя делить между потоками."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        ensure_dirs()
        conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)
    try:
        conn.executescript(FTS_SCHEMA)
    except sqlite3.OperationalError:
        # Сборка SQLite без FTS5 — поиск по диалогам сделаем через LIKE.
        pass
    for table, column, coltype in MIGRATIONS:
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")

    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value))


def has_fts() -> bool:
    row = get_conn().execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='segments_fts'"
    ).fetchone()
    return row is not None


def query(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return get_conn().execute(sql, tuple(params)).fetchall()


def query_one(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    return get_conn().execute(sql, tuple(params)).fetchone()


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    return get_conn().execute(sql, tuple(params))


def insert(sql: str, params: Iterable[Any] = ()) -> int:
    cur = get_conn().execute(sql, tuple(params))
    return int(cur.lastrowid or 0)


def get_settings() -> dict[str, str]:
    return {r["key"]: r["value"] for r in query("SELECT key, value FROM settings")}


def set_settings(values: dict[str, str]) -> None:
    conn = get_conn()
    for key, value in values.items():
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
