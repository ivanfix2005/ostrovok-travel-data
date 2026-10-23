import asyncio
import json
from datetime import datetime
from pathlib import Path

import httpx

from ..errors import DataError
from ..models import Candidate, ProviderResult, warning
from ..utils import minor_units, number, stable_id, text, timestamp

MEALS = {
    "breakfast": "BB",
    "half-board": "HB",
    "full-board": "FB",
    "all-inclusive": "AI",
    "ultra-all-inclusive": "AI",
    "room-only": "RO",
}
# 'nomeal' is deliberately excluded: provider documentation also uses it for unspecified meals.


class OstrovokHotels:
    name = "ostrovok_b2b"

    def __init__(self, settings, network):
        self.settings, self.network = settings, network
        self.auth = httpx.BasicAuth(settings.ostrovok_key_id, settings.ostrovok_api_key)

    async def api(self, method, body, deadline):
        result = await self.network.json(
            "POST",
            self.settings.ostrovok_base_url + method,
            deadline=deadline,
            auth=self.auth,
            headers={"User-Agent": "OdinZaprosHackathon-TravelData/1.0"},
            json=body,
        )
        if result.get("status") != "ok":
            error = result.get("error")
            if error in ("invalid_auth", "invalid_key", "access_denied"):
                raise DataError("SOURCE_AUTH_FAILED", "Островок отклонил доступ")
            if error in ("limit_exceeded", "rate_limit"):
                raise DataError("SOURCE_RATE_LIMITED", "Лимит Островок API", True)
            raise DataError("SOURCE_UNAVAILABLE", "Островок не подтвердил успешный ответ", True)
        if not isinstance(result.get("data"), dict):
            raise DataError("SOURCE_SCHEMA_CHANGED", "В ответе Островка отсутствует объект data")
        return result["data"]

    async def fetch(self, args, deadline):
        if not self.settings.ostrovok_key_id or not self.settings.ostrovok_api_key:
            raise DataError("ACCESS_NOT_CONFIGURED", "Не заданы OSTROVOK_KEY_ID и OSTROVOK_API_KEY")
        guests = args["guests"]
        if guests["adults"] > 6 or len(guests["children_ages"]) > 4:
            raise DataError(
                "UNSUPPORTED_REQUEST",
                "Островок: один номер поддерживает до 6 взрослых и 4 детей",
                field="guests",
            )
        nights = (datetime.fromisoformat(args["check_out"]) - datetime.fromisoformat(args["check_in"])).days
        if nights > 30:
            raise DataError(
                "UNSUPPORTED_REQUEST", "Островок: поиск не более чем на 30 ночей", field="check_out"
            )
        regions = await self.api(
            "search/multicomplete/", {"query": args["location"]["city"], "language": "ru"}, deadline
        )
        if not isinstance(regions.get("regions"), list):
            raise DataError("SOURCE_SCHEMA_CHANGED", "Отсутствует список регионов Островка")
        name = args["location"]["city"].strip().casefold().replace("ё", "е")
        matches = [
            row
            for row in regions["regions"]
            if isinstance(row, dict)
            and row.get("country_code") == args["location"]["country_code"]
            and row.get("type") == "City"
            and row.get("name", "").strip().casefold().replace("ё", "е") == name
        ]
        ids = {row.get("id") for row in matches if isinstance(row.get("id"), int)}
        if len(ids) != 1:
            raise DataError(
                "AMBIGUOUS_LOCATION", "Город не определён однозначно в Островке", field="location"
            )
        body = {
            "checkin": args["check_in"],
            "checkout": args["check_out"],
            "currency": "RUB",
            "language": "ru",
            "guests": [{"adults": guests["adults"], "children": guests["children_ages"]}],
            "region_id": next(iter(ids)),
            "hotels_limit": 10,
            "timeout": 5,
        }
        payload = await self.api("search/serp/region/", body, deadline)
        if not isinstance(payload.get("hotels"), list):
            raise DataError("SOURCE_SCHEMA_CHANGED", "Отсутствует hotels в ответе Островка")
        result = ProviderResult(
            warnings=[
                warning("SEARCH_QUOTE", "Поисковый тариф требует перепроверки перед бронированием"),
                warning("FEES_NOT_CONFIRMED", "Полнота обязательных доплат требует подтверждения"),
            ]
        )
        total = payload.get("total_hotels")
        result.has_more = isinstance(total, int) and total > len(payload["hotels"])
        catalogue = {}
        if Path(self.settings.ostrovok_hotels_file).is_file():
            try:
                catalogue = json.loads(Path(self.settings.ostrovok_hotels_file).read_text(encoding="utf-8"))
                if not isinstance(catalogue, dict):
                    raise ValueError()
            except (ValueError, OSError):
                raise DataError("SOURCE_SCHEMA_CHANGED", "Некорректный импорт контента отелей") from None

        async def enrich(hotel):
            identity = str(hotel.get("hid") or hotel.get("id") or "")
            if identity in catalogue:
                return identity, catalogue[identity]
            identifier = {"hid": hotel["hid"]} if hotel.get("hid") else {"id": hotel["id"]}
            try:
                data = await self.api("hotel/info/", {**identifier, "language": "ru"}, deadline)
                return identity, data
            except DataError as error:
                return identity, error

        # A fixed candidate page keeps enrichment finite; the host semaphore caps parallelism at two.
        hotels = payload["hotels"][:10]
        valid_hotels = [h for h in hotels if isinstance(h, dict) and (h.get("hid") or h.get("id"))]
        metadata = dict(await asyncio.gather(*(enrich(hotel) for hotel in valid_hotels)))
        bad = len(hotels) - len(valid_hotels)
        content_errors = []
        for hotel in valid_hotels:
            identity = str(hotel.get("hid") or hotel.get("id"))
            info = metadata[identity]
            if isinstance(info, DataError):
                content_errors.append(info)
                result.warnings.append(warning(info.code, "Не удалось получить контент части отелей"))
            if not isinstance(info, dict) or not info.get("name"):
                bad += 1
                result.warnings.append(
                    warning("HOTEL_CONTENT_UNAVAILABLE", "Пропущен отель без подтверждённого названия")
                )
                continue
            rates = hotel.get("rates")
            if not isinstance(rates, list):
                bad += 1
                continue
            if len(rates) > 100:
                result.has_more = True
                result.incomplete = True
                result.warnings.append(warning("SOURCE_LIMIT", "Количество разбираемых тарифов ограничено"))
            for rate in rates[:100]:
                if isinstance(rate, dict) and rate.get("is_package") is True:
                    result.warnings.append(
                        warning("PACKAGE_ONLY_RATE", "Тариф только для пакета исключён из поиска проживания")
                    )
                    continue
                try:
                    result.candidates.append(self.normalize(args, identity, info, rate))
                except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
                    bad += 1
        if bad:
            result.incomplete = True
            result.warnings.append(
                warning("INVALID_RECORDS", f"Не удалось получить или разобрать записи: {bad}")
            )
        if bad and not result.candidates:
            if content_errors:
                raise content_errors[0]
            raise DataError("SOURCE_SCHEMA_CHANGED", "Не удалось получить пригодные предложения отелей")
        return result

    def normalize(self, args, identity, info, rate):
        if not isinstance(rate, dict):
            raise ValueError()
        payment_types = rate.get("payment_options", {}).get("payment_types", [])
        if not isinstance(payment_types, list):
            raise ValueError()
        payments = [
            p
            for p in payment_types
            if isinstance(p, dict)
            and p.get("show_currency_code") == "RUB"
            and minor_units(p.get("show_amount")) is not None
        ]
        payment = min(payments, key=lambda p: minor_units(p["show_amount"])) if payments else None
        price = None
        cancellation, free = None, None
        if payment:
            taxes = (payment.get("tax_data") or {}).get("taxes", [])
            excluded = any(isinstance(t, dict) and t.get("included_by_supplier") is False for t in taxes)
            price = {
                "amount_minor": minor_units(payment["show_amount"]),
                "currency": "RUB",
                "basis": "requested_guests_total",
                "includes_mandatory_fees": False if excluded else None,
            }
            penalties = payment.get("cancellation_penalties")
            if isinstance(penalties, dict) and "free_cancellation_before" in penalties:
                raw = penalties["free_cancellation_before"]
                deadline = timestamp(raw, documented_utc=True)
                if deadline:
                    cancellation = {
                        "free_until": deadline,
                        "summary": "Бесплатная отмена до указанного срока (UTC)",
                    }
                    free = True
                elif raw is None:
                    free = False
                    cancellation = {
                        "free_until": None,
                        "summary": "Бесплатная отмена по тарифу не предусмотрена",
                    }
        meal_data = rate.get("meal_data") or {}
        meal = MEALS.get(meal_data.get("value", rate.get("meal")))
        if args["guests"]["children_ages"] and meal_data.get("no_child_meal"):
            meal = None
        coordinates = None
        if info.get("latitude") is not None and info.get("longitude") is not None:
            coordinates = {
                "lat": number(info["latitude"], minimum=-90, maximum=90),
                "lon": number(info["longitude"], minimum=-180, maximum=180),
            }
        # Free-text policies and generic amenities do not confirm acceptance of this traveller's pet.
        offer_id = text(rate.get("search_hash") or rate.get("match_hash"), 300)
        item = {
            "id": stable_id("ostrovok_offer", identity, offer_id or rate, args),
            "hotel_name": text(info["name"], 300),
            "offer_id": offer_id,
            "room_name": text(rate.get("room_name"), 300),
            "address": text(info.get("address")),
            "coordinates": coordinates,
            "check_in": args["check_in"],
            "check_out": args["check_out"],
            "guests": args["guests"],
            "meal": meal,
            "total_price": price,
            "cancellation": cancellation,
            "pets_allowed": None,
            "match_status": "requires_confirmation",
            "checks": [],
            "source_url": None,
        }
        return Candidate(
            item,
            {
                "free_cancellation_available": free,
                "force_confirmation": bool(rate.get("is_package")) or rate.get("any_residency") is not True,
            },
        )
