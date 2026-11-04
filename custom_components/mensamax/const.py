"""Konstanten der MensaMax-Integration."""

from datetime import timedelta

DOMAIN = "mensamax"

CONF_FACILITY = "facility"
CONF_PROJECT = "project"

# Ohne Eintrag darf niemand bestellen.
OPT_ORDER_USERS = "order_users"
OPT_NOTIFY = "notify_services"

DEFAULT_NAME = "Mensa"
UPDATE_INTERVAL = timedelta(hours=2)

WEEKDAYS = ("montag", "dienstag", "mittwoch", "donnerstag", "freitag")
