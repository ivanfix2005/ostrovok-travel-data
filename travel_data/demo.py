"""Deliberately fictitious, fixed examples. Never a fallback for a live source."""

from copy import deepcopy
from datetime import UTC, datetime

from .errors import DataError
from .models import Candidate, ProviderResult, warning

FIXED_NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)
ARGUMENTS = {
    "search_hotels": {
        "location": {"city": "Сочи", "country_code": "RU"},
        "check_in": "2026-11-10",
        "check_out": "2026-11-17",
        "guests": {"adults": 2, "children_ages": [7]},
        "max_total_price_minor": 8000000,
        "meal": "BB",
        "pets_required": True,
        "free_cancellation_required": True,
        "limit": 3,
    },
    "search_transport": {
        "origin": {"city": "Москва", "country_code": "RU"},
        "destination": {"city": "Сочи", "country_code": "RU"},
        "departure_date": "2026-11-10",
        "transport_type": "plane",
        "guests": {"adults": 2, "children_ages": [7]},
        "direct_only": True,
        "limit": 3,
    },
    "get_weather": {
        "location": {"lat": 43.5855, "lon": 39.7231},
        "start_date": "2026-10-25",
        "end_date": "2026-10-27",
    },
    "get_route": {
        "origin": {"lat": 43.4499, "lon": 39.9566},
        "destination": {"lat": 43.5855, "lon": 39.7231},
        "departure_at": None,
        "mode": "car",
    },
}


def fixture_candidates(tool):
    args = deepcopy(ARGUMENTS[tool])
    if tool == "search_hotels":
        return [
            Candidate(
                {
                    "id": "fixture_ru_hotel_001",
                    "hotel_name": "Демо отель",
                    "offer_id": "fixture_offer_001",
                    "room_name": "Семейный номер",
                    "address": "Демонстрационный адрес",
                    "coordinates": {"lat": 43.5855, "lon": 39.7231},
                    "check_in": args["check_in"],
                    "check_out": args["check_out"],
                    "guests": args["guests"],
                    "meal": "BB",
                    "total_price": {
                        "amount_minor": 7000000,
                        "currency": "RUB",
                        "basis": "requested_guests_total",
                        "includes_mandatory_fees": True,
                    },
                    "cancellation": {
                        "free_until": "2026-11-08T15:00:00+03:00",
                        "summary": "Бесплатная отмена до указанного срока",
                    },
                    "pets_allowed": None,
                    "match_status": "requires_confirmation",
                    "checks": [],
                    "source_url": None,
                },
                {"free_cancellation_available": True},
            )
        ]
    if tool == "search_transport":
        return [
            Candidate(
                {
                    "id": "fixture_ru_trip_001",
                    "transport_type": "plane",
                    "carrier": "Демо перевозчик",
                    "segments": [
                        {
                            "origin": {"name": "Аэропорт отправления", "code": None},
                            "destination": {"name": "Аэропорт прибытия", "code": None},
                            "departure_at": "2026-11-10T08:00:00+03:00",
                            "arrival_at": "2026-11-10T12:00:00+03:00",
                            "number": "DEMO 001",
                        }
                    ],
                    "duration_s": 14400,
                    "total_price": None,
                    "advertised_price": {"amount_minor": 1500000, "currency": "RUB", "basis": "one_adult"},
                    "price_kind": "cached_quote",
                    "baggage": None,
                    "match_status": "requires_confirmation",
                    "checks": [{"field": "requested_guests_price", "status": "unknown"}],
                    "source_url": None,
                }
            )
        ]
    if tool == "get_weather":
        return [
            Candidate(
                {
                    "date": day,
                    "kind": "forecast",
                    "day_temp_min_c": 16,
                    "day_temp_max_c": 19,
                    "night_temp_min_c": 11,
                    "night_temp_max_c": 13,
                    "water_temp_c": None,
                    "precipitation_probability": 0.3,
                    "source_url": None,
                }
            )
            for day in ("2026-10-25", "2026-10-26")
        ]
    return [
        Candidate(
            {
                "id": "fixture_ru_route_001",
                "origin": args["origin"],
                "destination": args["destination"],
                "distance_m": 31000,
                "duration_s": 2700,
                "traffic_mode": "unknown",
                "departure_at": None,
                "source_url": None,
            }
        )
    ]


class FixtureProvider:
    def __init__(self, tool):
        self.tool = tool
        self.name = f"fixture_ru_{tool}"

    async def fetch(self, args, deadline):
        result = ProviderResult(
            warnings=[warning("FIXTURE_DATA", "Вымышленные данные для проверки контракта")]
        )
        expected = deepcopy(ARGUMENTS[self.tool])
        actual = deepcopy(args)
        # limit is a presentation constraint; every other input remains exact and fixed.
        expected.pop("limit", None)
        actual.pop("limit", None)
        if actual != expected:
            if self.tool == "get_weather":
                raise DataError("OUT_OF_COVERAGE", "Для этого запроса нет фиксированного примера погоды")
            return result
        result.candidates = fixture_candidates(self.tool)
        return result
