"""Tests for fresh database initialization."""

from pathlib import Path
import sqlite3

import pytest

from energycosttracker import database


def test_initialize_database_creates_current_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Initialization creates the current schema without migration tables."""
    database_path = tmp_path / "energy_cost_tracker.db"
    monkeypatch.setattr(database, "DATABASE_PATH", database_path)

    database.initialize_database()

    with sqlite3.connect(database_path) as connection:
        meter_columns = tuple(
            row[1] for row in connection.execute("PRAGMA table_info(meter_intervals)")
        )
        table_names = {
            row[0]
            for row in connection.execute(
                """SELECT name FROM sqlite_master
                   WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"""
            )
        }

    assert meter_columns == (
        "id",
        "interval_start_utc",
        "interval_end_utc",
        "consumption_meter_kwh",
        "consumption_meter_start_kwh",
        "note",
    )
    assert table_names == {"meter_intervals", "tariffs", "payments"}
