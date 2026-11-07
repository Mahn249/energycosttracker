import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path


DATABASE_PATH = Path(__file__).parent / "energy_cost_tracker.db"
IMPORT_NOTE = "manual_import"


def parse_timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def inspect_database():
    with sqlite3.connect(DATABASE_PATH) as connection:
        rows = connection.execute(
            """
            SELECT
                interval_start_utc,
                interval_end_utc,
                consumption_meter_kwh,
                consumption_meter_start_kwh
            FROM meter_intervals
            WHERE note GLOB ?
            ORDER BY interval_start_utc
            """,
            (f"{IMPORT_NOTE}*",),
        ).fetchall()

    print("=== 1. DATENBESTAND ===")
    print(f"Datensätze: {len(rows)}")

    if not rows:
        print("Keine Daten gefunden.")
        return

    starts = [parse_timestamp(row[0]) for row in rows]
    print(f"Erstes Intervall: {rows[0][0]}")
    print(f"Letztes Intervall: {rows[-1][1]}")
    print(f"Zeitraum: {(starts[-1] - starts[0]).days} Tage")

    print("\n=== 2. MESSWERTE ===")
    consumption = [row[2] for row in rows if row[2] is not None]
    consumption_start = [row[3] for row in rows if row[3] is not None]

    print(f"Fehlende Verbrauchszählerstände: {len(rows) - len(consumption)}")
    print(
        f"Fehlende Verbrauchszählerstände am Intervallstart: "
        f"{len(rows) - len(consumption_start)}"
    )

    if consumption:
        print(
            f"Verbrauchszählerstand: {min(consumption):.3f} bis {max(consumption):.3f} kWh"
        )
    if consumption_start:
        print(
            f"Verbrauchszählerstand am Intervallstart: "
            f"{min(consumption_start):.3f} bis {max(consumption_start):.3f} kWh"
        )

    print("\n=== 3. ZEITLICHE LÜCKEN ===")
    gaps = []
    expected = timedelta(minutes=15)

    for index in range(1, len(starts)):
        difference = starts[index] - starts[index - 1]
        if difference != expected:
            gaps.append((rows[index - 1][0], rows[index][0], difference))

    print(f"Abweichungen vom 15-Minuten-Rhythmus: {len(gaps)}")
    for previous, current, difference in gaps[:10]:
        print(f"{previous} -> {current}: Abstand {difference}")
    if len(gaps) > 10:
        print(f"... und {len(gaps) - 10} weitere")

    print("\n=== 4. VERBRAUCH PRO MONAT (UTC) ===")
    monthly = defaultdict(lambda: {"intervals": 0})
    daily_counts = Counter()

    for row, start in zip(rows, starts):
        month = start.strftime("%Y-%m")
        day = start.strftime("%Y-%m-%d")

        monthly[month]["intervals"] += 1
        daily_counts[day] += 1

    for month in sorted(monthly):
        values = monthly[month]
        print(f"{month}: {values['intervals']} Intervalle")

    print("\n=== 5. UNVOLLSTÄNDIGE TAGE (UTC) ===")
    days = sorted(daily_counts)
    incomplete = [
        (day, daily_counts[day]) for day in days[1:-1] if daily_counts[day] != 96
    ]

    print(f"Unvollständige Tage innerhalb des Zeitraums: {len(incomplete)}")
    for day, count in incomplete[:10]:
        print(f"{day}: {count} Intervalle statt 96")
    if len(incomplete) > 10:
        print(f"... und {len(incomplete) - 10} weitere")


if __name__ == "__main__":
    inspect_database()
