"""Tests for cumulative meter validation and persistence."""

# Internal helpers are exercised to verify interval persistence.
# ruff: noqa: SLF001

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3

import pytest

from energycosttracker import meter_writings
from homeassistant.core import State


def _prepare_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    database_path = tmp_path / "meter.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("""
            CREATE TABLE meter_intervals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                interval_start_utc TEXT NOT NULL,
                interval_end_utc TEXT NOT NULL,
                consumption_meter_kwh REAL,
                consumption_meter_start_kwh REAL,
                note TEXT
            )
        """)
    monkeypatch.setattr(meter_writings, "DATABASE_PATH", database_path)
    return database_path


def test_validate_meter_state_requires_cumulative_energy_sensor() -> None:
    """Energy readings in kWh are accepted regardless of state class."""
    state = State(
        "sensor.energy",
        "12.5",
        {
            "device_class": "energy",
            "unit_of_measurement": "kWh",
        },
    )

    assert meter_writings.validate_meter_state(state, "sensor.energy") == 12.5
    state_with_other_state_class = State(
        "sensor.energy",
        "12.5",
        {
            "device_class": "energy",
            "unit_of_measurement": "kWh",
            "state_class": "measurement",
        },
    )
    assert (
        meter_writings.validate_meter_state(
            state_with_other_state_class, "sensor.energy"
        )
        == 12.5
    )

    invalid_states = [
        State("sensor.energy", "12.5", {"unit_of_measurement": "kWh"}),
        State(
            "sensor.energy",
            "12.5",
            {
                "device_class": "energy",
                "unit_of_measurement": "Wh",
            },
        ),
    ]
    for invalid_state in invalid_states:
        with pytest.raises(meter_writings.MeterSensorError):
            meter_writings.validate_meter_state(invalid_state, "sensor.energy")


def test_record_meter_readings_store_each_sensor_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Readings use per-sensor baselines and the note column as their source."""
    database_path = _prepare_database(tmp_path, monkeypatch)
    first_time = datetime(2026, 10, 26, 10, tzinfo=UTC)

    assert meter_writings._record_meter_reading("sensor.first", 100, first_time)
    assert meter_writings._record_meter_reading(
        "sensor.first", 101.25, first_time + timedelta(minutes=10)
    )
    assert meter_writings._record_meter_reading(
        "sensor.second", 5, first_time + timedelta(minutes=20)
    )
    assert meter_writings._record_meter_reading(
        "sensor.second", 2, first_time + timedelta(minutes=30)
    )
    assert meter_writings._record_meter_reading(
        "sensor.second", 2.5, first_time + timedelta(minutes=40)
    )

    with sqlite3.connect(database_path) as connection:
        columns = tuple(
            row[1] for row in connection.execute("PRAGMA table_info(meter_intervals)")
        )
        intervals = connection.execute(
            """SELECT note, consumption_meter_start_kwh, consumption_meter_kwh
               FROM meter_intervals ORDER BY id"""
        ).fetchall()

    assert columns == (
        "id",
        "interval_start_utc",
        "interval_end_utc",
        "consumption_meter_kwh",
        "consumption_meter_start_kwh",
        "note",
    )
    assert intervals == [
        ("sensor.first", None, 100),
        ("sensor.first", 100, 101.25),
        ("sensor.second", None, 5),
        ("sensor.second", 5, 2),
        ("sensor.second", 2, 2.5),
    ]


def test_record_meter_reading_ignores_stale_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale timestamp cannot produce an overlapping interval."""
    database_path = _prepare_database(tmp_path, monkeypatch)
    timestamp = datetime(2026, 10, 26, 10, tzinfo=UTC)

    assert meter_writings._record_meter_reading("sensor.energy", 10, timestamp)
    assert not meter_writings._record_meter_reading(
        "sensor.energy", 11, timestamp - timedelta(minutes=1)
    )

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM meter_intervals"
        ).fetchone() == (1,)


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan")])
def test_record_meter_reading_rejects_invalid_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: float,
) -> None:
    """Invalid cumulative values are rejected before they are stored."""
    database_path = _prepare_database(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="finite and non-negative"):
        meter_writings._record_meter_reading(
            "sensor.energy", value, datetime(2026, 10, 26, 10, tzinfo=UTC)
        )

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM meter_intervals"
        ).fetchone() == (0,)
