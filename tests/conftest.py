import asyncio
import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from travel_data.client import make_request
from travel_data.demo import ARGUMENTS, FIXED_NOW
from travel_data.models import ProviderResult
from travel_data.network import _RECENT_CALLS


@pytest.fixture(autouse=True)
def isolate_provider_quotas():
    # Unit tests use a simulated provider and must not spend each other's quota.
    _RECENT_CALLS.clear()
    yield
    _RECENT_CALLS.clear()


@pytest.fixture
def run():
    return asyncio.run


@pytest.fixture
def request_for():
    def build(tool, **overrides):
        return make_request(tool, {**deepcopy(ARGUMENTS[tool]), **overrides}, "test_request")

    return build


@pytest.fixture
def now():
    return FIXED_NOW


class StubProvider:
    name = "test_ru_source"

    def __init__(self, candidates=None, *, error=None, delay=0, warnings=None, incomplete=False):
        self.candidates = candidates or []
        self.error, self.delay = error, delay
        self.warnings = warnings or []
        self.incomplete = incomplete
        self.calls = 0

    async def fetch(self, args, deadline):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return ProviderResult(deepcopy(self.candidates), deepcopy(self.warnings), incomplete=self.incomplete)
