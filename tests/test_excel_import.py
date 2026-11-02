"""Tests for historical Excel import validation and persistence."""

from datetime import datetime
from pathlib import Path
import sqlite3

from openpyxl import Workbook
import pytest

from energycosttracker.imports import import_excel
from energycosttracker.imports.inspect_excel import (
    EXPECTED_HEADERS,
    InvalidExcelFile,
    inspect_excel as inspect_workbook,
)


def _write_workbook(path: Path, rows: list[tuple[object, ...]]) -> None:
    """Create an import workbook for testing."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(EXPECTED_HEADERS)
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def _valid_row(
    start: datetime | str = "2025-07-25T00:00:00Z",
    end: datetime | str = "2025-07-25T00:15:00Z",
) -> tuple[datetime | str, datetime | str, float, float, str]:
    """Return one valid five-column workbook row."""
    return start, end, 26087.284, 0.135, "Abgelesen"


def _create_meter_table(database_path: Path) -> None:
    """Create the current meter table schema."""
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


def test_inspect_excel_normalizes_timestamps_and_skips_blank_rows(
    tmp_path: Path,
) -> None:
    """Inspection normalizes timezone-naive Excel timestamps as UTC."""
    file_path = tmp_path / "readings.xlsx"
    _write_workbook(
        file_path,
        [
            _valid_row(
                datetime(2025, 7, 25, 0, 0),
                datetime(2025, 7, 25, 0, 15),
            ),
            (None, None, None, None, None),
            _valid_row(
                datetime(2025, 7, 25, 0, 15),
                datetime(2025, 7, 25, 0, 30),
            ),
        ],
    )

    result = inspect_workbook(file_path)

    assert not result.errors
    assert result.empty_rows == 1
    assert result.rows[0].start == "2025-07-25T00:00:00+00:00"
    assert result.rows[0].end == "2025-07-25T00:15:00+00:00"
    assert result.rows[0].note == "Abgelesen"
    assert not hasattr(result.rows[0], "feed_in")


def test_inspect_excel_reports_invalid_value(tmp_path: Path) -> None:
    """Invalid numbers identify their cell value."""
    file_path = tmp_path / "readings.xlsx"
    _write_workbook(
        file_path,
        [
            (
                "2025-07-25T00:00:00Z",
                "2025-07-25T00:15:00Z",
                "invalid",
                0.135,
                None,
            ),
            (
                "2025-07-25T00:15:00Z",
                "2025-07-25T00:30:00Z",
                26087.419,
                0.135,
                None,
            ),
        ],
    )

    result = inspect_workbook(file_path)

    assert result.error_count == 1
    assert result.errors == [
        "Zeile 2: Zählerstand - Verbrauch (kWh): ungültiger Zahlenwert 'invalid'"
    ]
    assert len(result.rows) == 1


@pytest.mark.parametrize(
    ("consumption", "delta", "expected_error"),
    [
        (-1, 0, "Zählerstand - Verbrauch (kWh) darf nicht negativ sein"),
        (1, -1, "Delta (Verbrauch) in kWh darf nicht negativ sein"),
        (1, 2, "Delta (Verbrauch) in kWh darf den Zählerstand nicht überschreiten"),
    ],
)
def test_inspect_excel_rejects_impossible_meter_values(
    tmp_path: Path,
    consumption: float,
    delta: float,
    expected_error: str,
) -> None:
    """Meter and delta values must yield a non-negative starting reading."""
    file_path = tmp_path / "readings.xlsx"
    _write_workbook(
        file_path,
        [("2025-07-25T00:00:00Z", "2025-07-25T00:15:00Z", consumption, delta, None)],
    )

    result = inspect_workbook(file_path)

    assert result.error_count == 1
    assert result.errors == [f"Zeile 2: {expected_error}"]
    assert not result.rows


def test_inspect_excel_rejects_wrong_headers(tmp_path: Path) -> None:
    """The workbook must use the documented five-column headers."""
    file_path = tmp_path / "readings.xlsx"
    workbook = Workbook()
    workbook.active.append(
        (
            *EXPECTED_HEADERS[:3],
            "Zählerstand - Einspeisung (kWh)",
            *EXPECTED_HEADERS[3:],
        )
    )
    workbook.save(file_path)

    with pytest.raises(InvalidExcelFile):
        inspect_workbook(file_path)


def test_import_excel_skips_duplicate_intervals_and_saves_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Imports are idempotent and save meter start and note in the current schema."""
    file_path = tmp_path / "readings.xlsx"
    _write_workbook(file_path, [_valid_row(), _valid_row()])
    database_path = tmp_path / "meter.db"
    _create_meter_table(database_path)
    monkeypatch.setattr(import_excel, "DATABASE_PATH", database_path)
    rows = inspect_workbook(file_path).rows

    assert import_excel.count_existing_duplicates(rows) == 1
    assert import_excel.import_excel(rows) == import_excel.ImportResult(
        imported=1, skipped=1
    )
    assert import_excel.import_excel(rows) == import_excel.ImportResult(
        imported=0, skipped=2
    )
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            """SELECT consumption_meter_kwh, consumption_meter_start_kwh, note
               FROM meter_intervals"""
        ).fetchone() == (26087.284, 26087.149, "manual_import | Abgelesen")


def test_import_excel_skips_equivalent_timestamp_formats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A naive stored timestamp matches the equivalent UTC ISO timestamp."""
    file_path = tmp_path / "readings.xlsx"
    _write_workbook(file_path, [_valid_row()])
    database_path = tmp_path / "meter.db"
    _create_meter_table(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO meter_intervals (
                interval_start_utc, interval_end_utc, note
            ) VALUES (?, ?, ?)
            """,
            (
                "2025-07-25 00:00:00",
                "2025-07-25 00:15:00",
                "manual_import",
            ),
        )
    monkeypatch.setattr(import_excel, "DATABASE_PATH", database_path)
    rows = inspect_workbook(file_path).rows

    assert import_excel.count_existing_duplicates(rows) == 1
    assert import_excel.import_excel(rows) == import_excel.ImportResult(
        imported=0, skipped=1
    )
