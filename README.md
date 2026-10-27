# cbpi4-gaerdruck

CraftBeerPi4-Plugin und Dashboard für die **Druckgärung im FermZilla 30 L All Rounder** und **zwei Kegs** an
einer CO₂-Flasche: Spundventil und CO₂-Ventil am Gärtank, je ein CO₂-Ventil pro Keg, Drucksensoren 0,5–4,5 V am
ADS1115 des HAT „Biersteuerung“ (Bogen *Ventile_Sensoren*, Zweig `ventile-druck`). Verbunden wird alles mit
8-mm-Schlauch und Duotight.

| In CraftBeerPi | Typ | Aufgabe |
|---|---|---|
| `DruckSensor` | Sensor | Drucktransmitter am ADS1115 (0x48 = Druck 1–4 an J18, 0x49 = Druck 5–8 an J21, je AIN0–3), Wert in bar, nie `None`, Meldung bei Kabelbruch/Kurzschluss |
| `GaerDruckLogik` | Gärtanklogik | Temperatur (Heizen/Kühlen mit Kompressorpause) und Druck (Spunden + Nachdrücken) |
| `KegDruckLogik` | Gärtanklogik | Keg auf Solldruck halten, nur Nachdrücken (ein Keg hat kein Ablassventil) |
| `GaerDruckAutostart` | Erweiterung | startet die Regelung nach jedem Neustart von cbpi wieder (die eingebaute „Fermenter Autostart“ startet in 4.7.7 nichts) |
| `einrichten.py` | Skript | legt die fünf Fühler, Ventile, Gärtank, beide Kegs und **Dashboard 2** per REST an |

Die Ventile sind `SicherAktor` aus [`cbpi4-brausicher`](https://github.com/timmkueck/cbpi4-brausicher) (lgpio, Wächter-Thread, Ausgang aus,
wenn cbpi hängt).

## Aufbau

Der FermZilla hat **einen** Gasanschluss. Tankfühler, Spundventil und CO₂-Zuleitung hängen deshalb über
Duotight-T-Stücke an derselben Leitung:

```
FermZilla ─ Gaspfosten ─ Duotight-Kupplung ─ T ─ T ─ CO₂-Ventil ─┐
                                             │   │   (V2, GPIO 17)│
                                    Tankdruck│   └ Spundventil ─ Abluft
                                    (Druck 1)     (V1, GPIO 16)    │
                                                                   Verteiler ─ T ─ Druckminderer ─ T ─ CO₂-Flasche
                                                                   │           │                      │
                                                                   │           CO₂-Vordruck (Druck 2)  Flaschendruck (Druck 5)
                                       Keg 1 ─ Kupplung ─ T ─ V3 (GPIO 26) ┤
                                                          Druck 3          │
                                       Keg 2 ─ Kupplung ─ T ─ V4 (GPIO 13) ┘
                                                          Druck 4
```

| Messstelle | Kanal | Wozu |
|---|---|---|
| Tankdruck, T zwischen Kupplung und Ventilen | Druck 1 / AIN0 | Regelgröße Gärtank, Grenzdruck |
| CO₂-Vordruck hinter dem Druckminderer | Druck 2 / AIN1 | kein Nachdrücken bei leerer Flasche oder zu hoch eingestelltem Druckminderer |
| Keg 1, T zwischen Ventil 3 und Kupplung | Druck 3 / AIN2 | Regelgröße Keg 1 |
| Keg 2, T zwischen Ventil 4 und Kupplung | Druck 4 / AIN3 | Regelgröße Keg 2 |
| CO₂-Flasche **vor** dem Druckminderer | Druck 5 / 0x49 AIN0 | Füllstand der Flasche, lange bevor sie leer ist |

Druck 1–4 hängen am ersten ADS1115 (0x48, J18), Druck 5 am zweiten (0x49, J21). Dessen Eingänge heißen in der
Oberfläche ebenfalls `AIN0 (Druck 1)` … `AIN3 (Druck 4)` -- entscheidend ist die **Adresse**: `AIN0` an `0x49`
ist Druck 5.

### Messbereich der CO₂-Flasche

Hinter dem Druckminderer genügt der übliche 0–80-psi-Geber. **Vor** dem Druckminderer nicht: In der Flasche steht
der Dampfdruck von CO₂, bei Raumtemperatur rund 50 bar und nahe dem kritischen Punkt (31 °C) bis etwa 74 bar.
Dort gehört ein Hochdruckgeber mit gleicher Schnittstelle (0,5–4,5 V), Messbereich **0–100 bar** (oder 0–1500 psi);
beide stehen in der Auswahl. Ein kleinerer Geber wäre dauerhaft übersteuert und meldete nur „Kurzschluss nach 5 V".

Der Flaschendruck ist eine Anzeige, keine Regelgröße: Das Nachdrücken entscheidet weiter der CO₂-Vordruck
(Druck 2) hinter dem Druckminderer.

Teile: Drucksensoren mit Duotight-Adapter wie in `BrewPiLess-PCB/docs/drucksensoren.md`, Duotight 8 mm Equal Tee,
Magnetventile 12 V (NC) mit 1/4"-BSP-Gewinde und Duotight 8 mm × 1/4" BSP.

Folgen der einen Leitung:

- Während eines Pulses misst Druck 1 den Leitungsdruck, nicht den Tank. Entschieden wird deshalb erst nach der
  Pause (3 s).
- Ist der Gaspfosten zu (Kräusen), sieht der Fühler nur noch die Leitung. Erkennbar: Ein Puls ändert den Druck um
  mehr als 0,3 bar, beim Tank mit 5–7 l Kopfraum sind es Hundertstel. Dann kommt die Meldung „Gasweg zum Tank
  verstopft“. Der Tank selbst ist dann nur noch durch sein Überdruckventil geschützt.
- Ist die Kupplung nicht aufgesteckt, misst der Fühler 0 bar. Nach 6 Drückpulsen ohne Anstieg wird das Nachdrücken
  gesperrt („Leck oder CO₂ leer“).

**Mechanisches Überdruckventil (PRV) am Tank bleibt Pflicht.** Die Software ist die zweite Sicherung, nicht die
erste: Hängt der Pi oder fehlt der Strom, sind alle Ventile (NC) zu. Der Druckminderer ist die Grenze für alles,
was über CO₂ hineinkommt.

**Ein Druckminderer für alles:** Liegt der Vordruck über dem Grenzdruck des Gärtanks (2,0 bar), sperrt dessen
Logik das Nachdrücken (das Keg darf weiter). Zum Zwangskarbonisieren mit 2,4 bar heißt das: Der Gärtank wird in
der Zeit nicht nachgedrückt, Spunden und Grenzdruck arbeiten weiter.

## Regelung

Jede Sekunde, nach jedem Ventilpuls 3 s Pause zum Beruhigen:

| Lage | Handlung |
|---|---|
| Druck ≥ Grenzdruck (2,0 bar; All Rounder max. 2,4 bar) | Spundventil **dauerhaft offen** bis 0,1 bar darunter, Meldung |
| Druck > Soll + 0,05 | Spundventil 0,5 s |
| Druck < Soll − 0,10 und Nachdrücken = Ja | CO₂-Ventil 0,3 s – nur mit gültigem, passendem Vordruck |
| 6 Drückpulse ohne Anstieg | Nachdrücken gesperrt („Leck oder CO₂ leer“) bis Gärtank aus/ein |
| 10 Spundpulse ohne Abfall | Hinweis „verstopft? Kräusen im Gaspfosten?“, spundet weiter |
| Tankdruck unbekannt | Vorgabe: Spundventil pulst vorsorglich (Einstellung „Bei Fühlerausfall“), nie Drücken |
| Biertemperatur unbekannt | Heizung und Kühlung aus |

Solldruck in **bar**. Soll 0 heißt drucklos gären. Setzen lässt er sich auf drei Wegen:

| Weg | Eingabe | Wird gelesen als |
|---|---|---|
| Dashboard, Knopf „Set target pressure“ (Schieber 0–400, „kPa“) | 120 | **kPa**, umgerechnet auf 1,20 bar |
| Gärplan-Schritt „Pressure“, Hardware → Gärtank bearbeiten („TargetPressure“), REST | 1.2 | bar |
| `einrichten.py` | 1.0 | bar |

Regel: **Werte ab 5 sind kPa** aus dem Schieber (bei `PRESSURE_UNIT` = PSI: PSI), darunter bar. Die Logik rechnet
beim nächsten Durchlauf um, schreibt den Wert in bar zurück und meldet „Solldruck 120 kPa als 1.20 bar übernommen“.
Danach zeigen Dashboard (Widget „SollDruck“, Einheit bar) und Dialog den bar-Wert; der Dialog beschriftet ihn
trotzdem mit „kPa“, weil cbpi4gui die Einheit fest einbaut. Auflösung über den Schieber: 0,01 bar.

Der Bereich 1–4 auf dem Schieber ist mehrdeutig und wird als bar gelesen. Liegt der Solldruck dadurch am oder über dem
Grenzdruck, drückt die Logik nicht nach und meldet eine Störung; der Grenzdruck lässt ohnehin vorher ab. Wer 1–4 kPa
meint (0,01–0,04 bar), stellt 0 ein (drucklos).

**Kegs** (`KegDruckLogik`): Unter Soll − 0,05 bar drückt Ventil 3/4 einen Puls von 0,5 s, danach 3 s Pause. Es gibt
kein Ablassventil: Über dem Grenzdruck (3,0 bar) kommt nur eine Meldung. Bei Fühlerausfall bleibt das Ventil zu.
Die Sprungprüfung ist aus, weil ein volles Keg nur wenig Kopfraum hat. Im cbpi ist jedes Keg ein Gärtank ohne
Temperatur: „Pressure Sensor“ = Druck 3/4, „Valve“ = Ventil 3/4, Solldruck im Dashboard.
Pulsdauern und Bänder sind Startwerte aus der Simulation; am echten Tank einstellen (Abschnitt Probelauf).

## Einbau

```bash
cd ~ && git clone https://github.com/timmkueck/cbpi4-gaerdruck.git
~/cbpi4-venv/bin/pip install smbus2 ~/cbpi4-gaerdruck
sudo raspi-config nonint do_i2c 0          # I2C an (falls noch nicht)
i2cdetect -y 1                             # 48 muss erscheinen
sudo systemctl restart craftbeerpi
python3 ~/cbpi4-gaerdruck/einrichten.py \
    --temperatur "Biertemperatur" --kuehlung "Kühlschrank"
```

`cbpi4-brausicher` muss installiert sein (Ventile). `--temperatur`, `--kuehlung`, `--heizung` nehmen die Namen
vorhandener Fühler/Aktoren. Das Skript findet den Konfigurationsordner (`~/config` oder `~/cbpi4/config`) für die
Ventilsymbole selbst, sonst `--config <Ordner>`. Mehrfach aufrufen schadet nicht: Vorhandenes wird wiederverwendet,
nur Dashboard 2 wird neu geschrieben (`--dashboard N` für ein anderes).

**Platine mit Ventilausgängen (Stand Biersteuerung f5a2bcf):** Ventile 1–4 an J17 = GPIO 16, 17, **26, 13**
(vorher 23 und 25 für Ventil 3/4). Wer die Ventile mit einer älteren Fassung angelegt hat: Das Skript übernimmt
vorhandene Aktoren anhand des Namens und ändert ihren GPIO nicht. „CO₂ Keg 1“ und „CO₂ Keg 2“ dann unter
*Hardware → Aktor* auf 26 und 13 umstellen.

Danach in cbpi: **Dashboard → oben rechts „2“**. Zu sehen:

- Schema der Leitung mit Gaspfosten, Duotight-Kupplung und T-Stücken. Jeder Zweig zeigt Fluss, solange sein
  Ventil offen ist
- Biertemperatur, Tankdruck, CO₂-Vordruck, Solltemperatur und -druck
- die CO₂-Verteilung zu beiden Kegs mit deren Druck
- **alle vier Ventile** als Symbol (grau zu, grün offen) mit Knopf („Ablassen“, „Drücken“ für die Kegs); das
  CO₂-Ventil des Gärtanks bewusst ohne Knopf
- je Keg: Druck, Solldruck, Regelung ein/aus
- Kühlung/Heizung, Gärtank ein/aus, Verlauf Druck und Temperatur, Gärplan

Die Knöpfe wirken nur bei ausgeschalteter Regelung dauerhaft; läuft sie, schließt sie das Ventil im nächsten
Durchlauf.

## Ausprobieren ohne Hardware

```bash
python3 einrichten.py --simulation --temperatur "…"
```

Fühler und Ventile laufen dann in der Simulation (eigener 50-ms-Takt, damit kurze Pulse richtig eingehen): Der Tank
baut Gärdruck auf und reagiert auf beide Ventile, die Kegs verlieren langsam Druck (CO₂ löst sich), der Vordruck
steht auf 1,5 bar. Aktion „Simulation: Wert setzen“ am Fühler erzwingt einen Druck (z. B. 2,1 bar für den
Grenzdruck).

## Tests

```bash
pip install cbpi4 pytest
python -m pytest tests/ -q
```

20 Tests: ADS1115 (Konfigurationswort, Vorzeichen, Zeitüberschreitung), Umrechnung nach
`BrewPiLess-PCB/docs/drucksensoren.md`, Kabelbruch/Kurzschluss, Spunden/Nachdrücken, Grenzdruck mit Hysterese,
Fühlerausfall, Fühlervergleich, Vordruck, Wirkungsprüfung, Sprung je Puls (Gaspfosten verstopft), Keg (nur
Nachdrücken, Grenzdruck, Karbonisierdruck), Temperatur mit Kompressorschutz.

Am 25.10.2026 im echten CraftBeerPi 4.7.7 (Simulation, nur an 127.0.0.1): Nachdrücken von 0 auf 1,0 bar,
Abspunden bei 1,057 bar, Grenzdruck 2,1 bar → Spundventil sofort offen, Autostart nach Neustart, Dashboard mit
Ventilsymbolen und Gegenprobe Ventil 3 per Knopf, Spundzweig fließt bei offenem Spundventil. Kegs: Keg 1 auf
1,0 bar, Keg 2 von 0,73 in Pulsen auf 1,2 bar. Dabei aufgefallen und behoben: `SicherAktor.get_state()` lieferte
ein dict, die Oberfläche zeigte deshalb jeden SicherAktor als „an“.

**Nicht getestet mit echter Hardware:** ADS1115 am Pi, Drucksensoren, Magnetventile, Pulsdauern am echten Tank.

## Probelauf am Tank

1. Tank offen, Fühler drucklos: am Fühler Aktion **„Nullpunkt setzen“**.
2. Tank zu, Soll 1,0 bar, Regelung an: Er muss nachdrücken und bei 1,0–1,05 bar stehen bleiben. Wie viele
   Pulse braucht es? Zu viele → „Puls Drücken s“ erhöhen; Überschwingen → verkleinern.
3. Soll auf 0,5 bar: Er muss in Pulsen abspunden.
4. Soll 2,5 bar eingeben (über dem Grenzdruck): Bei 2,0 bar muss das Spundventil öffnen und offen bleiben.
5. CO₂-Flasche zu, Soll hoch: nach 6 Pulsen Meldung „Leck oder CO₂ leer“.
6. Stecker von Druck 1 ziehen: Meldung nach 10 s, Spundventil pulst, kein Drücken.
7. Duotight-Kupplung vom Gaspfosten ziehen, Soll 1,0 bar: Sperre „Leck oder CO₂ leer“ nach 6 Pulsen.
8. Je Keg: Soll 1,0 bar, Keg angeschlossen → hält den Druck; Kupplung ab → Sperre nach 6 Pulsen.

## Brau-Verbund

Gehört zur Brauerei von Timm: Brau-Pi mit HAT [Biersteuerung](https://github.com/timmkueck/Biersteuerung) und CraftBeerPi 4.7.7.
Gesamtbild: `docs/systeme.md` im Repo Biersteuerung.

| Repo | Inhalt |
|---|---|
| [cbpi4-brausicher](https://github.com/timmkueck/cbpi4-brausicher) | sichere Fühler, Aktoren (Heizschutz, Wächter), Kessellogik, Fortsetzen nach Neustart |
| [cbpi4-gaerdruck](https://github.com/timmkueck/cbpi4-gaerdruck) | Druckgärung im FermZilla: Drucksensor, Spunden/Nachdrücken, Kegs, Dashboard 2 |
| [cbpi4-ventil](https://github.com/timmkueck/cbpi4-ventil) | Brauwasser dosieren (Magnetventil + Durchflussmesser) |
| [cbpi4-simulation](https://github.com/timmkueck/cbpi4-simulation) | ganzer Brautag als Simulation, Störfälle, Prüfung der Schutzfunktionen |
| [cbpi4-bme280](https://github.com/timmkueck/cbpi4-bme280) | BME280-Sensor (meldet Fehler statt still leer) |
