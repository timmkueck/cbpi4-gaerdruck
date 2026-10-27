"""CraftBeerPi4-Plugin: Druckgaerung im FermZilla und zwei Kegs mit CO2 aus einer Flasche.

DruckSensor    Drucktransmitter 0,5-4,5 V am ADS1115 (HAT Biersteuerung,
               Buchse J18 "Druck 1-4"), Wert in bar, nie None, kennt sein Alter.
GaerDruckLogik Gaertanklogik: Temperatur (Heizung/Kuehlung mit
               Kompressorschutz) und Druck (Spundventil = Ventil des
               Gaertanks, Nachdruecken ueber ein zweites Ventil mit CO2),
               Grenzdruck, Wirkungspruefung.
KegDruckLogik  Keg auf Solldruck halten: nur Nachdruecken ueber ein
               CO2-Ventil (HAT Ventil 3/4), kein Ablassventil.

Die Ventile selbst sind SicherAktor aus cbpi4-brausicher (lgpio, Waechter).

Solldruck (target_pressure) in bar. Werte ab 5 kommen aus dem Schieber
"Set Target Pressure" von cbpi4gui (kPa bzw. PSI nach PRESSURE_UNIT): Die
Logik rechnet sie um und schreibt bar zurueck (120 kPa -> 1,2 bar).
"""
import asyncio
import logging
import re
import time

from cbpi.api import (CBPiExtension, CBPiFermenterLogic, CBPiSensor, Property, action, on_startup,
                      parameters)
from cbpi.api.dataclasses import NotificationType

from . import technik
from .technik import (DRUECKEN, MESSBEREICHE, NOTABLASS, SPUNDEN, Druckregler,
                      Temperaturregler, TechnikFehler, solldruck_bar)

log = logging.getLogger("cbpi4_gaerdruck")

KANAELE = ["AIN0 (Druck 1)", "AIN1 (Druck 2)", "AIN2 (Druck 3)", "AIN3 (Druck 4)"]
ADRESSEN = ["0x48", "0x49", "0x4a", "0x4b"]


def _zahl(props, name, vorgabe):
    try:
        wert = props.get(name, vorgabe)
        return float(vorgabe if wert in (None, "") else wert)
    except (TypeError, ValueError):
        return float(vorgabe)


def _instanz(controller, item_id):
    item = controller.find_by_id(item_id) if item_id else None
    return getattr(item, "instance", None)


# --- Drucksensor ------------------------------------------------------------------

@parameters([
    Property.Select("Kanal", options=KANAELE,
                    description="Eingang am ADS1115. HAT: an 0x48 ist AIN0-3 = Druck 1-4 (J18), "
                                "an 0x49 dieselben Eingänge = Druck 5-8 (J21)"),
    Property.Select("Messbereich", options=list(MESSBEREICHE),
                    description="Laut Sensor (BrewPiLess-PCB/docs/drucksensoren.md: 0-80 psi). "
                                "CO₂-Flasche vor dem Druckminderer: Hochdruckgeber, 0-100 bar"),
    Property.Select("Adresse", options=ADRESSEN,
                    description="I2C-Adresse des ADS1115 (HAT: 0x48 = Druck 1-4, 0x49 = Druck 5-8)"),
    Property.Number("Teiler", configurable=True, default_value=0.6667,
                    description="Spannungsteiler vor dem ADS1115. HAT Biersteuerung: 0.6667, ohne Teiler: 1"),
    Property.Number("Nullpunkt bar", configurable=True, default_value=0,
                    description="Wird abgezogen. Aktion „Nullpunkt setzen“ bei offenem Tank"),
    Property.Number("Max Alter s", configurable=True, default_value=10,
                    description="Ohne gültigen Wert so lange -> Meldung, Gärtanklogik drückt nicht nach"),
    Property.Select("Simulation", options=["Nein", "Ja"], description="Ja = ohne Hardware, Tank wird nachgebildet"),
])
class DruckSensor(CBPiSensor):

    def __init__(self, cbpi, id, props):
        super().__init__(cbpi, id, props)
        # Immer eine Zahl: cbpi-Teile vergleichen Werte ungeprueft. Gueltigkeit steckt in alter().
        self.value = 0.0
        self.zeit = None
        self.fehler = ""
        self.gemeldet = False
        self._sim = None
        self._sim_task = None

    def alter(self):
        """Sekunden seit dem letzten gueltigen Wert, None ohne Wert."""
        return None if self.zeit is None else time.monotonic() - self.zeit

    def _einstellungen(self):
        p = self.props
        kanal = str(p.get("Kanal", KANAELE[0]))
        return dict(
            kanal=int(kanal[3]) if kanal.startswith("AIN") else int(float(kanal)),
            adresse=int(str(p.get("Adresse", "0x48")), 16),
            enddruck=MESSBEREICHE.get(p.get("Messbereich"), MESSBEREICHE["0-80 psi"]),
            teiler=_zahl(p, "Teiler", technik.TEILER_HAT) or technik.TEILER_HAT,
            nullpunkt=_zahl(p, "Nullpunkt bar", 0),
        )

    def _messen(self, e):
        """Median aus drei Wandlungen; laeuft im Thread."""
        u = technik.median([technik.ads1115_lesen(e["kanal"], e["adresse"]) for _ in range(3)])
        return technik.druck_auswerten(u, e["enddruck"], e["teiler"], e["nullpunkt"])

    async def run(self):
        start = time.monotonic()
        while self.running:
            wert, fehler = None, ""
            try:
                e = self._einstellungen()
                if self.props.get("Simulation", "Nein") == "Ja":
                    if self._sim_task is None:
                        self._sim_task = asyncio.create_task(self._sim_schleife())
                    wert = self._simulieren(0) - e["nullpunkt"]
                else:
                    wert, fehler = await asyncio.to_thread(self._messen, e)
            except TechnikFehler as ex:
                fehler = str(ex)
            except Exception as ex:
                log.exception("DruckSensor %s", self.id)
                fehler = f"Fehler: {ex}"
            self.fehler = fehler
            if wert is not None:
                self.value = round(wert, 3)
                self.zeit = time.monotonic()
                self.push_update(self.value)
                self.log_data(self.value)
                if self.gemeldet:
                    self.gemeldet = False
                    self.cbpi.notify("Drucksensor", f"{self._name()}: liefert wieder ({self.value:.2f} bar)",
                                     NotificationType.SUCCESS)
            else:
                alter = self.alter() if self.zeit is not None else time.monotonic() - start
                if alter > _zahl(self.props, "Max Alter s", 10) and not self.gemeldet:
                    self.gemeldet = True
                    log.error("Drucksensor %s: %s", self._name(), fehler)
                    self.cbpi.notify("Drucksensor", f"{self._name()}: seit {alter:.0f} s kein gültiger Wert "
                                     f"({fehler or 'unbekannt'}). Nachdrücken ist gesperrt.",
                                     NotificationType.ERROR)
            await asyncio.sleep(1)

    def _name(self):
        item = self.cbpi.sensor.find_by_id(self.id)
        return getattr(item, "name", None) or self.id

    # Simulation: Tank mit Gaerung, Spund- und CO2-Ventil; Vordruck konstant.
    # Eigener 50-ms-Takt, damit kurze Ventilpulse richtig eingehen.
    async def _sim_schleife(self):
        letzte = time.monotonic()
        while self.running:
            await asyncio.sleep(0.05)
            jetzt = time.monotonic()
            self._simulieren(jetzt - letzte)
            letzte = jetzt

    def _simulieren(self, dt):
        rolle = self._rolle()  # kann sich aendern, wenn der Gaertank spaeter angelegt wird
        if self._sim is None or rolle != getattr(self, "_sim_rolle", None):
            self._sim = 1.5 if rolle == "vordruck" else 0.0
            self._sim_rolle = rolle
        f = self._mein_gaertank()
        if f is not None:
            keg = f.type == "KegDruckLogik"
            if keg:  # kleiner Kopfraum, CO2 loest sich langsam im Bier, kein Ablassventil
                p, spund, co2, fuellen = self._sim - 0.001 * dt, None, f.valve, 0.6
            else:  # Gaerung baut Druck auf
                p, spund, co2, fuellen = self._sim + 0.002 * dt, f.valve, f.props.get("Ventil Drücken"), 0.15
            if getattr(_instanz(self.cbpi.actor, spund), "state", False):
                p -= 0.25 * dt * max(p, 0)
            if getattr(_instanz(self.cbpi.actor, co2), "state", False):
                vordruck = getattr(_instanz(self.cbpi.sensor, f.props.get("Fühler CO₂-Vordruck")), "value", 1.5)
                p += fuellen * dt * max(vordruck - p, 0)
            self._sim = max(p, 0.0)
        return self._sim

    def _gaertanks(self):
        return getattr(self.cbpi.fermenter, "data", []) or []

    def _mein_gaertank(self):
        return next((f for f in self._gaertanks() if f.pressure_sensor == self.id), None)

    def _rolle(self):
        for f in self._gaertanks():
            if f.props.get("Fühler CO₂-Vordruck") == self.id:
                return "vordruck"
        return "tank"

    @action("Nullpunkt setzen", parameters=[])
    async def nullpunkt_setzen(self, **kwargs):
        """Tank offen (drucklos): aktueller Wert wird zum Nullpunkt."""
        if self.zeit is None or self.alter() > 5:
            self.cbpi.notify("Drucksensor", "Kein aktueller Wert, Nullpunkt nicht gesetzt", NotificationType.ERROR)
            return
        neu = round(_zahl(self.props, "Nullpunkt bar", 0) + self.value, 3)
        if abs(neu) > 0.3:
            self.cbpi.notify("Drucksensor", f"Nullpunkt {neu:+.3f} bar ist unplausibel (Tank wirklich offen? "
                             "Messbereich richtig?). Nicht übernommen.", NotificationType.ERROR)
            return
        item = self.cbpi.sensor.find_by_id(self.id)
        item.props["Nullpunkt bar"] = neu
        self.props["Nullpunkt bar"] = neu
        await self.cbpi.sensor.save()
        self.cbpi.notify("Drucksensor", f"{self._name()}: Nullpunkt {neu:+.3f} bar", NotificationType.SUCCESS)

    @action("Simulation: Wert setzen", parameters=[Property.Number("bar", configurable=True, default_value=1.0)])
    async def sim_setzen(self, bar=1.0, **kwargs):
        if self.props.get("Simulation", "Nein") != "Ja":
            self.cbpi.notify("Drucksensor", "Nur in der Simulation", NotificationType.WARNING)
            return
        self._sim = float(bar)

    def get_state(self):
        return dict(value=self.value)

    def get_unit(self):
        return "bar"


# --- Gemeinsames fuer Gaertank und Keg ---------------------------------------------

class _DruckLogikBasis(CBPiFermenterLogic):
    TITEL = "Gärtank"

    def _wert(self, sensor_id):
        """(Wert, Alter in s) eines Fuehlers. Fuehler ohne Altersangabe gelten als frisch."""
        if not sensor_id:
            return None, None
        inst = _instanz(self.cbpi.sensor, sensor_id)
        if inst is None:
            return None, None
        wert = getattr(inst, "value", None)
        alter = inst.alter() if hasattr(inst, "alter") else 0.0
        return (wert if isinstance(wert, (int, float)) else None), alter

    async def _solldruck(self, f):
        """Solldruck in bar. Ein Wert aus dem kPa/PSI-Schieber wird umgerechnet
        und als bar zurueckgeschrieben, damit Dashboard und Dialog bar zeigen."""
        einheit = self.cbpi.config.get("PRESSURE_UNIT", "kPa") or "kPa"
        bar, umgerechnet = solldruck_bar(f.target_pressure, einheit)
        if umgerechnet:
            vorher = f.target_pressure
            await self.cbpi.fermenter.set_target_pressure(f.id, bar)
            text = f"Solldruck {vorher} {einheit} als {bar:.2f} bar übernommen"
            log.info("%s: %s", f.name, text)
            self.cbpi.notify(f.name or self.TITEL, text, NotificationType.INFO)
        return bar

    def _an(self, aktor_id):
        return bool(getattr(_instanz(self.cbpi.actor, aktor_id), "state", False))

    async def _schalten(self, aktor_id, an):
        if aktor_id and self._an(aktor_id) != an:
            await (self.actor_on(aktor_id) if an else self.actor_off(aktor_id))

    async def _puls(self, aktor_id, dauer):
        try:
            await self.actor_on(aktor_id)
            await asyncio.sleep(dauer)
        finally:
            await self.actor_off(aktor_id)

    def _melden(self, art, text, typ):
        """Jede Meldung einmal; Zahlen im Text zaehlen nicht als Aenderung."""
        kern = re.sub(r"[\d.,+-]+", "#", text)
        alt = self._gemeldet.get(art, "")
        if kern == alt:
            return
        self._gemeldet[art] = kern
        name = getattr(self.get_fermenter(self.id), "name", None) or self.TITEL
        if text:
            log.warning("%s: %s", name, text)
            self.cbpi.notify(name, text, typ)
        elif art == "stoerung":
            self.cbpi.notify(name, "Druckregelung wieder ohne Störung", NotificationType.SUCCESS)

    def _aktoren(self, f):
        return (f.valve,)

    async def _alles_aus(self, f):
        if f is None:
            return
        for aktor in self._aktoren(f):
            if aktor:
                try:
                    await self.actor_off(aktor)
                except Exception as e:
                    log.error("Ausschalten %s: %s", aktor, e)

    async def _durchlauf(self, f):
        """Ein Regeldurchlauf. Gibt zurueck, wie lange danach gewartet wird."""
        raise NotImplementedError

    async def run(self):
        self._gemeldet = {}
        self._vorbereiten()
        f = None
        try:
            f = self.get_fermenter(self.id)
            await self._alles_aus(f)
            while self.running:
                warten = 1.0
                try:
                    f = self.get_fermenter(self.id)
                    if f is None:
                        raise ValueError(f"{self.TITEL} nicht gefunden")
                    warten = await self._durchlauf(f)
                    self._melden("fehler", "", NotificationType.ERROR)
                except asyncio.CancelledError:
                    raise
                except Exception as ex:
                    log.exception("%s %s", type(self).__name__, self.id)
                    self._melden("fehler", f"Regelung gestört, alles aus: {ex}", NotificationType.ERROR)
                    await self._alles_aus(f)
                await asyncio.sleep(max(0.2, warten))
        except asyncio.CancelledError:
            pass
        finally:
            self.running = False
            await self._alles_aus(f)


# --- Gaertanklogik ----------------------------------------------------------------

@parameters([
    Property.Actor("Ventil Drücken", description="Magnetventil CO₂ ein (HAT Ventil 2, GPIO 17). Spundventil = "
                   "„Valve“ des Gärtanks (Ventil 1, GPIO 16)"),
    Property.Sensor("Fühler CO₂-Vordruck", description="Optional: Druck hinter dem Druckminderer (Druck 2)"),
    Property.Number("Grenzdruck bar", configurable=True, default_value=2.0,
                    description="FermZilla All Rounder: höchstens 2,4 bar. Ab hier bleibt das Spundventil offen"),
    Property.Number("Ablassen über Soll bar", configurable=True, default_value=0.05),
    Property.Number("Drücken unter Soll bar", configurable=True, default_value=0.10),
    Property.Select("Nachdrücken", options=["Ja", "Nein"], description="CO₂ nachdrücken, wenn der Druck fällt"),
    Property.Number("Puls Spunden s", configurable=True, default_value=0.5),
    Property.Number("Puls Drücken s", configurable=True, default_value=0.3),
    Property.Number("Pause s", configurable=True, default_value=3, description="Beruhigen nach jedem Puls"),
    Property.Select("Bei Fühlerausfall", options=["Spundventil pulsen", "Ventile zu"],
                    description="Tankdruck unbekannt: vorsorglich ablassen oder alles zu lassen"),
    Property.Number("Heizen ab K unter Soll", configurable=True, default_value=0.3),
    Property.Number("Heizen bis K unter Soll", configurable=True, default_value=0),
    Property.Number("Kühlen ab K über Soll", configurable=True, default_value=0.3),
    Property.Number("Kühlen bis K über Soll", configurable=True, default_value=0),
    Property.Number("Kompressorpause min", configurable=True, default_value=5,
                    description="Kühlschrank frühestens so lange nach dem Ausschalten wieder an"),
    Property.Select("AutoStart", options=["Yes", "No"],
                    description="Yes: nach Neustart von CraftBeerPi weiterregeln (empfohlen)"),
])
class GaerDruckLogik(_DruckLogikBasis):

    def _vorbereiten(self):
        self._druckregler, self._tempregler = self._regler()

    def _aktoren(self, f):
        return (f.heater, f.cooler, f.valve, f.props.get("Ventil Drücken"))

    async def _durchlauf(self, f):
        await self._temperatur(f, self._tempregler)
        return await self._druck(f, self._druckregler)

    def _regler(self):
        p = self.props
        druck = Druckregler(
            grenzdruck=_zahl(p, "Grenzdruck bar", 2.0),
            ablassen_ueber=_zahl(p, "Ablassen über Soll bar", 0.05),
            druecken_unter=_zahl(p, "Drücken unter Soll bar", 0.10),
            nachdruecken=p.get("Nachdrücken", "Ja") != "Nein",
            bei_ausfall=SPUNDEN if p.get("Bei Fühlerausfall", "Spundventil pulsen") != "Ventile zu" else None,
        )
        temp = Temperaturregler(
            heiz_an=_zahl(p, "Heizen ab K unter Soll", 0.3), heiz_aus=_zahl(p, "Heizen bis K unter Soll", 0),
            kuehl_an=_zahl(p, "Kühlen ab K über Soll", 0.3), kuehl_aus=_zahl(p, "Kühlen bis K über Soll", 0),
            kuehl_pause_s=_zahl(p, "Kompressorpause min", 5) * 60,
        )
        return druck, temp

    # -- Regelung --

    async def _temperatur(self, f, regler):
        if not f.sensor or not (f.heater or f.cooler):
            return
        ist, alter = self._wert(f.sensor)
        if alter is not None and alter > 30:
            ist = None
        heizen, kuehlen, hinweis = regler.entscheiden(ist, float(f.target_temp or 0))
        await self._schalten(f.heater, heizen and bool(f.heater))
        await self._schalten(f.cooler, kuehlen and bool(f.cooler))
        if not hinweis.startswith("Kompressorschutz"):
            self._melden("temperatur", hinweis, NotificationType.ERROR)

    async def _druck(self, f, regler):
        """Ein Druckdurchlauf. Gibt zurueck, wie lange danach gewartet wird."""
        soll = await self._solldruck(f)
        spund, co2 = f.valve, f.props.get("Ventil Drücken")
        if not f.pressure_sensor or not (spund or co2):
            return 1.0
        if spund and spund == co2:
            raise ValueError("Spundventil und Ventil Drücken sind derselbe Aktor")
        p, alter = self._wert(f.pressure_sensor)
        vordruck, vordruck_alter = self._wert(f.props.get("Fühler CO₂-Vordruck"))
        e = regler.entscheiden(
            soll, p, alter, None, None, vordruck, vordruck_alter,
            vordruck_da=bool(f.props.get("Fühler CO₂-Vordruck")))
        self._melden("stoerung", e.stoerung, NotificationType.ERROR)
        self._melden("hinweis", e.hinweis, NotificationType.WARNING)
        if e.aktion == NOTABLASS:
            await self._schalten(co2, False)
            await self._schalten(spund, True)
            return 1.0
        await self._schalten(spund, False)
        await self._schalten(co2, False)
        if e.aktion == SPUNDEN and spund:
            await self._puls(spund, _zahl(self.props, "Puls Spunden s", 0.5))
            return _zahl(self.props, "Pause s", 3)
        if e.aktion == DRUECKEN and co2:
            await self._puls(co2, _zahl(self.props, "Puls Drücken s", 0.3))
            return _zahl(self.props, "Pause s", 3)
        return 1.0



# --- Keg -----------------------------------------------------------------------------

@parameters([
    Property.Sensor("Fühler CO₂-Vordruck", description="Optional: Druck hinter dem Druckminderer (Druck 2)"),
    Property.Number("Grenzdruck bar", configurable=True, default_value=3.0,
                    description="Darüber Meldung. Ein Keg hat kein Ablassventil: Druckminderer und Keg-PRV prüfen"),
    Property.Number("Drücken unter Soll bar", configurable=True, default_value=0.05),
    Property.Number("Puls Drücken s", configurable=True, default_value=0.5),
    Property.Number("Pause s", configurable=True, default_value=3, description="Beruhigen nach jedem Puls"),
    Property.Select("AutoStart", options=["Yes", "No"],
                    description="Yes: nach Neustart von CraftBeerPi weiterregeln (empfohlen)"),
])
class KegDruckLogik(_DruckLogikBasis):
    """Haelt ein Keg auf Solldruck (Karbonisieren, Lagern, Zapfen).

    Im cbpi ist das Keg ein Gaertank ohne Temperatur: "Pressure Sensor" = Fuehler
    hinter dem Keg-Ventil (Druck 3/4), "Valve" = CO2-Ventil (Ventil 3/4),
    Solldruck = target_pressure. Gemessen wird nur, wenn das Ventil zu ist."""

    TITEL = "Keg"

    def _vorbereiten(self):
        p = self.props
        self._druckregler = Druckregler(
            grenzdruck=_zahl(p, "Grenzdruck bar", 3.0),
            ablassen_ueber=99.0,  # kein Ablassventil
            druecken_unter=_zahl(p, "Drücken unter Soll bar", 0.05),
            bei_ausfall=None,
            sprung_max=None,  # volles Keg = kleiner Kopfraum, grosse Spruenge sind normal
        )

    async def _durchlauf(self, f):
        soll = await self._solldruck(f)
        ventil = f.valve
        if not f.pressure_sensor or not ventil:
            return 1.0
        p, alter = self._wert(f.pressure_sensor)
        vordruck, vordruck_alter = self._wert(f.props.get("Fühler CO₂-Vordruck"))
        e = self._druckregler.entscheiden(soll, p, alter, vordruck=vordruck,
                                          vordruck_alter=vordruck_alter,
                                          vordruck_da=bool(f.props.get("Fühler CO₂-Vordruck")))
        stoerung = e.stoerung
        if e.aktion == NOTABLASS:
            stoerung = (f"{f.name or 'Keg'} über Grenzdruck ({e.druck:.2f} bar). Kein Ablassventil: "
                        "Druckminderer herunterdrehen, von Hand entlüften")
        self._melden("stoerung", stoerung, NotificationType.ERROR)
        self._melden("hinweis", e.hinweis, NotificationType.WARNING)
        await self._schalten(ventil, False)
        if e.aktion == DRUECKEN:
            await self._puls(ventil, _zahl(self.props, "Puls Drücken s", 0.5))
            return _zahl(self.props, "Pause s", 3)
        return 1.0


# --- Autostart ---------------------------------------------------------------------

class GaerDruckAutostart(CBPiExtension):
    """Startet Gaertanks und Kegs dieses Plugins mit AutoStart=Yes nach jedem Start von cbpi.

    Die eingebaute Erweiterung "Fermenter Autostart" (cbpi 4.7.7) liest die
    Gaertanks, bevor sie geladen sind, und ruft zudem start() (Gaerplan) statt
    start_logic() (Regelung) auf. Sie startet deshalb nichts."""

    def __init__(self, cbpi):
        self.cbpi = cbpi
        cbpi.register_on_startup(self)

    @on_startup("gaerdruck_autostart", order=99)
    async def beim_start(self):
        for f in list(getattr(self.cbpi.fermenter, "data", []) or []):
            if f.type not in ("GaerDruckLogik", "KegDruckLogik") or f.props.get("AutoStart") != "Yes":
                continue
            if f.instance is not None and f.instance.state:
                continue
            try:
                await self.cbpi.fermenter.start_logic(f.id)
                self.cbpi.fermenter.push_update()
                log.info("Gärtank %s nach Neustart gestartet", f.name)
                self.cbpi.notify("Gärtank", f"{f.name}: Regelung nach Neustart wieder an", NotificationType.INFO)
            except Exception as e:
                log.error("Autostart %s: %s", f.name, e)
                self.cbpi.notify("Gärtank", f"{f.name}: Autostart fehlgeschlagen ({e})", NotificationType.ERROR)


def setup(cbpi):
    cbpi.plugin.register("DruckSensor", DruckSensor)
    cbpi.plugin.register("GaerDruckLogik", GaerDruckLogik)
    cbpi.plugin.register("KegDruckLogik", KegDruckLogik)
    cbpi.plugin.register("GaerDruckAutostart", GaerDruckAutostart)
