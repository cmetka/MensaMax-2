"""Sensoren für Speiseplan, Bestellstatus und Kontostand."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_EURO
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import MensaMaxCoordinator

NO_MENU = "Kein Angebot"
NOT_ORDERED = "Nicht bestellt"
MAX_STATE = 255


def _has_dishes(day: dict) -> bool:
    return any(day["categories"].values())


def day_summary(day: dict | None) -> str:
    """Kurztext für den Sensorzustand: die erste Kategorie mit Gerichten."""
    if day is None:
        return NO_MENU
    for dishes in day["categories"].values():
        if dishes:
            text = ", ".join(dish["title"] for dish in dishes)
            return text if len(text) <= MAX_STATE else f"{text[: MAX_STATE - 1]}…"
    return NO_MENU


def day_attributes(iso_day: str | None, day: dict | None) -> dict[str, Any]:
    """Attribute eines Tages, nur Kategorien mit Gerichten."""
    if iso_day is None or day is None:
        return {"datum": None, "tag": None, "bestellt": [], "gerichte": {}}
    return {
        "datum": iso_day,
        "tag": day["label"],
        "bestellt": day["ordered"],
        "gerichte": {
            category: dishes for category, dishes in day["categories"].items() if dishes
        },
    }


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MensaMaxCoordinator = entry.runtime_data
    async_add_entities(
        [
            MensaTodaySensor(coordinator, entry),
            MensaNextDaySensor(coordinator, entry),
            MensaWeekSensor(coordinator, entry),
            MensaOrderedTodaySensor(coordinator, entry),
            MensaBalanceSensor(coordinator, entry),
            MensaBalanceAfterOrdersSensor(coordinator, entry),
        ]
    )


class MensaSensor(CoordinatorEntity[MensaMaxCoordinator], SensorEntity):
    """Gemeinsame Basis."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:silverware-fork-knife"
    # Der Plan kann groß werden und gehört nicht in die Verlaufsdatenbank.
    _unrecorded_attributes = frozenset({"gerichte", "tage", "hinweis"})
    _key: str

    def __init__(self, coordinator: MensaMaxCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{self._key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="MensaMax",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # "Heute" wechselt um Mitternacht, auch ohne neuen Abruf.
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._handle_new_day, hour=0, minute=0, second=30
            )
        )

    @callback
    def _handle_new_day(self, _now: datetime) -> None:
        self.async_write_ha_state()

    @property
    def _days(self) -> dict[str, dict]:
        return self.coordinator.data["days"]

    @property
    def _today(self) -> date:
        return dt_util.now().date()


class MensaTodaySensor(MensaSensor):
    """Speiseplan von heute."""

    _key = "heute"
    _attr_name = "Heute"

    @property
    def native_value(self) -> str:
        return day_summary(self._days.get(self._today.isoformat()))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        iso_day = self._today.isoformat()
        return day_attributes(iso_day, self._days.get(iso_day))


class MensaNextDaySensor(MensaSensor):
    """Nächster Tag nach heute, an dem Gerichte eingetragen sind."""

    _key = "naechster_tag"
    _attr_name = "Nächster Tag"

    def _next(self) -> str | None:
        today = self._today.isoformat()
        return min(
            (
                iso_day
                for iso_day, day in self._days.items()
                if iso_day > today and _has_dishes(day)
            ),
            default=None,
        )

    @property
    def native_value(self) -> str:
        iso_day = self._next()
        return day_summary(self._days.get(iso_day) if iso_day else None)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        iso_day = self._next()
        return day_attributes(iso_day, self._days.get(iso_day) if iso_day else None)


class MensaWeekSensor(MensaSensor):
    """Alle bekannten Tage ab heute."""

    _key = "woche"
    _attr_name = "Woche"

    @property
    def native_value(self) -> str:
        return self.coordinator.data["time_range"] or NO_MENU

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        today = self._today.isoformat()
        return {
            "tage": [
                day_attributes(iso_day, self._days[iso_day])
                for iso_day in sorted(self._days)
                if iso_day >= today
            ],
            "hinweis": self.coordinator.data["hinweis"],
        }


class MensaOrderedTodaySensor(MensaSensor):
    """Heute bestelltes Menü."""

    _key = "bestellt_heute"
    _attr_name = "Bestellt heute"
    _attr_icon = "mdi:food-outline"

    @property
    def _day(self) -> dict | None:
        return self._days.get(self._today.isoformat())

    @property
    def native_value(self) -> str:
        ordered = self._day["ordered"] if self._day else []
        return ", ".join(ordered) or NOT_ORDERED

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        day = self._day
        if day is None:
            return {"gerichte": []}
        return {
            "gerichte": [
                dish
                for category in day["ordered"]
                for dish in day["categories"].get(category, [])
            ]
        }


class MensaBalanceSensor(MensaSensor):
    """Aktueller Kontostand."""

    _key = "kontostand"
    _attr_name = "Kontostand"
    _attr_icon = "mdi:cash"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = CURRENCY_EURO
    _field = "balance"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data[self._field]


class MensaBalanceAfterOrdersSensor(MensaBalanceSensor):
    """Kontostand nach allen offenen Bestellungen."""

    _key = "kontostand_nach_bestellungen"
    _attr_name = "Kontostand nach Bestellungen"
    _field = "balance_after_orders"
