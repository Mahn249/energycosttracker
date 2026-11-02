"""Sensors for the energycosttracker integration."""

from datetime import timedelta
import sqlite3

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_METER_ENTITY, DEFAULT_INTERVAL_MINUTES
from .entities import async_setup_current_work_price_sensor
from .meter_writings import MeterSensorError, async_setup_meter_writer


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up consumption recording and the current work-price sensor."""
    if entity_id := entry.options.get(CONF_METER_ENTITY):
        interval = timedelta(minutes=DEFAULT_INTERVAL_MINUTES)
        try:
            meter_writer = await async_setup_meter_writer(hass, entity_id, interval)
        except MeterSensorError as err:
            raise ConfigEntryNotReady(str(err)) from err
        except (OSError, sqlite3.Error) as err:
            raise ConfigEntryNotReady(
                f"Unable to prepare the consumption database: {err}"
            ) from err
        entry.async_on_unload(meter_writer.async_stop)

    await async_setup_current_work_price_sensor(
        hass,
        entry,
        async_add_entities,
    )
