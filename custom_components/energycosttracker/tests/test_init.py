"""Tests for config-entry setup and meter writer lifecycle."""

# ruff: noqa: SLF001

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import energycosttracker as integration
from energycosttracker import meter_writings
from energycosttracker.const import CONF_METER_ENTITY, DEFAULT_INTERVAL_MINUTES
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.core import State


@pytest.mark.asyncio
async def test_register_card_resource_for_lovelace_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dashboard resource loads the card module with the dashboard."""
    resources = Mock(spec=ResourceStorageCollection)
    resources.loaded = False
    resources.async_items.return_value = []
    resources.async_load = AsyncMock()
    resources.async_create_item = AsyncMock()
    hass = SimpleNamespace(data={LOVELACE_DATA: SimpleNamespace(resources=resources)})
    add_extra_module = Mock()
    monkeypatch.setattr(integration.frontend, "add_extra_js_url", add_extra_module)

    await integration._async_register_card_resource(
        hass, "/energycosttracker_static/energycosttracker-card.js?v=test"
    )

    resources.async_load.assert_awaited_once()
    resources.async_create_item.assert_awaited_once_with(
        {
            "res_type": "module",
            "url": "/energycosttracker_static/energycosttracker-card.js?v=test",
        }
    )
    add_extra_module.assert_not_called()


@pytest.mark.asyncio
async def test_register_card_resource_updates_existing_and_legacy_urls() -> None:
    """An existing current or legacy resource is replaced instead of duplicated."""
    for existing_url in (
        "/energycosttracker_static/energycosttracker-card.js?v=old",
        "/stromkostentracker_static/stromkostentracker-card.js?v=old",
    ):
        resources = Mock(spec=ResourceStorageCollection)
        resources.loaded = True
        resources.async_items.return_value = [
            {"id": "resource_id", "url": existing_url}
        ]
        resources.async_update_item = AsyncMock()
        resources.async_create_item = AsyncMock()
        hass = SimpleNamespace(
            data={LOVELACE_DATA: SimpleNamespace(resources=resources)}
        )

        await integration._async_register_card_resource(
            hass, "/energycosttracker_static/energycosttracker-card.js?v=new"
        )

        resources.async_update_item.assert_awaited_once_with(
            "resource_id",
            {"url": "/energycosttracker_static/energycosttracker-card.js?v=new"},
        )
        resources.async_create_item.assert_not_awaited()


@pytest.mark.asyncio
async def test_register_card_resource_falls_back_without_storage_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """YAML dashboards continue using Home Assistant's extra-module loader."""
    hass = SimpleNamespace(data={})
    add_extra_module = Mock()
    monkeypatch.setattr(integration.frontend, "add_extra_js_url", add_extra_module)

    await integration._async_register_card_resource(hass, "/card.js")

    add_extra_module.assert_called_once_with(hass, "/card.js")


def _fake_hass() -> SimpleNamespace:
    """Create the Home Assistant methods used during config-entry setup."""
    return SimpleNamespace(
        async_add_executor_job=AsyncMock(),
        config_entries=SimpleNamespace(async_forward_entry_setups=AsyncMock()),
    )


def _fake_entry(options: dict[str, str]) -> SimpleNamespace:
    """Create a config entry that records unload callbacks."""
    return SimpleNamespace(
        options=options,
        async_on_unload=Mock(),
        add_update_listener=Mock(return_value=Mock()),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [{}, {CONF_METER_ENTITY: "sensor.missing"}],
    ids=["not-configured", "configured"],
)
async def test_setup_entry_keeps_sensor_platform_available_on_writer_failure(
    monkeypatch: pytest.MonkeyPatch, options: dict[str, str]
) -> None:
    """Writer startup failures do not block platform forwarding."""
    hass = _fake_hass()
    entry = _fake_entry(options)
    monkeypatch.setattr(integration, "initialize_database", Mock())
    hass.async_add_executor_job.side_effect = lambda function: function()
    start_writer = AsyncMock(side_effect=sqlite3.OperationalError("locked"))
    monkeypatch.setattr(integration, "async_setup_meter_writer", start_writer)

    assert await integration.async_setup_entry(hass, entry)
    hass.config_entries.async_forward_entry_setups.assert_awaited_once()
    assert start_writer.await_count == bool(options)
    if options:
        start_writer.assert_awaited_once_with(
            hass,
            "sensor.missing",
            timedelta(minutes=DEFAULT_INTERVAL_MINUTES),
        )


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_setup_entry_registers_writer_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unloading the entry stops its active meter writer."""
    hass = _fake_hass()
    entry = _fake_entry({CONF_METER_ENTITY: "sensor.energy"})
    writer = SimpleNamespace(async_stop=Mock())
    monkeypatch.setattr(integration, "initialize_database", Mock())
    hass.async_add_executor_job.side_effect = lambda function: function()
    monkeypatch.setattr(
        integration, "async_setup_meter_writer", AsyncMock(return_value=writer)
    )

    await integration.async_setup_entry(hass, entry)

    unload_callbacks = [call.args[0] for call in entry.async_on_unload.call_args_list]
    assert writer.async_stop in unload_callbacks
    next(callback for callback in unload_callbacks if callback == writer.async_stop)()
    writer.async_stop.assert_called_once()


@pytest.mark.parametrize(
    "initial_state",
    [
        None,
        State(
            "sensor.energy",
            "unknown",
            {"device_class": "energy", "unit_of_measurement": "kWh"},
        ),
    ],
    ids=["missing", "unknown"],
)
async def test_meter_writer_resumes_after_invalid_reading_and_stops_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    initial_state: State | None,
) -> None:
    """A writer remains scheduled while readings are invalid and can resume."""
    database_path = tmp_path / "meter.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("""
            CREATE TABLE meter_intervals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                interval_start_utc TEXT NOT NULL,
                interval_end_utc TEXT NOT NULL,
                consumption_meter_kwh REAL,
                consumption_meter_start_kwh REAL,
                note TEXT
            )
        """)
    monkeypatch.setattr(meter_writings, "DATABASE_PATH", database_path)

    states: dict[str, State | None] = {"sensor.energy": initial_state}
    timer_callbacks: list[Callable[[datetime], None]] = []
    timer_unsubscribers: list[Mock] = []

    def track_interval(
        _hass: object,
        callback: Callable[[datetime], object],
        _interval: timedelta,
    ) -> Callable[[], None]:
        timer_callbacks.append(callback)
        unsubscribe = Mock()
        timer_unsubscribers.append(unsubscribe)
        return unsubscribe

    async def async_add_executor_job(
        function: Callable[..., object], *args: object
    ) -> object:
        return await asyncio.to_thread(function, *args)

    hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get),
        async_add_executor_job=async_add_executor_job,
    )
    monkeypatch.setattr(meter_writings, "async_track_time_interval", track_interval)

    writer = await meter_writings.async_setup_meter_writer(
        hass, "sensor.energy", timedelta(minutes=DEFAULT_INTERVAL_MINUTES)
    )
    assert timer_callbacks

    states["sensor.energy"] = State(
        "sensor.energy",
        "12.5",
        {"device_class": "energy", "unit_of_measurement": "kWh"},
    )
    await timer_callbacks[0](datetime(2026, 11, 7, 12, tzinfo=UTC))
    writer.async_start()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT consumption_meter_kwh FROM meter_intervals"
        ).fetchall() == [(12.5,)]

    assert len(timer_callbacks) == 1
    writer.async_stop()
    writer.async_stop()
    timer_unsubscribers[0].assert_called_once()
