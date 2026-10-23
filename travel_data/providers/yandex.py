import json
from datetime import date
from pathlib import Path

from ..errors import DataError
from ..models import Candidate, ProviderResult, warning
from ..utils import number, safe_url, stable_id, text, timestamp


def read_cities(path):
    if not Path(path).is_file():
        raise DataError("ACCESS_NOT_CONFIGURED", "Импортируйте справочник городов: import-stations")
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError()
        for row in payload:
            if not isinstance(row, dict) or any(
                not isinstance(row.get(key), str) for key in ("city", "country_code", "yandex_code")
            ):
                raise ValueError()
        return payload
    except (ValueError, OSError):
        raise DataError("SOURCE_SCHEMA_CHANGED", "Некорректный локальный справочник городов") from None


def resolve_city(location, cities, field):
    name = location["city"].strip().casefold().replace("ё", "е")
    matches = [
        row
        for row in cities
        if row.get("country_code") == location["country_code"]
        and row.get("city", "").strip().casefold().replace("ё", "е") == name
    ]
    codes = {row.get("yandex_code") for row in matches if row.get("yandex_code")}
    if len(codes) != 1:
        raise DataError("AMBIGUOUS_LOCATION", "Город не найден однозначно в справочнике", field=field)
    return next(iter(codes))


class YandexTransport:
    name = "yandex_rasp"

    def __init__(self, settings, network):
        self.settings, self.network = settings, network

    async def fetch(self, args, deadline):
        if not self.settings.yandex_rasp_api_key:
            raise DataError("ACCESS_NOT_CONFIGURED", "Не задан YANDEX_RASP_API_KEY")
        cities = read_cities(self.settings.yandex_cities_file)
        origin = resolve_city(args["origin"], cities, "origin")
        destination = resolve_city(args["destination"], cities, "destination")
        payload = await self.network.json(
            "GET",
            self.settings.yandex_rasp_base_url + "search/",
            deadline=deadline,
            headers={"Authorization": self.settings.yandex_rasp_api_key},
            params={
                "from": origin,
                "to": destination,
                "date": args["departure_date"],
                "format": "json",
                "lang": "ru_RU",
                "transport_types": args["transport_type"],
                "transfers": str(not args["direct_only"]).lower(),
                "limit": 100,
                "offset": 0,
            },
        )
        if not isinstance(payload.get("segments"), list):
            raise DataError("SOURCE_SCHEMA_CHANGED", "В ответе расписаний отсутствует segments")
        result = ProviderResult(
            warnings=[
                warning("PRICE_NOT_CONFIRMED", "Расписание не подтверждает цену за группу"),
                warning("ATTRIBUTION_REQUIRED", "Источник: Яндекс Расписания"),
            ]
        )
        total = (payload.get("pagination") or {}).get("total", len(payload["segments"]))
        result.has_more = isinstance(total, int) and total > len(payload["segments"])
        if payload.get("interval_segments"):
            result.incomplete = True
            result.warnings.append(
                warning("INTERVAL_SCHEDULE_OMITTED", "Интервальные рейсы без точного времени исключены")
            )
        bad = 0
        for row in payload["segments"]:
            try:
                if not isinstance(row, dict):
                    raise ValueError()
                if args["direct_only"] and row.get("has_transfers"):
                    continue
                legs = row.get("details") if row.get("has_transfers") else [row]
                if not isinstance(legs, list) or not legs:
                    raise ValueError()
                segments, carriers = [], []
                for leg in legs:
                    if leg.get("is_transfer"):
                        continue
                    thread = leg["thread"]
                    if thread["transport_type"] != args["transport_type"]:
                        raise ValueError()
                    dep, arr = timestamp(leg.get("departure")), timestamp(leg.get("arrival"))
                    segments.append(
                        {
                            "origin": {
                                "name": text(leg["from"]["title"], 300),
                                "code": text(leg["from"].get("code"), 80),
                            },
                            "destination": {
                                "name": text(leg["to"]["title"], 300),
                                "code": text(leg["to"].get("code"), 80),
                            },
                            "departure_at": dep,
                            "arrival_at": arr,
                            "number": text(thread.get("number"), 80),
                        }
                    )
                    carrier = text((thread.get("carrier") or {}).get("title"), 300)
                    if carrier:
                        carriers.append(carrier)
                    if not dep or not arr:
                        result.warnings.append(
                            warning("TIME_UNKNOWN", "Некоторые времена или часовые пояса неизвестны")
                        )
                if not segments:
                    raise ValueError()
                if segments[0]["departure_at"] and segments[0]["departure_at"][:10] != args["departure_date"]:
                    raise ValueError()
                advertised = None
                for place in (row.get("tickets_info") or {}).get("places", []):
                    if place.get("currency") != "RUB":
                        continue
                    price = place["price"]
                    cents = number(price["cents"], minimum=0, maximum=99, integer=True)
                    amount = number(price["whole"], minimum=0, integer=True) * 100 + cents
                    if advertised is None or amount < advertised["amount_minor"]:
                        advertised = {"amount_minor": amount, "currency": "RUB", "basis": "unknown"}
                duration = row.get("duration")
                if duration is not None:
                    duration = number(duration, minimum=0, integer=True)
                item = {
                    "id": stable_id("yandex_trip", segments),
                    "transport_type": args["transport_type"],
                    "carrier": text(", ".join(dict.fromkeys(carriers)), 300) if carriers else None,
                    "segments": segments,
                    "duration_s": duration,
                    "total_price": None,
                    "advertised_price": advertised,
                    "price_kind": "cached_quote" if advertised else "schedule_only",
                    "baggage": None,
                    "match_status": "requires_confirmation",
                    "checks": [{"field": "requested_guests_price", "status": "unknown"}],
                    "source_url": "https://rasp.yandex.ru/",
                }
                result.candidates.append(Candidate(item))
            except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
                bad += 1
        if bad:
            result.incomplete = True
            result.warnings.append(warning("INVALID_RECORDS", f"Исключены повреждённые записи: {bad}"))
        if bad and not result.candidates:
            raise DataError("SOURCE_SCHEMA_CHANGED", "Не удалось разобрать ни одного рейса")
        return result


class YandexWeather:
    name = "yandex_weather"

    def __init__(self, settings, network):
        self.settings, self.network = settings, network

    async def fetch(self, args, deadline):
        if not self.settings.yandex_weather_api_key:
            raise DataError("ACCESS_NOT_CONFIGURED", "Не задан YANDEX_WEATHER_API_KEY")
        payload = await self.network.json(
            "GET",
            self.settings.yandex_weather_url,
            deadline=deadline,
            headers={"X-Yandex-Weather-Key": self.settings.yandex_weather_api_key},
            params={**args["location"], "lang": "ru_RU", "limit": 16, "hours": "false", "extra": "false"},
        )
        if not isinstance(payload.get("forecasts"), list):
            raise DataError("SOURCE_SCHEMA_CHANGED", "В ответе погоды отсутствует forecasts")
        result = ProviderResult()
        # now_dt describes response generation, NOT an independently documented model update time.
        source_url = safe_url((payload.get("info") or {}).get("url"))
        bad = 0
        for row in payload["forecasts"]:
            try:
                day = date.fromisoformat(row["date"]).isoformat()
                if not args["start_date"] <= day < args["end_date"]:
                    continue
                parts = row["parts"]
                daytime, night = parts.get("day", {}), parts.get("night", {})
                values = {}
                for prefix, part in (("day", daytime), ("night", night)):
                    for bound in ("min", "max"):
                        raw = part.get(f"temp_{bound}")
                        values[f"{prefix}_temp_{bound}_c"] = None if raw is None else number(raw)
                    lo, hi = values[f"{prefix}_temp_min_c"], values[f"{prefix}_temp_max_c"]
                    if lo is not None and hi is not None and lo > hi:
                        raise ValueError()
                prob = daytime.get("prec_prob")
                prob = None if prob is None else number(prob, minimum=0, maximum=100) / 100
                water = daytime.get("temp_water")
                water = None if water is None else number(water)
                item = {
                    "date": day,
                    "kind": "forecast",
                    **values,
                    "water_temp_c": water,
                    "precipitation_probability": prob,
                    "source_url": source_url,
                }
                result.candidates.append(Candidate(item))
                if any(value is None for value in values.values()):
                    result.incomplete = True
                    result.warnings.append(
                        warning("TEMPERATURE_UNKNOWN", "Часть температур в прогнозе отсутствует")
                    )
            except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
                bad += 1
        if bad:
            result.incomplete = True
            result.warnings.append(warning("INVALID_RECORDS", f"Исключены повреждённые дни: {bad}"))
        if bad and not result.candidates:
            raise DataError("SOURCE_SCHEMA_CHANGED", "Не удалось разобрать прогноз источника")
        return result
