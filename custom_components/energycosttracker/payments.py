"""SQLite access for payment periods."""

from datetime import date
from pathlib import Path
import sqlite3
from typing import Any

DATABASE_PATH = Path(__file__).resolve().parents[2] / "energy_cost_tracker.db"


class PaymentValidationError(Exception):
    """Raised when a payment conflicts with an existing payment period."""

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


def _validate_payment(
    connection: sqlite3.Connection,
    payment: dict[str, Any],
    payment_id: int | None = None,
) -> None:
    """Reject duplicate names and overlapping payment periods."""
    name = payment["name"].strip()
    valid_from = payment["valid_from"]
    valid_until = payment.get("valid_until") or None

    if valid_until is not None and valid_until < valid_from:
        raise PaymentValidationError("invalid_payment_period")

    duplicate = connection.execute(
        """SELECT 1 FROM payments
           WHERE lower(trim(name)) = lower(trim(?))
             AND (? IS NULL OR id != ?)""",
        (name, payment_id, payment_id),
    ).fetchone()
    if duplicate:
        raise PaymentValidationError("duplicate_payment_name")

    overlap = connection.execute(
        """SELECT 1 FROM payments
           WHERE (valid_until IS NULL OR valid_until >= ?)
             AND (? IS NULL OR valid_from <= ?)
             AND (? IS NULL OR id != ?)""",
        (valid_from, valid_until, valid_until, payment_id, payment_id),
    ).fetchone()
    if overlap:
        raise PaymentValidationError("overlapping_payment_period")


def list_payments() -> list[dict[str, Any]]:
    """Read payment periods from the existing payments table."""
    with _connect() as connection:
        rows = connection.execute(
            """SELECT id, name, valid_from, valid_until, amount_eur, note
               FROM payments ORDER BY valid_from, id"""
        ).fetchall()
    return [dict(row) for row in rows]


def get_current_payment(current_date: date) -> dict[str, Any] | None:
    """Read the payment period that is valid on the given date."""
    payment_date = current_date.isoformat()
    with _connect() as connection:
        row = connection.execute(
            """SELECT id, name, valid_from, valid_until, amount_eur, note
               FROM payments
               WHERE valid_from <= ?
                 AND (valid_until IS NULL OR valid_until >= ?)
               ORDER BY valid_from DESC, id DESC
               LIMIT 1""",
            (payment_date, payment_date),
        ).fetchone()
    return dict(row) if row is not None else None


def add_payment(payment: dict[str, Any]) -> None:
    """Insert a payment period into the existing payments table."""
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _validate_payment(connection, payment)
        connection.execute(
            """INSERT INTO payments (
                   name, valid_from, valid_until, amount_eur, note
               ) VALUES (?, ?, ?, ?, ?)""",
            (
                payment["name"].strip(),
                payment["valid_from"],
                payment.get("valid_until") or None,
                float(payment["amount_eur"]),
                payment.get("note") or None,
            ),
        )


def update_payment(payment_id: int, payment: dict[str, Any]) -> None:
    """Update a payment period in the existing payments table."""
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _validate_payment(connection, payment, payment_id)
        connection.execute(
            """UPDATE payments
               SET name = ?, valid_from = ?, valid_until = ?,
                   amount_eur = ?, note = ?
               WHERE id = ?""",
            (
                payment["name"].strip(),
                payment["valid_from"],
                payment.get("valid_until") or None,
                float(payment["amount_eur"]),
                payment.get("note") or None,
                payment_id,
            ),
        )


def delete_payment(payment_id: int) -> None:
    """Delete a payment period from the existing payments table."""
    with _connect() as connection:
        connection.execute("DELETE FROM payments WHERE id = ?", (payment_id,))
