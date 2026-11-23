"""Shared test setup.

The synthetic PIMS seed is not committed (it is rebuilt deterministically).
Tests build the default seed once per session into a temp directory and point
PIMS_DB_PATH at it, so CI needs no prebuilt database. Individual tests that
build their own small seed override PIMS_DB_PATH with monkeypatch.
"""

from __future__ import annotations

import os
from datetime import date

import pytest

from example_server.config import settings
from example_server.pims import generator
from example_server.staffing import provider as staffing_provider

SEED_START = date(2026, 10, 18)
SEED_DAYS = 30
SEED_RANDOM = 20261117


@pytest.fixture(scope="session", autouse=True)
def pims_seed(tmp_path_factory):
    path = tmp_path_factory.mktemp("pims") / "pims_pmis_synthetic.sqlite"
    generator.build_seed(path, start=SEED_START, days=SEED_DAYS, seed=SEED_RANDOM)
    previous = os.environ.get("PIMS_DB_PATH")
    os.environ["PIMS_DB_PATH"] = str(path)
    staffing_provider.get_provider.cache_clear()
    yield path
    if previous is None:
        os.environ.pop("PIMS_DB_PATH", None)
    else:
        os.environ["PIMS_DB_PATH"] = previous
    staffing_provider.get_provider.cache_clear()


@pytest.fixture
def csv_fixture_source(monkeypatch):
    """Run staffing tools against the small CSV fixture instead of the seed."""
    monkeypatch.setattr(settings, "staffing_source", "csv_fixture")
    staffing_provider.get_provider.cache_clear()
    yield
    staffing_provider.get_provider.cache_clear()
