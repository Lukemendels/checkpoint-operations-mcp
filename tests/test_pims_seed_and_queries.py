import csv
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from example_server.pims import generator, repository


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch):
    path = tmp_path / "test.sqlite"
    info = generator.build_seed(path, start=date(2026, 11, 15), days=2, seed=123)
    monkeypatch.setenv("PIMS_DB_PATH", str(path))
    return path, info


def test_seed_structural_quality_and_counts(seeded):
    path, info = seeded
    assert path.exists()
    assert info["customer_throughput_rows"] > 1000
    assert info["checkpoint_wait_time_rows"] == 2 * 6 * 2 * 2 * 20
    assert repository.quality_checks()["passed"] is True
    assert (
        repository.coverage()["metadata"]["national_baseline_kind"] == "synthetic_demo"
    )


def test_queries_return_correct_grain(seeded):
    from_date, to_date = "2026-11-15", "2026-11-16"
    t = repository.customer_throughput(
        from_date, to_date, airport_code="IAD", limit=500
    )
    assert t["returned"] == 500 and t["has_more"]
    assert t["active_modsets_not_additive"]
    assert all(
        r["airport_code"] == "IAD" and r["machine_type"] in ("AIT", "WTMD")
        for r in t["rows"]
    )
    w = repository.wait_times(from_date, to_date, airport_code="IAD", limit=4)
    assert len(w["rows"]) == 4
    assert "modset_id" not in w["rows"][0]
    assert isinstance(w["rows"][0]["exceeded_15m_flag"], bool)
    agg = repository.checkpoint_summary(
        from_date, to_date, airport_code="IAD", limit=10
    )
    assert agg["rows"][0]["active_modsets"] == agg["rows"][0]["observed_active_modsets"]
    n = repository.national_baseline(from_date, to_date)
    assert len(n["rows"]) == 2 and n["source_kind"] == "synthetic_demo"
    assert n["is_verified_tsa"] is False
    d = repository.airport_day_summary(from_date, to_date, airport_code="IAD", limit=2)
    assert d["returned"] == 2


def test_modset_aggregation_matches_raw_counts(seeded):
    raw = repository.customer_throughput(
        "2026-11-16",
        "2026-11-16",
        airport_code="IAD",
        checkpoint_id="Checkpoint A",
        screening_type="Standard",
        hour=7,
        limit=100,
    )
    sum_val = sum(r["passenger_throughput"] for r in raw["rows"])
    agg = repository.checkpoint_summary(
        "2026-11-16",
        "2026-11-16",
        airport_code="IAD",
        checkpoint_id="Checkpoint A",
        screening_type="Standard",
        hour=7,
    )
    assert sum_val == agg["rows"][0]["passenger_throughput"]
    assert len(raw["rows"]) == agg["rows"][0]["observed_active_modsets"]
    assert (
        len(raw["rows"]) * agg["rows"][0]["active_modsets"]
        != agg["rows"][0]["active_modsets"]
    )


@pytest.mark.parametrize(
    "args",
    [
        ("2026-11-17", "2026-11-16"),
        ("2026-11-1", "2026-11-16"),
        ("2026-11-16", "2027-11-17"),
    ],
)
def test_date_validation_rejects_invalid_ranges(seeded, args):
    with pytest.raises(ValueError):
        repository.customer_throughput(*args)


def test_invalid_filters_and_bounds(seeded):
    with pytest.raises(ValueError):
        repository.customer_throughput(
            "2026-11-15", "2026-11-16", airport_code="IAD' OR 1=1 --"
        )
    with pytest.raises(ValueError):
        repository.customer_throughput("2026-11-15", "2026-11-16", limit=501)
    with pytest.raises(ValueError):
        repository.wait_times("2026-11-15", "2026-11-16", hour=24)
    # A valid string with SQL punctuation is bound as a literal; never interpolated.
    got = repository.customer_throughput(
        "2026-11-15", "2026-11-16", checkpoint_id="North' OR 1=1 --"
    )
    assert got["returned"] == 0


def test_readonly_connection(seeded):
    with repository.db_connection() as con:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("DELETE FROM national_daily_baseline")


def test_deterministic_seed_data_and_no_overwrite(seeded, tmp_path):
    first, _ = seeded
    second = tmp_path / "other.sqlite"
    generator.build_seed(second, start=date(2026, 11, 15), days=2, seed=123)
    with sqlite3.connect(first) as a, sqlite3.connect(second) as b:
        for table in (
            "customer_throughput",
            "checkpoint_wait_times",
            "national_daily_baseline",
        ):
            assert (
                a.execute(f"SELECT * FROM {table} ORDER BY 1,2,3 LIMIT 10").fetchall()
                == b.execute(
                    f"SELECT * FROM {table} ORDER BY 1,2,3 LIMIT 10"
                ).fetchall()
            )
    with pytest.raises(FileExistsError):
        generator.build_seed(first, start=date(2026, 11, 15), days=2)


def test_user_national_csv_requires_full_date_coverage(seeded, tmp_path, monkeypatch):
    csv_path = tmp_path / "tsa.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ("date", "national_throughput", "prior_year_same_weekday_throughput")
        )
        writer.writerow(("2026-11-15", 2100000, 2000000))
    with pytest.raises(ValueError, match="missing"):
        generator.build_seed(
            tmp_path / "missing.sqlite",
            start=date(2026, 11, 15),
            days=2,
            national_csv=csv_path,
        )
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(("2026-11-16", 2150000, 2050000))
    imported = tmp_path / "imported.sqlite"
    generator.build_seed(
        imported, start=date(2026, 11, 15), days=2, national_csv=csv_path
    )
    monkeypatch.setenv("PIMS_DB_PATH", str(imported))
    actual = repository.national_baseline("2026-11-15", "2026-11-16")
    assert actual["source_kind"] == "user_supplied_csv_unverified"
    assert actual["rows"][0]["national_throughput"] == 2100000
