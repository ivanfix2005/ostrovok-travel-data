import asyncio
import json
import logging
from datetime import timedelta

import httpx
import pytest
from conftest import StubProvider
from test_providers import hotel_handler, hotel_payload, invoke, weather_payload

from travel_data import Settings, TravelDataClient, call
from travel_data.demo import FIXED_NOW, fixture_candidates


@pytest.mark.parametrize(
    "tool,changes", [("search_transport", {"transport_type": "ferry"}), ("get_route", {"mode": "bicycle"})]
)
def test_unsupported_mode_has_specific_error(tool, changes, request_for):
    result = call(request_for(tool, **changes), mode="fixture")
    assert result["error"]["code"] == "UNSUPPORTED_REQUEST"


def test_single_retry_budget_across_entire_function(run, request_for):
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if len(calls) in (1, 3):
            return httpx.Response(503)
        return hotel_handler()(request)

    result = invoke(
        run,
        "search_hotels",
        request_for("search_hotels"),
        handle,
        Settings(ostrovok_key_id="id", ostrovok_api_key="secret"),
    )
    assert result["error"]["code"] == "SOURCE_UNAVAILABLE"
    assert len(calls) == 3


def test_url_logs_redact_query_credentials(run, request_for, caplog):
    caplog.set_level(logging.INFO, logger="httpx")
    result = invoke(
        run,
        "get_route",
        request_for("get_route"),
        lambda _: httpx.Response(
            200, json={"status": "OK", "result": [{"total_distance": 100, "total_duration": 20}]}
        ),
        Settings(dgis_api_key="never-log-this-key"),
    )
    assert result["status"] == "ok"
    assert "never-log-this-key" not in caplog.text
    assert "REDACTED" in caplog.text


def test_hotel_content_auth_failure_is_not_hidden(run, request_for):
    def handle(request):
        if request.url.path.endswith("/hotel/info/"):
            return httpx.Response(403)
        return hotel_handler()(request)

    result = invoke(
        run,
        "search_hotels",
        request_for("search_hotels"),
        handle,
        Settings(ostrovok_key_id="id", ostrovok_api_key="secret"),
    )
    assert result["error"]["code"] == "SOURCE_AUTH_FAILED"


def test_package_only_rate_is_not_shown_as_standalone_hotel(run, request_for):
    payload = hotel_payload()
    payload["data"]["hotels"][0]["rates"][0]["is_package"] = True
    result = invoke(
        run,
        "search_hotels",
        request_for("search_hotels"),
        hotel_handler(payload),
        Settings(ostrovok_key_id="id", ostrovok_api_key="secret"),
    )
    assert result["status"] == "empty"
    assert any(w["code"] == "PACKAGE_ONLY_RATE" for w in result["warnings"])


def test_cached_free_cancellation_cannot_survive_its_deadline(run, request_for):
    clock = [FIXED_NOW]
    rows = fixture_candidates("search_hotels")
    rows[0].item["cancellation"]["free_until"] = (FIXED_NOW + timedelta(seconds=1)).isoformat()
    provider = StubProvider(rows)

    async def go():
        async with TravelDataClient(
            Settings(cache_allowed=True, cache_ttl_seconds=60),
            providers={"search_hotels": provider},
            clock=lambda: clock[0],
        ) as client:
            first = await client.execute(request_for("search_hotels"))
            clock[0] += timedelta(seconds=2)
            second = await client.execute(request_for("search_hotels"))
        assert first["status"] == "ok"
        assert second["status"] == "empty" and not second["meta"]["cache_hit"]
        assert provider.calls == 2

    run(go())


def test_malformed_nested_weather_record_keeps_other_good_days(run, request_for):
    payload = weather_payload()
    payload["forecasts"][0]["parts"] = []
    result = invoke(
        run, "get_weather", request_for("get_weather"), lambda _: httpx.Response(200, json=payload)
    )
    assert result["status"] == "partial" and result["meta"]["returned_count"] == 1


def test_malformed_city_index_is_a_normal_error(run, request_for, tmp_path):
    index = tmp_path / "cities.json"
    index.write_text('["changed format"]', encoding="utf-8")
    result = invoke(
        run,
        "search_transport",
        request_for("search_transport"),
        lambda _: httpx.Response(200),
        Settings(yandex_rasp_api_key="key", yandex_cities_file=str(index)),
    )
    assert result["error"]["code"] == "SOURCE_SCHEMA_CHANGED"


def test_cancellation_of_caller_is_not_swallowed(run, request_for):
    async def go():
        provider = StubProvider(delay=5)
        async with TravelDataClient(
            Settings(), providers={"get_route": provider}, clock=lambda: FIXED_NOW
        ) as client:
            task = asyncio.create_task(client.execute(request_for("get_route")))
            await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    run(go())


def test_settings_reject_urls_with_embedded_credentials():
    with pytest.raises(ValueError):
        Settings(dgis_routing_url="https://user:password@example.com/route")


def test_response_is_valid_json_even_after_utf8_size_limit(run, request_for):
    rows = []
    for index in range(5):
        row = fixture_candidates("search_hotels")[0]
        row.item.update(id=str(index), address="я" * 500, hotel_name="я" * 300, room_name="я" * 300)
        rows.append(row)

    async def go():
        async with TravelDataClient(
            Settings(max_response_bytes=4096),
            providers={"search_hotels": StubProvider(rows)},
            clock=lambda: FIXED_NOW,
        ) as client:
            response = await client.execute(request_for("search_hotels", limit=5))
        encoded = json.dumps(response, ensure_ascii=False, allow_nan=False).encode("utf-8")
        assert len(encoded) <= 4096
        assert json.loads(encoded)["meta"]["has_more"] is True

    run(go())
