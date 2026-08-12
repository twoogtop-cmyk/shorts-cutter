"""Реестр обработчиков задач.

Отдельный модуль, потому что worker запускается как `python -m app.worker`
и получает имя `__main__`: если бы реестр жил в нём, обработчики
регистрировались бы во второй копии модуля и worker их не увидел.
"""

from __future__ import annotations

from typing import Any, Callable

Handler = Callable[[dict[str, Any]], None]

HANDLERS: dict[str, Handler] = {}


def register(job_type: str) -> Callable[[Handler], Handler]:
    def wrapper(fn: Handler) -> Handler:
        HANDLERS[job_type] = fn
        return fn

    return wrapper
