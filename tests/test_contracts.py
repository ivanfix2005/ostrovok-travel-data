import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from travel_data import Settings, TravelDataClient, call
from travel_data.contracts import REQUEST_SCHEMAS, RESPONSE_SCHEMAS, TOOLS
from travel_data.demo import ARGUMENTS, FIXED_NOW
from travel_data.validation import assert_response

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("tool", TOOLS)
def test_fixtures_are_valid_and_explicit(tool, request_for):
    request = request_for(tool)
    result = call(request, mode="fixture")
    assert result["status"] == "ok"
    assert result["meta"]["is_fixture"] is True
    assert any(w["code"] == "FIXTURE_DATA" for w in result["warnings"])
    assert_response(request, result)


@pytest.mark.parametrize("tool", TOOLS)
def test_missing_credentials_are_not_fake_success(tool, request_for, run):
    async def check():
        async with TravelDataClient(Settings(hotel_provider="ostrovok"), clock=lambda: FIXED_NOW) as client:
            result = await client.execute(request_for(tool))
        assert result["status"] == "error"
        assert result["error"]["code"] == "ACCESS_NOT_CONFIGURED"
        assert result["meta"]["is_fixture"] is False
        assert result["data"]["items"] == []
        assert_response(request_for(tool), result)

    run(check())


@pytest.mark.parametrize("tool", TOOLS)
def test_no_unknown_arguments(tool, request_for):
    request = request_for(tool)
    request["arguments"]["url"] = "https://untrusted.example/"
    result = call(request, mode="fixture")
    assert result["error"]["code"] == "INVALID_ARGUMENT"


@pytest.mark.parametrize(
    "name,schema",
    [
        (f"{tool}.{kind}", schemas[tool])
        for tool in TOOLS
        for kind, schemas in (("request", REQUEST_SCHEMAS), ("response", RESPONSE_SCHEMAS))
    ],
)
def test_exported_schemas_match_runtime(name, schema):
    assert json.loads((ROOT / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8")) == schema
    Draft202012Validator.check_schema(schema)


def test_all_handoff_examples_validate():
    from travel_data.validation import REQUEST_VALIDATORS

    for path in (ROOT / "examples").glob("*.request.json"):
        request = json.loads(path.read_text(encoding="utf-8"))
        response = json.loads(
            path.with_name(path.name.replace(".request.", ".response.")).read_text(encoding="utf-8")
        )
        if "error.request" not in path.name:
            REQUEST_VALIDATORS[request["tool"]].validate(request)
        assert_response(request, response)


def test_tools_describe_arguments_without_infrastructure():
    tools = json.loads((ROOT / "tools.json").read_text(encoding="utf-8"))
    assert {t["name"] for t in tools} == set(TOOLS)
    for tool in tools:
        assert "request_id" not in tool["input_schema"]["properties"]
        assert "schema_version" not in tool["input_schema"]["properties"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"limit": 0},
        {"limit": 6},
        {"limit": True},
        {"check_out": "2026-11-10"},
        {"check_in": "2020-01-01"},
        {"check_in": "2026-02-30"},
        {"max_total_price_minor": -1},
        {"max_total_price_minor": 10.5},
        {"guests": {"adults": 0, "children_ages": []}},
        {"guests": {"adults": 10, "children_ages": []}},
        {"guests": {"adults": 1, "children_ages": [18]}},
        {"guests": {"adults": 1, "children_ages": [1] * 7}},
    ],
)
def test_invalid_hotel_inputs(overrides, request_for):
    result = call(request_for("search_hotels", **overrides), mode="fixture")
    assert result["error"]["code"] == "INVALID_ARGUMENT"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), True, 91, -91])
def test_bad_coordinates(value, request_for):
    result = call(request_for("get_weather", location={"lat": value, "lon": 40}), mode="fixture")
    assert result["error"]["code"] == "INVALID_ARGUMENT"


def test_weather_period_over_16_days(request_for):
    result = call(request_for("get_weather", end_date="2026-11-11"), mode="fixture")
    assert result["error"]["code"] == "UNSUPPORTED_REQUEST"


def test_offset_required(request_for):
    result = call(request_for("get_route", departure_at="2026-11-10T12:00:00"), mode="fixture")
    assert result["error"]["code"] == "INVALID_ARGUMENT"


def test_response_error_cannot_contain_items(request_for):
    request = request_for("get_route")
    result = call(request, mode="fixture")
    result["status"] = "error"
    with pytest.raises(ValidationError):
        Draft202012Validator(RESPONSE_SCHEMAS["get_route"]).validate(result)


def test_schema_version_is_not_silently_changed(request_for):
    request = request_for("get_weather")
    request["schema_version"] = "2.0.0"
    result = call(request, mode="fixture")
    assert result["error"]["code"] == "INVALID_ARGUMENT"


def test_fixture_does_not_invent_a_new_trip(request_for):
    request = request_for("search_hotels", check_in="2027-01-01", check_out="2027-01-02")
    result = call(request, mode="fixture")
    assert result["status"] == "empty"


def test_sync_functions_reject_running_event_loop(run):
    async def check():
        from travel_data import get_weather

        with pytest.raises(RuntimeError, match="TravelDataClient"):
            get_weather(deepcopy(ARGUMENTS["get_weather"]))

    run(check())


def test_cross_field_validator_rejects_wrong_count(request_for):
    request = request_for("get_route")
    result = call(request, mode="fixture")
    result["meta"]["returned_count"] = 0
    with pytest.raises(ValidationError):
        assert_response(request, result)
