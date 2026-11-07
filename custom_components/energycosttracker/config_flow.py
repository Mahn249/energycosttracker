"""Configuration flow and database-backed tariff options."""

import sqlite3
from typing import Any

import probatio

from homeassistant import config_entries
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.dispatcher import async_dispatcher_send

from . import payments as payment_store, tariffs as tariff_store
from .const import CONF_METER_ENTITY, DOMAIN, SIGNAL_TARIFFS_UPDATED
from .imports.import_excel import ImportResult, count_existing_duplicates, import_excel
from .imports.inspect_excel import (
    ExcelInspection,
    ImportedInterval,
    InvalidExcelFile,
    inspect_excel,
)
from .meter_writings import MeterSensorError, validate_meter_state

CONF_EXCEL_FILE = "excel_file"


def _inspect_uploaded_excel(hass: HomeAssistant, file_id: str) -> ExcelInspection:
    """Inspect and consume an uploaded Excel file."""
    with process_uploaded_file(hass, file_id) as file_path:
        return inspect_excel(file_path)


class EnergyCostTrackerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up energycosttracker in Home Assistant."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Create the single integration entry."""
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        if user_input is not None:
            return self.async_create_entry(title="energycosttracker", data={})
        return self.async_show_form(step_id="user", data_schema=probatio.Schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the tariff management options flow."""
        return EnergyCostTrackerOptionsFlow()


class EnergyCostTrackerOptionsFlow(config_entries.OptionsFlow):
    """Add, edit, and remove tariff periods in energy_cost_tracker.db."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Show tariff and meter configuration actions."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "configure_meter",
                "add_tariff",
                "edit_tariff",
                "delete_tariff",
                "add_payment",
                "edit_payment",
                "delete_payment",
                "import_legacy_data",
            ],
        )

    async def async_step_import_legacy_data(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Upload and validate historical meter readings."""
        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {}
        if user_input is not None:
            try:
                inspection = await self.hass.async_add_executor_job(
                    _inspect_uploaded_excel, self.hass, user_input[CONF_EXCEL_FILE]
                )
            except (InvalidExcelFile, ValueError):
                errors["base"] = "invalid_excel_file"
            else:
                if inspection.errors:
                    errors["base"] = "invalid_excel_data"
                    details = inspection.errors.copy()
                    if inspection.error_count > len(details):
                        details.append(
                            f"... und {inspection.error_count - len(details)} "
                            "weitere fehlerhafte Zeilen."
                        )
                    description_placeholders = {"validation_errors": "\n".join(details)}
                else:
                    try:
                        duplicates = await self.hass.async_add_executor_job(
                            count_existing_duplicates, inspection.rows
                        )
                    except (OSError, sqlite3.Error, ValueError):
                        errors["base"] = "database_error"
                    else:
                        if duplicates == len(inspection.rows):
                            return self.async_abort(
                                reason="legacy_import_no_new_data",
                                description_placeholders={
                                    "count": str(duplicates),
                                },
                            )
                        self._legacy_import_rows: list[ImportedInterval] = (
                            inspection.rows
                        )
                        self._legacy_import_empty_rows = inspection.empty_rows
                        self._legacy_import_duplicates = duplicates
                        return await self.async_step_confirm_legacy_import()

        return self.async_show_form(
            step_id="import_legacy_data",
            data_schema=_legacy_import_schema(),
            errors=errors,
            description_placeholders={
                "validation_errors": description_placeholders.get(
                    "validation_errors", ""
                )
            },
        )

    async def async_step_confirm_legacy_import(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Show the import preview and import only after confirmation."""
        if not hasattr(self, "_legacy_import_rows"):
            return await self.async_step_import_legacy_data()

        if user_input is not None:
            try:
                result: ImportResult = await self.hass.async_add_executor_job(
                    import_excel, self._legacy_import_rows
                )
            except (OSError, sqlite3.Error, ValueError):
                errors = {"base": "database_error"}
            else:
                return self.async_abort(
                    reason="legacy_import_complete",
                    description_placeholders={
                        "imported": str(result.imported),
                        "skipped": str(result.skipped),
                    },
                )
        else:
            errors = {}

        starts = [row.start for row in self._legacy_import_rows]
        ends = [row.end for row in self._legacy_import_rows]
        return self.async_show_form(
            step_id="confirm_legacy_import",
            errors=errors,
            description_placeholders={
                "valid_rows": str(len(self._legacy_import_rows)),
                "new_rows": str(
                    len(self._legacy_import_rows) - self._legacy_import_duplicates
                ),
                "duplicate_rows": str(self._legacy_import_duplicates),
                "empty_rows": str(self._legacy_import_empty_rows),
                "period_start": min(starts),
                "period_end": max(ends),
            },
        )

    async def async_step_configure_meter(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Configure the sensor used for consumption recording."""
        errors: dict[str, str] = {}
        if user_input is not None:
            state = self.hass.states.get(user_input[CONF_METER_ENTITY])
            if state is None:
                errors["base"] = "meter_sensor_not_found"
            else:
                try:
                    validate_meter_state(state, user_input[CONF_METER_ENTITY])
                except MeterSensorError:
                    errors["base"] = "unsupported_meter_sensor"
                else:
                    options = {
                        **self.config_entry.options,
                        **user_input,
                    }
                    return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="configure_meter",
            data_schema=_meter_schema(self.config_entry.options),
            errors=errors,
        )

    async def async_step_add_payment(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Add a payment period to energy_cost_tracker.db."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self.hass.async_add_executor_job(
                    payment_store.add_payment, user_input
                )
            except payment_store.PaymentValidationError as err:
                errors["base"] = err.reason
            except (OSError, sqlite3.Error):
                errors["base"] = "database_error"
            else:
                return self.async_create_entry(title="", data=self.config_entry.options)
        return self.async_show_form(
            step_id="add_payment", data_schema=_payment_schema(), errors=errors
        )

    async def async_step_edit_payment(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Choose a payment period to edit."""
        try:
            self._payments = await self.hass.async_add_executor_job(
                payment_store.list_payments
            )
        except (OSError, sqlite3.Error):
            return self.async_abort(reason="database_error")
        if not self._payments:
            return self.async_abort(reason="no_payments")
        if user_input is not None:
            self._selected_payment_id = int(user_input["payment_id"])
            return await self.async_step_edit_payment_values()
        return self.async_show_form(
            step_id="edit_payment",
            data_schema=_payment_select_schema(self._payments),
        )

    async def async_step_edit_payment_values(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Save changes to the selected payment period."""
        payment = next(
            item for item in self._payments if item["id"] == self._selected_payment_id
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self.hass.async_add_executor_job(
                    payment_store.update_payment,
                    self._selected_payment_id,
                    user_input,
                )
            except payment_store.PaymentValidationError as err:
                errors["base"] = err.reason
            except (OSError, sqlite3.Error):
                errors["base"] = "database_error"
            else:
                return self.async_create_entry(title="", data=self.config_entry.options)
        return self.async_show_form(
            step_id="edit_payment_values",
            data_schema=_payment_schema(payment),
            errors=errors,
        )

    async def async_step_delete_payment(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Choose a payment period to delete."""
        try:
            self._payments = await self.hass.async_add_executor_job(
                payment_store.list_payments
            )
        except (OSError, sqlite3.Error):
            return self.async_abort(reason="database_error")
        if not self._payments:
            return self.async_abort(reason="no_payments")
        if user_input is not None:
            self._selected_payment_id = int(user_input["payment_id"])
            return await self.async_step_confirm_delete_payment()
        return self.async_show_form(
            step_id="delete_payment",
            data_schema=_payment_select_schema(self._payments),
        )

    async def async_step_confirm_delete_payment(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Confirm deletion of the selected payment period."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self.hass.async_add_executor_job(
                    payment_store.delete_payment, self._selected_payment_id
                )
            except (OSError, sqlite3.Error):
                errors["base"] = "database_error"
            else:
                return self.async_create_entry(title="", data=self.config_entry.options)
        return self.async_show_form(step_id="confirm_delete_payment", errors=errors)

    async def async_step_add_tariff(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Add a tariff period to energy_cost_tracker.db."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self.hass.async_add_executor_job(
                    tariff_store.add_tariff, user_input
                )
            except tariff_store.TariffValidationError as exc:
                errors["base"] = exc.reason
            except (OSError, sqlite3.Error):
                errors["base"] = "database_error"
            else:
                async_dispatcher_send(self.hass, SIGNAL_TARIFFS_UPDATED)
                return self.async_create_entry(title="", data=self.config_entry.options)
        return self.async_show_form(
            step_id="add_tariff", data_schema=_tariff_schema(), errors=errors
        )

    async def async_step_edit_tariff(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Choose an existing tariff period to edit."""
        try:
            self._tariffs = await self.hass.async_add_executor_job(
                tariff_store.list_tariffs
            )
        except (OSError, sqlite3.Error):
            return self.async_abort(reason="database_error")
        if not self._tariffs:
            return self.async_abort(reason="no_tariffs")
        if user_input is not None:
            self._selected_id = int(user_input["tariff_id"])
            return await self.async_step_edit_tariff_values()
        return self.async_show_form(
            step_id="edit_tariff", data_schema=_tariff_select_schema(self._tariffs)
        )

    async def async_step_edit_tariff_values(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Save changes to the selected tariff period."""
        tariff = next(item for item in self._tariffs if item["id"] == self._selected_id)
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self.hass.async_add_executor_job(
                    tariff_store.update_tariff, self._selected_id, user_input
                )
            except tariff_store.TariffValidationError as exc:
                errors["base"] = exc.reason
            except (OSError, sqlite3.Error):
                errors["base"] = "database_error"
            else:
                async_dispatcher_send(self.hass, SIGNAL_TARIFFS_UPDATED)
                return self.async_create_entry(title="", data=self.config_entry.options)
        return self.async_show_form(
            step_id="edit_tariff_values",
            data_schema=_tariff_schema(tariff),
            errors=errors,
        )

    async def async_step_delete_tariff(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Choose a tariff period to remove."""
        try:
            self._tariffs = await self.hass.async_add_executor_job(
                tariff_store.list_tariffs
            )
        except (OSError, sqlite3.Error):
            return self.async_abort(reason="database_error")
        if not self._tariffs:
            return self.async_abort(reason="no_tariffs")
        if user_input is not None:
            self._selected_id = int(user_input["tariff_id"])
            return await self.async_step_confirm_delete()
        return self.async_show_form(
            step_id="delete_tariff", data_schema=_tariff_select_schema(self._tariffs)
        )

    async def async_step_confirm_delete(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Confirm removal of the selected tariff."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self.hass.async_add_executor_job(
                    tariff_store.delete_tariff, self._selected_id
                )
            except (OSError, sqlite3.Error):
                errors["base"] = "database_error"
            else:
                async_dispatcher_send(self.hass, SIGNAL_TARIFFS_UPDATED)
                return self.async_create_entry(title="", data=self.config_entry.options)
        return self.async_show_form(step_id="confirm_delete", errors=errors)


class _OptionalDateSelector(selector.DateSelector):
    """Date selector that accepts an empty value."""

    def __call__(self, data: Any) -> Any:
        """Validate a date while preserving an empty value as None."""
        if data is None or data == "":
            return None
        return super().__call__(data)


def _tariff_select_schema(tariffs: list[dict[str, Any]]) -> probatio.Schema:
    """Build the selector for choosing a tariff from the database."""
    return probatio.Schema(
        {
            probatio.Required("tariff_id"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {
                            "label": f"{item['name']} ({item['valid_from']})",
                            "value": str(item["id"]),
                        }
                        for item in tariffs
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        }
    )


def _legacy_import_schema() -> probatio.Schema:
    """Build the Excel upload form schema."""
    return probatio.Schema(
        {
            probatio.Required(CONF_EXCEL_FILE): selector.FileSelector(
                config=selector.FileSelectorConfig(accept=".xlsx")
            )
        }
    )


def _tariff_schema(tariff: dict[str, Any] | None = None) -> probatio.Schema:
    """Build the form schema for one tariff period."""
    tariff = tariff or {}
    return probatio.Schema(
        {
            probatio.Required("name", default=tariff.get("name", "")): str,
            probatio.Required(
                "valid_from", default=tariff.get("valid_from")
            ): selector.DateSelector(),
            probatio.Optional(
                "valid_until", default=tariff.get("valid_until")
            ): _OptionalDateSelector(),
            probatio.Required(
                "work_price_per_kwh", default=tariff.get("work_price_per_kwh", 0.0)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, step=0.001, mode=selector.NumberSelectorMode.BOX
                )
            ),
            probatio.Required(
                "base_price_per_month", default=tariff.get("base_price_per_month", 0.0)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, step=0.01, mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
    )


def _meter_schema(options: dict[str, Any]) -> probatio.Schema:
    """Build the cumulative energy sensor configuration schema."""
    return probatio.Schema(
        {
            probatio.Required(
                CONF_METER_ENTITY, default=options.get(CONF_METER_ENTITY)
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    filter={"domain": "sensor", "device_class": "energy"}
                )
            ),
        }
    )


def _payment_select_schema(payments: list[dict[str, Any]]) -> probatio.Schema:
    """Build the selector for choosing a payment period."""
    return probatio.Schema(
        {
            probatio.Required("payment_id"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {
                            "label": (f"{payment['name']} ({payment['valid_from']})"),
                            "value": str(payment["id"]),
                        }
                        for payment in payments
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        }
    )


def _payment_schema(payment: dict[str, Any] | None = None) -> probatio.Schema:
    """Build the form schema for one payment period."""
    payment = payment or {}
    return probatio.Schema(
        {
            probatio.Required("name", default=payment.get("name", "")): str,
            probatio.Required(
                "valid_from", default=payment.get("valid_from")
            ): selector.DateSelector(),
            probatio.Optional(
                "valid_until", default=payment.get("valid_until")
            ): _OptionalDateSelector(),
            probatio.Required(
                "amount_eur", default=payment.get("amount_eur", 0.0)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, step=0.01, mode=selector.NumberSelectorMode.BOX
                )
            ),
            probatio.Optional("note", default=payment.get("note", "")): str,
        }
    )
