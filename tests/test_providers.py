import asyncio
import json
from copy import deepcopy

import httpx
import pytest

from travel_data import Settings, TravelDataClient
from travel_data.demo import FIXED_NOW
from travel_data.errors import DataError
from travel_data.providers.yandex import resolve_city


def invoke(run, tool, request, handler, settings=None):
    async def go():
        async with TravelDataClient(
            settings or Settings(yandex_weather_api_key="private-weather-key"),
            transport=httpx.MockTransport(handler),
            clock=lambda: FIXED_NOW,
        ) as client:
            return await client.execute(request)

    return run(go())


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "SOURCE_AUTH_FAILED"),
        (403, "SOURCE_AUTH_FAILED"),
        (429, "SOURCE_RATE_LIMITED"),
        (500, "SOURCE_UNAVAILABLE"),
    ],
)
def test_http_errors_are_not_empty(status, code, run, request_for):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, json={"secret": "never-return-this"})

    result = invoke(run, "get_weather", request_for("get_weather"), handle)
    assert result["error"]["code"] == code
    assert result["data"]["items"] == []
    assert len(calls) == (2 if status == 500 else 1)
    assert "private-weather-key" not in json.dumps(result)
    assert "never-return-this" not in json.dumps(result)


def test_network_timeout_is_retried_once(run, request_for):
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout("url might include secrets", request=request)

    result = invoke(run, "get_weather", request_for("get_weather"), handle)
    assert len(calls) == 2 and result["error"]["code"] == "SOURCE_TIMEOUT"
    assert "url might" not in json.dumps(result)


def test_changed_source_structure(run, request_for):
    result = invoke(
        run, "get_weather", request_for("get_weather"), lambda _: httpx.Response(200, json={"unexpected": []})
    )
    assert result["error"]["code"] == "SOURCE_SCHEMA_CHANGED"


@pytest.mark.parametrize("body", [b"<html>not JSON</html>", b'{"x": NaN}', b"[]"])
def test_non_json_or_nonfinite_source(run, request_for, body):
    result = invoke(
        run, "get_weather", request_for("get_weather"), lambda _: httpx.Response(200, content=body)
    )
    assert result["error"]["code"] == "SOURCE_SCHEMA_CHANGED"


def weather_payload():
    return {
        "now_dt": "2026-10-23T12:00:00Z",
        "info": {"url": "https://yandex.ru/pogoda/sochi?token=private"},
        "forecasts": [
            {
                "date": day,
                "parts": {
                    "day": {"temp_min": 12, "temp_max": 15, "prec_prob": 30},
                    "night": {"temp_min": 5, "temp_max": 9},
                },
            }
            for day in ("2026-10-25", "2026-10-26")
        ],
    }


def test_weather_normalization_and_no_fake_water_temperature(run, request_for):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=weather_payload())

    result = invoke(run, "get_weather", request_for("get_weather"), handle)
    item = result["data"]["items"][0]
    assert item["precipitation_probability"] == 0.3
    assert item["water_temp_c"] is None
    assert item["source_url"] == "https://yandex.ru/pogoda/sochi"
    assert result["meta"]["source_updated_at"] is None
    assert calls[0].headers["X-Yandex-Weather-Key"] == "private-weather-key"
    assert calls[0].url.params["hours"] == "false"


def test_precipitation_is_not_inferred_from_amount(run, request_for):
    payload = weather_payload()
    for day in payload["forecasts"]:
        day["parts"]["day"].pop("prec_prob")
        day["parts"]["day"]["prec_mm"] = 10
    result = invoke(
        run, "get_weather", request_for("get_weather"), lambda _: httpx.Response(200, json=payload)
    )
    assert result["data"]["items"][0]["precipitation_probability"] is None


def test_2gis_routing_is_road_distance_and_coordinates_are_named(run, request_for):
    def handle(request):
        body = json.loads(request.content)
        assert body["points"][0] == {"lat": 43.4499, "lon": 39.9566, "type": "stop"}
        assert body["transport"] == "driving"
        return httpx.Response(
            200,
            json={
                "status": "OK",
                "result": [
                    {"id": "route-1", "total_distance": 31000, "total_duration": 2700},
                    {"id": "route-2", "total_distance": 40000, "total_duration": 5000},
                ],
            },
        )

    result = invoke(run, "get_route", request_for("get_route"), handle, Settings(dgis_api_key="route-secret"))
    assert result["status"] == "ok" and result["meta"]["returned_count"] == 1
    item = result["data"]["items"][0]
    assert item["distance_m"] == 31000 and item["duration_s"] == 2700
    assert item["traffic_mode"] == "unknown"
    assert "route-secret" not in json.dumps(result)


def test_route_does_not_silently_ignore_departure_time(run, request_for):
    def handle(_):
        raise AssertionError("Must not perform a request")

    result = invoke(
        run,
        "get_route",
        request_for("get_route", departure_at="2026-11-10T08:00:00+03:00"),
        handle,
        Settings(dgis_api_key="key"),
    )
    assert result["error"]["code"] == "UNSUPPORTED_REQUEST"


def test_city_resolution_never_selects_first_duplicate():
    cities = [
        {"city": "Советск", "country_code": "RU", "yandex_code": "c1"},
        {"city": "Советск", "country_code": "RU", "yandex_code": "c2"},
    ]
    with pytest.raises(DataError) as error:
        resolve_city({"city": "Советск", "country_code": "RU"}, cities, "origin")
    assert error.value.code == "AMBIGUOUS_LOCATION"


def test_transport_uses_exact_dates_and_never_multiplies_price(run, request_for, tmp_path):
    cities = tmp_path / "cities.json"
    cities.write_text(
        json.dumps(
            [
                {"city": "Москва", "country_code": "RU", "yandex_code": "c213"},
                {"city": "Сочи", "country_code": "RU", "yandex_code": "c239"},
            ]
        ),
        encoding="utf-8",
    )

    def handle(request):
        assert request.url.params["from"] == "c213" and request.url.params["to"] == "c239"
        assert request.url.params["date"] == "2026-11-10"
        assert "apikey" not in request.url.params
        assert request.headers["Authorization"] == "transport-secret"
        return httpx.Response(
            200,
            json={
                "pagination": {"total": 1},
                "segments": [
                    {
                        "from": {"title": "Отправление", "code": "s1"},
                        "to": {"title": "Прибытие", "code": "s2"},
                        "departure": "2026-11-10T08:00:00+03:00",
                        "arrival": "2026-11-10T12:00:00+03:00",
                        "duration": 14400,
                        "has_transfers": False,
                        "thread": {
                            "transport_type": "plane",
                            "number": "TEST 1",
                            "carrier": {"title": "Тест"},
                        },
                        "tickets_info": {
                            "places": [{"currency": "RUB", "price": {"whole": 15000, "cents": 50}}]
                        },
                    }
                ],
            },
        )

    settings = Settings(yandex_rasp_api_key="transport-secret", yandex_cities_file=str(cities))
    result = invoke(run, "search_transport", request_for("search_transport"), handle, settings)
    assert result["status"] == "ok"
    item = result["data"]["items"][0]
    assert item["advertised_price"] == {"amount_minor": 1500050, "currency": "RUB", "basis": "unknown"}
    assert item["total_price"] is None


def hotel_payload():
    return {
        "status": "ok",
        "data": {
            "total_hotels": 1,
            "hotels": [
                {
                    "id": "test_hotel",
                    "hid": 123,
                    "rates": [
                        {
                            "search_hash": "test_offer",
                            "room_name": "Тестовый номер",
                            "any_residency": True,
                            "meal_data": {"value": "breakfast", "no_child_meal": False},
                            "payment_options": {
                                "payment_types": [
                                    {
                                        "show_amount": "70000.00",
                                        "show_currency_code": "RUB",
                                        "tax_data": {
                                            "taxes": [{"included_by_supplier": False, "amount": "100"}]
                                        },
                                        "cancellation_penalties": {
                                            "free_cancellation_before": "2026-11-08T12:00:00"
                                        },
                                    }
                                ]
                            },
                        }
                    ],
                }
            ],
        },
    }


def hotel_handler(search_payload=None, duplicate_city=False):
    def handle(request):
        body = json.loads(request.content)
        if request.url.path.endswith("/multicomplete/"):
            assert body == {"query": "Сочи", "language": "ru"}
            city = {"id": 1, "name": "Сочи", "country_code": "RU", "type": "City"}
            rows = [city, {**city, "id": 2}] if duplicate_city else [city]
            return httpx.Response(200, json={"status": "ok", "data": {"regions": rows}})
        if request.url.path.endswith("/region/"):
            assert body["checkin"] == "2026-11-10" and body["checkout"] == "2026-11-17"
            assert body["guests"] == [{"adults": 2, "children": [7]}]
            assert body["currency"] == "RUB"
            return httpx.Response(200, json=deepcopy(search_payload or hotel_payload()))
        assert request.url.path.endswith("/hotel/info/")
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "data": {"name": "Тестовый отель", "address": "Адрес", "latitude": 43.5, "longitude": 39.7},
            },
        )

    return handle


def test_ostrovok_exact_guests_currency_and_documented_utc(run, request_for):
    result = invoke(
        run,
        "search_hotels",
        request_for("search_hotels"),
        hotel_handler(),
        Settings(ostrovok_key_id="id", ostrovok_api_key="secret"),
    )
    assert result["status"] == "ok"
    item = result["data"]["items"][0]
    assert item["total_price"]["amount_minor"] == 7000000
    assert item["total_price"]["includes_mandatory_fees"] is False
    assert item["cancellation"]["free_until"].endswith("+00:00")
    assert item["pets_allowed"] is None
    assert item["match_status"] == "requires_confirmation"


def test_ostrovok_does_not_use_nightly_price_as_total(run, request_for):
    payload = hotel_payload()
    rate = payload["data"]["hotels"][0]["rates"][0]
    rate["payment_options"] = {"payment_types": []}
    rate["daily_prices"] = ["1000"] * 7
    result = invoke(
        run,
        "search_hotels",
        request_for("search_hotels"),
        hotel_handler(payload),
        Settings(ostrovok_key_id="id", ostrovok_api_key="secret"),
    )
    assert result["data"]["items"][0]["total_price"] is None


def test_ostrovok_nomeal_is_unknown(run, request_for):
    payload = hotel_payload()
    payload["data"]["hotels"][0]["rates"][0]["meal_data"] = {"value": "nomeal"}
    result = invoke(
        run,
        "search_hotels",
        request_for("search_hotels"),
        hotel_handler(payload),
        Settings(ostrovok_key_id="id", ostrovok_api_key="secret"),
    )
    assert result["data"]["items"][0]["meal"] is None


def test_ostrovok_ambiguous_city(run, request_for):
    result = invoke(
        run,
        "search_hotels",
        request_for("search_hotels"),
        hotel_handler(duplicate_city=True),
        Settings(ostrovok_key_id="id", ostrovok_api_key="secret"),
    )
    assert result["error"]["code"] == "AMBIGUOUS_LOCATION"


def test_host_concurrency_is_at_most_two(run, request_for):
    async def go():
        active, maximum = 0, 0

        async def handle(request):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.02)
            active -= 1
            return httpx.Response(200, json=weather_payload())

        async with TravelDataClient(
            Settings(yandex_weather_api_key="key"),
            transport=httpx.MockTransport(handle),
            clock=lambda: FIXED_NOW,
        ) as client:
            results = await asyncio.gather(*(client.execute(request_for("get_weather")) for _ in range(7)))
        assert all(r["status"] == "ok" for r in results)
        assert maximum <= 2

    run(go())
