# CLAUDE.md – cbpi4-gaerdruck

Gilt zusätzlich zur übergeordneten `~/Claude/CLAUDE.md`. CraftBeerPi4-Plugin für die Druckgärung im FermZilla
30 L All Rounder (Spund- und CO₂-Ventil an einem Gaspfosten über Duotight-T, Drucksensoren am ADS1115 des HAT
„Biersteuerung“, Bogen `Ventile_Sensoren`, Zweig `ventile-druck` im Repo Biersteuerung). Teil des **Brau-Verbunds** von Timm. Gesamtbild in `docs/systeme.md` der Repos `Biersteuerung` und
`BrewPiLess-PCB` (gleiche Datei). Nachbar-Repos liegen lokal nebeneinander unter `~/Claude/`:
`cbpi4-brausicher`, `cbpi4-gaerdruck`, `cbpi4-ventil`, `cbpi4-simulation`, `cbpi4-bme280`, `Biersteuerung` (HAT,
Braubrücke, Plugin `cbpi4-kellersensor`).

## Inhalt

- `DruckSensor` (ADS1115, bar, nie `None`, Nullpunkt-Aktion, eigene einfache Simulation), `GaerDruckLogik`
  (Temperatur mit Kompressorpause, Spunden/Nachdrücken, Grenzdruck, Fühlervergleich, Wirkungs- und Sprungprüfung),
  `KegDruckLogik`, `GaerDruckAutostart` (eigener Autostart). Typnamen nicht umbenennen.
- `technik.py` ohne cbpi (ADS1115, Umrechnung, `Druckregler`, `Temperaturregler`), dort testbar.
- `einrichten.py` (nur Standardbibliothek) legt per REST Fühler, Ventile, Gärtank und Dashboard 2 an,
  Ventilsymbole aus `widgets/`.
- Die Ventile sind `SicherAktor` aus `cbpi4-brausicher` (Ventile 1–4 = GPIO 16, 17, 23, 25).

## Regeln

- **Mechanisches Überdruckventil bleibt Pflicht**, die Software ist die zweite Sicherung. Grenzdruck unter dem
  Tankmaximum (All Rounder 2,4 bar), Druckminderer unter den Grenzdruck stellen.
- Soll- und Messwerte in **bar**. Der Solldruck-Dialog von cbpi4gui kennt nur kPa/PSI (Schieber 0–400 in ganzen
  Schritten, Wert wird unverändert übernommen): Solldruck über Gärplan-Schritte („Pressure“) oder REST setzen.
- Prüfen ohne Hardware: Tests, dazu die Brausimulation (`cbpi4-simulation`) mit den echten Ventil-Aktoren. Bei
  Zeitraffer > ~20 braucht die Simulation `Pause s` = 1.
- Zweig `main`. Tests: `pip install cbpi4 pytest && python -m pytest tests/ -q`.
- UI-Texte mit Umlauten, Code-Kommentare ohne.

## cbpi-4.7.7-Fallen (gemessen, gelten für alle Plugins des Verbunds)

- Ein Sensor darf **nie `None`** liefern: Kessellogik, MashStep und Gärschritte vergleichen ungeprüft. Gültigkeit
  steckt im Alter des Werts (`alter()`).
- Aktor-`get_state()` liefert einen **bool**, kein dict, sonst zeigt die Oberfläche LED/SVG/Leitungen immer „an“.
- Schritte schalten nur mit `asyncio.create_task(self.next())` aus `run()` weiter.
- Kessel- und Gärtanklogik setzen im `finally` von `run()` `self.running = False`. cbpi stoppt sie am Schrittende nur
  über `state` und verweigert sonst den nächsten Start („already running“).
- Der eingebaute „Fermenter Autostart“ startet nichts (ruft `start()` statt `start_logic()`).
- `POST /kettle/{id}/off` liefert HTTP 500 (Methode fehlt), Kessel nur per `toggle` schalten.
- cbpi findet Plugins über `pkgutil.iter_modules`: `pip install -e` wird nicht geladen. Normal installieren oder
  `pip install -e . --config-settings editable_mode=compat`.
- Lokal testen: `web.run_app` bindet 0.0.0.0, also über einen Wrapper mit `host="127.0.0.1"` starten, eigener
  Konfigordner und Port (`cbpi --config-folder-path=… start`, Port in `config.yaml`).
