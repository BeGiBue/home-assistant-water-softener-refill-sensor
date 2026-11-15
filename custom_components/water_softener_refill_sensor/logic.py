"""Reine Logik der Regenerationserkennung und der Salzbestandsrechnung.

Dieses Modul hat bewusst keine Home-Assistant-Abhängigkeiten, damit es ohne Home Assistant getestet werden kann
(siehe tests/test_logic.py). Alle Zeitangaben sind zeitzonenbehaftete datetime-Objekte (in Home Assistant: Ortszeit).

Funktionsweise
--------------
* Ein Wasserzähler (kumulierter Stand in Litern) wird überwacht. Im Zeitfenster (Standard 02:00 bis 03:00 Uhr) werden
  alle Zuwächse des Zählerstands addiert.
* Eine Minute nach Ende des Fensters wird ausgewertet: Ist der Verbrauch im Fenster größer als der Schwellwert
  (Standard 45 Liter), zählt das als eine Regeneration. Erfasst wird bewusst bis zu diesem Auswertezeitpunkt, also
  einschließlich der Minute nach dem Fensterende, damit verspätet gemeldete Zählerwerte noch zählen.
* Zeitfenster in Anlagenzeit: Die Anlage stellt ihre Uhr **nicht** selbst um. Die Einstellung „Uhr der Anlage“
  (``Settings.clock_mode``) legt fest, nach welcher Uhr das Fenster gilt; alle Vergleiche laufen in UTC, die Länge
  (Ende − Beginn) wird in echten Stunden gerechnet. Beispiele für Europe/Berlin, Fenster 2–3 Uhr:
  - ``standard`` (Standard): Die Uhr bleibt ganzjährig auf Winterzeit. Fenster im Winter 02:00–03:00 MEZ, im Sommer
    03:00–04:00 MESZ; die Umstellungsnächte sind kein Sonderfall.
  - ``dst``: Die Uhr bleibt ganzjährig auf Sommerzeit. Fenster im Sommer 02:00–03:00 MESZ, im Winter 01:00–02:00 MEZ.
  - ``manual``: Der Nutzer stellt die Uhr von Hand um, in der Praxis nach der Umstellungsnacht; in dieser Nacht
    regeneriert die Anlage noch nach ihrer alten Uhr. Beginn = eingestellte Stunde in Ortszeit:
    Oktober 02:00 MESZ bis 02:00 MEZ (die erste der beiden Stunden), März 03:00 bis 04:00 MESZ (02:00 gibt es nicht).
  Der „Tag“ eines Fensters ist das Datum der Anlagenuhr zum Fensterbeginn.
* Lücken: Fällt der Zähler kurz aus oder startet Home Assistant neu, zählt der Zuwachs seit dem letzten Wert nur dann,
  wenn dieser letzte Wert im selben Fenster lag (``bridge_gap``); sonst wird nur ein neuer Bezugswert gesetzt.
* Jede Regeneration verbraucht eine einstellbare Menge Salz. Der Salzbestand ist rein rechnerisch. Beim Nachfüllen
  muss die nachgefüllte Menge (kg) angegeben werden; sie wird zum Bestand addiert (höchstens bis voll).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

# Das Fenster wird erst eine Minute nach seinem Ende ausgewertet, damit späte Zählerwerte noch erfasst werden.
EVAL_DELAY = timedelta(minutes=1)
# Die Anlage regeneriert spätestens 7 Tage nach der letzten Regeneration (Hygiene), auch ohne Wasserverbrauch.
HYGIENE_DAYS = 7
HISTORY_LENGTH = 10

# Uhr der Anlage (sie stellt sich nicht selbst um)
CLOCK_STANDARD = "standard"  # bleibt ganzjährig auf Winterzeit (Normalzeit)
CLOCK_DST = "dst"  # bleibt ganzjährig auf Sommerzeit
CLOCK_MANUAL = "manual"  # wird nach der Umstellungsnacht von Hand umgestellt (folgt der Ortszeit)
CLOCK_MODES = (CLOCK_STANDARD, CLOCK_DST, CLOCK_MANUAL)


@dataclass
class Settings:
    """Einstellungen der Enthärtungsanlage."""

    capacity_kg: float
    per_regen_kg: float
    threshold_l: float = 45.0
    start_hour: int = 2
    end_hour: int = 3
    warn_remaining: int = 3
    clock_mode: str = CLOCK_STANDARD


@dataclass
class State:
    """Veränderlicher Zustand (wird persistent gespeichert)."""

    stock_kg: float
    total_regens: int = 0
    regens_since_refill: int = 0
    last_value_l: float | None = None
    last_value_at: datetime | None = None
    window_date: date | None = None
    window_start_at: datetime | None = None
    window_usage_l: float = 0.0
    window_crossed_at: datetime | None = None
    evaluated_date: date | None = None
    last_regen_at: datetime | None = None
    last_refill_at: datetime | None = None
    history: list[datetime] | None = None

    def __post_init__(self) -> None:
        if self.history is None:
            self.history = []


def _utc(value: datetime) -> datetime:
    """Zeitpunkt in UTC: Vergleiche gleicher Zeitzone liefen sonst nach Wanduhrzeit (Fehler bei der Zeitumstellung)."""
    return value.astimezone(timezone.utc)


def _dt(value: Any) -> datetime | None:
    """Gespeicherter Zeitpunkt; unlesbare Werte und solche ohne Zeitzone werden verworfen."""
    try:
        parsed = datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None
    return parsed if parsed is not None and parsed.tzinfo is not None else None


def _d(value: Any) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def finite_float(value: Any) -> float:
    """Convert a finite number; NaN and infinity are never valid measurements."""
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("A finite number is required")
    return number


def _float(value: Any, default: float | None) -> float | None:
    try:
        return finite_float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _int(value: Any, default: int) -> int:
    try:
        return int(finite_float(value))
    except (TypeError, ValueError, OverflowError):
        return default


class SoftenerModel:
    """Rechenmodell: Regenerationen zählen und Salzbestand führen."""

    def __init__(self, settings: Settings, state: State | None = None, initial_stock_kg: float | None = None) -> None:
        self.settings = settings
        if state is None:
            stock = settings.capacity_kg if initial_stock_kg is None else initial_stock_kg
            state = State(stock_kg=self._clamp(stock))
        self.state = state
        # Falls die Behältergröße verkleinert wurde, darf der Bestand sie nicht übersteigen
        self.state.stock_kg = self._clamp(self.state.stock_kg)

    # ------------------------------------------------------------------ Hilfsfunktionen
    def _clamp(self, kg: float) -> float:
        return max(0.0, min(finite_float(kg), float(self.settings.capacity_kg)))

    def clock_tz(self, tz: Any, day: date) -> Any:
        """Zeitzone der Anlagenuhr am Tag day: Ortszeit tz (manual) oder fester Versatz (Winter- bzw. Sommerzeit)."""
        if self.settings.clock_mode == CLOCK_MANUAL:
            return tz
        noon = datetime.combine(day, time(12, 0), tzinfo=tz)
        standard = noon.utcoffset() - (noon.dst() or timedelta(0))
        if self.settings.clock_mode == CLOCK_DST:
            # Betrag der Sommerzeitverschiebung dieser Zeitzone (0 in Zeitzonen ohne Sommerzeit)
            standard += max(
                (datetime(day.year, month, 1, 12, tzinfo=tz).dst() or timedelta(0)) for month in (1, 7)
            )
        return timezone(standard)

    def clock_date(self, when: datetime) -> date:
        """Datum der Anlagenuhr zum Zeitpunkt when (when in Ortszeit)."""
        return when.astimezone(self.clock_tz(when.tzinfo, when.date())).date()

    def window_start(self, day: date, tz: Any) -> datetime:
        """Beginn des Fensters am Tag day der Anlagenuhr, zurückgegeben in Ortszeit tz.

        Nur manual: doppelte Ortszeit (Oktober): die erste (fold=0). Nicht existierende Ortszeit (März): fold=0 rechnet
        mit dem Versatz vor der Umstellung, der Beginn rückt damit um die Lücke nach vorn (02:00 → 03:00 MESZ).
        """
        local = datetime.combine(day, time(self.settings.start_hour, 0), tzinfo=self.clock_tz(tz, day))
        return _utc(local).astimezone(tz)

    def window_end(self, day: date, tz: Any) -> datetime:
        """Ende des Erfassungsfensters = Auswertezeitpunkt: Beginn + Fensterlänge in echten Stunden + Nachlauf."""
        hours = self.settings.end_hour - self.settings.start_hour
        return (_utc(self.window_start(day, tz)) + timedelta(hours=hours) + EVAL_DELAY).astimezone(tz)

    def window_day(self, now: datetime) -> date | None:
        """Tag des Fensters, in dem now liegt (Erfassung einschließlich Nachlauf), sonst None."""
        today = self.clock_date(now)
        for day in (today, today - timedelta(days=1)):
            if _utc(self.window_start(day, now.tzinfo)) <= _utc(now) < _utc(self.window_end(day, now.tzinfo)):
                return day
        return None

    def last_closed_day(self, now: datetime) -> date:
        """Tag des zuletzt beendeten Fensters (heute, wenn dessen Auswertezeitpunkt erreicht ist, sonst gestern)."""
        today = self.clock_date(now)
        if _utc(now) >= _utc(self.window_end(today, now.tzinfo)):
            return today
        return today - timedelta(days=1)

    def next_evaluation(self, now: datetime) -> datetime:
        """Nächster Auswertezeitpunkt nach now (für den Zeitplan im Manager)."""
        today = self.clock_date(now)
        for offset in (-1, 0, 1, 2):
            end = self.window_end(today + timedelta(days=offset), now.tzinfo)
            if _utc(end) > _utc(now):
                return end
        raise RuntimeError("kein Auswertezeitpunkt gefunden")  # pragma: no cover

    # ------------------------------------------------------------------ Kennzahlen
    @property
    def remaining_regens(self) -> int:
        """Rechnerischer Restbestand in Regenerationen (abgerundet)."""
        if self.settings.per_regen_kg <= 0:
            return 0
        return int(math.floor(self.state.stock_kg / self.settings.per_regen_kg + 1e-9))

    @property
    def salt_percent(self) -> float:
        if self.settings.capacity_kg <= 0:
            return 0.0
        return 100.0 * self.state.stock_kg / self.settings.capacity_kg

    @property
    def needs_refill(self) -> bool:
        return self.remaining_regens <= self.settings.warn_remaining

    # ------------------------------------------------------------------ 7-Tage-Regel (Hygieneregeneration)
    def _due_day(self, tz: Any) -> date | None:
        last = self.state.last_regen_at
        if last is None:
            return None
        return self.clock_date(last.astimezone(tz)) + timedelta(days=HYGIENE_DAYS)

    def next_regen_due(self, tz: Any) -> datetime | None:
        """Spätestens fälliger Zeitpunkt der nächsten Regeneration: Beginn des Zeitfensters 7 Tage nach der letzten."""
        due_day = self._due_day(tz)
        return None if due_day is None else self.window_start(due_day, tz)

    def regen_overdue(self, now: datetime) -> bool:
        """Wahr, wenn das Zeitfenster am Fälligkeitstag ausgewertet wurde und keine Regeneration erkannt wurde.

        Hinweis auf einen verpassten Zählerwert (Wasserzähler ausgefallen) oder auf eine Störung der Anlage.
        """
        due_day = self._due_day(now.tzinfo)
        if due_day is None:
            return False
        return _utc(now) >= _utc(self.window_end(due_day, now.tzinfo))

    # ------------------------------------------------------------------ Zählerstand verarbeiten
    def on_meter(
        self, value_l: float, now: datetime, baseline_only: bool = False, bridge_gap: bool = False
    ) -> list[dict[str, Any]]:
        """Neuen Zählerstand (in Litern) verarbeiten.

        baseline_only=True setzt nur den Bezugswert (z. B. nach einem Neustart oder wenn der Zähler zuvor
        nicht verfügbar war), ohne einen Zuwachs zu zählen. So werden Lücken in den Daten nicht fälschlich
        als Verbrauch im Zeitfenster gewertet.

        bridge_gap=True (nur zusammen mit baseline_only): Lag der letzte bekannte Wert im selben Fenster wie now,
        fällt die ganze Lücke ins Fenster; der Zuwachs zählt dann doch (kurzer Zählerausfall, Neustart im Fenster).
        """
        events = self.evaluate_pending(now)
        st = self.state
        if not math.isfinite(value_l) or value_l < 0:
            # A new baseline after invalid data avoids attributing the gap to a window.
            st.last_value_l = None
            st.last_value_at = None
            return events
        prev, prev_at = st.last_value_l, st.last_value_at
        st.last_value_l = value_l
        st.last_value_at = now
        if prev is None:
            return events
        day = self.window_day(now)
        if baseline_only:
            in_same_window = (
                bridge_gap
                and day is not None
                and prev_at is not None
                and _utc(prev_at) >= _utc(self.window_start(day, now.tzinfo))
            )
            if not in_same_window:
                return events
        delta = value_l - prev
        if delta <= 0:  # unverändert, oder Zähler zurückgesetzt/getauscht (negativ): nichts zählen
            return events
        if day is not None:
            start = self.window_start(day, now.tzinfo)
            # Neues Fenster: anderer Tag oder (nach geänderten Einstellungen) anderer Beginn am selben Tag
            moved = st.window_start_at is not None and _utc(st.window_start_at) != _utc(start)
            if st.window_date != day or moved:
                if st.evaluated_date == day:  # verschobenes Fenster am selben Tag wird noch einmal ausgewertet
                    st.evaluated_date = None
                st.window_date = day
                st.window_usage_l = 0.0
                st.window_crossed_at = None
            st.window_start_at = start
            usage = st.window_usage_l + delta
            if not math.isfinite(usage):
                st.last_value_l = None
                st.last_value_at = None
                return events
            st.window_usage_l = usage
            if st.window_crossed_at is None and st.window_usage_l > self.settings.threshold_l:
                st.window_crossed_at = now
        return events

    # ------------------------------------------------------------------ Auswertung
    def evaluate_pending(self, now: datetime, scheduled: bool = False) -> list[dict[str, Any]]:
        """Ein beendetes, noch nicht ausgewertetes Zeitfenster abschließen.

        scheduled=True: Aufruf zum planmäßigen Ende des Fensters. Gab es im Fenster gar keinen Verbrauch
        (kein Zählerupdate), wird "0 Liter" festgehalten.
        """
        st = self.state
        wd = st.window_date
        if wd is not None and st.evaluated_date != wd:
            if _utc(now) >= _utc(self._open_window_end(wd, now.tzinfo)):
                return self._finalize(wd, now.tzinfo)
            return []
        day = self.last_closed_day(now)
        if scheduled and (st.evaluated_date is None or st.evaluated_date < day):
            st.window_date = day
            st.window_start_at = self.window_start(day, now.tzinfo)
            st.window_usage_l = 0.0
            st.window_crossed_at = None
            st.evaluated_date = day
            return [{"type": "window_closed", "date": day, "usage_l": 0.0}]
        return []

    def _open_window_end(self, day: date, tz: Any) -> datetime:
        """Auswertezeitpunkt des offenen Fensters: aus seinem gespeicherten Beginn (auch nach geänderten Einstellungen)."""
        start = self.state.window_start_at
        if start is None:  # ältere Speicherstände
            return self.window_end(day, tz)
        hours = self.settings.end_hour - self.settings.start_hour
        return (_utc(start) + timedelta(hours=hours) + EVAL_DELAY).astimezone(tz)

    def _finalize(self, day: date, tz: Any) -> list[dict[str, Any]]:
        st = self.state
        st.evaluated_date = day
        usage = st.window_usage_l
        events: list[dict[str, Any]] = [{"type": "window_closed", "date": day, "usage_l": usage}]
        if usage > self.settings.threshold_l:
            at = st.window_crossed_at or self._open_window_end(day, tz)
            self._register(at)
            events.append({"type": "regeneration", "date": day, "at": at, "usage_l": usage, "manual": False})
        return events

    def _register(self, at: datetime) -> None:
        st = self.state
        st.total_regens += 1
        st.regens_since_refill += 1
        st.stock_kg = self._clamp(round(st.stock_kg - self.settings.per_regen_kg, 4))
        # Nachgetragene ältere Regenerationen verschieben die letzte (und damit die 7-Tage-Frist) nicht zurück
        if st.last_regen_at is None or _utc(at) >= _utc(st.last_regen_at):
            st.last_regen_at = at
        st.history = sorted([at] + (st.history or []), key=_utc, reverse=True)[:HISTORY_LENGTH]

    # ------------------------------------------------------------------ Eingriffe von Hand
    def add_manual_regeneration(self, at: datetime, count: int = 1) -> None:
        """Eine (nicht erkannte) Regeneration von Hand nachtragen (Zeitpunkt at; der Manager prüft „nicht in der Zukunft“)."""
        for _ in range(max(0, int(count))):
            self._register(at)

    def refill(self, now: datetime, kg: float) -> float:
        """Salz nachgefüllt: die Menge (kg, größer als 0) wird zum Bestand addiert, höchstens bis zur Behältergröße.

        Der Zähler „Regenerationen seit Nachfüllen“ wird nur zurückgesetzt, wenn der Behälter danach voll ist.
        Gibt die Menge zurück, die tatsächlich im Bestand angekommen ist (kleiner als kg, wenn der Behälter überfüllt wäre).
        """
        if kg is None or not finite_float(kg) > 0:
            raise ValueError("Die nachgefüllte Menge muss größer als 0 kg sein.")
        st = self.state
        before = st.stock_kg
        st.stock_kg = self._clamp(round(before + float(kg), 4))
        if st.stock_kg >= float(self.settings.capacity_kg) - 1e-9:
            st.regens_since_refill = 0
        st.last_refill_at = now
        return round(st.stock_kg - before, 4)

    def refill_full(self, now: datetime) -> float:
        """Behälter ist voll: Bestand = Behältergröße, Zähler seit Nachfüllen zurücksetzen.

        Gibt die Menge zurück, die dem rechnerischen Bestand hinzugefügt wurde.
        """
        st = self.state
        before = st.stock_kg
        st.stock_kg = self._clamp(self.settings.capacity_kg)
        st.regens_since_refill = 0
        st.last_refill_at = now
        return round(st.stock_kg - before, 4)

    def set_stock(self, kg: float) -> None:
        """Bestand setzen. „Regenerationen seit Nachfüllen“ bleibt unverändert: beide Werte sind unabhängig."""
        self.state.stock_kg = self._clamp(kg)

    def set_last_regeneration(self, at: datetime, now: datetime) -> None:
        """Zeitpunkt der letzten Regeneration von Hand setzen (bestimmt die 7-Tage-Regel). Nicht in der Zukunft."""
        if at > now:
            raise ValueError("Die letzte Regeneration darf nicht in der Zukunft liegen.")
        st = self.state
        st.last_regen_at = at
        # Verlauf angleichen: neuere Einträge entfallen; war keiner neuer, wird der jüngste Eintrag korrigiert
        history = sorted(st.history or [], key=_utc, reverse=True)
        older = [h for h in history if _utc(h) <= _utc(at)]
        if len(older) == len(history):
            older = older[1:]
        st.history = ([at] + older)[:HISTORY_LENGTH]

    def set_regens_since_refill(self, count: int) -> None:
        """Bekannte Regenerationen seit dem letzten Füllen bis voll setzen.

        Der Bestand ändert sich relativ: um (bisherige Anzahl − count) × Salzverbrauch pro Regeneration, begrenzt auf
        0 bis Behältergröße. Erneutes Bestätigen desselben Werts ändert nichts, Teil-Nachfüllungen bleiben erhalten.
        Bei vollem Behälter und Zähler 0 (frisch eingerichtet) ergibt das Behältergröße − count × Verbrauch.
        Der Gesamtzähler wird bei Bedarf auf mindestens count angehoben.
        """
        count = int(count)
        if count < 0:
            raise ValueError("Die Anzahl der Regenerationen darf nicht negativ sein.")
        st = self.state
        delta = st.regens_since_refill - count
        st.regens_since_refill = count
        st.stock_kg = self._clamp(round(st.stock_kg + delta * self.settings.per_regen_kg, 4))
        st.total_regens = max(st.total_regens, count)

    # ------------------------------------------------------------------ Speichern / Laden
    def to_dict(self) -> dict[str, Any]:
        s = self.state
        return {
            "stock_kg": s.stock_kg,
            "total_regens": s.total_regens,
            "regens_since_refill": s.regens_since_refill,
            "last_value_l": s.last_value_l,
            "last_value_at": s.last_value_at.isoformat() if s.last_value_at else None,
            "window_date": s.window_date.isoformat() if s.window_date else None,
            "window_start_at": s.window_start_at.isoformat() if s.window_start_at else None,
            "window_usage_l": s.window_usage_l,
            "window_crossed_at": s.window_crossed_at.isoformat() if s.window_crossed_at else None,
            "evaluated_date": s.evaluated_date.isoformat() if s.evaluated_date else None,
            "last_regen_at": s.last_regen_at.isoformat() if s.last_regen_at else None,
            "last_refill_at": s.last_refill_at.isoformat() if s.last_refill_at else None,
            "history": [h.isoformat() for h in (s.history or [])],
        }

    @classmethod
    def from_dict(cls, settings: Settings, data: dict[str, Any]) -> "SoftenerModel":
        """Gespeicherten Zustand laden. Fehlende oder unlesbare Felder bekommen Standardwerte (ältere Stände)."""
        state = State(
            stock_kg=_float(data.get("stock_kg"), 0.0) if "stock_kg" in data else float(settings.capacity_kg),
            total_regens=_int(data.get("total_regens"), 0),
            regens_since_refill=_int(data.get("regens_since_refill"), 0),
            last_value_l=_float(data.get("last_value_l"), None),
            last_value_at=_dt(data.get("last_value_at")),
            window_date=_d(data.get("window_date")),
            window_start_at=_dt(data.get("window_start_at")),
            window_usage_l=_float(data.get("window_usage_l"), 0.0),
            window_crossed_at=_dt(data.get("window_crossed_at")),
            evaluated_date=_d(data.get("evaluated_date")),
            last_regen_at=_dt(data.get("last_regen_at")),
            last_refill_at=_dt(data.get("last_refill_at")),
            history=[h for h in (_dt(x) for x in (data.get("history") or [])) if h is not None],
        )
        return cls(settings, state)
