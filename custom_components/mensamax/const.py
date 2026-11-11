"""Konstanten der MensaMax-Integration."""

from datetime import timedelta

DOMAIN = "mensamax"

CONF_FACILITY = "facility"
CONF_PROJECT = "project"
# MensaMax betreibt mehrere Server (z. B. app.mensamax.de, mensaweb.de);
# ein Konto ist nur auf seinem Server bekannt.
CONF_SERVER = "server"
DEFAULT_SERVER = "https://app.mensamax.de"

# Ohne Eintrag darf niemand bestellen.
OPT_ORDER_USERS = "order_users"
OPT_NOTIFY = "notify_services"
# Optional: Stundenplan-Kalender; dann gelten nur lange Schultage als Mensatage.
OPT_CALENDAR = "school_calendar"
OPT_MIN_HOURS = "min_school_hours"
DEFAULT_MIN_HOURS = 6

DEFAULT_NAME = "Mensa"
UPDATE_INTERVAL = timedelta(hours=2)

WEEKDAYS = ("montag", "dienstag", "mittwoch", "donnerstag", "freitag")
