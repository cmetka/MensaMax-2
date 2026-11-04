# MensaMax Speiseplan für Home Assistant

Meldet sich mit deinem Konto bei `app.mensamax.de` an und zeigt Speiseplan, Bestellstatus, Kontostand und Kioskkäufe als Sensoren.

Bestellen und Stornieren ist möglich, aber nur für Benutzer, die du freigibst. Ohne Freigabe liest die Integration nur.

Inoffiziell und auf Basis der Website gebaut, kann also ausfallen, wenn MensaMax seine Seiten ändert. Inspiriert von [philippdormann/mensamax-api](https://github.com/philippdormann/mensamax-api) (AGPL-3.0, archiviert).

## Installation

1. HACS → Menü oben rechts → **Benutzerdefinierte Repositories**.
2. `https://github.com/cmetka/MensaMax-2` eintragen, Typ **Integration**.
3. **MensaMax Speiseplan** herunterladen und Home Assistant neu starten.
4. **Einstellungen → Geräte & Dienste → Integration hinzufügen → MensaMax Speiseplan**.
5. Name, Projekt, Einrichtung, Benutzername und Kennwort eintragen. Projekt und Einrichtung stehen im Portal links unter „Ihr Projekt“ und „Schule“.

Für jedes weitere Konto wiederholst du Schritt 4 und 5 mit einem anderen Namen.

Home Assistant speichert das Kennwort unverschlüsselt in seinem Konfigurationsordner und in Backups. Verwende es deshalb nirgends sonst.

## Sensoren

Mit dem Namen `Mensa` entstehen:

| Sensor | Zustand | Attribute |
|---|---|---|
| `sensor.mensa_heute` | Gerichte der ersten belegten Kategorie oder `Kein Angebot` | `datum`, `tag`, `bestellt`, `gerichte` |
| `sensor.mensa_nachster_tag` | Dasselbe für den nächsten Tag mit Plan | `datum`, `tag`, `bestellt`, `gerichte` |
| `sensor.mensa_woche` | Zeitraum, z. B. `02.11.2026 - 08.11.2026 (KW45)` | `tage`, `hinweis` |
| `sensor.mensa_bestellt_heute` | z. B. `Menü B` oder `Nicht bestellt` | `gerichte` |
| `sensor.mensa_kontostand` | Aktueller Kontostand in Euro | |
| `sensor.mensa_kontostand_nach_bestellungen` | Kontostand nach offenen Bestellungen | |
| `sensor.mensa_montag` bis `sensor.mensa_freitag` | Datum oder `Kein Angebot` | `datum`, `menues` |
| `sensor.mensa_kiosk_letzte_woche` | Summe der letzten sieben Tage in Euro | `kaeufe` |

Am Wochenende zeigen die Wochentags-Sensoren schon die kommende Woche. `preis` ist leer, wenn das Portal für das Menü keinen Preis nennt.

Die Daten werden alle zwei Stunden abgerufen, für die laufende und, soweit vorhanden, die kommende Woche.

## Bestellen

Unter **Einstellungen → Geräte & Dienste → MensaMax Speiseplan → Konfigurieren** legst du fest, wer bestellen darf und wer benachrichtigt wird. Die Aktion `mensamax.bestellung` nimmt `tag`, `menue` und `aktion` (`bestellen`, `stornieren`, `umschalten`). Sie prüft den auslösenden Benutzer, lehnt Automationen ab und meldet nur dann Erfolg, wenn das Portal die Änderung danach anzeigt. Bestellungen kosten echtes Geld.

## Dashboard-Karte

```yaml
type: markdown
title: 🍽️ Heute in der Mensa
content: >
  {% set gerichte = state_attr('sensor.mensa_heute', 'gerichte') %}
  {% set bestellt = state_attr('sensor.mensa_heute', 'bestellt') or [] %}
  {% if gerichte %}
  {% for kategorie, speisen in gerichte.items() %}
  **{{ '✅ ' if kategorie in bestellt }}{{ kategorie }}:** {{ speisen | map(attribute='title') | join(', ') }}

  {% endfor %}
  {% else %}
  Heute gibt es kein Angebot.
  {% endif %}

  💶 Kontostand: {{ states('sensor.mensa_kontostand') }} €
```

## Tests

```bash
python3 tests/test_mensamax.py
```

Benötigt `beautifulsoup4`. Die Tests laufen gegen bereinigte Beispielseiten und einen nachgebauten Server, nicht gegen MensaMax selbst.

## Lizenz

AGPL-3.0, siehe `LICENSE`.
