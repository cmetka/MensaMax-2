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
# Das Portal liefert anderen Programmen eine abweichende Anmeldeseite.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0"
)


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
        # Ablauf ohne Inhalte und Zugangsdaten, nur für Fehlermeldungen.
        self.trace: list[str] = []

    async def _request(
        self,
        method: str,
        path: str,
        referer: str,
        data: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[ParsedPage, str]:
        headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de,en-US;q=0.9,en;q=0.8",
            "Referer": f"{BASE_URL}{referer}",
            "Origin": BASE_URL,
            **(extra_headers or {}),
        }
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
                status = response.status
        except (aiohttp.ClientError, TimeoutError) as err:
            # Nur den Typ weitergeben, damit nichts aus der Anfrage im Protokoll landet.
            raise CannotConnect(type(err).__name__) from err
        # Die Seiten sind groß, daher nicht in der Event-Loop auslesen.
        page = await asyncio.to_thread(parse_page, html)
        self._remember(method, path, status, final_url, page)
        return page, final_url

    def _remember(
        self, method: str, path: str, status: int, url: str, page: ParsedPage
    ) -> None:
        jar = getattr(self._session, "cookie_jar", None)
        cookies = ",".join(sorted(cookie.key for cookie in jar)) if jar else "-"
        self.trace.append(
            f"{method} {path.rsplit('/', 1)[-1]} {status} -> "
            f"{urlsplit(url).path.rsplit('/', 1)[-1]} [{cookies}] {page.messages}"
        )

    def _login_fields(self, page: ParsedPage) -> dict[str, str]:
        """Füllt die Felder unter den Namen aus, die die Seite gerade verwendet."""
        prefix = "tbx" if "tbxBenutzername" in page.input_names else ""

        def name(field: str) -> str:
            return f"tbx{field}" if prefix else field.lower()

        return {
            **page.hidden_fields,
            name("Projekt"): self._project,
            name("Einrichtung"): self._facility,
            name("Benutzername"): self._username,
            "hdfLanguage": "german",
            "hdfLogin": "",
        }

    def _password_field(self, page: ParsedPage) -> str:
        return "tbxKennwort" if "tbxKennwort" in page.input_names else "kennwort"

    async def async_login(self) -> None:
        """Meldet an wie die Website: falls verlangt erst prüfen, dann mit Kennwort."""
        page, url = await self._request("GET", LOGIN_PATH, LOGIN_PATH)
        if not page.has_login_form:
            raise PlanNotFound(_describe("Anmeldeseite", page, url))

        if page.has_check_button:
            page, url = await self._request(
                "POST",
                LOGIN_PATH,
                LOGIN_PATH,
                {
                    **self._login_fields(page),
                    self._password_field(page): "",
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
                self._password_field(page): self._password,
                "btnLogin": page.login_button_value,
                "hdfCheck": "True",
            },
        )
        if "login.aspx" in final_url.lower():
            raise InvalidAuth(" | ".join(self.trace))

        # Dieselben Schritte wie die Website nach der Anmeldung.
        await self._request("GET", "/mensamax/index.aspx", LOGIN_PATH)
        try:
            await self._request(
                "POST",
                "/mensamax/index.aspx/GetJwtToken",
                "/mensamax/index.aspx",
                extra_headers={"Content-Type": "application/json; charset=utf-8"},
            )
        except CannotConnect:
            self.trace.append("GetJwtToken fehlgeschlagen")
        await self._request("GET", "/mensamax/navigation.aspx", "/mensamax/index.aspx")

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
            raise InvalidAuth(" | ".join(self.trace))
        if not page.has_week:
            raise PlanNotFound(
                _describe("Speiseplan", page, final_url) + " | " + " | ".join(self.trace)
            )
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
