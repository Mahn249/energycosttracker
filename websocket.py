"""WebSocket API for the energycosttracker Lovelace card."""

from typing import Any

import probatio

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .lovelace_api import get_available_years, get_month_data, get_year_data


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "energycosttracker/available_years",
    }
)
@websocket_api.async_response
async def websocket_get_available_years(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return years that contain meter interval data."""
    years = await hass.async_add_executor_job(get_available_years)
    connection.send_result(msg["id"], years)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "energycosttracker/month_data",
        probatio.Required("year"): probatio.All(int, probatio.Range(min=1, max=9998)),
        probatio.Required("month"): probatio.All(int, probatio.Range(min=1, max=12)),
    }
)
@websocket_api.async_response
async def websocket_get_month_data(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return calculated data for a calendar month."""
    if not (local_timezone := dt_util.get_time_zone(hass.config.time_zone)):
        raise HomeAssistantError("Home Assistant time zone is not configured")

    result = await hass.async_add_executor_job(
        get_month_data, msg["year"], msg["month"], local_timezone
    )
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "energycosttracker/year_data",
        probatio.Required("year"): probatio.All(int, probatio.Range(min=1, max=9998)),
    }
)
@websocket_api.async_response
async def websocket_get_year_data(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return calculated data for a calendar year."""
    if not (local_timezone := dt_util.get_time_zone(hass.config.time_zone)):
        raise HomeAssistantError("Home Assistant time zone is not configured")

    result = await hass.async_add_executor_job(
        get_year_data, msg["year"], local_timezone
    )
    connection.send_result(msg["id"], result)
