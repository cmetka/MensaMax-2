"""Prüft Parser und Anmeldung ohne Home Assistant: python3 tests/test_mensamax.py"""

import asyncio
from datetime import date, timedelta
import importlib.util
import json
from pathlib import Path
import sys
import types
from urllib.parse import parse_qsl, urlparse
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
    '<input name="tbxKennwort"><input type="submit" name="btnLogin" value="Anmelden">'
    '<input type="submit" name="btnCheck"></form>'
)
LOGIN_2 = (
    "<form>" + HIDDEN.format("v2") + '<input type="hidden" name="hdfCheck" value="True">'
    '<input name="tbxBenutzername">'
    '<input name="tbxKennwort"><input type="submit" name="btnLogin" value="Anmelden"></form>'
)


KIOSK_REPLY = json.dumps(
    {
        "d": json.dumps(
            {
                "Summe": 400,
                "Gutschrift": 0,
                "Kaeufe": [
                    {
                        "AnzahlWare": "1x Brötchen",
                        "Betrag": 250,
                        "Datum": "2026-11-02T09:57:28.123",
                        "DatumAnzeige": "Mo, 02.11.2026",
                        "Gutschrift": 0,
                        "UhrzeitAnzeige": "09:57",
                    },
                    {
                        "AnzahlWare": "1x Brezel",
                        "Betrag": 150,
                        "Datum": "2026-11-03T12:25:45.097",
                        "DatumAnzeige": "Di, 03.11.2026",
                        "Gutschrift": 0,
                        "UhrzeitAnzeige": "12:25",
                    },
                ],
            }
        )
    }
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
    status = 200

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
        self.ordered = set()
        self.ignore_orders = False

    def request(self, method, url, data=None, headers=None, timeout=None):
        query = dict(parse_qsl(urlparse(url).query))
        if "action" in query:
            self.calls.append((method, url, {}))
            key = (query["day"], query["menue"])
            if not self.ignore_orders:
                if query["action"] == "save":
                    self.ordered.add(key)
                else:
                    self.ordered.discard(key)
            return Response(self.plan(), url)
        if url.endswith("/GKauf"):
            self.calls.append((method, url, {}))
            self.kiosk_body = json.loads(data)
            return Response(KIOSK_REPLY, url)
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
        if url.endswith("navigation.aspx"):
            return Response("<title>Menü</title>", url)
        if data and "btnVor" in data:
            self.week += 1
        if data and "btnZurueck" in data:
            self.week -= 1
        return Response(self.plan(), url)

    def plan(self):
        html = shifted(self.week)
        for day, menu in self.ordered:
            html = html.replace(
                f'id="td{day}_{menu}" class="speiseplan-menue"',
                f'id="td{day}_{menu}" class="speiseplan-menue tdSelected"',
            )
        return html


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
        self.assertEqual(login[2]["btnLogin"], "Anmelden")
        self.assertEqual(login[2]["hdfCheck"], "True")
        self.assertNotIn("btnCheck", login[2])

    def test_wrong_password(self):
        with self.assertRaises(api.InvalidAuth):
            fetch(Portal(), password="falsch")

    def test_rejection_explains_without_secrets(self):
        with self.assertRaises(api.InvalidAuth) as caught:
            fetch(Portal(), password="falsch")
        text = str(caught.exception)
        self.assertIn("Login.aspx", text)
        self.assertNotIn("falsch", text)

    def test_visits_portal_like_browser(self):
        portal = Portal()
        fetch(portal)
        paths = [call[1].rsplit("/", 1)[-1] for call in portal.calls]
        self.assertLess(paths.index("index.aspx"), paths.index("PlanForm.aspx"))
        self.assertLess(paths.index("GetJwtToken"), paths.index("PlanForm.aspx"))

    def test_wrong_account(self):
        with self.assertRaises(api.InvalidAccount):
            fetch(Portal(good_account=False))

    def test_session_rejected(self):
        with self.assertRaises(api.InvalidAuth):
            fetch(Portal(session_works=False))


def change(portal, day, category, order):
    client = api.MensaMaxClient(portal, "h2798", "Mensa-Gehrden", "kind", "geheim")
    return asyncio.run(client.async_set_order(day, category, order))


class OrderTest(unittest.TestCase):
    DAY = date(2026, 11, 4)

    def actions(self, portal):
        return [call[1] for call in portal.calls if "action=" in call[1]]

    def test_order_and_cancel(self):
        portal = Portal()
        self.assertTrue(change(portal, self.DAY, "Menü B", True))
        self.assertEqual(portal.ordered, {("20261104", "7229")})
        self.assertIn(
            "action=save&menue=7229&day=20261104&hinweis=&anzahl=1&ao=undefined",
            self.actions(portal)[0],
        )
        self.assertFalse(change(portal, self.DAY, "Menü B", False))
        self.assertEqual(portal.ordered, set())

    def test_toggle(self):
        portal = Portal()
        self.assertTrue(change(portal, self.DAY, "Menü A", None))
        self.assertFalse(change(portal, self.DAY, "Menü A", None))

    def test_already_ordered_sends_nothing(self):
        portal = Portal()
        portal.ordered.add(("20261104", "7229"))
        self.assertTrue(change(portal, self.DAY, "Menü B", True))
        self.assertEqual(self.actions(portal), [])

    def test_past_day_is_refused(self):
        portal = Portal()
        with self.assertRaises(api.OrderError):
            change(portal, date(2026, 11, 2), "Menü A", True)
        self.assertEqual(self.actions(portal), [])

    def test_unknown_menu_is_refused(self):
        portal = Portal()
        with self.assertRaises(api.OrderError):
            change(portal, self.DAY, "Menü Z", True)
        self.assertEqual(self.actions(portal), [])

    def test_unconfirmed_order_fails(self):
        portal = Portal()
        portal.ignore_orders = True
        with self.assertRaises(api.OrderError):
            change(portal, self.DAY, "Menü B", True)

    def test_fetch_never_orders(self):
        portal = Portal()
        fetch(portal)
        self.assertEqual(self.actions(portal), [])


class KioskTest(unittest.TestCase):
    def test_last_seven_days_newest_first(self):
        portal = Portal()
        data = fetch(portal)
        self.assertEqual(
            portal.kiosk_body, {"pv_strVon": "28.10.2026", "pv_strBis": "04.11.2026"}
        )
        self.assertEqual(data["kiosk"]["total"], 4.0)
        first = data["kiosk"]["purchases"][0]
        self.assertEqual(first["ware"], "1x Brezel")
        self.assertEqual(first["uhrzeit"], "12:25")
        self.assertEqual(first["datum"], "Di, 03.11.2026")
        self.assertEqual(first["betrag"], 1.5)

    def test_kiosk_failure_keeps_menu(self):
        class Broken(Portal):
            def request(self, method, url, data=None, headers=None, timeout=None):
                if url.endswith("/GKauf"):
                    return Response("kein json", url)
                return super().request(method, url, data, headers, timeout)

        data = fetch(Broken())
        self.assertIsNone(data["kiosk"])
        self.assertIn("2026-11-02", data["days"])


class PriceTest(unittest.TestCase):
    def test_price_when_portal_names_one(self):
        html = PLAN.replace(
            'class="menue-preis" style="width:30%;"></td>',
            'class="menue-preis" style="width:30%;">3,80 €</td>',
        )
        day = parser.parse_page(html).days["2026-11-04"]
        self.assertEqual(day["prices"]["Menü A"], 3.8)

    def test_no_price_stays_empty(self):
        self.assertEqual(parser.parse_page(PLAN).days["2026-11-04"]["prices"], {})


class DuplicateAttributeTest(unittest.TestCase):
    def test_first_name_wins_like_in_browsers(self):
        page = parser.parse_page(
            '<form><input name="tbxBenutzername" type="text" name="benutzername">'
            '<input name="tbxKennwort" type="password" type="text" name="kennwort"></form>'
        )
        self.assertTrue(page.has_login_form)
        self.assertEqual(page.input_names, ["tbxBenutzername", "tbxKennwort"])


class DiagnosticTest(unittest.TestCase):
    def test_details_name_the_page_but_no_password(self):
        class NoPlan(Portal):
            def request(self, method, url, data=None, headers=None, timeout=None):
                if "PlanForm" in url:
                    self.calls.append((method, url, dict(data or {})))
                    return Response("<title>Hinweise</title><input name='btnOk'>", url)
                return super().request(method, url, data, headers, timeout)

        with self.assertRaises(api.PlanNotFound) as caught:
            fetch(NoPlan())
        text = str(caught.exception)
        self.assertIn("PlanForm.aspx", text)
        self.assertIn("Hinweise", text)
        self.assertIn("btnOk", text)
        self.assertNotIn("geheim", text)


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
                self.assertTrue(
                    set(data) & {"btnVor", "btnZurueck"}
                    or method == "GET"
                    or url.endswith("/GetJwtToken")
                    or url.endswith("/GKauf")
                )
                self.assertFalse(data.get("hdfMenue") or data.get("hdfID"))


if __name__ == "__main__":
    unittest.main()
