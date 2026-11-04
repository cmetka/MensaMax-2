"""Liest die Seiten des MensaMax-Portals aus. Nur lesend, ohne Netzwerkzugriff."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import re

from bs4 import BeautifulSoup, Tag

_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_LEADING_ID = re.compile(r"^\d+\s+")
_AMOUNT = re.compile(r"(-?)\s*(\d{1,3}(?:\.\d{3})*|\d+),(\d{2})")
_ALERT = re.compile(r"""j?[Aa]lert\(\s*['"]([^'"]{1,120})""")
_ORDERED_SCRIPT = re.compile(r"fillTxt\(\s*(\d{8})\s*,\s*(\d+)")


@dataclass
class ParsedPage:
    """Ergebnis einer ausgelesenen Seite."""

    has_login_form: bool = False
    has_login_button: bool = False
    has_check_button: bool = False
    login_button_value: str = "Login"
    has_week: bool = False
    time_range: str = ""
    hinweis: str = ""
    balance: float | None = None
    balance_after_orders: float | None = None
    week_start: date | None = None
    # ISO-Datum -> {"label", "categories": {Kategorie: [Gericht]}, "ordered": [Kategorie]}
    days: dict[str, dict] = field(default_factory=dict)
    hidden_fields: dict[str, str] = field(default_factory=dict)
    has_next: bool = False
    has_previous: bool = False
    title: str = ""
    input_names: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


def parse_date(text: str) -> date | None:
    """Liest das erste Datum im Format TT.MM.JJJJ."""
    match = _DATE.search(text)
    if match is None:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_amount(text: str) -> float | None:
    """Liest einen Betrag wie 'aktueller Kontostand: -1.234,50 €'."""
    match = _AMOUNT.search(text)
    if match is None:
        return None
    sign, whole, cents = match.groups()
    value = float(f"{whole.replace('.', '')}.{cents}")
    return -value if sign else value


def _direct_rows(table: Tag) -> list[Tag]:
    """Zeilen dieser Tabelle ohne die der verschachtelten Tabellen."""
    return [row for row in table.find_all("tr") if row.find_parent("table") is table]


def _direct_cells(row: Tag) -> list[Tag]:
    return row.find_all(["td", "th"], recursive=False)


def _parse_dishes(cell: Tag) -> list[dict]:
    """Gerichte stehen im Portal in Listen, im alten öffentlichen Plan in div-Elementen."""
    dishes: list[dict] = []
    for item in cell.find_all("li") or cell.find_all("div"):
        allergens: list[str] = []
        names: list[str] = []
        for span in item.find_all("span"):
            code = span.get_text(strip=True).strip(" ,")
            if code:
                allergens.append(code)
            title = span.get("title")
            if title:
                names.append(str(title))
        for note in item.find_all(["sup", "sub"]):
            note.decompose()
        title = _LEADING_ID.sub("", " ".join(item.get_text(" ").split()))
        if title:
            dishes.append(
                {"title": title, "allergens": allergens, "allergen_names": names}
            )
    return dishes


def _find_plan_table(soup: BeautifulSoup) -> Tag | None:
    table = soup.find(id="tblMain")
    if isinstance(table, Tag):
        return table
    container = soup.find(id="divCon")
    if isinstance(container, Tag):
        table = container.find("table")
        if isinstance(table, Tag):
            return table
    return None


def _is_ordered(cell: Tag, ordered_cells: set[str]) -> bool:
    classes = cell.get("class") or []
    return "tdSelected" in classes or str(cell.get("id")) in ordered_cells


def _parse_table(table: Tag, page: ParsedPage, ordered_cells: set[str]) -> None:
    rows = _direct_rows(table)
    if not rows:
        return
    # Index 0 ist die leere Kopfzelle über den Kategorien.
    labels = [c.get_text(" ", strip=True) for c in _direct_cells(rows[0])][1:]
    columns: list[str | None] = []
    for label in labels:
        day = parse_date(label)
        if day is None:
            columns.append(None)
            continue
        key = day.isoformat()
        page.days[key] = {"label": label, "categories": {}, "ordered": []}
        columns.append(key)

    for row in rows[1:]:
        cells = _direct_cells(row)
        if len(cells) < 2:
            continue
        category = cells[0].get_text(" ", strip=True)
        if not category:
            continue
        for key, cell in zip(columns, cells[1:]):
            if key is None:
                continue
            page.days[key]["categories"][category] = _parse_dishes(cell)
            if _is_ordered(cell, ordered_cells):
                page.days[key]["ordered"].append(category)


def parse_page(html: str) -> ParsedPage:
    """Liest Anmeldeformular oder Wochenplan."""
    # Das Portal schreibt Attribute doppelt, z. B. name="tbxProjekt" und name="projekt".
    # Browser werten das erste aus, also hier genauso.
    soup = BeautifulSoup(html, "html.parser", on_duplicate_attribute="ignore")
    page = ParsedPage()
    page.has_login_form = soup.find(attrs={"name": "tbxBenutzername"}) is not None
    login_button = soup.find(attrs={"name": "btnLogin"})
    page.has_login_button = login_button is not None
    if isinstance(login_button, Tag) and login_button.get("value"):
        page.login_button_value = str(login_button.get("value"))
    page.has_check_button = soup.find(attrs={"name": "btnCheck"}) is not None
    page.has_next = soup.find(attrs={"name": "btnVor"}) is not None
    page.has_previous = soup.find(attrs={"name": "btnZurueck"}) is not None

    if soup.title is not None:
        page.title = " ".join(soup.title.get_text().split())
    page.input_names = [
        str(tag.get("name"))
        for tag in soup.find_all("input")
        if tag.get("name") and not str(tag.get("name")).startswith("__")
    ]

    for tag in soup.find_all(id=re.compile(r"^(lbl|msg|error|fehler)", re.I)):
        text = " ".join(tag.get_text(" ").split())
        if text and tag.get("id") != "lblSpeiesplanHinweis":
            page.messages.append(text[:120])
    page.messages = (page.messages + _ALERT.findall(html))[:4]

    # Über das ganze Dokument suchen: Ungültiges HTML kann das Formular vorzeitig beenden.
    for field_tag in soup.find_all("input", attrs={"type": "hidden"}):
        name = field_tag.get("name")
        if name:
            page.hidden_fields[str(name)] = str(field_tag.get("value") or "")

    week = soup.find(id="lblWoche")
    if week is not None:
        page.has_week = True
        page.time_range = week.get_text(" ", strip=True)
        page.week_start = parse_date(page.time_range)

    hinweis = soup.find(id="lblSpeiesplanHinweis")
    if hinweis is not None:
        page.hinweis = " ".join(hinweis.get_text(" ").split())

    balance = soup.find(id="lblKontostand")
    if balance is not None:
        page.balance = parse_amount(balance.get_text())
    after_orders = soup.find(id="lblVorausKontostand")
    if after_orders is not None:
        page.balance_after_orders = parse_amount(after_orders.get_text())

    table = _find_plan_table(soup)
    if table is not None:
        page.has_week = True
        ordered_cells = {
            f"td{day}_{menu}" for day, menu in _ORDERED_SCRIPT.findall(html)
        }
        _parse_table(table, page, ordered_cells)
        if page.week_start is None and page.days:
            page.week_start = date.fromisoformat(min(page.days))
    return page
