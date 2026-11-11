"""Datenabruf der MensaMax-Integration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, time, timedelta
import logging
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import InvalidAccount, InvalidAuth, MensaMaxClient, MensaMaxError
from .const import (
    CONF_FACILITY,
    CONF_PROJECT,
    CONF_SERVER,
    DEFAULT_SERVER,
    DEFAULT_MIN_HOURS,
    DOMAIN,
    OPT_CALENDAR,
    OPT_MIN_HOURS,
    UPDATE_INTERVAL,
)
from .parser import school_days

_LOGGER = logging.getLogger(__name__)


async def _async_with_client(
    hass: HomeAssistant, data: Mapping[str, Any], action: Callable[[MensaMaxClient], Any]
) -> Any:
    """Führt eine Aktion in einer eigenen Sitzung aus, damit Cookies getrennt bleiben."""
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
            data.get(CONF_SERVER, DEFAULT_SERVER),
        )
        return await action(client)
    finally:
        # Home Assistant teilt den Connector; close() ist hier nicht vorgesehen.
        session.detach()


async def async_fetch_plan(hass: HomeAssistant, data: Mapping[str, Any]) -> dict[str, Any]:
    """Ruft den Plan ab."""
    return await _async_with_client(
        hass, data, lambda client: client.async_fetch(dt_util.now().date())
    )


async def async_set_order(
    hass: HomeAssistant,
    data: Mapping[str, Any],
    day: date,
    category: str,
    order: bool | None,
) -> bool:
    """Ändert eine Bestellung und liefert den vom Portal bestätigten Stand."""
    return await _async_with_client(
        hass, data, lambda client: client.async_set_order(day, category, order)
    )


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

    async def _async_school_days(self) -> dict[str, dict[str, Any]] | None:
        """Liest den Stundenplan. None heißt: kein Filter, also alle Tage zeigen."""
        calendar = self.config_entry.options.get(OPT_CALENDAR)
        if not calendar:
            return None
        today = dt_util.now().date()
        monday = today - timedelta(days=today.weekday())
        try:
            response = await self.hass.services.async_call(
                "calendar",
                "get_events",
                {
                    "entity_id": calendar,
                    "start_date_time": datetime.combine(monday, time.min).isoformat(),
                    "end_date_time": datetime.combine(
                        monday + timedelta(days=21), time.min
                    ).isoformat(),
                },
                blocking=True,
                return_response=True,
            )
            return school_days(response[calendar]["events"])
        except (HomeAssistantError, KeyError, TypeError, ValueError) as err:
            _LOGGER.warning("Stundenplan nicht lesbar: %s", type(err).__name__)
            return None

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await async_fetch_plan(self.hass, dict(self.config_entry.data))
            data["schule"] = await self._async_school_days()
            data["min_stunden"] = self.config_entry.options.get(
                OPT_MIN_HOURS, DEFAULT_MIN_HOURS
            )
            return data
        except (InvalidAuth, InvalidAccount) as err:
            raise ConfigEntryAuthFailed("Anmeldung abgelehnt") from err
        except MensaMaxError as err:
            raise UpdateFailed(f"Speiseplan nicht abrufbar: {err!r}") from err
