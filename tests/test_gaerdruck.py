"""Tests ohne Hardware:  pip install cbpi4 pytest && python -m pytest tests/ -q"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cbpi4_gaerdruck import technik as T  # noqa: E402

FRISCH = 1.0


class Uhr:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# --- ADS1115 und Umrechnung -------------------------------------------------------

class FalscherBus:
    """Nachbildung von smbus2.SMBus: Register 1 = Konfiguration, 0 = Ergebnis."""

    def __init__(self, roh, fertig=True):
        self.roh, self.fertig, self.geschrieben = roh, fertig, []

    def __call__(self, nr):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def write_i2c_block_data(self, adr, reg, daten):
        self.geschrieben.append((adr, reg, daten))

    def read_i2c_block_data(self, adr, reg, n):
        if reg == 1:
            return [0x80 if self.fertig else 0x00, 0]
        return [(self.roh >> 8) & 0xFF, self.roh & 0xFF]


def test_ads1115_config():
    assert T.ads1115_config(0) == 0xC383  # AIN0, +-4,096 V, einzeln, 128 SPS
    assert T.ads1115_config(3) == 0xF383
    with pytest.raises(T.TechnikFehler):
        T.ads1115_config(4)


def test_ads1115_lesen():
    bus = FalscherBus(16000)
    assert T.ads1115_lesen(1, smbus=bus) == pytest.approx(2.0, abs=0.001)
    assert bus.geschrieben[0] == (0x48, 1, [0xD3, 0x83])
    assert T.ads1115_lesen(0, smbus=FalscherBus(0xFFF0)) < 0  # negativ (Vorzeichen)
    with pytest.raises(T.TechnikFehler):
        T.ads1115_lesen(0, smbus=FalscherBus(0, fertig=False))


def test_druck_auswerten_wie_drucksensoren_md():
    end = T.MESSBEREICHE["0-80 psi"]
    # p[psi] = (U_ADS - 0,333 V) x 30  ->  1,333 V = 30 psi
    p, f = T.druck_auswerten(4 / 3, end)
    assert f == "" and p == pytest.approx(30 * T.PSI, abs=0.003)
    assert T.druck_auswerten(0.5 * 2 / 3, end)[0] == pytest.approx(0, abs=0.001)
    assert T.druck_auswerten(4 / 3, end, nullpunkt_bar=0.1)[0] == pytest.approx(30 * T.PSI - 0.1, abs=0.003)


def test_druck_auswerten_kabelbruch_und_kurzschluss():
    end = T.MESSBEREICHE["0-80 psi"]
    p, f = T.druck_auswerten(0.01, end)
    assert p is None and "Kabelbruch" in f
    p, f = T.druck_auswerten(3.3, end)  # 5 V hinter dem Teiler
    assert p is None and "Kurzschluss" in f
    assert T.druck_auswerten(None, end)[0] is None


# --- Druckregler -------------------------------------------------------------------

def test_spunden_und_nachdruecken():
    r = T.Druckregler()
    assert r.entscheiden(1.0, 1.10, FRISCH).aktion == T.SPUNDEN
    assert r.entscheiden(1.0, 1.03, FRISCH).aktion is None  # im Band
    assert r.entscheiden(1.0, 0.85, FRISCH).aktion == T.DRUECKEN
    assert T.Druckregler(nachdruecken=False).entscheiden(1.0, 0.5, FRISCH).aktion is None


def test_soll_null_heisst_drucklos():
    r = T.Druckregler()
    assert r.entscheiden(0, 0.2, FRISCH).aktion == T.SPUNDEN
    assert r.entscheiden(None, 0.0, FRISCH).aktion is None


def test_grenzdruck_haelt_spundventil_offen_mit_hysterese():
    r = T.Druckregler(grenzdruck=2.0)
    e = r.entscheiden(1.0, 2.05, FRISCH)
    assert e.aktion == T.NOTABLASS and "Grenzdruck" in e.stoerung
    assert r.entscheiden(1.0, 1.95, FRISCH).aktion == T.NOTABLASS  # erst 0,1 bar darunter zu
    assert r.entscheiden(1.0, 1.85, FRISCH).aktion == T.SPUNDEN
    # Auch bei Soll ueber dem Grenzdruck wird nie gedrueckt, sondern abgelassen
    assert r.entscheiden(3.0, 2.1, FRISCH).aktion == T.NOTABLASS



def test_soll_ueber_grenzdruck_sperrt_nachdruecken():
    # Schieberwert 1-4 (kPa gemeint) wird als bar gelesen: darf nie hochdruecken
    r = T.Druckregler(grenzdruck=2.0)
    e = r.entscheiden(3.0, 1.0, FRISCH)
    assert e.aktion is None and "Grenzdruck" in e.stoerung
    assert r.entscheiden(1.9, 1.0, FRISCH).aktion == T.DRUECKEN
    assert keg_regler().entscheiden(4.0, 1.0, FRISCH).aktion is None


# --- Solldruck aus dem kPa/PSI-Schieber von cbpi4gui ------------------------------

@pytest.mark.parametrize("wert, einheit, erwartet", [
    (1.2, "kPa", (1.2, False)),      # Gaerplan, REST, Hardware-Formular: bar
    ("1,5", "kPa", (1.5, False)),
    ("2.4", "kPa", (2.4, False)),
    (0, "kPa", (0.0, False)),
    (None, "kPa", (0.0, False)),
    ("", "kPa", (0.0, False)),
    (-1, "kPa", (0.0, False)),
    (4.9, "kPa", (4.9, False)),      # unter der Schwelle: bar (sperrt dann ueber den Grenzdruck)
    (5, "kPa", (0.05, True)),
    (120, "kPa", (1.2, True)),       # Schieber "120 kPa"
    (250, "kPa", (2.5, True)),
    (400, "kPa", (4.0, True)),
    (17.5, "PSI", (1.207, True)),
    (30, "psi", (2.068, True)),
    (120, None, (1.2, True)),
])
def test_solldruck_bar(wert, einheit, erwartet):
    assert T.solldruck_bar(wert, einheit) == erwartet


def test_solldruck_unsinn():
    with pytest.raises(ValueError, match="keine Zahl"):
        T.solldruck_bar("viel")


def test_logik_schreibt_schieberwert_als_bar_zurueck():
    pytest.importorskip("cbpi")
    import asyncio
    from types import SimpleNamespace
    from cbpi4_gaerdruck import GaerDruckLogik, KegDruckLogik

    class Fermenter:
        def __init__(self):
            self.gesetzt = []

        async def set_target_pressure(self, id, wert):
            self.gesetzt.append((id, wert))

    for klasse in (GaerDruckLogik, KegDruckLogik):
        meldungen = []
        cbpi = SimpleNamespace(config=SimpleNamespace(get=lambda name, default=None: "kPa"),
                               fermenter=Fermenter(), notify=lambda *a: meldungen.append(a))
        logik = object.__new__(klasse)
        logik.cbpi = cbpi
        f = SimpleNamespace(id="t1", name="FermZilla", target_pressure=120)
        assert asyncio.run(logik._solldruck(f)) == 1.2
        assert cbpi.fermenter.gesetzt == [("t1", 1.2)] and "1.20 bar" in meldungen[0][1]
        f.target_pressure = 1.2  # Gaerplan oder schon umgerechnet: nichts schreiben
        assert asyncio.run(logik._solldruck(f)) == 1.2
        assert len(cbpi.fermenter.gesetzt) == 1

def test_fuehlerausfall():
    r = T.Druckregler()
    e = r.entscheiden(1.0, None, None)
    assert e.aktion == T.SPUNDEN and "unbekannt" in e.stoerung
    assert r.entscheiden(1.0, 0.5, 30.0).aktion == T.SPUNDEN  # zu alt zaehlt als Ausfall
    r2 = T.Druckregler(bei_ausfall=None)
    assert r2.entscheiden(1.0, None, None).aktion is None


def test_zweiter_tankfuehler():
    r = T.Druckregler()
    # passt: Mittel egal, groesserer zaehlt
    assert r.entscheiden(1.0, 0.80, FRISCH, 0.82, FRISCH).aktion == T.DRUECKEN
    # weicht ab: nach dem hoeheren, kein Nachdruecken
    e = r.entscheiden(1.0, 0.5, FRISCH, 0.8, FRISCH)
    assert e.aktion is None and "weichen ab" in e.stoerung
    e = r.entscheiden(1.0, 0.9, FRISCH, 1.3, FRISCH)
    assert e.aktion == T.SPUNDEN
    # einer faellt aus: der andere reicht
    assert r.entscheiden(1.0, None, None, 1.2, FRISCH).aktion == T.SPUNDEN


def test_vordruck():
    r = T.Druckregler(grenzdruck=2.0)
    kw = dict(vordruck_da=True)
    assert r.entscheiden(1.0, 0.8, FRISCH, vordruck=1.5, vordruck_alter=FRISCH, **kw).aktion == T.DRUECKEN
    e = r.entscheiden(1.0, 0.8, FRISCH, vordruck=0.85, vordruck_alter=FRISCH, **kw)
    assert e.aktion is None and "zu niedrig" in e.hinweis
    e = r.entscheiden(1.0, 0.8, FRISCH, vordruck=2.5, vordruck_alter=FRISCH, **kw)
    assert e.aktion is None and "Druckminderer" in e.stoerung
    e = r.entscheiden(1.0, 0.8, FRISCH, vordruck=None, vordruck_alter=None, **kw)
    assert e.aktion is None and "unbekannt" in e.hinweis
    # ohne Vordruckfuehler wird gedrueckt
    assert r.entscheiden(1.0, 0.8, FRISCH).aktion == T.DRUECKEN


def test_druecken_ohne_wirkung_sperrt():
    r = T.Druckregler(drueck_pulse=3)
    for _ in range(3):
        assert r.entscheiden(1.0, 0.5, FRISCH).aktion == T.DRUECKEN
    e = r.entscheiden(1.0, 0.5, FRISCH)
    assert e.aktion is None and "Leck" in e.stoerung
    e = r.entscheiden(1.0, 0.5, FRISCH)
    assert e.aktion is None and "Gesperrt" in e.hinweis
    # Spunden bleibt trotz Sperre moeglich
    assert r.entscheiden(1.0, 1.5, FRISCH).aktion == T.SPUNDEN


def test_druecken_mit_wirkung_sperrt_nicht():
    r = T.Druckregler(drueck_pulse=3)
    p = 0.3
    for _ in range(20):
        assert r.entscheiden(1.0, p, FRISCH).aktion == T.DRUECKEN
        p += 0.02
        if p > 0.85:
            break
    assert not r.druecken_gesperrt


def test_spunden_ohne_wirkung_meldet_aber_spundet_weiter():
    r = T.Druckregler(spund_pulse=3)
    for _ in range(3):
        e = r.entscheiden(1.0, 1.5, FRISCH)
        assert e.aktion == T.SPUNDEN and not e.hinweis
    e = r.entscheiden(1.0, 1.5, FRISCH)
    assert e.aktion == T.SPUNDEN and "verstopft" in e.hinweis


def test_sprung_je_puls_heisst_gasweg_verstopft():
    r = T.Druckregler()
    assert r.entscheiden(1.0, 0.5, FRISCH).aktion == T.DRUECKEN
    e = r.entscheiden(1.0, 0.53, FRISCH)  # Tank: Hundertstel je Puls
    assert "verstopft" not in e.stoerung
    e = r.entscheiden(1.0, 1.4, FRISCH)  # nur die Leitung hat sich gefuellt
    assert "verstopft" in e.stoerung and e.aktion == T.SPUNDEN
    e = r.entscheiden(1.0, 0.2, FRISCH)
    assert "verstopft" in e.stoerung
    # ohne vorherigen Puls keine Sprungpruefung
    r2 = T.Druckregler()
    assert "verstopft" not in r2.entscheiden(1.0, 1.02, FRISCH).stoerung
    assert "verstopft" not in r2.entscheiden(1.0, 0.5, FRISCH).stoerung


def keg_regler():
    # wie KegDruckLogik: kein Ablassventil, keine Sprungpruefung, bei Ausfall zu
    return T.Druckregler(grenzdruck=3.0, ablassen_ueber=99.0, druecken_unter=0.05, bei_ausfall=None,
                         sprung_max=None)


def test_keg_drueckt_nur_nach():
    r = keg_regler()
    assert r.entscheiden(1.0, 0.9, FRISCH).aktion == T.DRUECKEN
    assert r.entscheiden(1.0, 2.5, FRISCH).aktion is None  # zu hoch: nichts zu tun, kein Ventil
    assert r.entscheiden(1.0, None, None).aktion is None  # Fuehlerausfall: zu


def test_keg_ueber_grenzdruck_und_grosse_spruenge():
    r = keg_regler()
    assert r.entscheiden(1.0, 3.1, FRISCH).aktion == T.NOTABLASS  # Logik meldet nur, ohne Ventil
    r = keg_regler()
    assert r.entscheiden(2.4, 0.5, FRISCH).aktion == T.DRUECKEN
    e = r.entscheiden(2.4, 1.2, FRISCH)  # volles Keg: grosser Sprung ist normal
    assert e.aktion == T.DRUECKEN and not e.stoerung


def test_keg_vordruck_zum_karbonisieren():
    r = keg_regler()
    kw = dict(vordruck_da=True, vordruck_alter=FRISCH)
    assert r.entscheiden(2.4, 2.0, FRISCH, vordruck=2.6, **kw).aktion == T.DRUECKEN
    e = r.entscheiden(2.4, 2.0, FRISCH, vordruck=2.05, **kw)
    assert e.aktion is None and "zu niedrig" in e.hinweis


# --- Temperaturregler -------------------------------------------------------------

def test_temperatur_hysterese():
    r = T.Temperaturregler(heiz_an=0.3, kuehl_an=0.3, kuehl_pause_s=0)
    assert r.entscheiden(18.5, 18.0)[:2] == (False, True)
    assert r.entscheiden(18.1, 18.0)[:2] == (False, True)  # kuehlt bis Soll
    assert r.entscheiden(18.0, 18.0)[:2] == (False, False)
    assert r.entscheiden(17.6, 18.0)[:2] == (True, False)
    assert r.entscheiden(17.9, 18.0)[:2] == (True, False)
    assert r.entscheiden(18.0, 18.0)[:2] == (False, False)


def test_temperatur_ohne_wert_alles_aus():
    r = T.Temperaturregler()
    r.entscheiden(25.0, 18.0)
    heizen, kuehlen, hinweis = r.entscheiden(None, 18.0)
    assert (heizen, kuehlen) == (False, False) and "unbekannt" in hinweis


def test_kompressorschutz():
    uhr = Uhr()
    r = T.Temperaturregler(kuehl_pause_s=300, uhr=uhr)
    assert r.entscheiden(19.0, 18.0)[1] is True
    assert r.entscheiden(17.9, 18.0)[1] is False  # aus
    uhr.t += 60
    heizen, kuehlen, hinweis = r.entscheiden(19.0, 18.0)
    assert kuehlen is False and "Kompressorschutz" in hinweis
    uhr.t += 241
    assert r.entscheiden(19.0, 18.0)[1] is True


# --- CO2-Flasche: Hochdruckgeber vor dem Druckminderer ----------------------------

def lade_einrichten():
    """einrichten.py laedt nur die Standardbibliothek und laeuft ohne cbpi."""
    import importlib.util
    pfad = Path(__file__).resolve().parents[1] / "einrichten.py"
    spec = importlib.util.spec_from_file_location("gaerdruck_einrichten", pfad)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def test_messbereich_fuer_die_co2_flasche():
    # In der Flasche stehen bei Raumtemperatur rund 50 bar, nahe dem kritischen
    # Punkt (31 C) bis etwa 74 bar. Ein Bereich muss das abdecken.
    assert max(T.MESSBEREICHE.values()) >= 74
    assert T.FLASCHE_BEREICH in T.MESSBEREICHE
    assert T.MESSBEREICHE[T.FLASCHE_BEREICH] >= 74


def test_flaschendruck_wird_richtig_umgerechnet():
    end = T.MESSBEREICHE["0-100 bar"]
    # 50 bar = 0,5 V + 50/100 x 4 V = 2,5 V am Sensor -> 1,667 V am ADS1115
    p, f = T.druck_auswerten(2.5 * T.TEILER_HAT, end)
    assert f == "" and p == pytest.approx(50.0, abs=0.05)
    # Volle Flasche am oberen Ende bleibt im gueltigen Spannungsfenster
    p, f = T.druck_auswerten(4.5 * T.TEILER_HAT, end)
    assert f == "" and p == pytest.approx(100.0, abs=0.05)
    # Leere Flasche (drucklos) ist kein Kabelbruch
    assert T.druck_auswerten(0.5 * T.TEILER_HAT, end) == (0.0, "")


def test_einrichten_legt_den_flaschenfuehler_an():
    e = lade_einrichten()
    kanal, adresse, bereich = e.SENSOREN[e.FLASCHE]
    assert (kanal, adresse) == ("AIN0 (Druck 1)", "0x49")  # = Druck 5 an J21
    assert bereich == T.FLASCHE_BEREICH


def test_einrichten_nutzt_nur_bekannte_messbereiche_und_kanaele():
    e = lade_einrichten()
    for name, (kanal, adresse, bereich) in e.SENSOREN.items():
        assert bereich in T.MESSBEREICHE, f"{name}: Messbereich {bereich} gibt es nicht"
        assert adresse in ("0x48", "0x49"), f"{name}: {adresse} ist kein ADS1115 des HAT"
        assert kanal.startswith("AIN") and kanal[3] in "0123", f"{name}: Kanal {kanal}"
    # Je Adresse jeder Kanal nur einmal
    belegt = [(a, k) for k, a, _ in e.SENSOREN.values()]
    assert len(set(belegt)) == len(belegt)
    # Die vier Fuehler am ersten ADS1115 belegen AIN0-3 vollstaendig
    erster = sorted(k for k, a, _ in e.SENSOREN.values() if a == "0x48")
    assert erster == ["AIN0 (Druck 1)", "AIN1 (Druck 2)", "AIN2 (Druck 3)", "AIN3 (Druck 4)"]


def test_dashboard_zeigt_den_flaschendruck():
    e = lade_einrichten()
    ids = dict(tank="t", temp="s1", druck="s2", vordruck="s3", flasche="s4", spund="a1",
               co2="a2", kuehlung="a3", heizung="", ventile={"Spundventil": "a1"}, kegs=[])
    d = e.dashboard(ids)
    fuehler = [x for x in d["elements"] if x["type"] == "Sensor" and x["props"].get("sensor") == "s4"]
    assert len(fuehler) == 1 and fuehler[0]["props"]["unit"] == " bar"
    assert any(p["id"].endswith("flaschenfuehler") for p in d["pathes"])
    # Ohne Flaschenfuehler faellt nur das Feld weg, das Dashboard bleibt heil
    ohne = e.dashboard({**ids, "flasche": ""})
    assert not [x for x in ohne["elements"] if x["props"].get("sensor") == "s4"]
