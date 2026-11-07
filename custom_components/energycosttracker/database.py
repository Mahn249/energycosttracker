"""Database initialization and schema management for the integration."""

from pathlib import Path
import sqlite3

DATABASE_PATH = Path(__file__).resolve().parents[2] / "energy_cost_tracker.db"


def initialize_database() -> None:
    """Create the integration tables for a fresh database."""

    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS meter_intervals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                interval_start_utc TEXT NOT NULL,
                interval_end_utc TEXT NOT NULL,
                consumption_meter_kwh REAL,
                consumption_meter_start_kwh REAL,
                note TEXT
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS tariffs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                valid_from TEXT NOT NULL,
                valid_until TEXT,
                work_price_per_kwh REAL NOT NULL,
                base_price_per_month REAL NOT NULL
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                valid_from TEXT NOT NULL,
                valid_until TEXT,
                amount_eur REAL NOT NULL,
                note TEXT
            )
        """)

        connection.commit()


if __name__ == "__main__":
    initialize_database()
