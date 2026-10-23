import asyncio
import json
from collections import OrderedDict
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import uuid4

import httpx
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from .config import Settings
from .contracts import ITEMS, MAX_ITEMS, TOOLS, VERSION
from .demo import FIXED_NOW, FixtureProvider
from .errors import DataError
from .models import warning
from .network import RETRY_BUDGET, Network
from .providers.dgis import DgisRoute
from .providers.ostrovok import OstrovokHotels
from .providers.yandex import YandexTransport, YandexWeather
from .utils import canonical, ensure_finite, utc_iso
from .validation import assert_response, validate_request

ITEM_VALIDATORS = {
    tool: Draft202012Validator(schema, format_checker=FormatChecker()) for tool, schema in ITEMS.items()
}


def hotel_checks(candidate, args, now):
    item = candidate.item
    price = item["total_price"]
    complete = price is not None and price["includes_mandatory_fees"] is True
    checks = {
        "requested_guests_price": "passed" if price is not None else "unknown",
        "mandatory_fees": "passed" if complete else "unknown",
    }
    if args["max_total_price_minor"] is not None:
        budget = args["max_total_price_minor"]
        checks["max_total_price_minor"] = (
            "unknown"
            if price is None
            else "failed"
            if price["amount_minor"] > budget
            else "passed"
            if complete
            else "unknown"
        )
    if args["meal"] is not None:
        checks["meal"] = (
            "unknown" if item["meal"] is None else "passed" if item["meal"] == args["meal"] else "failed"
        )
    if args["pets_required"]:
        pets = item["pets_allowed"]
        checks["pets_required"] = "unknown" if pets is None else "passed" if pets else "failed"
    if args["free_cancellation_required"]:
        free = candidate.facts.get("free_cancellation_available")
        deadline = (item["cancellation"] or {}).get("free_until")
        if deadline:
            free = datetime.fromisoformat(deadline.replace("Z", "+00:00")) > now
        checks["free_cancellation_required"] = "unknown" if free is None else "passed" if free else "failed"
    item["checks"] = [{"field": field, "status": status} for field, status in checks.items()]
    if "failed" in checks.values():
        return False
    item["match_status"] = (
        "requires_confirmation"
        if "unknown" in checks.values() or candidate.facts.get("force_confirmation")
        else "matches"
    )
    return True


class TravelDataClient:
    """Use `async with TravelDataClient(...)` in an asynchronous backend.

    Providers/HTTP transport and clock can be injected for deterministic offline testing.
    A client belongs to one event loop; create one per application lifecycle.
    """

    def __init__(self, settings=None, *, mode="live", transport=None, providers=None, clock=None):
        if mode not in ("live", "fixture"):
            raise ValueError("mode must be live or fixture")
        self.settings = settings or Settings.from_env()
        self.mode = mode
        self.clock = clock or (lambda: FIXED_NOW if mode == "fixture" else datetime.now(UTC))
        self.http = httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        )
        network = Network(self.settings, self.http)
        self.providers = providers or (
            {tool: FixtureProvider(tool) for tool in TOOLS}
            if mode == "fixture"
            else {
                "search_hotels": OstrovokHotels(self.settings, network),
                "search_transport": YandexTransport(self.settings, network),
                "get_weather": YandexWeather(self.settings, network),
                "get_route": DgisRoute(self.settings, network),
            }
        )
        self.cache = OrderedDict()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.http.aclose()

    async def execute(self, request):
        now = self.clock()
        tool = request.get("tool") if isinstance(request, dict) else None
        if tool not in TOOLS:
            # No valid per-tool envelope can be produced for an unknown tool.
            raise DataError("INVALID_ARGUMENT", "Неизвестный инструмент", field="tool")
        provider = self.providers[tool]
        raw_id = request.get("request_id")
        request_id = (
            raw_id if isinstance(raw_id, str) and 0 < len(raw_id) <= 120 else f"invalid_{uuid4().hex}"
        )
        response = {
            "schema_version": VERSION,
            "request_id": request_id,
            "tool": tool,
            "status": "error",
            "data": {"items": []},
            "meta": {
                "source_name": provider.name,
                "fetched_at": utc_iso(now),
                "source_updated_at": None,
                "cache_hit": False,
                "is_stale": False,
                "is_fixture": self.mode == "fixture",
                "returned_count": 0,
                "has_more": False,
            },
            "warnings": [],
            "error": None,
        }
        if self.mode == "fixture":
            response["warnings"] = [warning("FIXTURE_DATA", "Вымышленные данные для проверки контракта")]
        retry_token = RETRY_BUDGET.set([1])
        try:
            async with asyncio.timeout(self.settings.time_budget_seconds):
                validate_request(request, now, fixture=self.mode == "fixture")
                key = canonical({"tool": tool, "arguments": request["arguments"], "mode": self.mode})
                cached = self.cache.get(key)
                cancellation_valid = True
                if cached and tool == "search_hotels" and request["arguments"]["free_cancellation_required"]:
                    cancellation_valid = all(
                        not (item["cancellation"] or {}).get("free_until")
                        or datetime.fromisoformat(item["cancellation"]["free_until"].replace("Z", "+00:00"))
                        > now
                        for item in cached[1]["data"]["items"]
                    )
                if cached and monotonic() < cached[0] and cancellation_valid:
                    response = deepcopy(cached[1])
                    response["request_id"] = request_id
                    response["meta"]["cache_hit"] = True
                    assert_response(request, response)
                    return response
                self.cache.pop(key, None)
                deadline = monotonic() + self.settings.time_budget_seconds
                result = await provider.fetch(request["arguments"], deadline)
                self.normalize(request, response, result, now)
                assert_response(request, response)
                if (
                    self.settings.cache_allowed
                    and self.settings.cache_ttl_seconds > 0
                    and response["status"] in ("ok", "empty")
                ):
                    self.cache[key] = (monotonic() + self.settings.cache_ttl_seconds, deepcopy(response))
                    while len(self.cache) > 256:
                        self.cache.popitem(last=False)
        except TimeoutError:
            response = self.set_error(
                response, DataError("SOURCE_TIMEOUT", "Общий бюджет 15 секунд исчерпан", True)
            )
        except DataError as error:
            response = self.set_error(response, error)
        except (
            ValidationError,
            AssertionError,
            KeyError,
            TypeError,
            ValueError,
            OSError,
            AttributeError,
            OverflowError,
        ):
            response = self.set_error(
                response, DataError("SOURCE_SCHEMA_CHANGED", "Данные источника нарушают контракт")
            )
        finally:
            RETRY_BUDGET.reset(retry_token)
        return response

    @staticmethod
    def set_error(response, error):
        response["status"], response["error"], response["data"] = "error", error.as_dict(), {"items": []}
        response["meta"]["returned_count"] = 0
        response["meta"]["has_more"] = False
        return response

    def normalize(self, request, response, result, now):
        tool, args = request["tool"], request["arguments"]
        response["warnings"] += result.warnings
        response["meta"]["source_updated_at"] = result.source_updated_at
        response["meta"]["has_more"] = result.has_more
        valid, bad, seen = [], 0, set()
        for original in result.candidates:
            candidate = deepcopy(original)
            item = candidate.item
            try:
                ensure_finite(item)
                ITEM_VALIDATORS[tool].validate(item)
                if tool == "search_hotels":
                    if (
                        item["check_in"] != args["check_in"]
                        or item["check_out"] != args["check_out"]
                        or item["guests"] != args["guests"]
                    ):
                        raise ValueError()
                    if not hotel_checks(candidate, args, now):
                        continue
                    if item["match_status"] == "requires_confirmation":
                        response["warnings"].append(
                            warning(
                                "CONDITIONS_UNCONFIRMED", "Часть условий предложения требует подтверждения"
                            )
                        )
                elif tool == "get_weather":
                    if not args["start_date"] <= item["date"] < args["end_date"]:
                        continue
                    for prefix in ("day", "night"):
                        lo, hi = item[f"{prefix}_temp_min_c"], item[f"{prefix}_temp_max_c"]
                        if lo is not None and hi is not None and lo > hi:
                            raise ValueError()
                elif tool == "search_transport":
                    if (
                        item["transport_type"] != args["transport_type"]
                        or args["direct_only"]
                        and len(item["segments"]) != 1
                    ):
                        continue
                    previous = None
                    for segment in item["segments"]:
                        dep, arr = segment["departure_at"], segment["arrival_at"]
                        if dep and arr:
                            dep, arr = datetime.fromisoformat(dep), datetime.fromisoformat(arr)
                            if arr < dep or previous is not None and dep < previous:
                                raise ValueError()
                            previous = arr
                    first = item["segments"][0]["departure_at"]
                    if first and first[:10] != args["departure_date"]:
                        raise ValueError()
                    price = item["total_price"]
                    confirmed = price is not None and price["includes_mandatory_fees"] is True
                    item["match_status"] = "matches" if confirmed else "requires_confirmation"
                    item["checks"] = [
                        {"field": "requested_guests_price", "status": "passed" if price else "unknown"}
                    ]
                else:
                    if (
                        item["origin"] != args["origin"]
                        or item["destination"] != args["destination"]
                        or item["departure_at"] != args["departure_at"]
                    ):
                        raise ValueError()
                identity = item.get("id", item.get("date"))
                if identity in seen:
                    continue
                seen.add(identity)
                valid.append(item)
            except (ValidationError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
                bad += 1
        if bad:
            result.incomplete = True
            response["warnings"].append(
                warning("INVALID_RECORDS", f"Исключены записи, нарушающие контракт: {bad}")
            )
            if not valid:
                raise DataError("SOURCE_SCHEMA_CHANGED", "Все полученные записи нарушают контракт")
        if tool == "get_weather":
            start, end = datetime.fromisoformat(args["start_date"]), datetime.fromisoformat(args["end_date"])
            missing = [
                (start + timedelta(days=i)).date().isoformat()
                for i in range((end - start).days)
                if (start + timedelta(days=i)).date().isoformat() not in seen
            ]
            if not valid:
                raise DataError("OUT_OF_COVERAGE", "Нет прогноза на запрошенные даты")
            if missing:
                result.incomplete = True
                response["warnings"].append(
                    warning("DATES_NOT_COVERED", "Нет прогноза на даты: " + ", ".join(missing))
                )
            valid.sort(key=lambda item: item["date"])
        elif tool in ("search_hotels", "search_transport"):
            valid.sort(
                key=lambda item: (
                    0
                    if item["total_price"] and item["total_price"]["includes_mandatory_fees"] is True
                    else 1
                    if item["total_price"]
                    else 2,
                    item["total_price"]["amount_minor"] if item["total_price"] else 0,
                    item["id"],
                )
            )
        limit = args.get("limit", MAX_ITEMS[tool])
        if len(valid) > limit:
            response["meta"]["has_more"] = True
        response["data"]["items"] = valid[:limit]
        # Remove repeated trusted warnings, while preserving why a response is partial.
        response["warnings"] = list({canonical(w): w for w in response["warnings"]}.values())[:23]
        response["status"] = "partial" if result.incomplete else "ok" if valid else "empty"
        if result.incomplete and not response["warnings"]:
            response["warnings"].append(warning("INCOMPLETE", "Источник вернул неполные данные"))
        while (
            len(json.dumps(response, ensure_ascii=False, allow_nan=False).encode())
            > self.settings.max_response_bytes
        ):
            if not response["data"]["items"]:
                raise DataError("SOURCE_SCHEMA_CHANGED", "Метаданные ответа превышают ограничение размера")
            response["data"]["items"].pop()
            response["meta"]["has_more"] = True
            response["status"] = "partial"
            if not any(w["code"] == "RESPONSE_TRUNCATED" for w in response["warnings"]):
                response["warnings"].append(
                    warning("RESPONSE_TRUNCATED", "Число записей уменьшено из-за размера ответа")
                )
        response["meta"]["returned_count"] = len(response["data"]["items"])

    async def search_hotels(self, arguments, *, request_id=None):
        return await self.execute(make_request("search_hotels", arguments, request_id))

    async def search_transport(self, arguments, *, request_id=None):
        return await self.execute(make_request("search_transport", arguments, request_id))

    async def get_weather(self, arguments, *, request_id=None):
        return await self.execute(make_request("get_weather", arguments, request_id))

    async def get_route(self, arguments, *, request_id=None):
        return await self.execute(make_request("get_route", arguments, request_id))


def make_request(tool, arguments, request_id=None):
    return {
        "schema_version": VERSION,
        "request_id": request_id or f"req_{uuid4().hex}",
        "tool": tool,
        "arguments": arguments,
    }


def call(request, *, settings=None, mode="live"):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("Use await TravelDataClient.execute() inside an asyncio event loop")

    async def run():
        async with TravelDataClient(settings, mode=mode) as client:
            return await client.execute(request)

    return asyncio.run(run())


def search_hotels(arguments, *, request_id=None, settings=None, mode="live"):
    return call(make_request("search_hotels", arguments, request_id), settings=settings, mode=mode)


def search_transport(arguments, *, request_id=None, settings=None, mode="live"):
    return call(make_request("search_transport", arguments, request_id), settings=settings, mode=mode)


def get_weather(arguments, *, request_id=None, settings=None, mode="live"):
    return call(make_request("get_weather", arguments, request_id), settings=settings, mode=mode)


def get_route(arguments, *, request_id=None, settings=None, mode="live"):
    return call(make_request("get_route", arguments, request_id), settings=settings, mode=mode)
