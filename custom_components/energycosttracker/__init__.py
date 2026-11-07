"""energycosttracker Home Assistant integration."""

from datetime import timedelta
import hashlib
import logging
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from homeassistant.components import frontend, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import CONF_METER_ENTITY, DEFAULT_INTERVAL_MINUTES
from .database import initialize_database
from .meter_writings import async_setup_meter_writer
from .websocket import (
    websocket_get_available_years,
    websocket_get_month_data,
    websocket_get_year_data,
)

_LOGGER = logging.getLogger(__name__)

LOVELACE_CARD_PATH = "/energycosttracker_static/energycosttracker-card.js"
LOVELACE_CARD_FILE = Path(__file__).parent / "lovelace" / "energycosttracker-card.js"
CONFIG_SCHEMA = cv.config_entry_only_config_schema
PLATFORMS = [Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration."""
    websocket_api.async_register_command(hass, websocket_get_available_years)
    websocket_api.async_register_command(hass, websocket_get_month_data)
    websocket_api.async_register_command(hass, websocket_get_year_data)
    card_hash = await hass.async_add_executor_job(
        lambda: hashlib.sha256(LOVELACE_CARD_FILE.read_bytes()).hexdigest()[:12]
    )
    card_url = f"{LOVELACE_CARD_PATH}?v={card_hash}"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                LOVELACE_CARD_PATH,
                str(LOVELACE_CARD_FILE),
                cache_headers=False,
            )
        ]
    )
    await _async_register_card_resource(hass, card_url)
    return True


async def _async_register_card_resource(hass: HomeAssistant, card_url: str) -> None:
    """Register the card as a Lovelace resource so dashboards await its module."""
    if (lovelace_data := hass.data.get(LOVELACE_DATA)) is None or not isinstance(
        resources := lovelace_data.resources, ResourceStorageCollection
    ):
        frontend.add_extra_js_url(hass, card_url)
        return

    if not resources.loaded:
        await resources.async_load()
        resources.loaded = True

    card_paths = {LOVELACE_CARD_PATH, LEGACY_LOVELACE_CARD_PATH}
    for resource in resources.async_items():
        if urlsplit(resource["url"]).path not in card_paths:
            continue
        if resource["url"] != card_url:
            await resources.async_update_item(resource["id"], {"url": card_url})
        return

    await resources.async_create_item({"res_type": "module", "url": card_url})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration from a config entry."""
    await hass.async_add_executor_job(initialize_database)
    if entity_id := entry.options.get(CONF_METER_ENTITY):
        try:
            writer = await async_setup_meter_writer(
                hass,
                entity_id,
                timedelta(minutes=DEFAULT_INTERVAL_MINUTES),
            )
        except OSError, sqlite3.Error:
            _LOGGER.exception("Unable to start consumption recording for %s", entity_id)
        else:
            entry.async_on_unload(writer.async_stop)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the integration platforms."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
