"""Datenabruf der MensaMax-Integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import InvalidAccount, InvalidAuth, MensaMaxClient, MensaMaxError
from .const import CONF_FACILITY, CONF_PROJECT, DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


async def async_fetch_plan(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Ruft den Plan in einer eigenen Sitzung ab, damit Cookies getrennt bleiben."""
    # quote_cookie=False: Die Cookies des Portals enthalten Zeichen wie = und /,
    # die unverändert zurückgeschickt werden müssen.
    session = async_create_clientsession(
        hass, auto_cleanup=False, cookie_jar=aiohttp.CookieJar(quote_cookie=False)
    )
    try:
        client = MensaMaxClient(
            session,
            data[CONF_PROJECT],
            data[CONF_FACILITY],
            data[CONF_USERNAME],
            data[CONF_PASSWORD],
        )
        return await client.async_fetch(dt_util.now().date())
    finally:
        # Home Assistant teilt den Connector; close() ist hier nicht vorgesehen.
        session.detach()


class MensaMaxCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Aktualisiert den Speiseplan alle zwei Stunden."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=UPDATE_INTERVAL,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await async_fetch_plan(self.hass, dict(self.config_entry.data))
        except (InvalidAuth, InvalidAccount) as err:
            raise ConfigEntryAuthFailed("Anmeldung abgelehnt") from err
        except MensaMaxError as err:
            raise UpdateFailed(f"Speiseplan nicht abrufbar: {err!r}") from err
