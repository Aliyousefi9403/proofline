import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.edgar import FactStore  # noqa: E402
from backend.models import Claim  # noqa: E402


@pytest.fixture(scope="session")
def store() -> FactStore:
    return FactStore()


@pytest.fixture
def make_claim():
    def _make(**overrides) -> Claim:
        base = dict(span="$391.0 billion", company="AAPL", metric="revenue", kind="absolute",
                    fiscal_year=2024, value=391.0e9, unit="USD")
        base.update(overrides)
        return Claim(**base)
    return _make
