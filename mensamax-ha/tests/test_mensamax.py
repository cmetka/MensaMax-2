"""Prüft Parser und Anmeldung ohne Home Assistant: python3 tests/test_mensamax.py"""

import asyncio
from datetime import date, timedelta
import importlib.util
import json
from pathlib import Path
import sys
import types
from urllib.parse import urlparse
import unittest

ROOT = Path(__file__).resolve().parent
PKG = ROOT.parent / "custom_components" / "mensamax"

try:
    import aiohttp  # noqa: F401
except ImportError:
    stub = types.ModuleType("aiohttp")
    stub.ClientError = type("ClientError", (Exception,), {})
    stub.ClientTimeout = lambda **kw: None
    stub.ClientSession = object
    sys.modules["aiohttp"] = stub

pkg = types.ModuleType("mm")
pkg.__path__ = [str(PKG)]
sys.modules["mm"] = pkg


def load(name):
    spec = importlib.util.spec_from_file_location(f"mm.{name}", PKG / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"mm.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


parser = load("parser")
api = load("api")

OLD_HTML = (ROOT / "fixtures/001.html").read_text(encoding="utf-8")
OLD_JSON = json.loads((ROOT / "fixtures/001.json").read_text(encoding="utf-8"))
PLAN = (ROOT / "fixtures/planform.html").read_text(encoding="utf-8")
PLAN_MONDAY = date(2026, 11, 2)
HIDDEN = '<input type="hidden" name="__VIEWSTATE" value="{}">'
LOGIN_1 = (
    "<form>" + HIDDEN.format("v1") + '<input name="tbxBenutzername">'
    '<input type="submit" name="btnCheck"></form>'
)
LOGIN_2 = (
    "<form>" + HIDDEN.format("v2") + '<input name="tbxBenutzername">'
    '<input name="tbxKennwort"><input type="submit" name="btnLogin"></form>'
)


def shifted(weeks):
    """Beispielseite um ganze Wochen verschoben."""
    html = PLAN
    for offset in range(7):
        old = PLAN_MONDAY + timedelta(days=offset)
        new = old + timedelta(weeks=weeks)
        html = html.replace(old.strftime("%d.%m.%Y"), new.strftime("%d.%m.%Y"))
    return html


class Response:
    def __init__(self, html, url):
        self.html, self.url = html, url

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self):
        pass

    async def text(self, errors=None):
        return self.html


class Portal:
    """Spielt das Portal nach und merkt sich jede Anfrage."""

    def __init__(self, good_account=True, password="geheim", session_works=True):
        self.good_account = good_account
        self.password = password
        self.session_works = session_works
        self.logged_in = False
        self.week = 0
        self.calls = []

    def request(self, method, url, data=None, headers=None, timeout=None):
        self.calls.append((method, url, dict(data or {})))
        login_url = "https://app.mensamax.de/Login.aspx"
        if url == login_url:
            if method == "GET":
                return Response(LOGIN_1, url)
            if "btnCheck" in data:
                return Response(LOGIN_2 if self.good_account else LOGIN_1, url)
            if data["tbxKennwort"] == self.password:
                self.logged_in = True
                return Response("<html>Start</html>", "https://app.mensamax.de/mensamax/index.aspx")
            return Response(LOGIN_2, url)
        if not self.logged_in or not self.session_works:
            return Response(LOGIN_1, login_url)
        if data and "btnVor" in data:
            self.week += 1
        if data and "btnZurueck" in data:
            self.week -= 1
        return Response(shifted(self.week), url)


def fetch(portal, today=date(2026, 11, 4), password="geheim"):
    client = api.MensaMaxClient(portal, "h2798", "Mensa-Gehrden", "kind", password)
    return asyncio.run(client.async_fetch(today))


class ParserTest(unittest.TestCase):
    def test_portal_plan(self):
        page = parser.parse_page(PLAN)
        self.assertEqual(page.time_range, "02.11.2026 - 08.11.2026 (KW45)")
        self.assertEqual(page.week_start, PLAN_MONDAY)
        self.assertEqual(page.balance, 12.34)
        self.assertEqual(page.balance_after_orders, 12.34)
        self.assertEqual(len(page.days), 5)
        day = page.days["2026-11-04"]
        self.assertEqual(day["label"], "Mi, 04.11.2026")
        self.assertEqual(list(day["categories"]), ["Menü A", "Menü B", "Menü C"])
        self.assertEqual(day["ordered"], [])
        dish = day["categories"]["Menü A"][0]
        self.assertEqual(
            dish["title"],
            "Schnippelbohneneintopf mit viel Gemüse und Putenbrustsaftschinken (Halal)",
        )
        self.assertEqual(dish["allergens"], ["b", "k", "2", "3", "8"])
        self.assertEqual(dish["allergen_names"][0], "Sellerie")
        self.assertEqual(
            [d["title"] for d in day["categories"]["Menü B"]],
            ["Kartoffelpuffer", "Apfelmus", "Rote Grütze", "Vanillesauce", "Kita: Obst/Rohkost"],
        )
        self.assertIn("Allergene", page.hinweis)

    def test_ordered_by_class_and_script(self):
        html = PLAN.replace(
            'id="td20261104_7229" class="speiseplan-menue"',
            'id="td20261104_7229" class="speiseplan-menue tdSelected"',
        )
        self.assertEqual(parser.parse_page(html).days["2026-11-04"]["ordered"], ["Menü B"])
        html = PLAN.replace("</form>", "<script>fillTxt(20261105,7499,1,0,'False');</script></form>")
        self.assertEqual(parser.parse_page(html).days["2026-11-05"]["ordered"], ["Menü C"])

    def test_amounts(self):
        self.assertEqual(parser.parse_amount("Kontostand: -1.234,50 €"), -1234.5)
        self.assertIsNone(parser.parse_amount("kein Betrag"))

    def test_old_public_plan(self):
        page = parser.parse_page(OLD_HTML)
        got = {
            day["label"]: {
                category: [
                    {"title": dish["title"], "additives_allergies": dish["allergens"]}
                    for dish in dishes
                ]
                for category, dishes in day["categories"].items()
            }
            for day in page.days.values()
        }
        self.assertEqual(got, OLD_JSON["json"])


class LoginTest(unittest.TestCase):
    def test_login_sequence(self):
        portal = Portal()
        fetch(portal)
        get, check, login = portal.calls[:3]
        self.assertEqual(get[0], "GET")
        self.assertEqual(check[2]["__VIEWSTATE"], "v1")
        self.assertEqual(check[2]["tbxKennwort"], "")
        self.assertEqual(check[2]["btnCheck"], "")
        self.assertEqual(check[2]["tbxProjekt"], "h2798")
        self.assertEqual(check[2]["tbxEinrichtung"], "Mensa-Gehrden")
        self.assertNotIn("btnLogin", check[2])
        self.assertEqual(login[2]["__VIEWSTATE"], "v2")
        self.assertEqual(login[2]["tbxKennwort"], "geheim")
        self.assertEqual(login[2]["btnLogin"], "Login")
        self.assertEqual(login[2]["hdfCheck"], "True")
        self.assertNotIn("btnCheck", login[2])

    def test_wrong_password(self):
        with self.assertRaises(api.InvalidAuth):
            fetch(Portal(), password="falsch")

    def test_wrong_account(self):
        with self.assertRaises(api.InvalidAccount):
            fetch(Portal(good_account=False))

    def test_session_rejected(self):
        with self.assertRaises(api.InvalidAuth):
            fetch(Portal(session_works=False))


class FetchTest(unittest.TestCase):
    def test_both_weeks_and_balance(self):
        data = fetch(Portal())
        self.assertIn("2026-11-02", data["days"])
        self.assertIn("2026-11-09", data["days"])
        self.assertEqual(data["balance"], 12.34)
        self.assertTrue(data["time_range"].startswith("02.11.2026"))

    def test_navigates_forward_and_back(self):
        self.assertIn("2026-11-16", fetch(Portal(), date(2026, 11, 18))["days"])
        portal = Portal()
        portal.week = 2
        self.assertIn("2026-11-02", fetch(portal)["days"])

    def test_only_reads(self):
        """Das Passwort geht nur an die Anmeldung, und nichts löst eine Bestellung aus."""
        portal = Portal()
        fetch(portal)
        for method, url, data in portal.calls:
            parsed = urlparse(url)
            self.assertEqual(parsed.scheme, "https")
            self.assertEqual(parsed.netloc, "app.mensamax.de")
            self.assertEqual(parsed.query, "")
            self.assertNotIn("action", data)
            if not url.endswith("/Login.aspx"):
                self.assertNotIn("tbxKennwort", data)
                self.assertTrue(set(data) & {"btnVor", "btnZurueck"} or method == "GET")
                self.assertFalse(data.get("hdfMenue") or data.get("hdfID"))


if __name__ == "__main__":
    unittest.main()
