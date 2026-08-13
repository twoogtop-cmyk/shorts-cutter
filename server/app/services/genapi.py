"""Клиент gen-api.ru: распознавание речи и текстовые модели.

Особенности сервиса, проверенные на живом API:
  * задача создаётся POST-ом на /networks/{network}, ответ — request_id;
  * результат забирается опросом /request/get/{request_id};
  * аудио передаётся multipart-полем `audio_url` — публичный URL не нужен
    (ссылки без HTTPS сервис отклоняет, поэтому шлём файл напрямую);
  * текстовые модели доступны по OpenAI-совместимому пути /v1/chat/completions.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from ..config import (
    GENAPI_BASE,
    GENAPI_PROXY,
    GENAPI_TOKEN,
    LLM_MODEL,
    STT_MODEL_VERSION,
    STT_NETWORK,
)


class GenApiError(RuntimeError):
    pass


def _headers(json_body: bool = True) -> dict[str, str]:
    if not GENAPI_TOKEN:
        raise GenApiError("Не задан GENAPI_TOKEN")
    headers = {"Authorization": f"Bearer {GENAPI_TOKEN}", "Accept": "application/json"}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def get_balance() -> float:
    with httpx.Client(timeout=20) as client:
        resp = client.get(f"{GENAPI_BASE}/user", headers=_headers())
        resp.raise_for_status()
        return float(resp.json().get("balance", 0))


def _wait_result(
    request_id: int,
    timeout: float = 3600,
    poll_interval: float = 5.0,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    with httpx.Client(timeout=30) as client:
        while time.time() < deadline:
            if should_cancel and should_cancel():
                raise GenApiError("Задача отменена пользователем")
            time.sleep(poll_interval)
            resp = client.get(f"{GENAPI_BASE}/request/get/{request_id}", headers=_headers())
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status")
            if status == "success":
                return data
            if status in ("error", "failed"):
                raise GenApiError(f"gen-api вернул ошибку: {data.get('result') or data}")
    raise GenApiError("Превышено время ожидания ответа gen-api")


def transcribe_file(
    audio_path: Path,
    language: str = "rus",
    diarize: bool = True,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Распознаёт один аудиофайл. Возвращает full_response модели."""
    with httpx.Client(timeout=httpx.Timeout(600, connect=30)) as client:
        with audio_path.open("rb") as fh:
            files = {"audio_url": (audio_path.name, fh, "audio/mpeg")}
            data = {
                "model": STT_MODEL_VERSION,
                "language": language,
                "diarize": "true" if diarize else "false",
            }
            resp = client.post(
                f"{GENAPI_BASE}/networks/{STT_NETWORK}",
                headers=_headers(json_body=False),
                files=files,
                data=data,
            )
        if resp.status_code >= 400:
            raise GenApiError(f"STT: HTTP {resp.status_code} {resp.text[:300]}")
        created = resp.json()

    request_id = created.get("request_id")
    if not request_id:
        raise GenApiError(f"STT: не получен request_id ({created})")

    result = _wait_result(request_id, should_cancel=should_cancel)
    full = result.get("full_response")
    if isinstance(full, str):
        full = json.loads(full)
    if not isinstance(full, dict):
        raise GenApiError(f"STT: неожиданный формат ответа: {str(result)[:300]}")
    full["_cost"] = result.get("cost")
    return full


def chat(
    messages: list[dict[str, str]],
    model: str | None = None,
    # Сервис резервирует деньги по максимуму ответа, поэтому завышенный лимит
    # блокирует запрос при живом балансе. 6000 токенов хватает на ~25 моментов.
    max_tokens: int = 6000,
    temperature: float = 0.3,
    timeout: float = 600,
) -> str:
    """Запрос к текстовой модели через OpenAI-совместимый эндпоинт."""
    body = {
        "model": model or LLM_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=30)) as client:
        resp = client.post(
            f"{GENAPI_PROXY}/chat/completions", headers=_headers(), json=body
        )
        if resp.status_code == 402:
            raise GenApiError(
                "Недостаточно средств на счёте gen-api для анализа. "
                "Пополните баланс — транскрипция уже сохранена, "
                "повторный поиск моментов её не оплачивает."
            )
        if resp.status_code >= 400:
            raise GenApiError(f"LLM: HTTP {resp.status_code} {resp.text[:300]}")
        data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise GenApiError(f"LLM: неожиданный ответ: {str(data)[:300]}") from exc
