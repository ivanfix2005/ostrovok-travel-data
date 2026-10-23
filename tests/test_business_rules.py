from copy import deepcopy

import pytest
from conftest import StubProvider

from travel_data import Settings, TravelDataClient
from travel_data.demo import fixture_candidates
from travel_data.errors import DataError
from travel_data.validation import assert_response


def execute(run, request, candidates, now, settings=None, **provider_kwargs):
    provider = StubProvider(candidates, **provider_kwargs)

    async def go():
        async with TravelDataClient(
            settings or Settings(), providers={request["tool"]: provider}, clock=lambda: now
        ) as client:
            return await client.execute(request)

    return run(go())


def test_unknown_pet_is_not_passed(run, request_for, now):
    request = request_for("search_hotels")
    result = execute(run, request, fixture_candidates("search_hotels"), now)
    item = result["data"]["items"][0]
    assert item["match_status"] == "requires_confirmation"
    assert {c["field"]: c["status"] for c in item["checks"]}["pets_required"] == "unknown"
    assert_response(request, result)


@pytest.mark.parametrize("field,value", [("pets_allowed", False), ("meal", "RO")])
def test_failed_filter_excludes_offer(field, value, run, request_for, now):
    rows = fixture_candidates("search_hotels")
    rows[0].item[field] = value
    result = execute(run, request_for("search_hotels"), rows, now)
    assert result["status"] == "empty"


def test_unknown_price_remains_null(run, request_for, now):
    rows = fixture_candidates("search_hotels")
    rows[0].item["total_price"] = None
    result = execute(run, request_for("search_hotels"), rows, now)
    item = result["data"]["items"][0]
    assert item["total_price"] is None
    assert item["match_status"] == "requires_confirmation"
    assert {c["field"]: c["status"] for c in item["checks"]}["max_total_price_minor"] == "unknown"


def test_incomplete_price_does_not_pass_budget(run, request_for, now):
    rows = fixture_candidates("search_hotels")
    rows[0].item["total_price"]["includes_mandatory_fees"] = False
    result = execute(run, request_for("search_hotels"), rows, now)
    checks = {c["field"]: c["status"] for c in result["data"]["items"][0]["checks"]}
    assert checks["max_total_price_minor"] == "unknown" and checks["mandatory_fees"] == "unknown"


def test_over_budget_excludes_offer(run, request_for, now):
    result = execute(
        run, request_for("search_hotels", max_total_price_minor=1), fixture_candidates("search_hotels"), now
    )
    assert result["status"] == "empty"


def test_cancellation_deadline_is_checked_against_clock(run, request_for, now):
    rows = fixture_candidates("search_hotels")
    rows[0].item["cancellation"]["free_until"] = "2026-10-22T12:00:00+03:00"
    assert execute(run, request_for("search_hotels"), rows, now)["status"] == "empty"


def test_sort_limit_and_has_more(run, request_for, now):
    rows = []
    for identity, price, included in [("b", 300, True), ("a", 300, True), ("c", 1, None), ("d", None, None)]:
        candidate = fixture_candidates("search_hotels")[0]
        candidate.item["id"] = identity
        if price is None:
            candidate.item["total_price"] = None
        else:
            candidate.item["total_price"].update(amount_minor=price, includes_mandatory_fees=included)
        rows.append(candidate)
    result = execute(run, request_for("search_hotels", limit=3), rows, now)
    assert [i["id"] for i in result["data"]["items"]] == ["a", "b", "c"]
    assert result["meta"]["has_more"] is True
    assert result["meta"]["returned_count"] == 3


def test_partial_malformed_record(run, request_for, now):
    rows = fixture_candidates("search_hotels")
    bad = deepcopy(rows[0])
    bad.item["id"] = "bad"
    bad.item["total_price"]["amount_minor"] = -1
    result = execute(run, request_for("search_hotels"), rows + [bad], now)
    assert result["status"] == "partial" and result["meta"]["returned_count"] == 1


def test_all_malformed_is_schema_change(run, request_for, now):
    rows = fixture_candidates("search_hotels")
    del rows[0].item["hotel_name"]
    result = execute(run, request_for("search_hotels"), rows, now)
    assert result["error"]["code"] == "SOURCE_SCHEMA_CHANGED"


def test_weather_missing_dates_are_partial(run, request_for, now):
    result = execute(run, request_for("get_weather"), fixture_candidates("get_weather")[:1], now)
    assert result["status"] == "partial"
    assert any("2026-10-26" in w["message"] for w in result["warnings"])


def test_weather_no_forecast_is_out_of_coverage(run, request_for, now):
    result = execute(run, request_for("get_weather"), [], now)
    assert result["error"]["code"] == "OUT_OF_COVERAGE"


def test_weather_outside_period_is_excluded(run, request_for, now):
    rows = fixture_candidates("get_weather")
    rows[1].item["date"] = "2026-10-27"
    result = execute(run, request_for("get_weather"), rows, now)
    assert result["meta"]["returned_count"] == 1
    assert result["status"] == "partial"


def test_temperature_minimum_cannot_exceed_maximum(run, request_for, now):
    rows = fixture_candidates("get_weather")
    rows[0].item["day_temp_min_c"] = 25
    result = execute(run, request_for("get_weather"), rows, now)
    assert result["status"] == "partial" and result["meta"]["returned_count"] == 1


def test_transport_price_is_never_multiplied(run, request_for, now):
    result = execute(run, request_for("search_transport"), fixture_candidates("search_transport"), now)
    assert result["data"]["items"][0]["total_price"] is None
    assert result["data"]["items"][0]["advertised_price"]["amount_minor"] == 1500000


def test_transport_bad_temporal_order_is_rejected(run, request_for, now):
    rows = fixture_candidates("search_transport")
    rows[0].item["segments"][0]["arrival_at"] = "2026-11-10T07:00:00+03:00"
    result = execute(run, request_for("search_transport"), rows, now)
    assert result["error"]["code"] == "SOURCE_SCHEMA_CHANGED"


def test_route_must_match_requested_coordinates(run, request_for, now):
    rows = fixture_candidates("get_route")
    rows[0].item["origin"] = {"lat": 0, "lon": 0}
    result = execute(run, request_for("get_route"), rows, now)
    assert result["error"]["code"] == "SOURCE_SCHEMA_CHANGED"


def test_whole_function_deadline(run, request_for, now):
    settings = Settings(time_budget_seconds=0.03)
    result = execute(run, request_for("get_route"), [], now, settings, delay=0.2)
    assert result["error"]["code"] == "SOURCE_TIMEOUT"


def test_cache_key_includes_children_and_request_id_is_refreshed(run, request_for, now):
    provider = StubProvider(fixture_candidates("search_hotels"))
    settings = Settings(cache_allowed=True, cache_ttl_seconds=30)

    async def go():
        async with TravelDataClient(
            settings, providers={"search_hotels": provider}, clock=lambda: now
        ) as client:
            request = request_for("search_hotels")
            first = await client.execute(request)
            second_request = {**request, "request_id": "second"}
            second = await client.execute(second_request)
            third_request = request_for("search_hotels", guests={"adults": 2, "children_ages": [8]})
            third = await client.execute(third_request)
        assert not first["meta"]["cache_hit"]
        assert second["meta"]["cache_hit"] and second["request_id"] == "second"
        assert not third["meta"]["cache_hit"] and provider.calls == 2

    run(go())


def test_errors_are_not_cached_as_empty(run, request_for, now):
    provider = StubProvider(error=DataError("SOURCE_TIMEOUT", "timeout", True))

    async def go():
        async with TravelDataClient(
            Settings(cache_allowed=True, cache_ttl_seconds=30),
            providers={"get_route": provider},
            clock=lambda: now,
        ) as client:
            for _ in range(2):
                result = await client.execute(request_for("get_route"))
                assert result["status"] == "error"
        assert provider.calls == 2

    run(go())


def test_response_size_reduces_items_without_breaking_json(run, request_for, now):
    rows = []
    for i in range(5):
        row = fixture_candidates("search_hotels")[0]
        row.item.update(id=str(i), address="я" * 500, hotel_name="я" * 300, room_name="я" * 300)
        rows.append(row)
    request = request_for("search_hotels", limit=5)
    result = execute(run, request, rows, now, Settings(max_response_bytes=4096))
    assert result["status"] == "partial" and result["meta"]["has_more"]
    assert len(result["data"]["items"]) < 5
    assert_response(request, result)
