"""MensaMax-Speiseplan für Home Assistant."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import MensaMaxCoordinator
from .services import async_register_services

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Registriert die Bestell-Aktion."""
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Richtet eine Schule ein."""
    coordinator = MensaMaxCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_changed))
    return True


async def _async_options_changed(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Lädt neu, damit geänderte Einstellungen sofort gelten."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Entfernt eine Schule."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
