"""Einrichtung per UI."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import CannotConnect, InvalidAccount, InvalidAuth, MensaMaxError, PlanNotFound
from .const import CONF_FACILITY, CONF_PROJECT, DEFAULT_NAME, DOMAIN
from .coordinator import async_fetch_plan

_LOGGER = logging.getLogger(__name__)

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


class MensaMaxConfigFlow(ConfigFlow, domain=DOMAIN):
    """Fragt die Zugangsdaten ab und testet den Abruf."""

    VERSION = 2

    async def _async_test(self, data: dict[str, Any]) -> dict[str, str]:
        try:
            await async_fetch_plan(self.hass, data)
        except CannotConnect:
            return {"base": "cannot_connect"}
        except InvalidAccount:
            return {"base": "invalid_account"}
        except InvalidAuth:
            return {"base": "invalid_auth"}
        except PlanNotFound:
            return {"base": "plan_not_found"}
        except MensaMaxError:
            _LOGGER.debug("Unerwarteter MensaMax-Fehler", exc_info=True)
            return {"base": "unknown"}
        return {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                CONF_PROJECT: user_input[CONF_PROJECT].strip(),
                CONF_FACILITY: user_input[CONF_FACILITY].strip(),
                CONF_USERNAME: user_input[CONF_USERNAME].strip(),
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            await self.async_set_unique_id(
                "_".join(
                    (data[CONF_PROJECT], data[CONF_FACILITY], data[CONF_USERNAME])
                ).lower()
            )
            self._abort_if_unique_id_configured()
            errors = await self._async_test(data)
            if not errors:
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data=data
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_PROJECT): str,
                vol.Required(CONF_FACILITY): str,
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): _PASSWORD,
            }
        )
        suggested = {
            key: value
            for key, value in (user_input or {}).items()
            if key != CONF_PASSWORD
        }
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            errors = await self._async_test(data)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): _PASSWORD}),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )
