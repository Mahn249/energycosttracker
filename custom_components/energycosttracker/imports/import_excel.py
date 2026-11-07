"""Import validated historical meter readings into the database."""

from dataclasses import dataclass
import sqlite3

from ..database import DATABASE_PATH  # noqa: TID252
from .inspect_excel import ImportedInterval, normalize_timestamp

IMPORT_NOTE = "manual_import"


def _get_import_note(note: str | None) -> str:
    """Keep the import marker and the workbook note in the note column."""
    return f"{IMPORT_NOTE} | {note}" if note else IMPORT_NOTE


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Counts from an Excel import."""

    imported: int
    skipped: int


def count_existing_duplicates(rows: list[ImportedInterval]) -> int:
    """Count intervals already imported or repeated within the uploaded workbook."""
    with sqlite3.connect(DATABASE_PATH) as connection:
        existing = {
            (
                normalize_timestamp(start),
                normalize_timestamp(end),
            )
            for start, end in connection.execute(
                """
                SELECT interval_start_utc, interval_end_utc
                FROM meter_intervals
                WHERE note GLOB ?
                """,
                (f"{IMPORT_NOTE}*",),
            )
        }

    seen: set[tuple[str, str]] = set()
    duplicates = 0
    for row in rows:
        key = (normalize_timestamp(row.start), normalize_timestamp(row.end))
        if key in existing or key in seen:
            duplicates += 1
        seen.add(key)
    return duplicates


def import_excel(rows: list[ImportedInterval]) -> ImportResult:
    """Insert validated intervals; existing intervals are left unchanged."""
    with sqlite3.connect(DATABASE_PATH) as connection:
        existing = {
            (
                normalize_timestamp(start),
                normalize_timestamp(end),
            )
            for start, end in connection.execute(
                """
                SELECT interval_start_utc, interval_end_utc
                FROM meter_intervals
                WHERE note GLOB ?
                """,
                (f"{IMPORT_NOTE}*",),
            )
        }
        imported = 0
        skipped = 0
        for row in rows:
            key = (normalize_timestamp(row.start), normalize_timestamp(row.end))
            if key in existing:
                skipped += 1
                continue
            connection.execute(
                """
                INSERT INTO meter_intervals (
                    interval_start_utc,
                    interval_end_utc,
                    consumption_meter_kwh,
                    consumption_meter_start_kwh,
                    note
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row.start,
                    row.end,
                    row.consumption,
                    row.consumption - row.delta,
                    _get_import_note(row.note),
                ),
            )
            imported += 1
            existing.add(key)
    return ImportResult(imported=imported, skipped=skipped)
