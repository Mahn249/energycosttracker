"""Record consumption intervals from a cumulative Home Assistant energy sensor."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
import math
import sqlite3

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, State
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .database import DATABASE_PATH

_LOGGER = logging.getLogger(__name__)
_ENERGY_UNIT = "kWh"


class MeterSensorError(Exception):
    """Raised when the configured meter sensor is unsupported."""


def validate_meter_state(state: State | None, entity_id: str) -> float:
    """Return a valid cumulative kWh reading or raise a configuration error."""
    if state is None:
        raise MeterSensorError(f"Sensor {entity_id} is not available")
    if (
        state.attributes.get(ATTR_DEVICE_CLASS) != SensorDeviceClass.ENERGY
        or state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) != _ENERGY_UNIT
    ):
        raise MeterSensorError(f"Sensor {entity_id} must be an energy sensor in kWh")
    try:
        value = float(state.state)
    except ValueError as err:
        raise MeterSensorError(
            f"Sensor {entity_id} has no numeric reading: {state.state}"
        ) from err
    if not math.isfinite(value) or value < 0:
        raise MeterSensorError(
            f"Sensor {entity_id} has an invalid cumulative reading: {state.state}"
        )
    return value


def _record_meter_reading(
    entity_id: str, value_kwh: float, timestamp: datetime
) -> bool:
    """Store one cumulative reading and the previous reading for the same sensor."""
    if not math.isfinite(value_kwh) or value_kwh < 0:
        raise ValueError("Cumulative meter readings must be finite and non-negative")

    timestamp_utc = dt_util.as_utc(timestamp).isoformat()
    with sqlite3.connect(DATABASE_PATH, timeout=30) as connection:
        connection.execute("BEGIN IMMEDIATE")
        previous = connection.execute(
            """SELECT interval_end_utc, consumption_meter_kwh
               FROM meter_intervals
               WHERE note = ?
               ORDER BY datetime(interval_end_utc) DESC, id DESC
               LIMIT 1""",
            (entity_id,),
        ).fetchone()
        if previous is not None and dt_util.as_utc(
            datetime.fromisoformat(previous[0])
        ) >= dt_util.as_utc(timestamp):
            return False

        if previous is not None:
            interval_start = str(previous[0])
            start_value = previous[1]
        else:
            interval_start = timestamp_utc
            start_value = None

        connection.execute(
            """INSERT INTO meter_intervals (
                   interval_start_utc, interval_end_utc,
                   consumption_meter_kwh, consumption_meter_start_kwh,
                   note
               ) VALUES (?, ?, ?, ?, ?)""",
            (
                interval_start,
                timestamp_utc,
                value_kwh,
                start_value,
                entity_id,
            ),
        )
    return True


@dataclass(repr=False)
class MeterIntervalWriter:
    """Persist periodic readings from one cumulative energy sensor."""

    hass: HomeAssistant
    entity_id: str
    interval: timedelta
    _unsubscribe_interval: CALLBACK_TYPE | None = field(init=False, default=None)

    @classmethod
    async def async_create(
        cls,
        hass: HomeAssistant,
        entity_id: str,
        interval: timedelta,
    ) -> MeterIntervalWriter:
        """Create a writer and persist an initial reading when it is valid."""
        writer = cls(hass, entity_id, interval)
        await writer._async_record_reading(dt_util.utcnow())
        return writer

    def async_start(self) -> None:
        """Start periodic energy meter sampling."""
        if self._unsubscribe_interval is not None:
            return
        self._unsubscribe_interval = async_track_time_interval(
            self.hass, self._async_finish_interval, self.interval
        )

    def async_stop(self) -> None:
        """Stop periodic energy meter sampling."""
        if self._unsubscribe_interval is not None:
            self._unsubscribe_interval()
            self._unsubscribe_interval = None

    async def _async_finish_interval(self, now: datetime) -> None:
        """Validate and atomically persist the latest cumulative reading."""
        await self._async_record_reading(now)

    async def _async_record_reading(self, timestamp: datetime) -> None:
        """Record a reading when the selected sensor and database are available."""
        try:
            value_kwh = validate_meter_state(
                self.hass.states.get(self.entity_id), self.entity_id
            )
        except MeterSensorError as err:
            _LOGGER.warning(
                "Skipping invalid cumulative energy reading from %s: %s",
                self.entity_id,
                err,
            )
            return

        try:
            stored = await self.hass.async_add_executor_job(
                _record_meter_reading, self.entity_id, value_kwh, timestamp
            )
        except (OSError, sqlite3.Error, ValueError):
            _LOGGER.exception(
                "Unable to store cumulative energy reading for %s",
                self.entity_id,
            )
            return

        if not stored:
            _LOGGER.debug(
                "Ignoring stale cumulative energy reading from %s", self.entity_id
            )


async def async_setup_meter_writer(
    hass: HomeAssistant,
    entity_id: str,
    interval: timedelta,
) -> MeterIntervalWriter:
    """Start recording consumption for the selected entity."""
    writer = await MeterIntervalWriter.async_create(hass, entity_id, interval)
    writer.async_start()
    return writer
