"""Einrichtung per UI."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import CannotConnect, InvalidAccount, InvalidAuth, MensaMaxError, PlanNotFound
from .const import (
    CONF_FACILITY,
    CONF_PROJECT,
    DEFAULT_MIN_HOURS,
    DEFAULT_NAME,
    DOMAIN,
    OPT_CALENDAR,
    OPT_MIN_HOURS,
    OPT_NOTIFY,
    OPT_ORDER_USERS,
)
from .coordinator import async_fetch_plan

_LOGGER = logging.getLogger(__name__)

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


class MensaMaxConfigFlow(ConfigFlow, domain=DOMAIN):
    """Fragt die Zugangsdaten ab und testet den Abruf."""

    VERSION = 2

    _details = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return MensaMaxOptionsFlow()

    async def _async_test(self, data: dict[str, Any]) -> dict[str, str]:
        try:
            await async_fetch_plan(self.hass, data)
        except CannotConnect:
            return {"base": "cannot_connect"}
        except InvalidAccount as err:
            _LOGGER.warning("Konto nicht angenommen: %s", err)
            return {"base": "invalid_account"}
        except InvalidAuth as err:
            _LOGGER.warning("Anmeldung abgelehnt: %s", err)
            return {"base": "invalid_auth"}
        except PlanNotFound as err:
            self._details = str(err)
            _LOGGER.warning("Kein Speiseplan: %s", err)
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
            description_placeholders={"details": self._details},
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
            description_placeholders={
                "username": entry.data[CONF_USERNAME],
                "details": self._details,
            },
            errors=errors,
        )


class MensaMaxOptionsFlow(OptionsFlow):
    """Legt fest, wer bestellen darf und wer benachrichtigt wird."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        users = [
            SelectOptionDict(value=user.id, label=user.name or user.id)
            for user in await self.hass.auth.async_get_users()
            if not user.system_generated and user.is_active
        ]
        services = sorted(self.hass.services.async_services().get("notify", {}))
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        OPT_ORDER_USERS, default=options.get(OPT_ORDER_USERS, [])
                    ): SelectSelector(SelectSelectorConfig(options=users, multiple=True)),
                    vol.Optional(
                        OPT_NOTIFY, default=options.get(OPT_NOTIFY, [])
                    ): SelectSelector(
                        SelectSelectorConfig(options=services, multiple=True)
                    ),
                    vol.Optional(
                        OPT_CALENDAR,
                        description={"suggested_value": options.get(OPT_CALENDAR)},
                    ): EntitySelector(EntitySelectorConfig(domain="calendar")),
                    vol.Optional(
                        OPT_MIN_HOURS,
                        default=options.get(OPT_MIN_HOURS, DEFAULT_MIN_HOURS),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=12, step=0.5, mode=NumberSelectorMode.BOX
                        )
                    ),
                }
            ),
        )
