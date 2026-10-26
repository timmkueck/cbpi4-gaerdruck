#!/usr/bin/env python3
"""Legt in CraftBeerPi4 alles fuer die Druckgaerung an und baut das Dashboard.

Nur Standardbibliothek. Laeuft auf dem Brau-Pi gegen das laufende cbpi:

    python3 einrichten.py                        # echte Hardware, Dashboard 2
    python3 einrichten.py --temperatur "Bier" --kuehlung "Kühlschrank"
    python3 einrichten.py --simulation           # ohne Hardware ausprobieren

Vorhandenes (gleicher Name) wird nicht doppelt angelegt, sondern wiederverwendet.
Das Dashboard wird jedes Mal neu geschrieben (nur die gewaehlte Nummer).
"""
import argparse
import json
import shutil
import sys
from pathlib import Path
import urllib.error
import urllib.request

TANK = "FermZilla 30 L"
SENSOREN = {  # Name -> Kanal am ADS1115 (HAT J18)
    "Tankdruck": "AIN0 (Druck 1)",
    "CO₂-Vordruck": "AIN1 (Druck 2)",
    "Keg 1 Druck": "AIN2 (Druck 3)",
    "Keg 2 Druck": "AIN3 (Druck 4)",
}
VENTILE = {  # Name -> GPIO (HAT J17 Ventile 1-4: 16, 17, 23, 25)
    "Spundventil": 16,
    "CO₂-Ventil": 17,
    "CO₂ Keg 1": 23,
    "CO₂ Keg 2": 25,
}
KEGS = {  # Name -> (Drucksensor, CO2-Ventil)
    "Keg 1": ("Keg 1 Druck", "CO₂ Keg 1"),
    "Keg 2": ("Keg 2 Druck", "CO₂ Keg 2"),
}
WIDGETS = Path(__file__).resolve().parent / "widgets"
ZU, OFFEN = "gaerdruck_ventil_zu", "gaerdruck_ventil_offen"
ABSTAND_OBEN = 40  # px zwischen Titelzeile und Schema


class Cbpi:
    def __init__(self, url):
        self.url = url.rstrip("/")

    def rufen(self, pfad, daten=None, methode=None):
        body = None if daten is None else json.dumps(daten).encode()
        req = urllib.request.Request(self.url + pfad, data=body, method=methode or ("POST" if body else "GET"),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                text = r.read().decode()
        except urllib.error.HTTPError as e:
            sys.exit(f"{methode or 'GET'} {pfad}: HTTP {e.code} {e.read().decode()[:300]}")
        except urllib.error.URLError as e:
            sys.exit(f"CraftBeerPi unter {self.url} nicht erreichbar: {e.reason}")
        return json.loads(text) if text.strip() else None

    def liste(self, art):
        antwort = self.rufen(f"/{art}/")
        return antwort.get("data", antwort) if isinstance(antwort, dict) else antwort

    def typen(self, art):
        antwort = self.rufen(f"/{art}/")
        return antwort.get("types", {}) if isinstance(antwort, dict) else {}


def nach_name(eintraege, name):
    return next((e for e in eintraege if e.get("name") == name), None)


def sicherstellen(cbpi, art, name, daten):
    vorhanden = nach_name(cbpi.liste(art), name)
    if vorhanden:
        print(f"  vorhanden: {name}")
        return vorhanden["id"]
    neu = cbpi.rufen(f"/{art}/", dict(name=name, **daten))
    print(f"  angelegt:  {name}")
    return neu["id"]


def id_von(cbpi, art, name):
    if not name:
        return ""
    e = nach_name(cbpi.liste(art), name)
    if e is None:
        namen = ", ".join(x["name"] for x in cbpi.liste(art)) or "keine"
        sys.exit(f"{art} „{name}“ nicht gefunden (vorhanden: {namen})")
    return e["id"]


# --- Dashboard -----------------------------------------------------------------

def element(nr, name, typ, x, y, **props):
    return dict(id=f"gaerdruck{nr:02d}", name=name, type=typ, x=x, y=y,
                props={k: str(v) for k, v in props.items()})


def dashboard(t):
    """t: IDs (tank, temp, druck, vordruck, spund, co2, kuehlung, heizung, ventile={Name: ID},
    kegs=[{name, id, druck, ventil}]).

    Der FermZilla hat einen Gasanschluss. Alles haengt an einer 8-mm-Leitung:
    Gaspfosten -> Duotight-Kupplung -> T (Tankdruck) -> T (Spundventil -> Abluft)
    -> CO2-Ventil -> Verteiler -> T (CO2-Vordruck) -> Druckminderer -> Flasche.
    Am Verteiler: Ventil 3 -> T (Druck 3) -> Keg 1, Ventil 4 -> T (Druck 4) -> Keg 2."""
    e = []
    n = iter(range(1, 100))

    def add(*a, **k):
        e.append(element(next(n), *a, **k))

    def ventil(name, aktor, x, y, breite=50):
        add(name, "CustomSVG", x, y, actor=aktor, WidgetOff=ZU, WidgetOn=OFFEN, width=breite, rotation=0)

    weiss, grau, gruen, blau, orange = "#ffffff", "#9e9e9e", "#4caf50", "#4fc3f7", "#ffb74d"
    add("FermZilla 30 L · Druckgärung", "Text", 20, 10, size=24, fontweight="bold", color=weiss)
    add("Uhr", "Clock", 760, 14, size=18, showDate="yes", color=grau)

    # Schema: eine Leitung (y = 45) vom Gaspfosten bis zur CO2-Flasche
    add("Gärtank", "Tank", 20, 62, width=110, heigth=200)
    add("Bier", "Text", 36, 112, size=12, color=grau)
    if t["temp"]:
        add("Biertemperatur", "Sensor", 36, 128, sensor=t["temp"], unit=" °C", size=22, fontweight="bold",
            digits=1, color=weiss)
    add("Gaspfosten + Duotight-Kupplung", "Text", 84, 52, size=9, color=grau)

    add("Duotight-T", "Text", 150, 28, size=9, color=grau)
    add("Drucksensor", "Text", 146, 84, size=10, color=grau)
    add("Tankdruck", "Sensor", 146, 100, sensor=t["druck"], unit=" bar", size=22, fontweight="bold",
        digits=2, color=gruen)

    add("Duotight-T ", "Text", 260, 28, size=9, color=grau)
    ventil("Schema Spundventil", t["spund"], 300, 131)
    add("Spundventil", "Text", 298, 172, size=10, color=grau)
    add("Abluft", "Text", 386, 143, size=10, color=grau)

    ventil("Schema CO₂-Ventil", t["co2"], 430, 26)
    add("CO₂-Ventil", "Text", 430, 68, size=10, color=grau)

    add("Verteiler", "Text", 520, 28, size=9, color=grau)
    add("Duotight-T  ", "Text", 584, 28, size=9, color=grau)
    add("CO₂-Vordruck ", "Text", 576, 84, size=10, color=grau)
    if t["vordruck"]:
        add("CO₂-Vordruck", "Sensor", 576, 100, sensor=t["vordruck"], unit=" bar", size=18, digits=2, color=blau)
    add("Druckminderer", "Text", 615, 52, size=10, color=grau)
    add("CO₂-Flasche", "Text", 690, 37, size=14, color=weiss)

    # Kegs am Verteiler (x = 540): Ventil, T mit Drucksensor, Gaskupplung
    for k, y in zip(t["kegs"], (150, 215)):
        ventil(f"Schema {k['name']}", k["ventil"], 560, y - 19)
        add(f"Ventil {k['name']}", "Text", 488, y - 9, size=9, color=grau)
        add(f"{k['name']} Druck", "Sensor", 618, y + 22, sensor=k["druck"], unit=" bar", size=14, digits=2,
            color=gruen)
        add(k["name"], "Text", 705, y - 9, size=14, color=weiss)

    # Soll, Kuehlung/Heizung, Gaertank ein/aus
    add("Soll Temperatur", "Text", 760, 60, size=12, color=grau)
    add("SollTemp", "FermenterTargetTemp", 760, 76, fermenter=t["tank"], unit=" °C", size=20, color=weiss)
    add("Soll Druck", "Text", 890, 60, size=12, color=grau)
    add("SollDruck", "FermenterTargetPressure", 890, 76, fermenter=t["tank"], unit=" bar", size=20, color=weiss)
    x = 760
    for name, aktor, farbe in (("Kühlung", t["kuehlung"], "blue"), ("Heizung", t["heizung"], "red")):
        if aktor:
            add(f"LED {name}", "Led", x, 122, actor=aktor, color=farbe)
            add(name, "Text", x + 30, 124, size=14, color=weiss)
            x += 130
    add("Gärtank", "FermenterControl", 760, 170, fermenter=t["tank"], size="normal", orientation="horizontal")

    # Alle Ventile des HAT in einer Reihe: Symbol, Name, Knopf
    add("Ventile", "Text", 20, 262, size=14, fontweight="bold", color=weiss)
    for i, (name, aktor) in enumerate(t["ventile"].items()):
        x = 20 + i * 180
        ventil(f"Ventil {name}", aktor, x, 288, breite=52)
        add(name, "Text", x + 60, 288, size=12, color=weiss)
        if aktor != t["co2"]:  # CO2 nur ueber die Regelung, nie dauerhaft von Hand
            add("Ablassen" if aktor == t["spund"] else "Drücken", "ActorButton", x + 60, 306, actor=aktor,
                size=10, action="no", powerslider="No")
        else:
            add("nur Regelung", "Text", x + 60, 310, size=10, color=grau)

    # Kegs: Druck, Soll, Regelung ein/aus
    for i, k in enumerate(t["kegs"]):
        x = 20 + i * 500
        add(f"{k['name']} ", "Text", x, 352, size=14, fontweight="bold", color=weiss)
        add(f"{k['name']} Druck ", "Sensor", x, 372, sensor=k["druck"], unit=" bar", size=20, digits=2, color=gruen)
        add(f"{k['name']} Soll", "Text", x + 120, 356, size=11, color=grau)
        add(f"{k['name']} SollDruck", "FermenterTargetPressure", x + 120, 372, fermenter=k["id"], unit=" bar",
            size=20, color=weiss)
        add(k["name"], "FermenterControl", x + 230, 362, fermenter=k["id"], size="small", orientation="horizontal")

    # Verlauf und Gaerplan
    add("Tankdruck (bar)", "Text", 20, 432, size=12, color=grau)
    add("Verlauf Druck", "Chart", 20, 448, sensor=t["druck"], linecolor=gruen, refresh=30, width=480, height=200)
    if t["temp"]:
        add("Biertemperatur (°C)", "Text", 520, 432, size=12, color=grau)
        add("Verlauf Temperatur", "Chart", 520, 448, sensor=t["temp"], linecolor=orange, refresh=30,
            width=480, height=200)
    add("Gärplan", "FermenterSteps", 20, 670, fermenter=t["tank"], width=980, maxheight=220, stepsize=14,
        namesize=14)

    # Leitungen: fliessen, solange das Ventil des Zweigs offen ist. Die Oberflaeche verlangt alle
    # Aktoren einer Bedingung gleichzeitig an, deshalb je Verbraucher ein eigener, ueberlappender Pfad
    # ab der Flasche. Fuehlerstiche stehen.
    grau_leitung = "#4A4A4A"

    def leitung(name, punkte, aktoren=()):
        return dict(id=f"gaerdruck_{name}", coordinates=punkte,
                    condition=dict(left=[], right=[a for a in aktoren if a], stroke=7, color=grau_leitung))

    pfade = [
        leitung("stamm", [[680, 45], [75, 45], [75, 64]], [t["co2"]]),
        leitung("tankfuehler", [[170, 45], [170, 80]]),
        leitung("spund", [[280, 45], [280, 150], [380, 150]], [t["spund"]]),
        leitung("vordruck", [[600, 45], [600, 80]]),
    ]
    for i, (k, y) in enumerate(zip(t["kegs"], (150, 215))):
        pfade.append(leitung(f"keg{i + 1}", [[680, 45], [540, 45], [540, y], [700, y]], [k["ventil"]]))
        pfade.append(leitung(f"keg{i + 1}_fuehler", [[640, y], [640, y + 20]]))
    # Alles unter Titel und Uhr eine Zeile tiefer, damit die Leitung frei vom Titel liegt
    for el in e[2:]:
        el["y"] += ABSTAND_OBEN
    for pf in pfade:
        pf["coordinates"] = [[x, y + ABSTAND_OBEN] for x, y in pf["coordinates"]]
    return dict(elements=e, pathes=pfade)


def widgets_kopieren(config):
    kandidaten = [Path(config).expanduser()] if config else [Path.home() / "config", Path.home() / "cbpi4" / "config"]
    for c in kandidaten:
        if (c / "config.yaml").exists():
            ziel = c / "dashboard" / "widgets"
            ziel.mkdir(parents=True, exist_ok=True)
            for svg in WIDGETS.glob("*.svg"):
                shutil.copy(svg, ziel / svg.name)
            print(f"Ventilsymbole nach {ziel}")
            return
    print("WARNUNG: Konfigurationsordner von CraftBeerPi nicht gefunden (config.yaml), Ventilsymbole fehlen. "
          "Mit --config <Ordner> angeben.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--dashboard", type=int, default=2, help="Nummer des Dashboards (1-4), wird überschrieben")
    ap.add_argument("--temperatur", help="Name des vorhandenen Fühlers für die Biertemperatur")
    ap.add_argument("--kuehlung", help="Name des vorhandenen Aktors für die Kühlung")
    ap.add_argument("--heizung", help="Name des vorhandenen Aktors für die Heizung")
    ap.add_argument("--simulation", action="store_true", help="Fühler und Ventile ohne Hardware")
    ap.add_argument("--config", help="Konfigurationsordner von CraftBeerPi (mit config.yaml), für die Ventilsymbole")
    a = ap.parse_args()
    cbpi = Cbpi(a.url)
    sim = "Ja" if a.simulation else "Nein"

    fehlend = [t for t, art in (("DruckSensor", "sensor"), ("SicherAktor", "actor"))
               if t not in json.dumps(cbpi.typen(art))]
    if fehlend:
        sys.exit(f"Plugin fehlt in CraftBeerPi: {', '.join(fehlend)} "
                 "(cbpi4-gaerdruck bzw. cbpi4-brausicher installieren, cbpi neu starten)")

    print("Fühler:")
    s = {name: sicherstellen(cbpi, "sensor", name, dict(type="DruckSensor", props={
        "Kanal": kanal, "Messbereich": "0-80 psi", "Adresse": "0x48", "Teiler": 0.6667,
        "Nullpunkt bar": 0, "Max Alter s": 10, "Simulation": sim})) for name, kanal in SENSOREN.items()}
    print("Ventile:")
    v = {name: sicherstellen(cbpi, "actor", name, dict(type="SicherAktor", props={
        "GPIO": gpio, "Aktiv bei": "HIGH", "Art": "Sonstiges", "Takt s": 5, "Simulation": sim}))
        for name, gpio in VENTILE.items()}
    temp, kuehl, heiz = (id_von(cbpi, "sensor", a.temperatur), id_von(cbpi, "actor", a.kuehlung),
                         id_von(cbpi, "actor", a.heizung))
    print("Gärtank:")
    tank = sicherstellen(cbpi, "fermenter", TANK, dict(
        type="GaerDruckLogik", sensor=temp, pressure_sensor=s["Tankdruck"], heater=heiz, cooler=kuehl,
        valve=v["Spundventil"], brewname="", description="Druckgärung, Spunden und Nachdrücken",
        target_temp=18, target_pressure=1.0, props={
            "Ventil Drücken": v["CO₂-Ventil"], "Fühler CO₂-Vordruck": s["CO₂-Vordruck"],
            "Grenzdruck bar": 2.0, "Nachdrücken": "Ja", "Bei Fühlerausfall": "Spundventil pulsen",
            "AutoStart": "Yes"}))
    # Bei vorhandenem Gaertank die tatsaechlichen Zuordnungen fuers Dashboard nehmen
    f = nach_name(cbpi.liste("fermenter"), TANK)
    ids = dict(tank=tank, temp=f.get("sensor") or temp, druck=f.get("pressure_sensor") or s["Tankdruck"],
               vordruck=(f.get("props") or {}).get("Fühler CO₂-Vordruck") or s["CO₂-Vordruck"],
               spund=f.get("valve") or v["Spundventil"],
               co2=(f.get("props") or {}).get("Ventil Drücken") or v["CO₂-Ventil"],
               kuehlung=f.get("cooler") or kuehl, heizung=f.get("heater") or heiz)
    print("Kegs:")
    for name, (sensor, ventil) in KEGS.items():
        sicherstellen(cbpi, "fermenter", name, dict(
            type="KegDruckLogik", sensor="", pressure_sensor=s[sensor], heater="", cooler="", valve=v[ventil],
            brewname="", description="Keg auf Solldruck halten", target_temp=0, target_pressure=1.0, props={
                "Fühler CO₂-Vordruck": s["CO₂-Vordruck"], "Grenzdruck bar": 3.0, "AutoStart": "Yes"}))
    gaertanks = cbpi.liste("fermenter")
    ids["kegs"] = [dict(name=name, id=k["id"], druck=k.get("pressure_sensor") or s[sensor],
                        ventil=k.get("valve") or v[ventil])
                   for name, (sensor, ventil) in KEGS.items() for k in [nach_name(gaertanks, name)]]
    ids["ventile"] = {"Spundventil": ids["spund"], "CO₂-Ventil": ids["co2"],
                      **{f"CO₂ {k['name']}": k["ventil"] for k in ids["kegs"]}}
    widgets_kopieren(a.config)
    cbpi.rufen(f"/dashboard/{a.dashboard}/content", dashboard(ids))
    print(f"Dashboard {a.dashboard} geschrieben: {a.url}/  (oben rechts Dashboard {a.dashboard} wählen)")


if __name__ == "__main__":
    main()
