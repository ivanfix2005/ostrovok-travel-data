"""Shared per-host concurrency and deadline-bound HTTP, with no credential logging."""

import asyncio
import json
import logging
import re
import threading
from contextlib import asynccontextmanager
from contextvars import ContextVar
from time import monotonic
from urllib.parse import urlsplit

import httpx

from .errors import DataError

_LIMITERS = {}
_LOCK = threading.Lock()
# Conservative default also respects hotel/info's documented 30 calls/minute.
_RECENT_CALLS = {}
RETRY_BUDGET = ContextVar("travel_data_retry_budget", default=None)


class RedactQuerySecrets(logging.Filter):
    """HTTPX logs whole URLs at INFO; 2GIS requires its API key in the query string."""

    pattern = re.compile(r"(?i)([?&](?:key|apikey|api_key|token|access_token)=)[^&\s\"']+")

    def filter(self, record):
        record.msg = self.pattern.sub(r"\1[REDACTED]", record.getMessage())
        record.args = ()
        return True


if not any(isinstance(f, RedactQuerySecrets) for f in logging.getLogger("httpx").filters):
    logging.getLogger("httpx").addFilter(RedactQuerySecrets())


def allow_retry(attempt):
    budget = RETRY_BUDGET.get()
    if attempt != 0 or budget is not None and budget[0] <= 0:
        return False
    if budget is not None:
        budget[0] -= 1
    return True


@asynccontextmanager
async def source_slot(host, path, *, calls_per_minute=None):
    with _LOCK:
        limiter = _LIMITERS.setdefault(host, threading.BoundedSemaphore(2))
    while not limiter.acquire(blocking=False):
        await asyncio.sleep(0.01)
    try:
        limit = 30 if path.endswith("/hotel/info/") else calls_per_minute
        if limit is not None:
            with _LOCK:
                now = monotonic()
                bucket = (host, path)
                calls = [t for t in _RECENT_CALLS.get(bucket, []) if now - t < 60]
                if len(calls) >= limit:
                    raise DataError("SOURCE_RATE_LIMITED", "Локальный минутный лимит источника", True)
                _RECENT_CALLS[bucket] = [*calls, now]
        yield
    finally:
        limiter.release()


class Network:
    def __init__(self, settings, http):
        self.settings = settings
        self.http = http

    async def json(self, method, url, *, deadline, **kwargs):
        parsed = urlsplit(url)
        for attempt in range(2):
            if monotonic() >= deadline:
                raise DataError("SOURCE_TIMEOUT", "Общий лимит времени исчерпан", True)
            try:
                async with source_slot(
                    parsed.netloc,
                    parsed.path,
                    calls_per_minute=(
                        self.settings.dgis_requests_per_minute
                        if url == self.settings.dgis_routing_url
                        else None
                    ),
                ):
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise DataError("SOURCE_TIMEOUT", "Общий лимит времени исчерпан", True)
                    timeout = min(remaining, self.settings.request_timeout_seconds)
                    async with self.http.stream(
                        method, url, timeout=timeout, follow_redirects=False, **kwargs
                    ) as response:
                        status = response.status_code
                        if status in (401, 403):
                            raise DataError("SOURCE_AUTH_FAILED", "Источник отклонил доступ", False)
                        if status == 429:
                            raise DataError("SOURCE_RATE_LIMITED", "Превышен лимит источника", True)
                        if status >= 500:
                            if allow_retry(attempt):
                                await asyncio.sleep(0.15)
                                continue
                            raise DataError("SOURCE_UNAVAILABLE", "Источник временно недоступен", True)
                        if status == 404:
                            raise DataError("OUT_OF_COVERAGE", "Источник не покрывает этот запрос")
                        if status >= 400 or 300 <= status < 400:
                            raise DataError("SOURCE_UNAVAILABLE", "Источник отклонил запрос")
                        payload = bytearray()
                        async for chunk in response.aiter_bytes():
                            payload.extend(chunk)
                            if len(payload) > 8_000_000:
                                raise DataError("SOURCE_SCHEMA_CHANGED", "Ответ источника слишком велик")
                        try:
                            result = json.loads(
                                payload, parse_constant=lambda _: (_ for _ in ()).throw(ValueError())
                            )
                        except (ValueError, UnicodeError):
                            raise DataError(
                                "SOURCE_SCHEMA_CHANGED", "Источник вернул некорректный JSON"
                            ) from None
                        if not isinstance(result, dict):
                            raise DataError("SOURCE_SCHEMA_CHANGED", "Изменилась структура ответа источника")
                        return result
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as error:
                if allow_retry(attempt):
                    await asyncio.sleep(0.15)
                    continue
                if isinstance(error, httpx.TimeoutException):
                    raise DataError("SOURCE_TIMEOUT", "Истёк таймаут источника", True) from None
                raise DataError("SOURCE_UNAVAILABLE", "Сетевая ошибка источника", True) from None
        raise DataError("SOURCE_UNAVAILABLE", "Источник недоступен", True)
