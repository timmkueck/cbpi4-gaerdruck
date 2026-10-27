"""Hardwarenahe Teile und Regelentscheidungen ohne cbpi-Abhaengigkeit.

ADS1115 lesen, Spannung in Druck umrechnen, Druckregler (Spunden und
Nachdruecken mit zwei Magnetventilen), Temperaturregler mit Kompressorschutz.

Grundsatz: Ohne gueltigen Wert wird kein CO2 nachgedrueckt. Im Zweifel geht
der Druck nach unten, nie nach oben.
"""
import logging
import threading
import time
from dataclasses import dataclass

log = logging.getLogger("cbpi4_gaerdruck")

PSI = 0.0689476  # bar
KPA = 0.01  # bar

# Solldruck (target_pressure): Werte ab hier stammen aus dem Schieber von
# cbpi4gui (PRESSURE_UNIT kPa oder PSI), darunter sind es bar. Kein Gaertank
# und kein Keg wird mit 5 bar betrieben, 5 kPa (0,05 bar) stellt niemand ein.
SOLL_AB_SCHIEBER = 5.0

# Messbereich -> Enddruck in bar (Ausgang 0,5 V = 0, 4,5 V = Enddruck)
# Die beiden grossen Bereiche gehoeren zur CO2-Flasche vor dem Druckminderer
# (Druck 5 an J21, zweiter ADS1115). Dort stehen bei Raumtemperatur rund 50 bar
# an, nahe dem kritischen Punkt (31 C) bis etwa 74 bar; jeder kleinere Geber
# waere dauerhaft uebersteuert. Hinter dem Druckminderer bleibt es bei 0-80 psi.
MESSBEREICHE = {
    "0-80 psi": 80 * PSI,
    "0-100 psi": 100 * PSI,
    "0-60 psi": 60 * PSI,
    "0-30 psi": 30 * PSI,
    "0-5 bar": 5.0,
    "0-10 bar": 10.0,
    "0-1500 psi": 1500 * PSI,
    "0-100 bar": 100.0,
}

# Vorgabe fuer die CO2-Flasche; einrichten.py legt den Fuehler damit an.
FLASCHE_BEREICH = "0-100 bar"

# HAT Biersteuerung (Bogen Ventile_Sensoren): 10 k / 20 k vor dem ADS1115
TEILER_HAT = 2 / 3


class TechnikFehler(Exception):
    pass


def solldruck_bar(wert, einheit="kPa"):
    """target_pressure -> (bar, umgerechnet). Ab SOLL_AB_SCHIEBER gilt der Wert
    als kPa bzw. PSI aus dem Dialog "Set Target Pressure", sonst als bar.
    Leer gilt als 0 (drucklos), Unsinn loest ValueError aus."""
    if wert is None or wert == "":
        return 0.0, False
    try:
        zahl = float(str(wert).replace(",", "."))
    except ValueError:
        raise ValueError(f"Solldruck {wert!r} ist keine Zahl") from None
    if zahl < SOLL_AB_SCHIEBER:
        return max(0.0, zahl), False
    faktor = PSI if str(einheit).strip().upper() == "PSI" else KPA
    return round(zahl * faktor, 3), True


# --- ADS1115 ----------------------------------------------------------------

_i2c_lock = threading.Lock()  # eine Wandlung nach der anderen, alle Kanaele teilen den Baustein


def ads1115_config(kanal):
    """Konfigurationswort: Einzelwandlung, Eingang AINx gegen GND, +-4,096 V, 128 SPS."""
    if kanal not in (0, 1, 2, 3):
        raise TechnikFehler(f"Kanal {kanal} gibt es nicht (0-3)")
    return 0x8000 | ((4 + kanal) << 12) | (0b001 << 9) | (1 << 8) | (0b100 << 5) | 0b11


def ads1115_lesen(kanal, adresse=0x48, bus_nr=1, smbus=None):
    """Spannung am Eingang in V. Wirft TechnikFehler, wenn der Baustein nicht antwortet."""
    if smbus is None:
        try:
            from smbus2 import SMBus as smbus
        except ImportError as e:
            raise TechnikFehler("Python-Modul smbus2 fehlt im Python von CraftBeerPi (pip install smbus2)") from e
    config = ads1115_config(kanal)
    with _i2c_lock:
        try:
            with smbus(bus_nr) as bus:
                bus.write_i2c_block_data(adresse, 1, [config >> 8, config & 0xFF])
                for _ in range(25):
                    time.sleep(0.002)
                    if bus.read_i2c_block_data(adresse, 1, 2)[0] & 0x80:
                        break
                else:
                    raise TechnikFehler(f"ADS1115 0x{adresse:02x}: Wandlung endet nicht")
                hoch, tief = bus.read_i2c_block_data(adresse, 0, 2)
        except OSError as e:
            raise TechnikFehler(f"ADS1115 0x{adresse:02x} an I2C-Bus {bus_nr}: {e}") from e
    roh = (hoch << 8) | tief
    if roh & 0x8000:
        roh -= 1 << 16
    return roh * 4.096 / 32768


def druck_auswerten(u_ads, enddruck_bar, teiler=TEILER_HAT, nullpunkt_bar=0.0):
    """(Druck in bar, Fehlertext). Druck ist None, wenn die Spannung keinen Sinn ergibt.

    Unter 0,25 V am Sensor: Kabelbruch oder Sensor fehlt (ein gesunder Sensor
    liefert drucklos 0,5 V). Ueber 4,75 V: Kurzschluss nach 5 V oder defekt."""
    if u_ads is None:
        return None, "kein Messwert"
    u = u_ads / teiler
    if u < 0.25:
        return None, f"{u:.2f} V am Sensor: Kabelbruch oder kein Sensor"
    if u > 4.75:
        return None, f"{u:.2f} V am Sensor: Kurzschluss nach 5 V oder Sensor defekt"
    return round((u - 0.5) / 4.0 * enddruck_bar - nullpunkt_bar, 3), ""


def median(werte):
    w = sorted(werte)
    return w[len(w) // 2]


# --- Druckregler --------------------------------------------------------------

ZU, SPUNDEN, DRUECKEN, NOTABLASS = None, "spunden", "druecken", "notablass"


@dataclass
class Entscheidung:
    aktion: str = None  # ZU, SPUNDEN (Puls), DRUECKEN (Puls), NOTABLASS (Spundventil dauernd auf)
    stoerung: str = ""  # Fehler, der das Eingreifen verlangt
    hinweis: str = ""  # Auffaelligkeit, Regelung laeuft weiter
    druck: float = None  # der Druck, nach dem entschieden wurde


class Druckregler:
    """Entscheidet einmal je Durchlauf, ob gespundet, nachgedrueckt oder nichts getan wird.

    Gemessen werden Tankdruck, optional ein zweiter Tankfuehler zum Vergleich
    und der CO2-Vordruck hinter dem Druckminderer. Nachgedrueckt wird nur, wenn
    alle vorhandenen Fuehler gueltig sind und zusammenpassen.

    Der FermZilla hat einen Gasanschluss: Tankfuehler, Spund- und CO2-Ventil
    haengen ueber Duotight-T an derselben Leitung. Der Fuehler sieht den Tank
    also nur, solange der Gaspfosten frei ist. Springt der Druck je Puls um mehr
    als `sprung_max`, antwortet nur das kleine Leitungsvolumen -> Meldung."""

    def __init__(self, grenzdruck=2.0, ablassen_ueber=0.05, druecken_unter=0.10, nachdruecken=True,
                 max_alter_s=10.0, vergleich_max=0.15, vordruck_reserve=0.10, bei_ausfall=SPUNDEN,
                 wirkung_bar=0.01, spund_pulse=10, drueck_pulse=6, sprung_max=0.3):
        self.grenzdruck = grenzdruck
        self.ablassen_ueber = ablassen_ueber
        self.druecken_unter = druecken_unter
        self.nachdruecken = nachdruecken
        self.max_alter_s = max_alter_s
        self.vergleich_max = vergleich_max
        self.vordruck_reserve = vordruck_reserve
        self.bei_ausfall = bei_ausfall
        self.wirkung_bar = wirkung_bar
        self.spund_pulse = spund_pulse
        self.drueck_pulse = drueck_pulse
        self.sprung_max = sprung_max
        self._puls_vorher = None  # Druck vor dem letzten Puls
        self.druecken_gesperrt = ""  # verriegelt bis zum Neustart der Regelung
        self._notablass = False
        self._folge = None  # (aktion, anzahl, druck beim ersten Puls)

    def _gueltig(self, wert, alter):
        return isinstance(wert, (int, float)) and alter is not None and alter <= self.max_alter_s

    def _wirkung(self, aktion, p):
        """Zaehlt Pulse in eine Richtung und meldet, wenn der Druck nicht folgt."""
        art, anzahl, start = self._folge if self._folge and self._folge[0] == aktion else (aktion, 0, p)
        if (aktion == SPUNDEN and p <= start - self.wirkung_bar) or \
                (aktion == DRUECKEN and p >= start + self.wirkung_bar):
            anzahl, start = 0, p
        anzahl += 1
        self._folge = (aktion, anzahl, start)
        return anzahl

    def entscheiden(self, soll, p, alter, p2=None, alter2=None, vordruck=None, vordruck_alter=None,
                    vordruck_da=False):
        e = self._entscheiden(soll, p, alter, p2, alter2, vordruck, vordruck_alter, vordruck_da)
        vorher, self._puls_vorher = self._puls_vorher, None
        if self.sprung_max and vorher is not None and e.druck is not None and abs(e.druck - vorher) > self.sprung_max:
            text = (f"Druck springt um {abs(e.druck - vorher):.2f} bar je Puls: Gasweg zum Tank verstopft "
                    "(Kräusen im Gaspfosten?) oder Duotight-Kupplung nicht aufgesteckt")
            e.stoerung = f"{e.stoerung}; {text}" if e.stoerung else text
        if e.aktion in (SPUNDEN, DRUECKEN) and e.druck is not None:
            self._puls_vorher = e.druck
        return e

    def _entscheiden(self, soll, p, alter, p2, alter2, vordruck, vordruck_alter, vordruck_da):
        soll = max(0.0, float(soll or 0))
        werte = [w for w, a in ((p, alter), (p2, alter2)) if self._gueltig(w, a)]
        hinweis = ""
        stoerung = ""
        abweichung = False
        if not werte:
            self._folge = None
            self._notablass = False
            stoerung = "Tankdruck unbekannt (Fühler liefert nicht)"
            if self.bei_ausfall == SPUNDEN:
                return Entscheidung(SPUNDEN, stoerung + ", Spundventil pulst vorsorglich")
            return Entscheidung(ZU, stoerung + ", beide Ventile zu")
        if len(werte) == 2 and abs(werte[0] - werte[1]) > self.vergleich_max:
            abweichung = True
            stoerung = (f"Tankfühler weichen ab ({werte[0]:.2f} / {werte[1]:.2f} bar), "
                        "geregelt wird nach dem höheren, Nachdrücken gesperrt")
        druck = max(werte)

        # Grenzdruck: Spundventil bleibt offen, bis 0,1 bar darunter
        if druck >= self.grenzdruck or (self._notablass and druck > self.grenzdruck - 0.1):
            self._notablass = True
            self._folge = None
            return Entscheidung(NOTABLASS, f"Grenzdruck {self.grenzdruck:g} bar erreicht ({druck:.2f} bar), "
                                "Spundventil offen", druck=druck)
        self._notablass = False

        if druck > soll + self.ablassen_ueber:
            if self._wirkung(SPUNDEN, druck) > self.spund_pulse:
                hinweis = (f"Spunden ohne Wirkung seit {self.spund_pulse} Pulsen "
                           "(Leitung verstopft, Kräusen im Gaspfosten?)")
            return Entscheidung(SPUNDEN, stoerung, hinweis, druck)

        if druck < soll - self.druecken_unter and self.nachdruecken:
            if abweichung:
                pass
            elif soll >= self.grenzdruck:
                stoerung = (f"Solldruck {soll:g} bar liegt nicht unter dem Grenzdruck {self.grenzdruck:g} bar: "
                            "Nachdrücken gesperrt")
            elif self.druecken_gesperrt:
                hinweis = self.druecken_gesperrt
            elif vordruck_da and not self._gueltig(vordruck, vordruck_alter):
                hinweis = "CO₂-Vordruck unbekannt, Nachdrücken ausgesetzt"
            elif vordruck_da and vordruck >= self.grenzdruck:
                stoerung = (f"CO₂-Vordruck {vordruck:.2f} bar liegt über dem Grenzdruck {self.grenzdruck:g} bar: "
                            "Druckminderer herunterdrehen. Nachdrücken gesperrt")
            elif vordruck_da and vordruck < druck + self.vordruck_reserve:
                hinweis = f"CO₂-Vordruck {vordruck:.2f} bar zu niedrig (Flasche leer oder zu?)"
            else:
                if self._wirkung(DRUECKEN, druck) > self.drueck_pulse:
                    self.druecken_gesperrt = (f"Nachdrücken ohne Wirkung nach {self.drueck_pulse} Pulsen: "
                                              "Leck oder CO₂ leer. Gesperrt bis Gärtank aus/ein")
                    self._folge = None
                    return Entscheidung(ZU, self.druecken_gesperrt, druck=druck)
                return Entscheidung(DRUECKEN, stoerung, druck=druck)
            self._folge = None
            return Entscheidung(ZU, stoerung, hinweis, druck)

        self._folge = None
        return Entscheidung(ZU, stoerung, druck=druck)


# --- Temperaturregler -----------------------------------------------------------

class Temperaturregler:
    """Zweipunktregler fuer Heizung und Kuehlung, nie beides zugleich.

    Kompressorschutz: Die Kuehlung (Kuehlschrank) startet fruehestens
    `kuehl_pause_s` nach dem letzten Ausschalten."""

    def __init__(self, heiz_an=0.3, heiz_aus=0.0, kuehl_an=0.3, kuehl_aus=0.0, kuehl_pause_s=300,
                 uhr=time.monotonic):
        self.heiz_an, self.heiz_aus = heiz_an, heiz_aus
        self.kuehl_an, self.kuehl_aus = kuehl_an, kuehl_aus
        self.kuehl_pause_s = kuehl_pause_s
        self.uhr = uhr
        self.heizt = False
        self.kuehlt = False
        self._kuehl_aus_seit = None

    def entscheiden(self, ist, soll):
        """(heizen, kuehlen, hinweis). Ohne Messwert ist alles aus."""
        if not isinstance(ist, (int, float)) or not isinstance(soll, (int, float)):
            self._setzen(False, False)
            return False, False, "Biertemperatur unbekannt, Heizung und Kühlung aus"
        heizen, kuehlen = self.heizt, self.kuehlt
        if ist <= soll - self.heiz_an:
            heizen = True
        elif ist >= soll - self.heiz_aus:
            heizen = False
        if ist >= soll + self.kuehl_an:
            kuehlen = True
        elif ist <= soll + self.kuehl_aus:
            kuehlen = False
        if heizen and kuehlen:  # nur bei unsinnigen Offsets denkbar
            heizen = kuehlen = False
        hinweis = ""
        if kuehlen and not self.kuehlt and self._kuehl_aus_seit is not None:
            rest = self.kuehl_pause_s - (self.uhr() - self._kuehl_aus_seit)
            if rest > 0:
                kuehlen = False
                hinweis = f"Kompressorschutz: Kühlung startet in {rest:.0f} s"
        self._setzen(heizen, kuehlen)
        return heizen, kuehlen, hinweis

    def _setzen(self, heizen, kuehlen):
        if self.kuehlt and not kuehlen:
            self._kuehl_aus_seit = self.uhr()
        self.heizt, self.kuehlt = heizen, kuehlen
