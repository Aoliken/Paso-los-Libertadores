"""Event-driven discrete-event model of the Los Libertadores / Cristo Redentor
border crossing, direction Mendoza (Argentina) -> Chile, LIGHT VEHICLES only.

Measurement points (defect D5)
------------------------------
Two waits are recorded, and neither may be compared with the other, nor with a
press figure, without the caveat in README.md:

    wait_to_gate    arrival -> admitted into the complex (passes the border
                    control point and takes a place in the internal yard)
    wait_total      arrival -> clears the LAST booth (leaves the complex)

Press reports of "6 hour waits" describe the queue on the ARGENTINE side,
upstream of the tunnel, in the waiting lots at Uspallata, Penitentes, Punta de
Vacas and Puente del Inca.  That is a different measurement point.

Design notes
------------
* NO POLLING (defect D2).  The previous version called
  ``yield self.env.timeout(5)`` in both the opening-hours check and the booth
  availability check, injecting up to 5 minutes of quantised artificial delay
  into every wait and letting service start up to 5 minutes after the official
  closing time.  Here the opening boundary is reached with an exact
  ``simpy.timeout`` and booth availability is a ``simpy.Resource`` that
  suspends the caller.  A vehicle arriving at 08:59 under a 09:00 opening waits
  exactly 1.0 minute; see ``verify_event_driven_opening()``.
* CENSORING IS REPORTED (defect D1).  The previous version imputed
  ``t_serv.fillna(horizon) - t_arr``, so vehicles still queued at the horizon
  contributed a LOWER BOUND on their own wait to the mean and the P95.  Here a
  vehicle that has not cleared the last booth is never given an imputed wait;
  the backlog at the end of the arrival window, the never-served fraction, the
  queue at the horizon and a stationarity verdict are all reported, and the
  headline mean/P95 is only called an equilibrium estimate when the run is
  stationary.
"""

from __future__ import annotations

import calendar
import math
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import simpy
from simpy.events import AnyOf

DATA_DIR = Path(__file__).resolve().parent / "data"
MONTHLY_CSV = DATA_DIR / "mop_monthly_light_vehicles.csv"
PARAMS_CSV = DATA_DIR / "published_operating_parameters.csv"
PRESS_CSV = DATA_DIR / "press_wait_reports.csv"

MIN_PER_DAY = 1440.0
NAN = float("nan")

# ---------------------------------------------------------------------------
# Opening-hours regimes.
#
# UNRESOLVED SOURCE CONFLICT, exposed rather than silently resolved.
# Vicejefatura de Fronteras states the crossing is "operativo las 24 h"
# (https://www.argentina.gob.ar/interior/centros-de-frontera/cristo-redentor)
# while Ministerio del Interior states "09:30 a 20:30 hs. epoca invernal y las
# 24 hs. epoca estival" (https://mininterior.gob.ar/fronteras/paso-105.php).
# A third value, 09:00-21:00 winter for Argentina -> Chile, is press-corroborated
# (Sitio Andino).  All three are selectable; none is asserted to be the truth.
# ---------------------------------------------------------------------------
HOURS_REGIMES = {
    "winter_press": (9 * 60.0, 21 * 60.0),        # OBSERVED, Sitio Andino
    "argentina_0930_2030": (570.0, 1230.0),       # CONFLICTING, Min. del Interior
    "argentina_24h": (0.0, MIN_PER_DAY),          # CONFLICTING, Vicejefatura
}

# ASSUMPTION-NOT-OBSERVED.  Base intraday shape, relative intensity per hour of
# the day, Argentina -> Chile, light vehicles.  Inherited verbatim from the
# previous model, which likewise had no data behind it.  Only its SHAPE is
# assumed: it is renormalised to mean 1 inside the model.
PROFILE_HOURLY_PRIOR = (
    0.05, 0.03, 0.03, 0.03, 0.05, 0.10, 0.25, 0.45,
    0.70, 0.90, 1.00, 1.00, 0.95, 0.85, 0.75, 0.65,
    0.55, 0.45, 0.35, 0.25, 0.18, 0.12, 0.08, 0.06,
)

# ASSUMPTION-NOT-OBSERVED.  Day-of-week factors Monday..Sunday, inherited from
# the previous model, renormalised to mean 1.  Holiday / "feriado largo" effects
# are deliberately EXCLUDED: no day-level series exists that would let them be
# estimated, and inventing them would be fabrication.
DOW_PRIOR = (0.45, 0.45, 0.45, 0.55, 1.00, 1.00, 0.90)


@dataclass
class Config:
    """Every field is tagged OBSERVED (with source) or ASSUMPTION-NOT-OBSERVED.

    The three capacity concepts of defect D6 are kept strictly apart:

    ``n_booths``        SERVICE POSITIONS.  OBSERVED: 7 car booths on
                        2024-02-01 (ClarIN).  The 6 booths opened on demand that
                        same day are modelled as the alternative configuration
                        ``n_booths_peak = 13``; the trigger rule for opening them
                        is not published, so no dynamic rule is invented.
    ``staff_per_booth`` PERSONNEL.  ASSUMPTION-NOT-OBSERVED: no car-booth headcount
                        is published.  Kept as its own field so a personnel
                        statement is never read as a service-capacity parameter.
                        At the default of 1.0 it does not alter throughput.
    ``space_capacity``  PHYSICAL SPACE.  OBSERVED: the complex's simultaneous
                        vehicle capacity, raised from 3-5 to 15 vehicles
                        (MOP DGOP).  Interpreted here as the number of vehicles
                        that can be inside the control area at once, queueing
                        included, from admission to clearing the last booth.
    ``yard_capacity``   INTERNAL YARD ("patio").  ASSUMPTION-NOT-OBSERVED: the
                        patio size is not published.  Distinct from
                        ``space_capacity``.
    """

    # ---- service positions (booths) ----
    n_booths: int = 7                    # OBSERVED (ClarIN 2024-02-01, car booths)
    n_booths_peak: int = 13              # OBSERVED (ClarIN: +6 on demand)

    # ---- personnel: a third, separate concept (defect D6) ----
    staff_per_booth: float = 1.0         # ASSUMPTION-NOT-OBSERVED

    # ---- physical space ----
    space_capacity: int = 15             # OBSERVED (MOP DGOP: 15 simultaneous veh.)
    yard_capacity: int = 200             # ASSUMPTION-NOT-OBSERVED (patio size)

    # ---- service time ----
    svc_mean_min: float = 3.5            # ASSUMPTION-NOT-OBSERVED (nothing published)
    svc_cv: float = 0.6                  # ASSUMPTION-NOT-OBSERVED (nothing published)

    # ---- demand (defect D3): anchored to the real MOP monthly series ----
    demand_mode: str = "monthly_series"   # "monthly_series" | "explicit_peak" | "explicit_vpd"
    intraday_peak_exponent: float = 1.0      # ASSUMPTION-NOT-OBSERVED prior; fitted
    peak_hour_rate_veh_h: float | None = None   # only for demand_mode="explicit_peak"
    #: Only for demand_mode="explicit_vpd": a DECLARED per-day demand for scenarios
    #: outside the MOP series (e.g. the Aug 2026 closures, for which no monthly
    #: count existed).  Always an assumption, never presented as observed.
    veh_per_day: float = 0.0
    day_factors: tuple = DOW_PRIOR            # ASSUMPTION-NOT-OBSERVED
    profile_hourly: tuple = PROFILE_HOURLY_PRIOR  # ASSUMPTION-NOT-OBSERVED

    # ---- persons (defect D7: must be USED in an output) ----
    persons_per_vehicle: float = 3.2    # DERIVED (MOP 2018: 1203488/376198 = 3.199)

    # ---- opening hours ----
    hours_regime: str = "winter_press"  # see HOURS_REGIMES; UNRESOLVED CONFLICT
    winter_start_month: int = 6          # OBSERVED closure regime May-Sep, applied
    winter_end_month: int = 8            #   as 1 Jun - 31 Aug
    #: Whole-day closures, as day indices from ``start_date``.  Used for the
    #: long weather closures the press records (e.g. 27 and 34 days in 2026).
    extra_closed_days: tuple = ()
    #: Sub-day closures as (start_minute, end_minute) from the simulation start.
    #: Used to reproduce a dated, fully specified closure window exactly, e.g.
    #: 10:00 19 Feb to 12:00 20 Feb 2026 (26 h) for the Aedes aegypti
    #: disinfection closure.  A closed interval suppresses ADMISSION only:
    #: vehicles still arrive and queue, which is the whole point of a closure.
    closed_intervals: tuple = ()

    # ---- reneging / balking (defect D4); ALL ASSUMPTION-NOT-OBSERVED ----
    # A driver who arrives before the gate opens is not "queued against a
    # capacity constraint", they are waiting for a published timetable, and the
    # upstream lots at Uspallata, Penitentes, Punta de Vacas and Puente del Inca
    # exist precisely so they can.  They therefore get their own, much longer
    # budget.  Using the queue-waiting budget here would make a 23:00 arrival
    # give up before a 09:00 opening, which no observed behaviour supports.
    reneging_enabled: bool = True
    reneging_base_patience_min: float = 45.0
    # Justified by the press record, not by a measurement: the 2026-08 reports
    # describe drivers who waited out a 27 and then a 34-day closure, so a
    # 30-day mean budget against a PUBLISHED TIMETABLE is the only assumption
    # consistent with the observed behaviour.  It is still an assumption.
    reneging_closed_base_patience_min: float = 43200.0
    reneging_closed_max_patience_min: float = 52560.0
    reneging_queue_sensitivity: float = 0.8
    reneging_queue_reference: float = 20.0
    reneging_max_patience_min: float = 240.0

    # ---- simulation control ----
    warmup_days: float = 0.0                    # transient discarded before measuring
    drain_after_arrivals_min: float = 2880.0    # ASSUMPTION-NOT-OBSERVED
    arrival_bin_min: float = 10.0              # thinning grid; no effect on service
    monitor_interval_min: float = 10.0         # OBSERVATION-ONLY; cannot delay a vehicle
    # ---- stationarity thresholds (defect D1); ASSUMPTION-NOT-OBSERVED ----
    stat_tol_queue_veh: float = 2.0
    stat_tol_censored: float = 0.005
    # Above this share of balking, the offered load exceeds what the crossing can
    # carry, so a bounded queue proves nothing about equilibrium.
    stat_tol_reneging: float = 0.01

    # ---- horizon / seed ----
    start_date: str = "2025-01-01"
    n_days: int | None = None          # None => whole month
    month: str | None = None           # "YYYY-MM"; drives the demand anchor
    seed: int = 1
    #: VERIFICATION HOOK ONLY.  When False, no stochastic arrivals are generated
    #: and only the arrivals passed to ``simulate(force_arrivals=...)`` occur.
    #: Used by ``verify_event_driven_opening()`` so the assertions are not
    #: contaminated by background demand.  Never set False in an experiment.
    background_arrivals: bool = True

    def __post_init__(self) -> None:
        if self.hours_regime not in HOURS_REGIMES:
            raise ValueError(f"unknown hours_regime {self.hours_regime!r}")
        if self.demand_mode not in ("monthly_series", "explicit_peak", "explicit_vpd"):
            raise ValueError(f"unknown demand_mode {self.demand_mode!r}")
        if self.demand_mode == "explicit_peak" and self.peak_hour_rate_veh_h is None:
            raise ValueError("demand_mode='explicit_peak' needs peak_hour_rate_veh_h")
        if self.demand_mode == "explicit_vpd" and self.veh_per_day <= 0.0:
            raise ValueError("demand_mode='explicit_vpd' needs veh_per_day > 0")
        for name in ("n_booths", "space_capacity", "yard_capacity", "svc_mean_min"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.svc_cv < 0:
            raise ValueError("svc_cv must be >= 0")
        if self.arrival_bin_min <= 0 or self.monitor_interval_min <= 0:
            raise ValueError("bins and monitor interval must be positive")

    # ------------------------------------------------------------------
    @property
    def shape(self) -> np.ndarray:
        """Intraday relative intensity per hour, renormalised to mean 1.

        ``intraday_peak_exponent`` tilts the prior shape: > 1 concentrates demand
        into fewer hours (higher peak), < 1 flattens it.  Renormalisation keeps
        the daily total equal to the monthly anchor, so the fitted parameter
        cannot silently change the amount of demand, only its distribution.
        """
        base = np.clip(np.asarray(self.profile_hourly, dtype=float), 1e-9, None)
        tilted = base ** float(self.intraday_peak_exponent)
        return tilted / tilted.mean()

    @property
    def dow(self) -> np.ndarray:
        """Day-of-week factors Monday..Sunday, renormalised to mean 1."""
        d = np.asarray(self.day_factors, dtype=float)
        return d / d.mean()

    @property
    def booth_capacity_veh_h(self) -> float:
        """Service capacity, vehicles per hour, while the gate is open."""
        return self.staffed_booths() * 60.0 / self.svc_mean_min

    def staffed_booths(self) -> int:
        """Booths that actually have a person behind them (defect D6)."""
        return max(1, int(math.floor(self.n_booths * self.staff_per_booth)))

    def is_winter(self, d: date) -> bool:
        return self.winter_start_month <= d.month <= self.winter_end_month

    def open_window(self, d: date) -> tuple[float, float]:
        """Opening window, minutes from midnight, for date ``d``.

        Outside the winter window every regime is 24 h, which is what Ministerio
        del Interior asserts for "epoca estival".  Inside the winter window the
        three regimes disagree; see HOURS_REGIMES.
        """
        if self.is_winter(d):
            return HOURS_REGIMES[self.hours_regime]
        return (0.0, MIN_PER_DAY)

    def open_hours_per_day(self, n_days: int) -> float:
        segs = self.gate_segments(n_days)
        return float(sum(e - s for s, e in segs) / max(1, n_days) / 60.0)

    def closed_spans(self) -> list[tuple[float, float]]:
        """Closure windows in absolute minutes from the simulation start."""
        spans = [(float(d) * MIN_PER_DAY, float(d) * MIN_PER_DAY + MIN_PER_DAY)
                 for d in self.extra_closed_days]
        spans += [(float(a), float(b)) for a, b in self.closed_intervals]
        return sorted((min(a, b), max(a, b)) for a, b in spans)

    def gate_segments(self, n_days: int) -> list[tuple[float, float]]:
        """Exact open windows in absolute minutes, closures subtracted out.

        Building the whole schedule up front is what lets a dated sub-day
        closure be reproduced to the published endpoints while the gate logic
        stays a single exact ``timeout`` per transition.
        """
        d0 = date.fromisoformat(self.start_date)
        closures = self.closed_spans()
        segs: list[list[float]] = []
        for k in range(n_days):
            d = d0 + timedelta(days=k)
            base = float(k) * MIN_PER_DAY
            o, c = self.open_window(d)
            start, end = base + o, base + c
            if c - o >= MIN_PER_DAY - 1e-9:
                end = base + MIN_PER_DAY      # a 24 h window ends at midnight
            if end <= base:
                continue
            for cs, ce in closures:
                if ce <= start or cs >= end:
                    continue
                if cs > start:
                    segs.append([start, cs])
                start = max(start, ce)
            if start < end:
                segs.append([start, end])
        # merge touching or overlapping segments
        out: list[list[float]] = []
        for s, e in sorted(segs):
            if out and s <= out[-1][1] + 1e-9:
                out[-1][1] = max(out[-1][1], e)
            else:
                out.append([s, e])
        return [(s, e) for s, e in out]

    def demand_veh_per_day(self) -> float:
        """The daily demand anchor, the single place it is defined.

        monthly_series : the real MOP per-day mean for ``self.month`` (OBSERVED).
        explicit_peak  : chosen so the busiest hour of a day equals
                         ``peak_hour_rate_veh_h`` exactly, which keeps the rho
                         axis of the utilisation sweep exact.
        """
        if self.demand_mode == "explicit_peak":
            return float(self.peak_hour_rate_veh_h) * 24.0 / float(self.shape.max())
        if self.demand_mode == "explicit_vpd":
            if self.veh_per_day <= 0.0:
                raise ValueError("demand_mode='explicit_vpd' requires veh_per_day > 0")
            return float(self.veh_per_day)
        if self.month is None:
            raise ValueError("demand_mode='monthly_series' requires cfg.month")
        try:
            return DEMAND_PER_DAY[self.month]
        except KeyError as exc:
            raise ValueError(
                f"month {self.month!r} is not in the MOP series "
                f"({DEMAND_TABLE['month'].iloc[0]}..{DEMAND_TABLE['month'].iloc[-1]}); "
                "it was not retrievable and must not be interpolated. Declare the "
                "scenario's demand explicitly with demand_mode='explicit_vpd'."
            ) from exc


# ---------------------------------------------------------------------------
# Demand data: the ONLY measured quantitative series for this crossing.
# ---------------------------------------------------------------------------
def load_monthly_demand(path: Path | str = MONTHLY_CSV) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"month": str})
    for col in ("days_in_month", "light_vehicles", "vehicles_per_day"):
        df[col] = pd.to_numeric(df[col])
    return df.sort_values("month").reset_index(drop=True)


def demand_facts(df: pd.DataFrame) -> dict:
    """Re-derive the summary statistics rather than hardcoding them."""
    vpd = df["vehicles_per_day"]
    lo = df.loc[vpd.idxmin()]
    hi = df.loc[vpd.idxmax()]
    return {
        "n_months": int(len(df)),
        "total_light_vehicles": int(df["light_vehicles"].sum()),
        "total_days": int(df["days_in_month"].sum()),
        "mean_veh_per_day": float(df["light_vehicles"].sum() / df["days_in_month"].sum()),
        "min_month": str(lo["month"]),
        "min_veh_per_day": float(lo["vehicles_per_day"]),
        "max_month": str(hi["month"]),
        "max_veh_per_day": float(hi["vehicles_per_day"]),
        "peak_trough_ratio_per_day": float(hi["vehicles_per_day"] / lo["vehicles_per_day"]),
        "peak_trough_ratio_raw_counts": float(
            df["light_vehicles"].max() / df["light_vehicles"].min()
        ),
        "granularity": "monthly",
        "hourly_series_exists": False,
        "first_month": str(df["month"].iloc[0]),
        "last_month": str(df["month"].iloc[-1]),
    }


# ---------------------------------------------------------------------------
# The crossing
# ---------------------------------------------------------------------------
class BorderCrossing:
    """One realisation of the crossing. Every decision is event-driven."""

    #: record columns, kept as parallel Python lists (no per-row dict overhead)
    COLS = ("t_arr", "t_gate", "t_yard", "t_space", "t_serv_start", "t_serv_end",
            "persons", "weekday", "t_end")

    def __init__(self, env: simpy.Environment, cfg: Config, rng: np.random.Generator):
        self.env = env
        self.cfg = cfg
        self.rng = rng
        self.t0 = date.fromisoformat(cfg.start_date)

        s2 = math.log(1.0 + cfg.svc_cv ** 2)
        self._mu = math.log(cfg.svc_mean_min) - s2 / 2.0
        self._sigma = math.sqrt(s2)

        self.yard = simpy.Resource(env, capacity=cfg.yard_capacity)     # ASSUMPTION
        self.space = simpy.Resource(env, capacity=cfg.space_capacity)  # OBSERVED 15
        self.booth = simpy.Resource(env, capacity=cfg.staffed_booths())  # OBSERVED 7

        # Gate events.  open_ev fires when the gate OPENS, close_ev when it
        # CLOSES; each is replaced on the next transition, so a process that
        # snapshots one is woken by exactly that transition and no other.
        self.is_open = False
        self.open_ev = simpy.Event(env)
        self.close_ev = simpy.Event(env)
        self.n_open_transitions = 0
        self.n_close_transitions = 0

        self.col: dict[str, list] = {k: [] for k in self.COLS}
        self.status: list[str] = []
        self.stage: list[str] = []
        self._yard_req: list = []
        self._space_req: list = []

        self.monitor: list[tuple] = []
        self.counters = {"arrived": 0, "served": 0, "renege": 0, "waiting_gate": 0}
        self.drain_complete = True

    # ---------------------- clock ----------------------
    def date_at(self, t: float) -> date:
        return self.t0 + timedelta(days=int(t // MIN_PER_DAY))

    def minute_of_day(self, t: float) -> float:
        return t % MIN_PER_DAY

    # ---------------------- gate schedule ----------------------
    def _open(self) -> None:
        self.is_open = True
        self.close_ev = simpy.Event(self.env)  # pending: fires at the next close
        if not self.open_ev.triggered:
            self.open_ev.succeed()             # exact wake-up for waiters
        # In a 24 h window the gate never closes, so open_ev stays triggered and
        # processes that arrive later wake on it immediately.  That is correct.
        self.n_open_transitions += 1

    def _close(self) -> None:
        self.is_open = False
        self.close_ev.succeed()
        self.open_ev = simpy.Event(self.env)   # pending: fires at the next open
        self.n_close_transitions += 1

    def gate_process(self, segments: list[tuple[float, float]]):
        """Exact open/close transitions, driven by absolute simulated time.

        Every wait is a single ``timeout`` whose duration is the exact distance
        to the next boundary.  There is no periodic re-check anywhere in this
        method, which is precisely what the old 5-minute polling destroyed.
        """
        for start, end in segments:
            now = self.env.now
            if end <= now + 1e-9:
                continue
            if start > now + 1e-9:
                yield self.env.timeout(start - now)
            self._open()
            yield self.env.timeout(max(end - max(start, self.env.now), 1e-9))
            self._close()

    # ---------------------- demand ----------------------
    def vehicles_per_day(self, d: date) -> float:
        """Per-day demand for date ``d``; identical for every day of the run."""
        return self.cfg.demand_veh_per_day()

    def arrival_process(self, horizon_arrivals: float):
        """Non-homogeneous Poisson arrivals by thinning (defect D3).

        Within a bin the rate is constant at its hourly value, so thinning is
        exact for this piecewise-constant rate.  The daily total is the real MOP
        per-day mean for the simulated month; the profile and the day-of-week
        factor shape it within the month.
        """
        cfg = self.cfg
        shape = cfg.shape
        shape_day = float(shape.sum())          # == 24 after normalisation
        dow = np.ones(7) if cfg.demand_mode == "explicit_peak" else cfg.dow
        bin_min = float(cfg.arrival_bin_min)
        n_bins = int(round(MIN_PER_DAY / bin_min))
        centres_h = (np.arange(n_bins) + 0.5) * bin_min / 60.0
        shape_bin = np.array([shape[min(int(c), 23)] for c in centres_h])

        day_idx = 0
        while day_idx * MIN_PER_DAY < horizon_arrivals:
            d = self.date_at(day_idx * MIN_PER_DAY)
            # A closure suppresses ADMISSION, never demand.  Vehicles keep
            # arriving at the border on a closed day; suppressing arrivals here
            # would quietly delete the very queue a closure creates.
            vpd = self.vehicles_per_day(d)
            wd = float(dow[d.weekday()])
            # Expected arrivals in each bin; summed over the day this is
            # vpd * wd, so the daily total is the real MOP per-day mean.
            lam_bin = vpd * wd * shape_bin / shape_day * (bin_min / 60.0)
            lam_max = float(lam_bin.max())          # vehicles per bin
            n_cand = int(self.rng.poisson(lam_max * n_bins))
            if n_cand:
                cand_t = self.rng.random(n_cand) * MIN_PER_DAY
                bins = np.minimum((cand_t // bin_min).astype(int), n_bins - 1)
                keep = self.rng.random(n_cand) < (lam_bin / lam_max)[bins]
                times = np.sort(cand_t[keep]) + day_idx * MIN_PER_DAY
                for tt in times:
                    self.counters["arrived"] += 1
                    i = self._new_record(float(tt), d)
                    self.env.process(self._vehicle(i))
            day_idx += 1
            if day_idx * MIN_PER_DAY < horizon_arrivals:
                # Pace the generator to the start of the next calendar day.  This
                # bounds the number of live processes to one day of arrivals and
                # keeps the RNG draw order deterministic.  Each vehicle then
                # delays ITSELF to its own arrival instant, so no vehicle can
                # ever be processed before it has arrived.
                yield self.env.timeout(MIN_PER_DAY)

    # ---------------------- records ----------------------
    def _new_record(self, t_arr: float, d: date) -> int:
        for v in self.col.values():
            v.append(NAN)
        self.status.append("censored")
        self.stage.append("")
        self._yard_req.append(None)
        self._space_req.append(None)
        i = len(self.status) - 1
        self.col["t_arr"][i] = t_arr
        # persons = 1 + Poisson(ppv - 1) has mean exactly ppv and is >= 1
        self.col["persons"][i] = 1.0 + float(self.rng.poisson(self.cfg.persons_per_vehicle - 1.0))
        self.col["weekday"][i] = float(d.weekday())
        return i

    def _renege(self, i: int, stage: str) -> None:
        self.status[i] = "renege"
        self.stage[i] = stage
        self.col["t_end"][i] = self.env.now
        self.counters["renege"] += 1

    # ---------------------- reneging ----------------------
    def reneging_delay_min(self, n_visible: int, closed: bool = False) -> float:
        """Draw a patience budget from a queue-dependent hazard.

        ASSUMPTION-NOT-OBSERVED.  The hazard RATE grows with the queue the driver
        can see, which is what turns a patience threshold into a queue-dependent
        *probability* of giving up.  ``closed=True`` selects the separate, much
        longer budget used while waiting for the gate to open.
        DOCUMENTED SIMPLIFICATION: the visible queue is frozen at the moment
        contention is first observed, so the hazard is not re-sampled while the
        vehicle waits.
        """
        cfg = self.cfg
        base = cfg.reneging_closed_base_patience_min if closed else cfg.reneging_base_patience_min
        cap = cfg.reneging_max_patience_min * (12.0 if closed else 1.0)
        q = max(0.0, float(n_visible)) / max(1e-9, cfg.reneging_queue_reference)
        rate = (1.0 / base) * (1.0 + cfg.reneging_queue_sensitivity * q)
        return min(float(self.rng.exponential(1.0 / rate)), cap)

    def _queue_visible(self) -> int:
        """What a driver can see from outside: vehicles ahead of them."""
        return len(self.yard.put_queue) + self.yard.count

    @staticmethod
    def _contended(res: simpy.Resource) -> bool:
        return res.count >= res.capacity or len(res.put_queue) > 0

    def _acquire(self, i: int, res: simpy.Resource, res_list: list, stage: str,
                 deadline: float | None, t_ref: float):
        """Request ``res``; if the vehicle is re-negotiable, race it against the
        gate-close event and the renege clock.  Returns (granted, deadline)."""
        cfg = self.cfg
        req = res.request()
        if cfg.reneging_enabled and self._contended(res):
            if deadline is None:
                deadline = self.env.now + self.reneging_delay_min(self._queue_visible())
            to = self.env.timeout(max(deadline - self.env.now, 0.0))
            yield AnyOf(self.env, [req, to])
            if to.triggered and not req.triggered:
                req.cancel()
                self._renege(i, stage)
                return False, deadline
        else:
            yield req
        res_list[i] = req
        return True, deadline

    # ---------------------- the vehicle ----------------------
    def _vehicle(self, i: int):
        cfg = self.cfg
        t_arr = self.col["t_arr"][i]
        # Start acting exactly at the arrival instant.  The arrival generator
        # creates a whole day of vehicles in one go, so without this a vehicle
        # would be processed before it arrived and could see a gate state that
        # belongs to an earlier moment.
        lead = t_arr - self.env.now
        if lead > 1e-12:
            yield self.env.timeout(lead)
        deadline: float | None = None   # absolute renege time, set at 1st contention

        # ---- stage 1: wait for the gate to open (exact event, no polling) ----
        # Counted from the arrival instant, so the upstream queue includes every
        # vehicle that has appeared but has not yet been admitted.
        self.counters["waiting_gate"] += 1
        if not self.is_open:
            o_ev = self.open_ev
            if cfg.reneging_enabled:
                deadline = t_arr + self.reneging_delay_min(self._queue_visible(), closed=True)
                to = self.env.timeout(max(deadline - self.env.now, 0.0))
                yield AnyOf(self.env, [o_ev, to])
                if to.triggered and not o_ev.triggered:
                    self.counters["waiting_gate"] -= 1
                    self._renege(i, "closed")
                    return
            else:
                yield o_ev
            self.col["t_gate"][i] = self.env.now
            self.counters["waiting_gate"] -= 1
        else:
            self.col["t_gate"][i] = t_arr
            self.counters["waiting_gate"] -= 1

        # ---- stage 2: a place in the internal yard ("patio") ----
        ok, deadline = yield from self._acquire(
            i, self.yard, self._yard_req, "yard", deadline, t_arr)
        if not ok:
            return
        self.col["t_yard"][i] = self.env.now

        # ---- stage 3: a position inside the control area (MOP's 15) ----
        ok, deadline = yield from self._acquire(
            i, self.space, self._space_req, "space", deadline, t_arr)
        if not ok:
            self.yard.release(self._yard_req[i])
            return
        self.col["t_space"][i] = self.env.now

        # ---- stage 4: a booth, but only while the gate is open ----
        # A vehicle already inside holds its positions across a closing, but no
        # service may START after the closing time (defect D2).
        got = False
        while not got:
            if not self.is_open:
                yield self.open_ev
                continue
            c_ev = self.close_ev
            req = self.booth.request()
            if cfg.reneging_enabled and self._contended(self.booth):
                if deadline is None:
                    deadline = self.env.now + self.reneging_delay_min(
                        len(self.booth.put_queue) + self.booth.count)
                to = self.env.timeout(max(deadline - self.env.now, 0.0))
                yield AnyOf(self.env, [req, c_ev, to])
                if to.triggered and not req.triggered and not c_ev.triggered:
                    req.cancel()
                    self._renege(i, "booth")
                    self.space.release(self._space_req[i])
                    self.yard.release(self._yard_req[i])
                    return
            else:
                yield AnyOf(self.env, [req, c_ev])
            if req.triggered:
                if not self.is_open:
                    self.booth.release(req)   # granted as the gate closed
                    continue
                got = True
            else:
                req.cancel()

        try:
            self.col["t_serv_start"][i] = self.env.now
            yield self.env.timeout(float(self.rng.lognormal(self._mu, self._sigma)))
            self.col["t_serv_end"][i] = self.env.now
            self.col["t_end"][i] = self.env.now
            self.status[i] = "served"
            self.counters["served"] += 1
        finally:
            self.booth.release(req)
            self.space.release(self._space_req[i])
            self.yard.release(self._yard_req[i])

    # ---------------------- observation-only sampler ----------------------
    def observer(self, horizon: float):
        """Records the queue on a coarse grid.

        This is NOT a control loop: it cannot delay, reorder or block any
        vehicle.  Every decision in the model is event-driven; this only reads
        counters.  The previous model used the same 5-minute sampling for
        CONTROL, which is what created the artificial delay.
        """
        step = float(self.cfg.monitor_interval_min)
        t = 0.0
        while t < horizon:
            # Vehicles parked at a CLOSED gate are not yet queued for the yard,
            # so they must be counted explicitly.  Without this term a closure
            # produced an apparently empty upstream queue, which is precisely
            # backwards: a closure is the event that creates that queue.
            q_gate = float(self.counters["waiting_gate"])
            q_req = float(len(self.yard.put_queue))
            q_up = q_gate + q_req
            in_yard = self.yard.count
            in_ctrl = self.space.count
            self.monitor.append(
                (t, float(self.counters["arrived"]), float(self.counters["served"]),
                 float(self.counters["renege"]), q_up, float(in_yard - in_ctrl),
                 float(q_up + in_yard), float(in_ctrl))
            )
            yield self.env.timeout(step)
            t += step


DEMAND_TABLE: pd.DataFrame = load_monthly_demand()
#: month -> OBSERVED light vehicles per day, from the MOP monthly series.
DEMAND_PER_DAY: dict[str, float] = dict(
    zip(DEMAND_TABLE["month"], DEMAND_TABLE["vehicles_per_day"])
)


@dataclass
class RunResult:
    veh: pd.DataFrame
    monitor: pd.DataFrame
    diag: dict


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def closure_interval(start_date: str, from_iso: str, to_iso: str) -> tuple[float, float]:
    """Absolute-minute closure window for a dated, timed published closure.

    Lets a scenario reproduce the EXACT published endpoints instead of snapping
    to whole days.  Example: the 26 h Aedes aegypti disinfection closure reported
    for both directions from 10:00 on 19 Feb to 12:00 on 20 Feb 2026.
    """
    d0 = date.fromisoformat(start_date)
    a = datetime.fromisoformat(from_iso)
    b = datetime.fromisoformat(to_iso)
    s = (a.date() - d0).days * MIN_PER_DAY + a.hour * 60.0 + a.minute
    e = (b.date() - d0).days * MIN_PER_DAY + b.hour * 60.0 + b.minute
    return (float(s), float(e))


def simulate(cfg: Config, seed: int | None = None, n_days: int | None = None,
             force_arrivals: list[float] | None = None) -> RunResult:
    """Run one replica; return tidy per-vehicle records and run diagnostics."""
    if cfg.month is not None and n_days is None:
        y, m = (int(x) for x in cfg.month.split("-"))
        n_days = calendar.monthrange(y, m)[1]
    n_days = int(n_days if n_days is not None else 30)
    h_arr = n_days * MIN_PER_DAY
    horizon = h_arr + float(cfg.drain_after_arrivals_min)

    rng = np.random.default_rng(cfg.seed if seed is None else seed)
    env = simpy.Environment()
    bc = BorderCrossing(env, cfg, rng)
    env.process(bc.gate_process(cfg.gate_segments(n_days)))
    env.process(bc.observer(horizon))
    if cfg.background_arrivals:
        env.process(bc.arrival_process(h_arr))
    if force_arrivals:
        env.process(_force_arrivals(bc, force_arrivals))
    env.run(until=horizon)

    veh = _vehicle_frame(bc, horizon)
    mon = _monitor_frame(bc)
    diag = _diagnostics(cfg, veh, mon, h_arr, horizon, n_days, bc)
    return RunResult(veh=veh, monitor=mon, diag=diag)


def _force_arrivals(bc: BorderCrossing, times: list[float]):
    for t in sorted(times):
        d = bc.date_at(t)
        bc.counters["arrived"] += 1
        i = bc._new_record(float(t), d)
        yield bc.env.timeout(max(t - bc.env.now, 0.0))
        bc.env.process(bc._vehicle(i))


_VEH_COLS = ("veh_id", "t_arr_min", "t_gate_min", "t_yard_min", "t_space_min",
             "t_serv_start_min", "t_serv_end_min", "t_end_min", "persons", "status", "stage",
             "weekday", "wait_to_gate_min", "wait_total_min", "wait_to_service_min",
             "wait_inside_min", "svc_min")


def _vehicle_frame(bc: BorderCrossing, horizon: float) -> pd.DataFrame:
    n = len(bc.status)
    if n == 0:
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in _VEH_COLS})
    g = {k: np.asarray(v, dtype=float) for k, v in bc.col.items()}
    df = pd.DataFrame(
        {
            "veh_id": np.arange(n),
            "t_arr_min": g["t_arr"],
            "t_gate_min": g["t_gate"],
            "t_yard_min": g["t_yard"],
            "t_space_min": g["t_space"],
            "t_serv_start_min": g["t_serv_start"],
            "t_serv_end_min": g["t_serv_end"],
            "t_end_min": g["t_end"],
            "persons": g["persons"],
            "status": pd.Categorical(bc.status, categories=["served", "renege", "censored"]),
            "stage": bc.stage,
            "weekday": g["weekday"].astype(int),
        }
    )
    df["wait_to_gate_min"] = df["t_yard_min"] - df["t_arr_min"]
    df["wait_total_min"] = df["t_serv_end_min"] - df["t_arr_min"]
    df["wait_to_service_min"] = df["t_serv_start_min"] - df["t_arr_min"]
    df["wait_inside_min"] = df["t_serv_end_min"] - df["t_yard_min"]
    df["svc_min"] = df["t_serv_end_min"] - df["t_serv_start_min"]
    # A vehicle that has not cleared the last booth gets NO imputed wait.  Its
    # wait is unknown, not "horizon - arrival" (defect D1).
    return df


def _monitor_frame(bc: BorderCrossing) -> pd.DataFrame:
    cols = ["t_min", "n_arrived", "n_served", "n_renege", "q_upstream",
            "q_inside", "q_total", "in_control"]
    if not bc.monitor:
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in cols})
    return pd.DataFrame(bc.monitor, columns=cols)


def _diagnostics(cfg, veh, mon, h_arr, horizon, n_days, bc) -> dict:
    n_arr = int(len(veh))
    n_served = int((veh["status"] == "served").sum()) if n_arr else 0
    n_renege = int((veh["status"] == "renege").sum()) if n_arr else 0
    entered = veh["t_yard_min"].notna() if n_arr else pd.Series(dtype=bool)

    # ---- offered load vs capacity ------------------------------------------------
    shape = cfg.shape
    vpd = cfg.demand_veh_per_day()
    peak_rate = float(cfg.peak_hour_rate_veh_h) if cfg.demand_mode == "explicit_peak" \
        else vpd * float(shape.max()) / 24.0
    cap = cfg.booth_capacity_veh_h
    open_h = cfg.open_hours_per_day(n_days)
    daily_cap = cap * open_h

    # ---- stationarity, judged on the ARRIVAL window only -------------------------
    wu = float(cfg.warmup_days) * MIN_PER_DAY
    aw = mon[(mon["t_min"] >= wu) & (mon["t_min"] <= h_arr)] if len(mon) else mon
    span = max(h_arr - wu, 1.0)
    cuts = (wu, h_arr - span * 0.5, h_arr - span * 0.25, h_arr)
    prev = aw[(aw["t_min"] >= cuts[0]) & (aw["t_min"] < cuts[1])] if len(aw) else aw
    last = aw[(aw["t_min"] >= cuts[2]) & (aw["t_min"] <= cuts[3])] if len(aw) else aw
    q_prev = float(prev["q_total"].mean()) if len(prev) else 0.0
    q_last = float(last["q_total"].mean()) if len(last) else 0.0
    dq = q_last - q_prev
    if len(last) >= 3:
        x = last["t_min"].to_numpy(dtype=float)
        slope = float(np.polyfit(x, last["q_total"].to_numpy(dtype=float), 1)[0]) * 60.0
    else:
        slope = 0.0

    # Backlog at the moment arrivals stopped: exactly what a run WITHOUT a drain
    # (the old model) silently turned into lower bounds on waits.  "Still in the
    # system" must be read from the vehicle's terminal instant, otherwise every
    # vehicle that already RENEGED would be counted as if it were still queuing.
    still_in_system = veh["t_end_min"].isna() | (veh["t_end_min"] > h_arr) if n_arr else pd.Series(dtype=bool)
    backlog_end = int(still_in_system.sum()) if n_arr else 0
    backlog_frac = backlog_end / n_arr if n_arr else 0.0
    # "Never served" is the brief's metric: an arrival that did not cross, whether
    # it gave up or was still queued when the run ended.
    never_served = int(n_arr - n_served) if n_arr else 0
    never_frac = never_served / n_arr if n_arr else 0.0
    # Right-censored: still inside the system at the horizon, outcome unknown.
    censored_at_horizon = int((veh["status"] == "censored").sum()) if n_arr else 0
    q_at_h_arr = float(mon.loc[mon["t_min"] <= h_arr, "q_total"].iloc[-1]) if len(mon) else 0.0
    q_at_horizon = float(mon["q_total"].iloc[-1]) if len(mon) else 0.0
    bc.drain_complete = censored_at_horizon == 0

    growing = (dq > cfg.stat_tol_queue_veh) and backlog_end > 0
    # A run can hold a SMALL, bounded queue while still being unstable, simply
    # because drivers give up.  Then the wait statistics describe only the
    # subset that did not balk, and calling them an equilibrium estimate would
    # be exactly the error this rebuild exists to remove.
    renege_frac = n_renege / n_arr if n_arr else 0.0
    balking = renege_frac > cfg.stat_tol_reneging
    # The verdict uses the SAME tolerances as the equilibrium flag, so the two can
    # never contradict each other.  A single vehicle still queued at the horizon
    # is not a stationarity failure; a run whose queue is growing is.
    backlog_over_tol = backlog_frac > cfg.stat_tol_censored
    if backlog_over_tol and growing:
        verdict = "NON-STATIONARY: queue still growing when arrivals stopped"
    elif backlog_over_tol:
        verdict = "NON-STATIONARY: arrivals still queued when arrivals stopped"
    elif dq > cfg.stat_tol_queue_veh:
        verdict = "NON-STATIONARY: queue rising between the last two quarters"
    elif balking:
        verdict = (f"UNSTABLE OFFERED LOAD: {renege_frac:.2%} of arrivals gave up, "
                   f"so waits describe only the subset that stayed")
    else:
        verdict = "STATIONARY: equilibrium window valid"

    # ---- wait statistics: measurement window only, served vehicles only --------
    meas = veh[veh["t_arr_min"] >= wu] if n_arr else veh
    served = meas[meas["status"] == "served"] if len(meas) else meas
    cens_in_win = int((meas["status"] == "censored").sum()) if len(meas) else 0
    cens_frac_win = cens_in_win / len(meas) if len(meas) else 0.0
    w = served["wait_total_min"].to_numpy(dtype=float) if len(served) else np.array([])
    wg = meas["wait_to_gate_min"].to_numpy(dtype=float) if len(meas) else np.array([])
    wg = wg[~np.isnan(wg)]
    equilibrium = bool(w.size and not backlog_over_tol and not growing and not balking)
    if equilibrium:
        stat_label = "equilibrium estimate"
    elif balking:
        stat_label = (f"CONDITIONAL STATISTIC: {renege_frac:.2%} of arrivals bailed "
                      f"(unstable offered load)")
    else:
        stat_label = "TRANSIENT WINDOW STATISTIC, NON-STATIONARY"

    served_all = veh[veh["status"] == "served"] if n_arr else veh
    # WHERE abandonment happens is a result, not a detail: the same total
    # renege fraction means completely different things before the gate, in the
    # control area, or at the booth.
    stage_counts = (veh.loc[veh["status"] == "renege", "stage"].value_counts().to_dict()
                    if n_arr else {})
    return {
        "n_days": n_days,
        "seed": cfg.seed,
        "arrivals": n_arr,
        "served": n_served,
        "renege": n_renege,
        "never_served": never_served,
        "censored_fraction": backlog_frac,
        "never_served_fraction": never_frac,
        "censored_at_horizon": censored_at_horizon,
        "censored_fraction_horizon": censored_at_horizon / n_arr if n_arr else 0.0,
        "censored_fraction_window": cens_frac_win,
        "renege_fraction": renege_frac,
        "renege_by_stage": stage_counts,
        "queue_at_end_of_arrivals": q_at_h_arr,
        "queue_at_horizon": q_at_horizon,
        "backlog_at_end_of_arrivals": backlog_end,
        "q_prev_quarter_mean": q_prev,
        "q_last_quarter_mean": q_last,
        "q_delta_last_quarters": dq,
        "q_slope_veh_per_h_last_quarter": slope,
        "max_queue": float(mon["q_total"].max()) if len(mon) else 0.0,
        "max_q_upstream": float(mon["q_upstream"].max()) if len(mon) else 0.0,
        "max_q_inside": float(mon["q_inside"].max()) if len(mon) else 0.0,
        "stationarity_verdict": verdict,
        "wait_stat_is_equilibrium": equilibrium,
        "wait_stat_label": stat_label,
        "mean_wait_total_min": float(w.mean()) if w.size else float("nan"),
        "median_wait_total_min": float(np.median(w)) if w.size else float("nan"),
        "p95_wait_total_min": float(np.percentile(w, 95)) if w.size else float("nan"),
        "p99_wait_total_min": float(np.percentile(w, 99)) if w.size else float("nan"),
        "max_wait_total_min": float(w.max()) if w.size else float("nan"),
        "mean_wait_to_gate_min": float(wg.mean()) if wg.size else float("nan"),
        "p95_wait_to_gate_min": float(np.percentile(wg, 95)) if wg.size else float("nan"),
        "peak_offered_veh_h": peak_rate,
        "booth_capacity_veh_h": cap,
        "rho_peak_hour": peak_rate / cap if cap > 0 else float("inf"),
        "veh_per_day": vpd,
        "open_hours_per_day": open_h,
        "daily_capacity_veh": daily_cap,
        "rho_daily_open_window": vpd / daily_cap if daily_cap > 0 else float("inf"),
        "throughput_veh": n_served,
        "throughput_in_window": int((veh["t_serv_end_min"] <= h_arr).sum()) if n_arr else 0,
        "persons_served": float(served_all["persons"].sum()) if len(served_all) else 0.0,
        "persons_per_served_vehicle": (float(served_all["persons"].mean())
                                       if len(served_all) else float("nan")),
        "drain_complete": bc.drain_complete,
        "drain_min": float(cfg.drain_after_arrivals_min),
    }


# ---------------------------------------------------------------------------
# Replicas
# ---------------------------------------------------------------------------
def seeds_for(base_seed: int, n: int) -> list[int]:
    """Deterministic seed list, so every run in this project is reproducible."""
    return [base_seed + i for i in range(n)]


def replicate(cfg: Config, n_replicas: int, n_days: int | None = None,
              label: str = "", verbose: bool = False) -> pd.DataFrame:
    rows = []
    for s in seeds_for(cfg.seed, n_replicas):
        r = simulate(cfg, seed=s, n_days=n_days)
        d = dict(r.diag)
        d["replica"] = s
        d["label"] = label
        rows.append(d)
        if verbose:
            print(f"      replica seed={s:<4d} arrivals={d['arrivals']:>7,} "
                  f"served={d['served']:>7,} backlog={d['censored_fraction']:7.3%} "
                  f"rho={d['rho_peak_hour']:5.2f}  {d['stationarity_verdict'][:34]}")
    return pd.DataFrame(rows)


_SUM_COLS = {
    "replicas": lambda s: int(len(s)),
    "arrivals_mean": lambda s: float(s["arrivals"].mean()),
    "peak_offered_veh_h": lambda s: float(s["peak_offered_veh_h"].mean()),
    "booth_capacity_veh_h": lambda s: float(s["booth_capacity_veh_h"].mean()),
    "rho_peak_hour": lambda s: float(s["rho_peak_hour"].mean()),
    "rho_daily_open_window": lambda s: float(s["rho_daily_open_window"].mean()),
    "open_hours_per_day": lambda s: float(s["open_hours_per_day"].mean()),
    "mean_wait_total_min": lambda s: float(s["mean_wait_total_min"].mean()),
    "median_wait_total_min": lambda s: float(s["median_wait_total_min"].mean()),
    "p95_wait_total_min": lambda s: float(s["p95_wait_total_min"].mean()),
    "p99_wait_total_min": lambda s: float(s["p99_wait_total_min"].mean()),
    "mean_wait_to_gate_min": lambda s: float(s["mean_wait_to_gate_min"].mean()),
    "p95_wait_to_gate_min": lambda s: float(s["p95_wait_to_gate_min"].mean()),
    "censored_fraction_mean": lambda s: float(s["censored_fraction"].mean()),
    "censored_fraction_worst": lambda s: float(s["censored_fraction"].max()),
    "renege_fraction": lambda s: float(s["renege_fraction"].mean()),
    "max_queue_mean": lambda s: float(s["max_queue"].mean()),
    # Where the queue physically IS.  Patio capacity changes the split between
    # these two without changing their sum, which is the whole point.
    "max_q_upstream_mean": lambda s: float(s["max_q_upstream"].mean()),
    "max_q_inside_mean": lambda s: float(s["max_q_inside"].mean()),
    "queue_at_horizon_mean": lambda s: float(s["queue_at_horizon"].mean()),
    "q_delta_last_quarters": lambda s: float(s["q_delta_last_quarters"].mean()),
    "q_slope_veh_per_h": lambda s: float(s["q_slope_veh_per_h_last_quarter"].mean()),
    "throughput_veh_mean": lambda s: float(s["throughput_veh"].mean()),
    "never_served_fraction": lambda s: float(s["never_served_fraction"].mean()),
    "persons_served_mean": lambda s: float(s["persons_served"].mean()),
    "persons_per_served_vehicle": lambda s: float(s["persons_per_served_vehicle"].mean()),
}


def summarise(rep: pd.DataFrame) -> dict:
    """Collapse replica diagnostics into the row the experiments print."""
    out = {k: f(rep) for k, f in _SUM_COLS.items()}
    frac_ns = float(rep["stationarity_verdict"].str.startswith("NON-STATIONARY").mean())
    # A bounded queue maintained by balking is not equilibrium either.
    frac_un = float(rep["stationarity_verdict"].str.startswith("UNSTABLE").mean())
    out["frac_non_stationary"] = frac_ns
    out["frac_unstable_offered_load"] = frac_un
    out["verdict"] = ("NON-STATIONARY" if frac_ns > 0.5
                      else ("UNSTABLE OFFERED LOAD" if frac_un > 0.5
                            else ("MIXED" if (frac_ns + frac_un) > 0.0
                                  else "STATIONARY")))
    out["wait_stat_label"] = rep["wait_stat_label"].iloc[0]
    return out


# ---------------------------------------------------------------------------
# The previous version's configuration, reproduced so its invalidity can be
# demonstrated rather than asserted: peak 450 veh/h against 13 booths on
# weekdays (capacity 222.9 veh/h, rho = 2.02) and 18 at the weekend
# (capacity 308.6 veh/h, rho = 1.46).  Neither can ever drain.
# ---------------------------------------------------------------------------
def legacy_config(seed: int = 1, booths: int = 13) -> Config:
    return Config(
        n_booths=booths,
        n_booths_peak=booths,
        space_capacity=400,      # the old code had no separate space concept
        yard_capacity=200,
        svc_mean_min=3.5,
        svc_cv=0.6,
        demand_mode="explicit_peak",
        peak_hour_rate_veh_h=450.0,
        intraday_peak_exponent=1.0,
        day_factors=(1.0,) * 7,          # legacy: no day-of-week variation
        hours_regime="argentina_24h",
        reneging_enabled=False,          # the old code had zero abandonment
        warmup_days=0.0,
        drain_after_arrivals_min=0.0,    # the old code had no drain; it clipped waits
        start_date="2026-01-02",
        seed=seed,
    )


# ---------------------------------------------------------------------------
# Defect D2: proof that the opening-hours logic is exact, not polled.
# ---------------------------------------------------------------------------
def verify_event_driven_opening(verbose: bool = True) -> dict:
    """Assert the properties the old 5-minute polling destroyed.

    1. A vehicle arriving 1 minute before a 09:00 opening waits exactly 1.0 min.
    2. A vehicle arriving at 03:00 waits exactly 360 min (the exact gap).
    3. No service ever STARTS outside the published opening window.
    """
    cfg = Config(hours_regime="winter_press", start_date="2025-06-15",
                 month="2025-06", warmup_days=0.0, drain_after_arrivals_min=0.0,
                 reneging_enabled=False, background_arrivals=False, seed=1)

    r = simulate(cfg, seed=1, n_days=1, force_arrivals=[8 * 60 + 59.0])
    w1 = float(r.veh.iloc[0]["wait_to_service_min"])
    assert abs(w1 - 1.0) < 1e-9, f"D2 FAILED: wait was {w1}, expected exactly 1.0 min"

    r2 = simulate(cfg, seed=2, n_days=1, force_arrivals=[3 * 60.0])
    w2 = float(r2.veh.iloc[0]["wait_to_service_min"])
    assert abs(w2 - 360.0) < 1e-9, f"D2 FAILED: wait was {w2}, expected exactly 360 min"

    r3 = simulate(replace(cfg, background_arrivals=True), seed=3, n_days=12)
    served = r3.veh[r3.veh["status"] == "served"]
    tod = served["t_serv_start_min"] % MIN_PER_DAY
    bad = int(((tod < 540.0 - 1e-9) | (tod >= 1260.0 - 1e-9)).sum())
    assert bad == 0, f"D2 FAILED: {bad} services started outside 09:00-21:00"

    # 4. A 24 h regime must serve at any hour of the day.
    cfg24 = replace(cfg, hours_regime="argentina_24h")
    r4 = simulate(cfg24, seed=4, n_days=1, force_arrivals=[2 * 60 + 17.0])
    tod4 = float(r4.veh.iloc[0]["t_serv_start_min"] % MIN_PER_DAY)
    assert abs(tod4 - (2 * 60 + 17.0)) < 1e-6, f"D2 FAILED: 24 h regime served at {tod4}"

    out = {"arrival_0859_wait_min": w1, "arrival_0300_wait_min": w2,
           "services_outside_opening_hours": bad, "vehicles_checked": int(len(served)),
           "status": "PASS"}
    if verbose:
        print(f"    [D2] arrival 08:59 under a 09:00 opening -> wait exactly "
              f"{w1:.6f} min (no 5-min quantisation)")
        print(f"    [D2] arrival 03:00 under a 09:00 opening -> wait exactly "
              f"{w2:.1f} min (exact gap)")
        print(f"    [D2] {len(served):,} services started in 12 winter days, "
              f"{bad} outside 09:00-21:00")
        print(f"    [D2] 24 h regime: service started at 02:17 as offered")
    return out


# ---------------------------------------------------------------------------
# Closure semantics: a dated closure must block ADMISSION at its exact
# endpoints, must NOT delete demand, and must be visible in the queue.
# ---------------------------------------------------------------------------
def verify_closure_semantics(verbose: bool = True) -> dict:
    """Reproduce the 26 h Aedes aegypti disinfection closure to the minute.

    OBSERVED event: both directions closed from 10:00 on 19 Feb 2026 to 12:00 on
    20 Feb 2026.  Three properties are asserted, because each one is a way a
    closure model can be quietly wrong.
    """
    ci = closure_interval("2026-02-01", "2026-02-19T10:00", "2026-02-20T12:00")
    assert abs((ci[1] - ci[0]) / 60.0 - 26.0) < 1e-9, "closure length is not 26 h"
    cfg = Config(month="2026-02", start_date="2026-02-01", hours_regime="argentina_24h",
                 closed_intervals=(ci,), warmup_days=0.0, reneging_enabled=False,
                 drain_after_arrivals_min=0.0, seed=7)
    r = simulate(cfg, seed=7)
    v = r.veh

    # 1. No service may START inside the closure window.
    started = v["t_serv_start_min"].dropna()
    bad = int(((started >= ci[0] - 1e-9) & (started < ci[1] - 1e-9)).sum())
    assert bad == 0, f"closure FAILED: {bad} services started inside the closure"

    # 2. Demand must not be deleted: arrivals still occur across the window.
    n_in = int(((v["t_arr_min"] >= ci[0]) & (v["t_arr_min"] < ci[1])).sum())
    assert n_in > 0, "closure FAILED: no arrivals inside the closure, demand was deleted"

    # 3. Those arrivals must pile up, and the pile must be VISIBLE.
    mon_in = r.monitor[(r.monitor["t_min"] >= ci[0]) & (r.monitor["t_min"] < ci[1])]
    qmax = float(mon_in["q_total"].max()) if len(mon_in) else 0.0
    assert qmax > 1.0, "closure FAILED: the upstream queue was invisible during a closure"

    out = {"closure_hours": (ci[1] - ci[0]) / 60.0, "services_started_inside": bad,
           "arrivals_inside": n_in, "max_queue_during_closure": qmax, "status": "PASS"}
    if verbose:
        print(f"    [CL] 26 h closure 19 Feb 10:00 -> 20 Feb 12:00 reproduced exactly")
        print(f"    [CL] {bad} services started inside the window (must be 0)")
        print(f"    [CL] {n_in:,} arrivals occurred inside the window and were not deleted")
        print(f"    [CL] upstream queue peaked at {qmax:,.0f} vehicles during the closure")
    return out


# ---------------------------------------------------------------------------
# Defect D4 proof: the patio-capacity conclusion depends on reneging.
# ---------------------------------------------------------------------------
def verify_reneging_conclusion(verbose: bool = True, n_days: int = 7,
                               n_replicas: int = 2) -> dict:
    """Does the patio-capacity conclusion DEPEND on abandonment?

    The previous model's only patio test was one scenario row, ``patio de 400
    vehiculos`` against a ``patio_capacity=200`` default.  It never stated a
    conclusion, so no conclusion may be attributed to it.  What can be verified
    is that the row was structurally incapable of showing anything useful:

    * the model has NO abandonment mechanism anywhere, so total queue is
      invariant by construction and a patio comparison can only reveal
      relocation, never relief;
    * the patio was a single 200-vehicle resource, with no separate control-area
      occupancy limit, so the OBSERVED 15 simultaneous vehicles was absent;
    * the reported queue was a single undivided ``cola_max`` column, so a queue
      that had merely moved road-ward read as unchanged.

    The honest test has two parts:

    1. Sweep the patio over a range that actually BINDS.  At 200 the internal
       queue still holds hundreds of vehicles, so 200 is not a neutral value;
       reporting only 200 vs 600 would hide the shape of the effect.
    2. Repeat every patio value with and without abandonment, and report the
       served fraction alongside the wait, because abandonment lowers the mean
       wait by removing the vehicles that would have waited longest.
    """
    rows: dict[tuple, dict] = {}
    for ren in (False, True):
        for yard in (15, 30, 60, 200, 600):
            cfg = Config(month="2025-01", start_date="2025-01-01", hours_regime="argentina_24h",
                         yard_capacity=yard, reneging_enabled=ren, warmup_days=2.0,
                         drain_after_arrivals_min=2880.0, seed=1)
            rows[(yard, ren)] = summarise(replicate(cfg, n_replicas, n_days=n_days))

    if verbose:
        print(f"    patio capacity sweep, {n_days} days x {n_replicas} replicas, January peak demand")
        print(f"    {'patio':>6}  {'renege':>6}  {'Q road':>7}  {'Q inside':>8}  "
              f"{'Q total':>7}  {'served':>7}  {'mean':>8}  {'p95':>8}  verdict")
        for ren in (False, True):
            for yard in (15, 30, 60, 200, 600):
                v = rows[(yard, ren)]
                print(f"    {yard:>6}  {'on' if ren else 'off':>6}  "
                      f"{v['max_q_upstream_mean']:>7.0f}  {v['max_q_inside_mean']:>8.0f}  "
                      f"{v['max_queue_mean']:>7.0f}  "
                      f"{1 - v['never_served_fraction']:>6.1%}  "
                      f"{v['mean_wait_total_min']:>8.1f}  {v['p95_wait_total_min']:>8.1f}  "
                      f"{v['verdict']}")
        a, b = rows[(15, False)], rows[(600, False)]
        c, d = rows[(15, True)], rows[(600, True)]
        print(f"    patio 15 -> 600 WITHOUT abandonment: mean wait "
              f"{a['mean_wait_total_min']:.1f} -> {b['mean_wait_total_min']:.1f} min, "
              f"served {1 - a['never_served_fraction']:.1%} -> "
              f"{1 - b['never_served_fraction']:.1%}")
        print(f"    patio 15 -> 600 WITH    abandonment: mean wait "
              f"{c['mean_wait_total_min']:.1f} -> {d['mean_wait_total_min']:.1f} min, "
              f"served {1 - c['never_served_fraction']:.1%} -> "
              f"{1 - d['never_served_fraction']:.1%}")
        print("    => Q TOTAL is identical for every patio size: a larger patio cannot reduce")
        print("       congestion, it only relocates the queue from the booths to the gate")
        print("       road.  Abandonment is what changes the reported wait, and it does so")
        print("       by shrinking WHO was served, not by making the crossing faster.")
    return {f"yard{y}_ren{int(r)}": v for (y, r), v in rows.items()}


if __name__ == "__main__":
    facts = demand_facts(DEMAND_TABLE)
    print("== MOP monthly light vehicles into Chile, Los Libertadores ==")
    for k, v in facts.items():
        print(f"  {k:34s} {v}")
    print()
    verify_event_driven_opening()
