"""Anmeldung am MensaMax-Portal und Abruf des Wochenplans.

Die Integration liest nur. Bestellungen löst das Portal über PlanForm.aspx?action=...
aus. Solche Adressen werden hier nie aufgerufen; PLAN_PATH wird immer unverändert genutzt.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
import logging
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from .parser import ParsedPage, parse_page

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://app.mensamax.de"
LOGIN_PATH = "/Login.aspx"
PLAN_PATH = "/mensamax/Essenbestellung/bestellen-stornieren/PlanForm.aspx"

_TIMEOUT = aiohttp.ClientTimeout(total=60)
_MAX_STEPS = 8


class MensaMaxError(Exception):
    """Basisfehler."""


class CannotConnect(MensaMaxError):
    """MensaMax ist nicht erreichbar."""


class InvalidAccount(MensaMaxError):
    """Projekt, Einrichtung oder Benutzername wurden nicht angenommen."""


class InvalidAuth(MensaMaxError):
    """Die Anmeldung wurde abgelehnt."""


class PlanNotFound(MensaMaxError):
    """Nach der Anmeldung kam kein Speiseplan zurück."""


def _describe(stage: str, page: ParsedPage, url: str) -> str:
    """Beschreibt eine unerwartete Seite ohne Inhalte oder Zugangsdaten."""
    fields = ",".join(page.input_names[:8]) or "keine"
    return f"{stage}: {urlsplit(url).path}, Titel '{page.title[:40]}', Felder {fields}"


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


class MensaMaxClient:
    """Eine Sitzung im Portal. Die Session muss einen eigenen Cookie-Speicher haben."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        project: str,
        facility: str,
        username: str,
        password: str,
    ) -> None:
        self._session = session
        self._project = project
        self._facility = facility
        self._username = username
        self._password = password

    async def _request(
        self, method: str, path: str, referer: str, data: dict[str, str] | None = None
    ) -> tuple[ParsedPage, str]:
        headers = {"Referer": f"{BASE_URL}{referer}", "Origin": BASE_URL}
        try:
            async with self._session.request(
                method,
                f"{BASE_URL}{path}",
                data=data,
                headers=headers,
                timeout=_TIMEOUT,
            ) as response:
                response.raise_for_status()
                html = await response.text(errors="replace")
                final_url = str(response.url)
        except (aiohttp.ClientError, TimeoutError) as err:
            # Nur den Typ weitergeben, damit nichts aus der Anfrage im Protokoll landet.
            raise CannotConnect(type(err).__name__) from err
        # Die Seiten sind groß, daher nicht in der Event-Loop auslesen.
        return await asyncio.to_thread(parse_page, html), final_url

    def _login_fields(self, page: ParsedPage) -> dict[str, str]:
        return {
            **page.hidden_fields,
            "tbxProjekt": self._project,
            "tbxEinrichtung": self._facility,
            "tbxBenutzername": self._username,
            "hdfLanguage": "german",
            "hdfLogin": "",
        }

    async def async_login(self) -> None:
        """Meldet in zwei Schritten an wie die Website: erst prüfen, dann Kennwort."""
        page, url = await self._request("GET", LOGIN_PATH, LOGIN_PATH)
        if not page.has_login_form:
            raise PlanNotFound(_describe("Anmeldeseite", page, url))

        page, _ = await self._request(
            "POST",
            LOGIN_PATH,
            LOGIN_PATH,
            {
                **self._login_fields(page),
                "tbxKennwort": "",
                "hdfCheck": "",
                "btnCheck": "",
            },
        )
        if not page.has_login_button:
            raise InvalidAccount

        page, final_url = await self._request(
            "POST",
            LOGIN_PATH,
            LOGIN_PATH,
            {
                **self._login_fields(page),
                "tbxKennwort": self._password,
                "btnLogin": "Login",
                "hdfCheck": "True",
            },
        )
        if "login.aspx" in final_url.lower():
            raise InvalidAuth

    async def _open_plan(self) -> ParsedPage:
        page, final_url = await self._request(
            "GET", PLAN_PATH, "/mensamax/navigation.aspx"
        )
        if not page.has_week and not page.has_login_form:
            # Das Portal lädt den Plan aus dem Menü heraus; dieses zuerst öffnen.
            await self._request("GET", "/mensamax/navigation.aspx", "/mensamax/index.aspx")
            page, final_url = await self._request(
                "GET", PLAN_PATH, "/mensamax/navigation.aspx"
            )
        if "login.aspx" in final_url.lower() or page.has_login_form:
            _LOGGER.debug("Plan leitet trotz Anmeldung zur Anmeldeseite")
            raise InvalidAuth
        if not page.has_week:
            raise PlanNotFound(_describe("Speiseplan", page, final_url))
        return page

    async def _navigate(
        self, page: ParsedPage, target: date, steps: list[int]
    ) -> ParsedPage | None:
        """Blättert bis zur Zielwoche. None, wenn sie nicht erreichbar ist."""
        while page.week_start is not None and _monday(page.week_start) != target:
            if steps[0] <= 0:
                return None
            steps[0] -= 1
            forward = _monday(page.week_start) < target
            if not (page.has_next if forward else page.has_previous):
                return None
            button = {"btnVor": ">"} if forward else {"btnZurueck": "<"}
            before = page.week_start
            page, _ = await self._request(
                "POST", PLAN_PATH, PLAN_PATH, {**page.hidden_fields, **button}
            )
            if page.has_login_form or not page.has_week or page.week_start == before:
                return None
        return page if page.week_start is not None else None

    async def async_fetch(self, today: date) -> dict[str, Any]:
        """Meldet an und holt die laufende und, falls vorhanden, die kommende Woche."""
        await self.async_login()
        page = await self._open_plan()
        steps = [_MAX_STEPS]
        this_monday = _monday(today)

        current = await self._navigate(page, this_monday, steps)
        if current is not None:
            page = current

        days: dict[str, dict] = dict(page.days)
        result: dict[str, Any] = {
            "time_range": page.time_range if current is not None else "",
            "hinweis": page.hinweis,
            "balance": page.balance,
            "balance_after_orders": page.balance_after_orders,
            "days": days,
        }

        try:
            following = await self._navigate(
                page, this_monday + timedelta(days=7), steps
            )
        except MensaMaxError as err:
            _LOGGER.debug("Kommende Woche nicht abrufbar: %s", type(err).__name__)
            following = None
        if following is not None:
            days.update(following.days)
        return result
