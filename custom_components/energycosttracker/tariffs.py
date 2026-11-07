"""SQLite access for tariff periods."""

from datetime import date
from pathlib import Path
import sqlite3
from typing import Any

DATABASE_PATH = Path(__file__).resolve().parents[2] / "energy_cost_tracker.db"


class TariffValidationError(Exception):
    """Raised when a tariff conflicts with an existing tariff period."""

    def __init__(self, reason: str) -> None:
        """Initialize the validation error with its translation key."""
        self.reason = reason


def _connect() -> sqlite3.Connection:
    """Open the existing project database without creating a new one."""
    if not DATABASE_PATH.is_file():
        raise FileNotFoundError(f"Datenbank nicht gefunden: {DATABASE_PATH}")
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def _validate_tariff(
    connection: sqlite3.Connection,
    tariff: dict[str, Any],
    tariff_id: int | None = None,
) -> None:
    """Reject duplicate names and overlapping tariff periods."""
    name = tariff["name"].strip()
    valid_from = tariff["valid_from"]
    valid_until = tariff.get("valid_until") or None

    if valid_until is not None and valid_until < valid_from:
        raise TariffValidationError("invalid_tariff_period")

    duplicate = connection.execute(
        """SELECT 1 FROM tariffs
           WHERE lower(trim(name)) = lower(trim(?))
             AND (? IS NULL OR id != ?)""",
        (name, tariff_id, tariff_id),
    ).fetchone()
    if duplicate:
        raise TariffValidationError("duplicate_tariff_name")

    overlap = connection.execute(
        """SELECT 1 FROM tariffs
           WHERE (valid_until IS NULL OR valid_until >= ?)
             AND (? IS NULL OR valid_from <= ?)
             AND (? IS NULL OR id != ?)""",
        (valid_from, valid_until, valid_until, tariff_id, tariff_id),
    ).fetchone()
    if overlap:
        raise TariffValidationError("overlapping_tariff_period")


def list_tariffs() -> list[dict[str, Any]]:
    """Read tariff periods from the existing tariffs table."""
    with _connect() as connection:
        rows = connection.execute(
            """SELECT id, name, valid_from, valid_until,
                      work_price_per_kwh, base_price_per_month
               FROM tariffs ORDER BY valid_from, id"""
        ).fetchall()
    return [dict(row) for row in rows]


def get_current_tariff(current_date: date) -> dict[str, Any] | None:
    """Read the tariff period that is valid on the given date."""
    tariff_date = current_date.isoformat()
    with _connect() as connection:
        row = connection.execute(
            """SELECT id, name, valid_from, valid_until,
                      work_price_per_kwh, base_price_per_month
               FROM tariffs
               WHERE valid_from <= ?
                 AND (valid_until IS NULL OR valid_until >= ?)
               ORDER BY valid_from DESC, id DESC
               LIMIT 1""",
            (tariff_date, tariff_date),
        ).fetchone()
    return dict(row) if row is not None else None


def add_tariff(tariff: dict[str, Any]) -> None:
    """Insert a tariff period into the existing tariffs table."""
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _validate_tariff(connection, tariff)
        connection.execute(
            """INSERT INTO tariffs (
                   name, valid_from, valid_until,
                   work_price_per_kwh, base_price_per_month
               ) VALUES (?, ?, ?, ?, ?)""",
            (
                tariff["name"].strip(),
                tariff["valid_from"],
                tariff.get("valid_until") or None,
                float(tariff["work_price_per_kwh"]),
                float(tariff["base_price_per_month"]),
            ),
        )


def update_tariff(tariff_id: int, tariff: dict[str, Any]) -> None:
    """Update a tariff period in the existing tariffs table."""
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _validate_tariff(connection, tariff, tariff_id)
        connection.execute(
            """UPDATE tariffs
               SET name = ?, valid_from = ?, valid_until = ?,
                   work_price_per_kwh = ?, base_price_per_month = ?
               WHERE id = ?""",
            (
                tariff["name"].strip(),
                tariff["valid_from"],
                tariff.get("valid_until") or None,
                float(tariff["work_price_per_kwh"]),
                float(tariff["base_price_per_month"]),
                tariff_id,
            ),
        )


def delete_tariff(tariff_id: int) -> None:
    """Delete a tariff period from the existing tariffs table."""
    with _connect() as connection:
        connection.execute("DELETE FROM tariffs WHERE id = ?", (tariff_id,))
