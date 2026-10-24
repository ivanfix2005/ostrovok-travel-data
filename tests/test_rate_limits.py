import httpx
import pytest

from travel_data import Settings, TravelDataClient
from travel_data.demo import FIXED_NOW
from travel_data.errors import DataError
from travel_data.network import source_slot


def test_route_quota_is_shared_across_clients_and_blocks_before_http(run, request_for):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            200, json={"status": "OK", "result": [{"total_distance": 100, "total_duration": 20}]}
        )

    async def go():
        settings = Settings(dgis_api_key="test-key", dgis_requests_per_minute=2)
        async with (
            TravelDataClient(
                settings, transport=httpx.MockTransport(handle), clock=lambda: FIXED_NOW
            ) as first,
            TravelDataClient(
                settings, transport=httpx.MockTransport(handle), clock=lambda: FIXED_NOW
            ) as second,
        ):
            assert (await first.execute(request_for("get_route")))["status"] == "ok"
            assert (await second.execute(request_for("get_route")))["status"] == "ok"
            result = await first.execute(request_for("get_route"))
            assert result["error"]["code"] == "SOURCE_RATE_LIMITED"
            assert result["data"]["items"] == []
        assert len(calls) == 2

    run(go())


def test_minute_quota_expires_without_waiting_past_function_deadline(run, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("travel_data.network.monotonic", lambda: clock[0])

    async def go():
        async with source_slot("example.test", "/route", calls_per_minute=1):
            pass
        with pytest.raises(DataError) as error:
            async with source_slot("example.test", "/route", calls_per_minute=1):
                pass
        assert error.value.code == "SOURCE_RATE_LIMITED"
        clock[0] += 61
        async with source_slot("example.test", "/route", calls_per_minute=1):
            pass

    run(go())
