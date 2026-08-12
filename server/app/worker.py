"""Фоновый обработчик очереди. Запускается отдельным systemd-юнитом."""

from __future__ import annotations

import json
import logging
import signal
import time
from typing import Any

from .config import ensure_dirs
from .db import init_db
from .registry import HANDLERS
from .services import queue

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("worker")

_running = True


def _stop(signum: int, frame: Any) -> None:  # noqa: ARG001
    global _running
    _running = False
    log.info("получен сигнал %s, завершаем цикл", signum)


def run_job(job: dict[str, Any]) -> None:
    job_id = job["id"]
    handler = HANDLERS.get(job["type"])
    if handler is None:
        queue.log(job_id, f"нет обработчика для типа задачи '{job['type']}'", "error")
        queue.finish_job(job_id, "failed", f"Неизвестный тип задачи: {job['type']}")
        return

    queue.log(job_id, f"старт задачи {job['type']}")
    try:
        handler(job)
    except Exception as exc:  # noqa: BLE001 — worker не должен падать целиком
        log.exception("задача %s упала", job_id)
        queue.log(job_id, f"ошибка: {exc}", "error")
        queue.finish_job(job_id, "failed", str(exc)[:2000])
        return

    if queue.is_cancelled(job_id):
        queue.finish_job(job_id, "canceled")
        queue.log(job_id, "задача отменена")
        # Видео не должно остаться в промежуточном статусе после отмены.
        if job.get("video_id"):
            from .db import execute

            execute(
                "UPDATE videos SET status='uploaded', updated_at=datetime('now') "
                "WHERE id=? AND status NOT IN ('failed','completed')",
                (job["video_id"],),
            )
    else:
        queue.finish_job(job_id, "done")
        queue.log(job_id, "задача завершена")


def main() -> None:
    # Импорт здесь, а не наверху: модуль задач импортирует register из этого файла.
    from . import tasks  # noqa: F401

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    ensure_dirs()
    init_db()
    # Задачи, оставшиеся в состоянии running после перезапуска, не переживают
    # рестарт процесса — помечаем их как упавшие, чтобы не висели вечно.
    from .db import execute

    execute(
        "UPDATE jobs SET status='failed', error='Прервано перезапуском сервиса', "
        "finished_at=datetime('now') WHERE status='running'"
    )
    log.info("worker запущен, обработчиков: %s", ", ".join(sorted(HANDLERS)) or "нет")

    while _running:
        job = queue.claim_next_job()
        if job is None:
            time.sleep(2)
            continue
        payload = job.get("payload")
        job["payload"] = json.loads(payload) if payload else {}
        run_job(job)

    log.info("worker остановлен")


if __name__ == "__main__":
    main()
