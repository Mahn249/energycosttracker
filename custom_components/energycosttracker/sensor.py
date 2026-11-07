"""Sensors for the energycosttracker integration."""

from datetime import datetime
import logging
import math
import sqlite3

from homeassistant import config_entries
from homeassistant.components.sensor import RestoreSensor, SensorStateClass
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from . import tariffs
from .const import DOMAIN, SIGNAL_TARIFFS_UPDATED

_LOGGER = logging.getLogger(__name__)
_PRICE_UNIT = "EUR/kWh"
_RETRY_DELAY_SECONDS = 30


class CurrentTariffCoordinator(DataUpdateCoordinator[float | None]):
    """Fetch the tariff that is valid today from the local database."""

    def __init__(
        self, hass: HomeAssistant, config_entry: config_entries.ConfigEntry
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=config_entry)
        self._retry_unsub: CALLBACK_TYPE | None = None

    async def _async_update_data(self) -> float | None:
        """Read the current tariff without blocking Home Assistant."""
        try:
            tariff = await self.hass.async_add_executor_job(
                tariffs.get_current_tariff, dt_util.now().date()
            )
        except (OSError, sqlite3.Error) as err:
            self._schedule_retry()
            raise UpdateFailed(f"Unable to read the current tariff: {err}") from err

        if tariff is None:
            self._cancel_retry()
            return None

        try:
            price = tariff["work_price_per_kwh"]
        except (KeyError, TypeError) as err:
            self._schedule_retry()
            raise UpdateFailed(f"Invalid current tariff data: {err}") from err

        if (
            isinstance(price, bool)
            or not isinstance(price, int | float)
            or not math.isfinite(price)
        ):
            self._schedule_retry()
            raise UpdateFailed("Invalid current tariff data: work price is not finite")

        self._cancel_retry()
        return float(price)

    @callback
    def async_stop(self) -> None:
        """Cancel a pending database retry."""
        self._cancel_retry()

    @callback
    def _schedule_retry(self) -> None:
        """Schedule one retry after a transient database failure."""
        if self._retry_unsub is None:
            self._retry_unsub = async_call_later(
                self.hass, _RETRY_DELAY_SECONDS, self._async_retry
            )

    @callback
    def _cancel_retry(self) -> None:
        """Cancel a pending retry after a successful database query."""
        if self._retry_unsub is not None:
            self._retry_unsub()
            self._retry_unsub = None

    @callback
    def _async_retry(self, _now: datetime) -> None:
        """Request a retry after the scheduled delay."""
        self._retry_unsub = None
        self.hass.async_create_task(self.async_request_refresh())


async def async_setup_entry(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the current work-price sensor."""
    coordinator = CurrentTariffCoordinator(hass, entry)
    entry.async_on_unload(coordinator.async_stop)
    await coordinator.async_refresh()
    entry.async_on_unload(
        async_dispatcher_connect(
            hass, SIGNAL_TARIFFS_UPDATED, coordinator.async_request_refresh
        )
    )

    async def _async_refresh(_now: datetime) -> None:
        await coordinator.async_request_refresh()

    entry.async_on_unload(
        async_track_time_change(hass, _async_refresh, hour=0, minute=0, second=0)
    )
    async_add_entities([CurrentWorkPriceSensor(coordinator, entry)])


class CurrentWorkPriceSensor(
    CoordinatorEntity[CurrentTariffCoordinator], RestoreSensor
):
    """Show the work price for the tariff period valid today."""

    _attr_has_entity_name = True
    _attr_name = "Aktueller Arbeitspreis"
    _attr_native_unit_of_measurement = _PRICE_UNIT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: CurrentTariffCoordinator,
        entry: config_entries.ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_current_work_price"
        self._attr_native_value: float | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the last valid price when no current tariff is available."""
        await super().async_added_to_hass()
        if self.coordinator.data is not None:
            self._attr_native_value = self.coordinator.data
            return

        if (last_sensor_data := await self.async_get_last_sensor_data()) is None:
            return
        value = last_sensor_data.native_value
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            return
        self._attr_native_value = float(value)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Keep the last valid price when no tariff is valid today."""
        if self.coordinator.data is not None:
            self._attr_native_value = self.coordinator.data
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float | None:
        """Return the latest valid work price."""
        return self._attr_native_value
