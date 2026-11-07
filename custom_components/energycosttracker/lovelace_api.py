"""Prepare daily consumption and cost data for the Lovelace card."""

from calendar import monthrange
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3
from typing import Any

from homeassistant.util import dt as dt_util

DATABASE_PATH = Path(__file__).resolve().parents[2] / "energy_cost_tracker.db"


def _read_month_data(
    year: int, month: int, local_timezone: tzinfo
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Read intervals, tariffs, and payment periods needed for one month."""
    month_start = datetime.combine(date(year, month, 1), time.min, local_timezone)
    next_month_start = datetime.combine(
        date(year + (month == 12), month % 12 + 1, 1), time.min, local_timezone
    )
    query_start = dt_util.as_utc(month_start).isoformat()
    query_end = dt_util.as_utc(next_month_start).isoformat()

    if not DATABASE_PATH.is_file():
        raise FileNotFoundError(f"Datenbank nicht gefunden: {DATABASE_PATH}")

    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.row_factory = sqlite3.Row
        intervals = [
            dict(row)
            for row in connection.execute(
                """SELECT interval_start_utc, interval_end_utc,
                          consumption_meter_kwh, consumption_meter_start_kwh, note
                   FROM meter_intervals
                   WHERE datetime(interval_start_utc) < datetime(?)
                     AND datetime(interval_end_utc) > datetime(?)
                   ORDER BY interval_start_utc""",
                (query_end, query_start),
            ).fetchall()
        ]
        tariffs = [
            dict(row)
            for row in connection.execute(
                """SELECT valid_from, valid_until, work_price_per_kwh,
                          base_price_per_month
                   FROM tariffs
                   WHERE valid_from <= ?
                     AND (valid_until IS NULL OR valid_until >= ?)
                   ORDER BY valid_from""",
                (next_month_start.date().isoformat(), month_start.date().isoformat()),
            ).fetchall()
        ]
        payments = [
            dict(row)
            for row in connection.execute(
                """SELECT valid_from, valid_until, amount_eur
                   FROM payments
                   WHERE valid_from <= ?
                     AND (valid_until IS NULL OR valid_until >= ?)
                   ORDER BY valid_from""",
                (next_month_start.date().isoformat(), month_start.date().isoformat()),
            ).fetchall()
        ]
    return intervals, tariffs, payments


def _parse_timestamp(value: str) -> datetime:
    """Parse a timestamp stored in UTC."""
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _valid_number(value: Any) -> Decimal | None:
    """Return a finite Decimal value or None."""
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation, ValueError:
        return None
    return result if result.is_finite() else None


def _period_value(periods: list[dict[str, Any]], day: date) -> dict[str, Any] | None:
    """Return the period active on a given date."""
    day_string = day.isoformat()
    return next(
        (
            period
            for period in periods
            if period["valid_from"] <= day_string
            and (period["valid_until"] is None or period["valid_until"] >= day_string)
        ),
        None,
    )


def split_interval_by_local_day(
    start: datetime,
    end: datetime,
    consumption_kwh: Decimal | None,
    local_timezone: tzinfo,
) -> list[tuple[datetime, datetime, date, Decimal | None]]:
    """Split an interval at local midnights, allocating its amount by UTC time."""
    start_utc = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)
    if end_utc <= start_utc:
        return []

    duration_seconds = Decimal(str((end_utc - start_utc).total_seconds()))
    cursor = start_utc
    remaining = consumption_kwh
    segments: list[tuple[datetime, datetime, date, Decimal | None]] = []
    while cursor < end_utc:
        local_day = cursor.astimezone(local_timezone).date()
        next_midnight = datetime.combine(
            local_day + timedelta(days=1), time.min, local_timezone
        ).astimezone(UTC)
        segment_end = min(end_utc, next_midnight)
        segment_seconds = Decimal(str((segment_end - cursor).total_seconds()))
        if consumption_kwh is None:
            segment_consumption = None
        elif segment_end == end_utc:
            segment_consumption = remaining
        else:
            segment_consumption = consumption_kwh * segment_seconds / duration_seconds
            remaining -= segment_consumption
        segments.append((cursor, segment_end, local_day, segment_consumption))
        cursor = segment_end
    return segments


def get_available_years() -> list[int]:
    """Return years that contain meter interval data, newest first."""
    if not DATABASE_PATH.is_file():
        raise FileNotFoundError(f"Datenbank nicht gefunden: {DATABASE_PATH}")

    with sqlite3.connect(DATABASE_PATH) as connection:
        years = connection.execute("""
            SELECT DISTINCT CAST(strftime('%Y', interval_start_utc) AS INTEGER)
            FROM meter_intervals
            WHERE strftime('%Y', interval_start_utc) IS NOT NULL
              AND CAST(strftime('%Y', interval_start_utc) AS INTEGER)
                  BETWEEN 1 AND 9998
            ORDER BY 1 DESC
        """).fetchall()

    return [year for (year,) in years]


def _calculate_month_data(
    year: int,
    month: int,
    intervals: list[dict[str, Any]],
    tariffs: list[dict[str, Any]],
    payments: list[dict[str, Any]],
    *,
    now: datetime,
    local_timezone: tzinfo,
) -> dict[str, Any]:
    """Calculate daily consumption and cost values from split meter intervals."""
    days_in_month = monthrange(year, month)[1]
    month_start = date(year, month, 1)
    month_end = date(year, month, days_in_month)
    today = now.astimezone(local_timezone).date()
    through_date = min(today, month_end)
    if through_date < month_start:
        through_date = month_start - timedelta(days=1)

    intervals_by_day_and_source: dict[
        date, dict[str, list[tuple[datetime, datetime, Decimal | None]]]
    ] = {}
    for interval in intervals:
        start = _parse_timestamp(interval["interval_start_utc"])
        end = _parse_timestamp(interval["interval_end_utc"])
        meter_start = _valid_number(interval.get("consumption_meter_start_kwh"))
        meter_end = _valid_number(interval.get("consumption_meter_kwh"))
        delta = (
            meter_end - meter_start
            if meter_start is not None and meter_end is not None
            else None
        )
        if delta is not None and delta < 0:
            delta = None
        note = interval.get("note")
        source = str(note).partition(" | ")[0] if note else "unknown"
        for (
            segment_start,
            segment_end,
            segment_day,
            segment_delta,
        ) in split_interval_by_local_day(
            start,
            end,
            delta,
            local_timezone,
        ):
            intervals_by_day_and_source.setdefault(segment_day, {}).setdefault(
                source, []
            ).append((segment_start, segment_end, segment_delta))

    days: list[dict[str, Any]] = []
    for day_number in range(1, days_in_month + 1):
        day = date(year, month, day_number)
        tariff = _period_value(tariffs, day)
        payment = _period_value(payments, day)
        next_day = day + timedelta(days=1)
        day_start = datetime.combine(day, time.min, local_timezone).astimezone(UTC)
        day_end = datetime.combine(next_day, time.min, local_timezone).astimezone(UTC)
        consumption_by_source: list[tuple[float, float, str, Decimal | None, bool]] = []
        for source, source_intervals in intervals_by_day_and_source.get(
            day, {}
        ).items():
            cursor = day_start
            consumption_kwh = Decimal(0)
            known_coverage = 0.0
            total_coverage = 0.0
            has_known_interval = False
            has_unknown = False
            complete = True
            for start, end, delta in sorted(source_intervals):
                if end <= start or start < cursor:
                    complete = False
                    continue
                if start != cursor:
                    complete = False
                segment_seconds = (end - start).total_seconds()
                total_coverage += segment_seconds
                if delta is None:
                    has_unknown = True
                else:
                    consumption_kwh += delta
                    known_coverage += segment_seconds
                    has_known_interval = True
                cursor = end
            if cursor != day_end or has_unknown:
                complete = False
            if source_intervals:
                consumption_by_source.append(
                    (
                        total_coverage,
                        known_coverage,
                        source,
                        consumption_kwh if has_known_interval else None,
                        complete,
                    )
                )

        if consumption_by_source:
            (
                _,
                _,
                consumption_source,
                known_consumption_kwh,
                consumption_complete,
            ) = max(
                consumption_by_source,
                key=lambda item: (item[0], item[1], item[2]),
            )
        else:
            consumption_source = None
            known_consumption_kwh = None
            consumption_complete = False
        consumption_kwh = known_consumption_kwh if consumption_complete else None

        daily_payment_eur = (
            Decimal(str(payment["amount_eur"])) / days_in_month if payment else None
        )
        base_cost_eur = (
            Decimal(str(tariff["base_price_per_month"])) / days_in_month
            if tariff
            else None
        )
        work_cost_eur = (
            known_consumption_kwh * Decimal(str(tariff["work_price_per_kwh"]))
            if known_consumption_kwh is not None and tariff
            else None
        )
        known_cost_eur = sum(
            (cost for cost in (base_cost_eur, work_cost_eur) if cost is not None),
            Decimal(0),
        )
        total_cost_eur = (
            base_cost_eur + work_cost_eur
            if (
                base_cost_eur is not None
                and work_cost_eur is not None
                and consumption_complete
            )
            else None
        )
        days.append(
            {
                "date": day.isoformat(),
                "consumption_kwh": consumption_kwh,
                "known_consumption_kwh": known_consumption_kwh,
                "consumption_complete": consumption_complete,
                "consumption_source": (
                    "intervals" if consumption_source is not None else None
                ),
                "work_cost_eur": work_cost_eur,
                "base_cost_eur": base_cost_eur,
                "known_cost_eur": known_cost_eur,
                "total_cost_eur": total_cost_eur,
                "daily_payment_eur": daily_payment_eur,
                "balance_eur": (
                    daily_payment_eur - total_cost_eur
                    if daily_payment_eur is not None and total_cost_eur is not None
                    else None
                ),
                "over_budget": (
                    known_cost_eur > daily_payment_eur
                    if daily_payment_eur is not None
                    else None
                ),
            }
        )

    elapsed_days = max(0, (through_date - month_start).days + 1)
    elapsed = days[:elapsed_days]
    payment_to_date = sum(
        (
            day["daily_payment_eur"]
            for day in elapsed
            if day["daily_payment_eur"] is not None
        ),
        Decimal(0),
    )
    base_costs_to_date = sum(
        (day["base_cost_eur"] for day in elapsed if day["base_cost_eur"] is not None),
        Decimal(0),
    )
    work_costs_to_date = sum(
        (day["work_cost_eur"] for day in elapsed if day["work_cost_eur"] is not None),
        Decimal(0),
    )
    costs_to_date = sum((day["known_cost_eur"] for day in elapsed), Decimal(0))
    cost_days_count = sum(day["total_cost_eur"] is not None for day in elapsed)
    return {
        "year": year,
        "month": month,
        "current_year": today.year,
        "current_month": today.month,
        "current_date": today.isoformat(),
        "days": days,
        "payment_to_date_eur": payment_to_date,
        "base_costs_to_date_eur": base_costs_to_date,
        "work_costs_to_date_eur": work_costs_to_date,
        "costs_to_date_eur": costs_to_date,
        "balance_to_date_eur": payment_to_date - costs_to_date,
        "has_cost_data": any(
            day["base_cost_eur"] is not None or day["work_cost_eur"] is not None
            for day in elapsed
        ),
        "cost_days_count": cost_days_count,
    }


def _json_numbers(value: Any) -> Any:
    """Convert Decimal values to JSON-compatible numbers."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _json_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_numbers(item) for item in value]
    return value


def calculate_month_data(
    year: int,
    month: int,
    intervals: list[dict[str, Any]],
    tariffs: list[dict[str, Any]],
    payments: list[dict[str, Any]],
    *,
    now: datetime,
    local_timezone: tzinfo,
) -> dict[str, Any]:
    """Calculate per-day data and convert monetary values for JSON output."""
    return _json_numbers(
        _calculate_month_data(
            year,
            month,
            intervals,
            tariffs,
            payments,
            now=now,
            local_timezone=local_timezone,
        )
    )


def get_month_data(year: int, month: int, local_timezone: tzinfo) -> dict[str, Any]:
    """Load database data and calculate chart values for one month."""
    intervals, tariffs, payments = _read_month_data(year, month, local_timezone)
    return calculate_month_data(
        year,
        month,
        intervals,
        tariffs,
        payments,
        now=dt_util.now(),
        local_timezone=local_timezone,
    )


def get_year_data(year: int, local_timezone: tzinfo) -> dict[str, Any]:
    """Load and calculate monthly consumption costs for a calendar year."""
    now = dt_util.now()
    months: list[dict[str, Any]] = []
    payment_to_date = Decimal(0)
    costs_to_date = Decimal(0)
    base_costs_to_date = Decimal(0)
    work_costs_to_date = Decimal(0)
    cost_days_count = 0

    for month in range(1, 13):
        intervals, tariffs, payments = _read_month_data(year, month, local_timezone)
        data = _calculate_month_data(
            year,
            month,
            intervals,
            tariffs,
            payments,
            now=now,
            local_timezone=local_timezone,
        )
        month_payment = sum(
            (
                day["daily_payment_eur"]
                for day in data["days"]
                if day["daily_payment_eur"] is not None
            ),
            Decimal(0),
        )
        has_payment = any(day["daily_payment_eur"] is not None for day in data["days"])
        month_costs = data["costs_to_date_eur"] if data["has_cost_data"] else None
        months.append(
            {
                "month": month,
                "costs_eur": month_costs,
                "base_costs_eur": data["base_costs_to_date_eur"],
                "work_costs_eur": data["work_costs_to_date_eur"],
                "payment_eur": month_payment if has_payment else None,
                "payment_to_date_eur": data["payment_to_date_eur"],
                "balance_eur": (
                    data["payment_to_date_eur"] - data["costs_to_date_eur"]
                    if data["has_cost_data"]
                    else None
                ),
                "cost_days_count": data["cost_days_count"],
            }
        )

        if now.astimezone(local_timezone).date() >= date(year, month, 1):
            payment_to_date += data["payment_to_date_eur"]
            costs_to_date += data["costs_to_date_eur"]
            base_costs_to_date += data["base_costs_to_date_eur"]
            work_costs_to_date += data["work_costs_to_date_eur"]
            cost_days_count += data["cost_days_count"]

    return _json_numbers(
        {
            "year": year,
            "current_year": now.astimezone(local_timezone).year,
            "current_month": now.astimezone(local_timezone).month,
            "months": months,
            "payment_to_date_eur": payment_to_date,
            "base_costs_to_date_eur": base_costs_to_date,
            "work_costs_to_date_eur": work_costs_to_date,
            "costs_to_date_eur": costs_to_date,
            "balance_to_date_eur": payment_to_date - costs_to_date,
            "cost_days_count": cost_days_count,
        }
    )
