"""energycosttracker Home Assistant integration."""

import hashlib
from pathlib import Path

from homeassistant.components import frontend, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .database import initialize_database
from .websocket import (
    websocket_get_available_years,
    websocket_get_month_data,
    websocket_get_year_data,
)

LOVELACE_CARD_PATH = "/energycosttracker_static/energycosttracker-card.js"
LOVELACE_CARD_FILE = Path(__file__).parent / "lovelace" / "energycosttracker-card.js"
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
    frontend.add_extra_js_url(hass, card_url)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration from a config entry."""
    await hass.async_add_executor_job(initialize_database)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the integration platforms."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
