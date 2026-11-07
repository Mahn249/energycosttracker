"""Tests for data exposed to the energycosttracker Lovelace card."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from energycosttracker import lovelace_api


def test_get_available_years_returns_years_with_intervals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only years with stored meter intervals are available."""
    database_path = tmp_path / "meter.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("""
            CREATE TABLE meter_intervals (
                interval_start_utc TEXT NOT NULL,
                interval_end_utc TEXT NOT NULL
            )
        """)
        connection.executemany(
            """
            INSERT INTO meter_intervals (interval_start_utc, interval_end_utc)
            VALUES (?, ?)
            """,
            [
                ("2024-07-25T00:00:00+00:00", "2024-07-25T00:15:00+00:00"),
                ("2025-01-01T00:00:00+00:00", "2025-01-01T00:15:00+00:00"),
                ("2024-07-25T00:15:00+00:00", "2024-07-25T00:30:00+00:00"),
            ],
        )

    monkeypatch.setattr(lovelace_api, "DATABASE_PATH", database_path)

    assert lovelace_api.get_available_years() == [2025, 2024]


def test_read_month_data_uses_current_meter_interval_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Month data reads the current meter interval fields from the database."""
    database_path = tmp_path / "meter.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("""
            CREATE TABLE meter_intervals (
                interval_start_utc TEXT NOT NULL,
                interval_end_utc TEXT NOT NULL,
                consumption_meter_kwh REAL,
                consumption_meter_start_kwh REAL,
                note TEXT
            )
        """)
        connection.execute(
            """
            INSERT INTO meter_intervals (
                interval_start_utc, interval_end_utc, consumption_meter_kwh,
                consumption_meter_start_kwh, note
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-10-01T00:00:00+00:00",
                "2026-10-02T00:00:00+00:00",
                101,
                100,
                "sensor.energy",
            ),
        )
        connection.execute("""
            CREATE TABLE tariffs (
                valid_from TEXT,
                valid_until TEXT,
                work_price_per_kwh REAL,
                base_price_per_month REAL
            )
        """)
        connection.execute("""
            CREATE TABLE payments (
                valid_from TEXT,
                valid_until TEXT,
                amount_eur REAL
            )
        """)

    monkeypatch.setattr(lovelace_api, "DATABASE_PATH", database_path)
    monkeypatch.setattr(
        lovelace_api.dt_util,
        "now",
        lambda: datetime(2026, 10, 1, 12, tzinfo=UTC),
    )

    result = lovelace_api.get_month_data(2026, 10, UTC)

    assert result["current_date"] == "2026-10-01"
    assert result["days"][0]["consumption_kwh"] == 1
    assert result["days"][0]["consumption_source"] == "intervals"


def test_calculate_month_data_keeps_days_with_unknown_intervals_unknown() -> None:
    """A partial sum cannot hide an unknown quantity within that day."""
    day = date(2026, 10, 26)
    next_day = day + timedelta(days=1)
    intervals = [
        {
            "interval_start_utc": "2026-10-26T00:00:00+00:00",
            "interval_end_utc": "2026-10-26T10:00:00+00:00",
            "consumption_meter_start_kwh": 100,
            "consumption_meter_kwh": 110,
            "note": "sensor.energy",
        },
        {
            "interval_start_utc": "2026-10-26T10:00:00+00:00",
            "interval_end_utc": "2026-10-26T11:00:00+00:00",
            "consumption_meter_start_kwh": None,
            "consumption_meter_kwh": 111,
            "note": "sensor.energy",
        },
        {
            "interval_start_utc": "2026-10-26T11:00:00+00:00",
            "interval_end_utc": f"{next_day.isoformat()}T00:00:00+00:00",
            "consumption_meter_start_kwh": 111,
            "consumption_meter_kwh": 200,
            "note": "sensor.energy",
        },
    ]

    result = lovelace_api.calculate_month_data(
        2026,
        10,
        intervals,
        [
            {
                "valid_from": "2026-10-01",
                "valid_until": None,
                "work_price_per_kwh": 0.1,
                "base_price_per_month": 0,
            }
        ],
        [
            {
                "valid_from": "2026-10-01",
                "valid_until": None,
                "amount_eur": 1,
            }
        ],
        now=datetime(2026, 10, 26, 12, tzinfo=UTC),
        local_timezone=UTC,
    )

    assert result["days"][25]["consumption_kwh"] is None
    assert result["days"][25]["known_consumption_kwh"] == 99
    assert not result["days"][25]["consumption_complete"]
    assert result["days"][25]["total_cost_eur"] is None
    assert result["days"][25]["over_budget"]
    assert result["balance_to_date_eur"] < 0


def test_calculate_month_data_treats_decreasing_meter_reading_as_unknown() -> None:
    """A reset or decreasing cumulative reading is not charged as negative use."""
    result = lovelace_api.calculate_month_data(
        2026,
        10,
        [
            {
                "interval_start_utc": "2026-10-01T00:00:00+00:00",
                "interval_end_utc": "2026-10-02T00:00:00+00:00",
                "consumption_meter_start_kwh": 12,
                "consumption_meter_kwh": 3,
                "note": "sensor.energy",
            }
        ],
        [
            {
                "valid_from": "2026-10-01",
                "valid_until": None,
                "work_price_per_kwh": 0.1,
                "base_price_per_month": 0,
            }
        ],
        [],
        now=datetime(2026, 10, 2, tzinfo=UTC),
        local_timezone=UTC,
    )

    assert result["days"][0]["consumption_kwh"] is None
    assert result["days"][0]["known_consumption_kwh"] is None
    assert not result["days"][0]["consumption_complete"]
    assert result["days"][0]["work_cost_eur"] is None


def test_split_interval_by_local_midnight_conserves_consumption() -> None:
    """A cross-midnight amount is allocated by elapsed UTC time."""
    interval = lovelace_api.split_interval_by_local_day(
        datetime(2026, 10, 26, 23, 30, tzinfo=UTC),
        datetime(2026, 10, 27, 0, 30, tzinfo=UTC),
        Decimal(2),
        UTC,
    )

    assert [(segment[2], segment[3]) for segment in interval] == [
        (date(2026, 10, 26), Decimal(1)),
        (date(2026, 10, 27), Decimal(1)),
    ]
    assert sum(segment[3] for segment in interval) == Decimal(2)


def test_split_interval_uses_utc_duration_across_dst() -> None:
    """A skipped daylight-saving hour does not distort proportional amounts."""
    timezone = ZoneInfo("America/New_York")
    interval = lovelace_api.split_interval_by_local_day(
        datetime(2026, 3, 7, 23, 0, tzinfo=timezone),
        datetime(2026, 3, 8, 3, 0, tzinfo=timezone),
        Decimal(3),
        timezone,
    )

    assert [(segment[2], segment[3]) for segment in interval] == [
        (date(2026, 3, 7), Decimal(1)),
        (date(2026, 3, 8), Decimal(2)),
    ]
    assert sum(segment[3] for segment in interval) == Decimal(3)


def test_daily_costs_split_at_month_and_tariff_boundaries() -> None:
    """Daily and monthly known costs use the same split interval basis."""
    interval = {
        "interval_start_utc": "2026-10-31T23:30:00+00:00",
        "interval_end_utc": "2026-11-01T00:30:00+00:00",
        "consumption_meter_start_kwh": 100,
        "consumption_meter_kwh": 102,
        "note": "sensor.energy",
    }
    tariff_interval = {
        "interval_start_utc": "2026-10-15T23:30:00+00:00",
        "interval_end_utc": "2026-10-16T00:30:00+00:00",
        "consumption_meter_start_kwh": 200,
        "consumption_meter_kwh": 202,
        "note": "sensor.energy",
    }
    tariffs = [
        {
            "valid_from": "2026-10-01",
            "valid_until": "2026-10-15",
            "work_price_per_kwh": 0.2,
            "base_price_per_month": 31,
        },
        {
            "valid_from": "2026-10-16",
            "valid_until": "2026-10-31",
            "work_price_per_kwh": 0.3,
            "base_price_per_month": 62,
        },
        {
            "valid_from": "2026-11-01",
            "valid_until": None,
            "work_price_per_kwh": 0.4,
            "base_price_per_month": 62,
        },
    ]
    october = lovelace_api.calculate_month_data(
        2026,
        10,
        [interval, tariff_interval],
        tariffs,
        [],
        now=datetime(2026, 11, 2, tzinfo=UTC),
        local_timezone=UTC,
    )
    november = lovelace_api.calculate_month_data(
        2026,
        11,
        [interval],
        tariffs,
        [],
        now=datetime(2026, 11, 2, tzinfo=UTC),
        local_timezone=UTC,
    )

    october_last_day = october["days"][30]
    november_first_day = november["days"][0]
    october_tariff_change_days = (october["days"][14], october["days"][15])
    assert october_last_day["known_consumption_kwh"] == 1
    assert november_first_day["known_consumption_kwh"] == 1
    assert october_tariff_change_days[0]["known_consumption_kwh"] == 1
    assert october_tariff_change_days[1]["known_consumption_kwh"] == 1
    assert october_last_day["work_cost_eur"] == pytest.approx(0.3)
    assert november_first_day["work_cost_eur"] == pytest.approx(0.4)
    assert october_tariff_change_days[0]["work_cost_eur"] == pytest.approx(0.2)
    assert october_tariff_change_days[1]["work_cost_eur"] == pytest.approx(0.3)
    assert october_last_day["base_cost_eur"] == pytest.approx(2)
    assert november_first_day["base_cost_eur"] == pytest.approx(62 / 30)
    assert october["costs_to_date_eur"] == pytest.approx(
        sum(day["known_cost_eur"] for day in october["days"][:31])
    )
    assert november["costs_to_date_eur"] == pytest.approx(
        sum(day["known_cost_eur"] for day in november["days"][:2])
    )
    assert october["base_costs_to_date_eur"] == pytest.approx(47)
    assert october["work_costs_to_date_eur"] == pytest.approx(0.8)
    assert november["base_costs_to_date_eur"] == pytest.approx(2 * 62 / 30)
    assert november["work_costs_to_date_eur"] == pytest.approx(0.4)
    assert october_last_day["total_cost_eur"] is None
    assert november_first_day["total_cost_eur"] is None


def test_base_costs_are_counted_when_consumption_is_unknown() -> None:
    """The monthly base price accrues even without any meter intervals."""
    result = lovelace_api.calculate_month_data(
        2026,
        10,
        [],
        [
            {
                "valid_from": "2026-10-01",
                "valid_until": None,
                "work_price_per_kwh": 0.3,
                "base_price_per_month": 31,
            }
        ],
        [],
        now=datetime(2026, 10, 2, tzinfo=UTC),
        local_timezone=UTC,
    )

    assert result["days"][0]["consumption_kwh"] is None
    assert result["days"][0]["work_cost_eur"] is None
    assert result["days"][0]["base_cost_eur"] == pytest.approx(1)
    assert result["days"][0]["total_cost_eur"] is None
    assert result["costs_to_date_eur"] == pytest.approx(2)
    assert result["base_costs_to_date_eur"] == pytest.approx(2)
    assert result["work_costs_to_date_eur"] == 0


def test_work_costs_keep_sub_cent_precision_until_output() -> None:
    """Meter consumption and monetary values are not rounded per interval."""
    result = lovelace_api.calculate_month_data(
        2026,
        10,
        [
            {
                "interval_start_utc": "2026-10-01T00:00:00+00:00",
                "interval_end_utc": "2026-10-02T00:00:00+00:00",
                "consumption_meter_start_kwh": 0,
                "consumption_meter_kwh": 0.123456,
                "note": "sensor.energy",
            }
        ],
        [
            {
                "valid_from": "2026-10-01",
                "valid_until": None,
                "work_price_per_kwh": 0.333333,
                "base_price_per_month": 0,
            }
        ],
        [],
        now=datetime(2026, 10, 1, 12, tzinfo=UTC),
        local_timezone=UTC,
    )

    day = result["days"][0]
    expected_work_cost = float(Decimal("0.123456") * Decimal("0.333333"))
    assert day["consumption_kwh"] == pytest.approx(0.123456)
    assert day["work_cost_eur"] == pytest.approx(expected_work_cost)
    assert day["total_cost_eur"] == pytest.approx(expected_work_cost)
