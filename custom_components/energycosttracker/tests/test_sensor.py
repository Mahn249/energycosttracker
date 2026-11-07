"""Tests for the current work-price sensor."""

# ruff: noqa: SLF001

from datetime import date
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from energycosttracker import sensor
from homeassistant.helpers.update_coordinator import UpdateFailed


def _coordinator(
    tariff: dict[str, object] | None = None,
    error: Exception | None = None,
) -> sensor.CurrentTariffCoordinator:
    """Create a coordinator without starting Home Assistant services."""

    async def async_add_executor_job(
        function: object, *args: object
    ) -> dict[str, object] | None:
        if error is not None:
            raise error
        assert callable(function)
        return tariff

    coordinator = object.__new__(sensor.CurrentTariffCoordinator)
    coordinator.hass = SimpleNamespace(async_add_executor_job=async_add_executor_job)
    coordinator._retry_unsub = None
    return coordinator


@pytest.mark.asyncio
async def test_coordinator_reads_current_price_in_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The coordinator reads today's tariff using the executor."""
    tariff = {"work_price_per_kwh": 0.284}
    coordinator = _coordinator(tariff)
    get_current_tariff = Mock(return_value=tariff)
    executor_call = AsyncMock(side_effect=lambda function, *args: function(*args))
    coordinator.hass.async_add_executor_job = executor_call
    monkeypatch.setattr(sensor.tariffs, "get_current_tariff", get_current_tariff)
    monkeypatch.setattr(
        sensor.dt_util,
        "now",
        lambda: SimpleNamespace(date=lambda: date(2026, 11, 7)),
    )

    assert await coordinator._async_update_data() == 0.284
    executor_call.assert_awaited_once_with(get_current_tariff, date(2026, 11, 7))


@pytest.mark.asyncio
async def test_coordinator_retries_database_error_and_clears_retry_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A database error schedules one retry, which a success cancels."""
    coordinator = _coordinator(error=sqlite3.OperationalError("locked"))
    timer_unsub = Mock()
    retry = Mock(return_value=timer_unsub)
    monkeypatch.setattr(sensor, "async_call_later", retry)
    monkeypatch.setattr(
        sensor.tariffs,
        "get_current_tariff",
        Mock(side_effect=sqlite3.OperationalError("locked")),
    )

    with pytest.raises(UpdateFailed, match="Unable to read the current tariff"):
        await coordinator._async_update_data()
    retry.assert_called_once()

    async def async_add_executor_job(function: object, *_args: object) -> None:
        assert callable(function)

    coordinator.hass.async_add_executor_job = async_add_executor_job
    monkeypatch.setattr(sensor.tariffs, "get_current_tariff", Mock(return_value=None))
    assert await coordinator._async_update_data() is None
    timer_unsub.assert_called_once()
    assert coordinator._retry_unsub is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("current_price", "restored_price", "expected_price"),
    [
        (0.25, 0.20, 0.25),
        (None, 0.20, 0.20),
        (None, None, None),
    ],
)
async def test_sensor_uses_current_or_restored_price(
    monkeypatch: pytest.MonkeyPatch,
    current_price: float | None,
    restored_price: float | None,
    expected_price: float | None,
) -> None:
    """A tariff gap falls back to the last valid restored price."""
    monkeypatch.setattr(sensor.CoordinatorEntity, "async_added_to_hass", AsyncMock())
    coordinator = SimpleNamespace(data=current_price, last_update_success=True)
    entry = SimpleNamespace(entry_id="entry_id")
    work_price_sensor = sensor.CurrentWorkPriceSensor(coordinator, entry)
    work_price_sensor.async_get_last_sensor_data = AsyncMock(
        return_value=(
            None
            if restored_price is None
            else SimpleNamespace(native_value=restored_price)
        )
    )

    await work_price_sensor.async_added_to_hass()

    assert work_price_sensor.native_value == expected_price
    assert work_price_sensor.available


@pytest.mark.asyncio
async def test_sensor_retains_price_when_database_fails_then_recovers_without_tariff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sensor keeps its old price through an error and a successful gap."""
    monkeypatch.setattr(sensor.CoordinatorEntity, "async_added_to_hass", AsyncMock())
    coordinator = SimpleNamespace(data=None, last_update_success=False)
    work_price_sensor = sensor.CurrentWorkPriceSensor(
        coordinator, SimpleNamespace(entry_id="entry_id")
    )
    work_price_sensor.async_get_last_sensor_data = AsyncMock(
        return_value=SimpleNamespace(native_value=0.31)
    )
    await work_price_sensor.async_added_to_hass()
    assert not work_price_sensor.available
    assert work_price_sensor.native_value == 0.31

    coordinator.last_update_success = True
    monkeypatch.setattr(sensor.CoordinatorEntity, "_handle_coordinator_update", Mock())
    work_price_sensor._handle_coordinator_update()
    assert work_price_sensor.available
    assert work_price_sensor.native_value == 0.31

    coordinator.data = 0.32
    work_price_sensor._handle_coordinator_update()
    assert work_price_sensor.native_value == 0.32


@pytest.mark.asyncio
async def test_sensor_does_not_restore_invalid_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only finite numeric restored prices become sensor states."""
    monkeypatch.setattr(sensor.CoordinatorEntity, "async_added_to_hass", AsyncMock())
    work_price_sensor = sensor.CurrentWorkPriceSensor(
        SimpleNamespace(data=None, last_update_success=True),
        SimpleNamespace(entry_id="entry_id"),
    )
    work_price_sensor.async_get_last_sensor_data = AsyncMock(
        return_value=SimpleNamespace(native_value=float("nan"))
    )

    await work_price_sensor.async_added_to_hass()

    assert work_price_sensor.native_value is None


@pytest.mark.asyncio
async def test_sensor_unsubscribes_listeners_and_midnight_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entry unload removes the coordinator retry, signal, and time listeners."""
    coordinator = SimpleNamespace(
        data=0.2,
        async_refresh=AsyncMock(),
        async_stop=Mock(),
        async_request_refresh=AsyncMock(),
    )
    monkeypatch.setattr(
        sensor, "CurrentTariffCoordinator", Mock(return_value=coordinator)
    )
    signal_unsub = Mock()
    midnight_unsub = Mock()
    monkeypatch.setattr(
        sensor, "async_dispatcher_connect", Mock(return_value=signal_unsub)
    )
    monkeypatch.setattr(
        sensor, "async_track_time_change", Mock(return_value=midnight_unsub)
    )
    unload_callbacks: list[Mock] = []
    entry = SimpleNamespace(
        entry_id="entry_id", async_on_unload=unload_callbacks.append
    )
    entities: list[sensor.CurrentWorkPriceSensor] = []

    await sensor.async_setup_entry(
        SimpleNamespace(),
        entry,
        entities.extend,
    )

    assert len(entities) == 1
    coordinator.async_refresh.assert_awaited_once()
    assert len(unload_callbacks) == 3
    for callback in unload_callbacks:
        callback()
    coordinator.async_stop.assert_called_once()
    signal_unsub.assert_called_once()
    midnight_unsub.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_price", [float("nan"), float("inf"), "0.2", True])
async def test_coordinator_rejects_non_finite_or_non_numeric_prices(
    monkeypatch: pytest.MonkeyPatch, invalid_price: object
) -> None:
    """Invalid tariff data is an update failure, not a missing tariff."""
    coordinator = _coordinator({"work_price_per_kwh": invalid_price})
    monkeypatch.setattr(sensor, "async_call_later", Mock(return_value=Mock()))

    with pytest.raises(UpdateFailed, match="Invalid current tariff data"):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_sensor_preserves_unique_id_and_metadata() -> None:
    """The sensor keeps its established identity and unit metadata."""
    work_price_sensor = sensor.CurrentWorkPriceSensor(
        SimpleNamespace(data=None, last_update_success=True),
        SimpleNamespace(entry_id="entry_id"),
    )

    assert work_price_sensor.unique_id == "entry_id_current_work_price"
    assert work_price_sensor.name == "Aktueller Arbeitspreis"
    assert work_price_sensor.native_unit_of_measurement == "EUR/kWh"
    assert work_price_sensor.state_class == "measurement"
