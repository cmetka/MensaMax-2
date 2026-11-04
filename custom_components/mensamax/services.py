"""Aktion zum Bestellen und Stornieren."""

from __future__ import annotations

from datetime import date, timedelta
import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util

from .api import MensaMaxError
from .const import DOMAIN, OPT_NOTIFY, OPT_ORDER_USERS, WEEKDAYS
from .coordinator import async_set_order

_LOGGER = logging.getLogger(__name__)

SERVICE = "bestellung"
_ACTIONS = {"bestellen": True, "stornieren": False, "umschalten": None}
_SCHEMA = vol.Schema(
    {
        vol.Required("tag"): vol.In(WEEKDAYS),
        vol.Required("menue"): str,
        vol.Required("aktion"): vol.In(tuple(_ACTIONS)),
        vol.Optional("konto"): str,
    }
)


def weekday_date(today: date, weekday: str) -> date:
    """Datum des Wochentags: laufende Woche, am Wochenende die kommende."""
    monday = today - timedelta(days=today.weekday())
    if today.weekday() >= 5:
        monday += timedelta(days=7)
    return monday + timedelta(days=WEEKDAYS.index(weekday))


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Registriert die Aktion einmalig."""

    async def _handle(call: ServiceCall) -> None:
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if konto := call.data.get("konto"):
            entries = [entry for entry in entries if entry.entry_id == konto]
        if len(entries) != 1:
            raise ServiceValidationError("Bitte genau ein MensaMax-Konto angeben")
        entry = entries[0]

        # Die Sperre gilt hier und nicht nur in der Anzeige. Ohne Benutzer (etwa bei
        # Automationen) oder ohne Eintrag in der Liste wird nichts ausgeführt.
        user_id = call.context.user_id
        if user_id is None or user_id not in entry.options.get(OPT_ORDER_USERS, []):
            _LOGGER.warning("Bestellung ohne Berechtigung abgelehnt (Benutzer %s)", user_id)
            raise ServiceValidationError("Du darfst bei MensaMax nicht bestellen")

        day = weekday_date(dt_util.now().date(), call.data["tag"])
        category = call.data["menue"]
        try:
            ordered = await async_set_order(
                hass, entry.data, day, category, _ACTIONS[call.data["aktion"]]
            )
        except MensaMaxError as err:
            raise HomeAssistantError(f"MensaMax: {err}") from err

        await entry.runtime_data.async_request_refresh()

        user = await hass.auth.async_get_user(user_id)
        who = user.name if user else "unbekannt"
        verb = "bestellt" if ordered else "storniert"
        for service in entry.options.get(OPT_NOTIFY, []):
            try:
                await hass.services.async_call(
                    "notify",
                    service,
                    {
                        "title": f"MensaMax {entry.title}",
                        "message": f"{category} für {day:%d.%m.%Y} {verb} von {who}",
                    },
                )
            except HomeAssistantError:
                _LOGGER.warning("Benachrichtigung über %s fehlgeschlagen", service)

    hass.services.async_register(DOMAIN, SERVICE, _handle, schema=_SCHEMA)
