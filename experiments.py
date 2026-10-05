#!/usr/bin/env python
"""Stress tests: utilisation sweep, legacy invalidation, patio, closures.

Five experiments, each aimed at a specific way the previous model was wrong.

E1  Utilisation sweep over a 24 h gate.  Finds where the crossing actually
    saturates, and shows that a bounded queue can be bought with abandonment
    rather than with capacity.
E2  The previous model's own parameters, reproduced verbatim: peak 450 veh/h
    against 13 booths (rho 2.02) and 18 booths (rho 1.46).  Neither can drain.
E3  Patio capacity, with and without abandonment, over a range that BINDS.
E4  Staffing: 7 observed booths vs the 13-booth on-demand complement, and the
    effect of staffing them with fewer than one person each.
E5  Opening-hours regimes and two OBSERVED dated closures, including the 26 h
    Aedes aegypti disinfection closure reproduced to the minute.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import pandas as pd

from .model import (Config, DEMAND_PER_DAY, closure_interval, legacy_config,
                    replicate, summarise, verify_closure_semantics,
                    verify_event_driven_opening, verify_reneging_conclusion)

RESULTS = Path(__file__).resolve().parent / "results"

#: E1 sweep levels.  Chosen to bracket the 120 veh/h capacity of 7 booths
#: (rho = 1 at 120 veh/h) and to go well past it.
SWEEP_LEVELS = (60, 80, 100, 110, 120, 130, 140, 160, 200, 280, 400, 550, 700)
#: 5 days, not fewer: the stationarity test compares the last two quarters of the
#: run, and a 3-day window silently failed to detect divergence at 280 veh/h.
#: Getting this wrong would understate where the crossing actually saturates.
SWEEP_DAYS = 5
SWEEP_REPLICAS = 2

#: E3 patio levels.  200 and 600 are the old defaults and are far above the
#: binding range, which is exactly why the old conclusion was uninformative.
PATIO_LEVELS = (15, 30, 60, 200, 600)
PATIO_DAYS = 4
PATIO_REPLICAS = 2

#: 7 days, not fewer. The stationarity test compares the last two quarters of
#: the run; at 5 days the 18-booth legacy row (rho = 1.46) flipped to MIXED and
#: a headline invalidation verdict became window-length dependent. A validity
#: claim must not depend on how long the probe ran.
SCEN_DAYS = 7
#: E4 only. Its verdicts are driven by balking fractions that are far from the
#: tolerance, so it does not need the same window as E2's invalidation test.
STAFF_DAYS = 5
SCEN_REPLICAS = 2
#: The closure episodes are long runs whose conclusion is a queue of thousands
#: either way, so they get one replica; the short scenarios get two.
LONG_REPLICAS = 1

#: OBSERVED: both directions closed 19 Feb 2026 10:00 -> 20 Feb 2026 12:00 for
#: Aedes aegypti disinfection (26 h).
FEB_CLOSURE = closure_interval("2026-02-01", "2026-02-19T10:00", "2026-02-20T12:00")


def _row(label: str, s: dict, **extra) -> dict:
    return {"label": label, **s, **extra}


def _hdr(rows: list[dict], cols: list[tuple[str, str]]) -> str:
    head = "  ".join(f"{h:>{w}s}" for h, w in cols)
    lines = [head, "    " + "-" * len(head)]
    for r in rows:
        cells = []
        for key, width in cols:
            v = r.get(key, "")
            if isinstance(v, bool):
                cells.append(f"{('on' if v else 'off'):>{width}s}")
            elif isinstance(v, float):
                cells.append(f"{v:>{width}.3f}" if abs(v) < 10 else f"{v:>{width},.1f}")
            else:
                cells.append(f"{str(v):>{width}s}")
        lines.append("    " + "  ".join(cells))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# E1: utilisation sweep
# ---------------------------------------------------------------------------
def utilisation_sweep(days: int = SWEEP_DAYS, replicas: int = SWEEP_REPLICAS,
                      verbose: bool = True) -> pd.DataFrame:
    print()
    print("  E1  UTILISATION SWEEP  (24 h gate, 7 staffed booths, "
          "capacity 120.0 veh/h)")
    print(f"      peak arrival rate swept {min(SWEEP_LEVELS)}..{max(SWEEP_LEVELS)} veh/h, "
          f"{days} days x {replicas} replicas per level")
    rows = []
    for peak in SWEEP_LEVELS:
        cfg = Config(demand_mode="explicit_peak", peak_hour_rate_veh_h=float(peak),
                     hours_regime="argentina_24h", start_date="2025-06-01",
                     warmup_days=1.0, drain_after_arrivals_min=4320.0, seed=1)
        rep = replicate(cfg, replicas, n_days=days)
        s = summarise(rep)
        rows.append(_row(f"peak={peak}", s, peak_rate_veh_h=float(peak)))
    df = pd.DataFrame(rows)
    df.to_csv(RESULTS / "utilisation_sweep.csv", index=False)
    if verbose:
        print(_hdr(rows, [("peak_rate_veh_h", 9), ("rho_peak_hour", 8),
                          ("mean_wait_total_min", 10), ("p95_wait_total_min", 10),
                          ("max_queue_mean", 10), ("never_served_fraction", 10),
                          ("verdict", 24)]))
        crit = _critical_rate(df)
        print(f"    critical peak arrival rate : {crit['critical_rate_veh_h']:.1f} veh/h "
              f"(rho = 1, capacity {crit['capacity_veh_h']:.1f} veh/h)")
        print(f"    last level still a valid equilibrium : {crit['last_equilibrium_peak']} veh/h")
        print(f"    first level where abandonment breaks it : {crit['first_unstable_peak']} veh/h")
        print("    With abandonment ON the queue never diverges: it saturates at a")
        print("    bounded level that giving up maintains.  So 'did the queue diverge'")
        print("    is the wrong saturation test and would have reported a falsely safe")
        print("    result here.  E1b below repeats the probe with abandonment OFF, where")
        print("    true divergence is visible.")
    return df


def divergence_probe(levels=(120, 280, 700), days: int = 4, replicas: int = 1,
                     verbose: bool = True) -> pd.DataFrame:
    """E1b: the same rates with abandonment OFF, where divergence is honest."""
    if verbose:
        print()
        print("  E1b  DIVERGENCE PROBE  (same rates, abandonment OFF)")
    rows = []
    for peak in levels:
        cfg = Config(demand_mode="explicit_peak", peak_hour_rate_veh_h=float(peak),
                     hours_regime="argentina_24h", start_date="2025-06-01",
                     warmup_days=1.0, drain_after_arrivals_min=4320.0,
                     reneging_enabled=False, seed=1)
        rep = replicate(cfg, replicas, n_days=days)
        s = summarise(rep)
        rows.append(_row(f"peak={peak}", s, peak_rate_veh_h=float(peak)))
    df = pd.DataFrame(rows)
    df.to_csv(RESULTS / "divergence_probe.csv", index=False)
    if verbose:
        print(_hdr(rows, [("peak_rate_veh_h", 9), ("rho_peak_hour", 8),
                          ("mean_wait_total_min", 10), ("max_queue_mean", 10),
                          ("frac_non_stationary", 10), ("verdict", 23)]))
        diverged = df[df["frac_non_stationary"] > 0.0]
        if len(diverged):
            print(f"    first rate with a genuinely diverging queue (abandonment off): "
                  f"{int(diverged['peak_rate_veh_h'].min())} veh/h")
        else:
            print("    no divergence detected even with abandonment off at these rates")
        print("    rho = 1 sits at 120 veh/h for 7 staffed booths, so any rate above")
        print("    that MUST build a queue during the peak hour.")
    return df


def _critical_rate(df: pd.DataFrame, balking_tol: float = 0.01) -> dict:
    """Where the crossing stops being able to clear its own arrivals.

    With abandonment ON the queue never DIVERGES: it saturates at a bounded level
    that balking maintains.  So "did the queue diverge" is the wrong test here and
    reports a falsely safe answer.  The honest indicator is the stationarity
    verdict itself: the highest peak rate at which the run is still a valid
    equilibrium, i.e. abandonment stays below tolerance.
    """
    cap = float(df["booth_capacity_veh_h"].iloc[0])
    eq = df[df["verdict"] == "STATIONARY"]
    bad = df[df["verdict"] != "STATIONARY"]
    ns = df[df["frac_non_stationary"] > 0.0]
    return {
        "capacity_veh_h": cap,
        "critical_rate_veh_h": cap,
        "last_equilibrium_peak": int(eq["peak_rate_veh_h"].max()) if len(eq) else None,
        "first_unstable_peak": int(bad["peak_rate_veh_h"].min()) if len(bad) else None,
        "any_diverging_peak": int(ns["peak_rate_veh_h"].min()) if len(ns) else None,
        "balking_tol": balking_tol,
    }


# ---------------------------------------------------------------------------
# E2: the previous model's parameters, reproduced
# ---------------------------------------------------------------------------
def legacy_invalidation(days: int = 7, replicas: int = 2,
                        verbose: bool = True) -> pd.DataFrame:
    print()
    print("  E2  LEGACY CONFIGURATION  (peak 450 veh/h, no abandonment, "
          "no drain, 24 h)")
    print(f"      {days} days x {replicas} replicas.  Both rows carry rho > 1 at the")
    print("      peak hour, so a queue MUST build every day; the question the model")
    print("      answers is whether it ever drains.")
    rows = []
    for booths in (13, 18):
        cfg = legacy_config(booths=booths)
        rep = replicate(cfg, replicas, n_days=days)
        s = summarise(rep)
        rows.append(_row(f"legacy {booths} booths", s, booths=booths,
                         note="peak-hour overload by construction"))
    df = pd.DataFrame(rows)
    # The verdict from the cross-day drift test is NOT what invalidates this
    # configuration, and it must not be made to carry that weight: with no
    # drain, a queue can settle into a REPEATING DAILY CYCLE, which the
    # last-two-quarters drift test correctly reports as having no cross-day
    # trend (MIXED) while every single day still carries a peak-hour overload.
    # A repeating cycle is not an equilibrium. The decisive quantity is
    # rho_peak_hour > 1, so record it as its own flag.
    df["peak_overload"] = df["rho_peak_hour"] > 1.0
    df["equilibrium_wait_valid"] = False
    df.to_csv(RESULTS / "legacy_450.csv", index=False)
    if verbose:
        print(_hdr(rows, [("label", 18), ("booth_capacity_veh_h", 18), ("rho_peak_hour", 9),
                          ("rho_daily_open_window", 20), ("mean_wait_total_min", 17),
                          ("max_queue_mean", 14), ("frac_non_stationary", 17),
                          ("verdict", 18)]))
        print("    rho_peak > 1 in BOTH rows: during the peak hour the arrival rate is")
        print("    2.02x (13 booths) and 1.46x (18 booths) what the booths can clear, so")
        print("    a queue necessarily builds EVERY DAY.  That is the invalidation, and")
        print("    it does not depend on the drift test.")
        print("    Note the drift verdict is WINDOW-SENSITIVE: at 5 days the 18-booth row")
        print("    reports MIXED because the queue settles into a repeating daily cycle")
        print("    and the cross-day test finds no trend. At 7 days it reports")
        print("    NON-STATIONARY. Neither result is a pass: a repeating cycle is not an")
        print("    equilibrium, and rho_peak = 1.46 forces a queue in every peak hour.")
        print("    rho_daily is BELOW 1, so the previous model could still clear the day")
        print("    in aggregate - but only after every driver who queued during the peak")
        print("    had already absorbed the whole delay.  Those waits are a transient of")
        print("    a peak-hour overload, never an equilibrium.  No run with rho_peak > 1")
        print("    may be presented as steady state, and this model refuses to label it")
        print("    as such.")
    return df


# ---------------------------------------------------------------------------
# E3: patio capacity, with and without abandonment
# ---------------------------------------------------------------------------
def patio_sweep(days: int = PATIO_DAYS, replicas: int = PATIO_REPLICAS,
                verbose: bool = True) -> pd.DataFrame:
    print()
    print("  E3  PATIO CAPACITY vs ABANDONMENT  (January peak demand)")
    rows = []
    for ren in (False, True):
        for yard in PATIO_LEVELS:
            cfg = Config(month="2025-01", start_date="2025-01-01",
                         hours_regime="argentina_24h", yard_capacity=yard,
                         reneging_enabled=ren, warmup_days=2.0, seed=1)
            rep = replicate(cfg, replicas, n_days=days)
            s = summarise(rep)
            rows.append(_row(f"patio={yard} reneging={'on' if ren else 'off'}",
                             s, patio=yard, reneging=ren))
    df = pd.DataFrame(rows)
    df.to_csv(RESULTS / "patio_sweep.csv", index=False)
    if verbose:
        print(_hdr(rows, [("patio", 7), ("reneging", 9), ("max_q_upstream_mean", 15),
                          ("max_q_inside_mean", 13), ("max_queue_mean", 12),
                          ("mean_wait_total_min", 13), ("never_served_fraction", 13),
                          ("verdict", 22)]))
        print("    Q total is IDENTICAL across every patio size: a larger patio does")
        print("    not reduce congestion, it relocates the queue from the booths to the")
        print("    gate road.  Abandonment is what changes the reported wait, and it")
        print("    does so by shrinking WHO was served, not by making the crossing")
        print("    faster.  The previous model's only patio test was a single scenario")
        print("    row (200 -> 400 vehicles) in a model that had no abandonment")
        print("    mechanism anywhere, no separate control-area space limit (the")
        print("    observed 15 simultaneous vehicles was absent), and one undivided")
        print("    cola_max column - so a queue that had merely moved road-ward")
        print("    read as unchanged.")
    return df


# ---------------------------------------------------------------------------
# E4: staffing the booths
# ---------------------------------------------------------------------------
def staffing(days: int = STAFF_DAYS, replicas: int = SCEN_REPLICAS,
             verbose: bool = True) -> pd.DataFrame:
    print()
    print("  E4  BOOTH COUNT AND STAFFING  (one person per booth is an ASSUMPTION)")
    rows = []
    cases = [
        ("observed 7 booths", dict(n_booths=7)),
        ("observed 7, one booth unstaffed", dict(n_booths=7, staff_per_booth=6 / 7)),
        ("on-demand complement, 13 staffed", dict(n_booths=13)),
        ("on-demand complement, 12 staffed", dict(n_booths=13, staff_per_booth=12 / 13)),
    ]
    for label, kw in cases:
        cfg = Config(month="2025-01", start_date="2025-01-01",
                     hours_regime="argentina_24h", warmup_days=2.0, seed=1, **kw)
        rep = replicate(cfg, replicas, n_days=days)
        s = summarise(rep)
        rows.append(_row(label, s, staffed=float(cfg.staffed_booths())))
    df = pd.DataFrame(rows)
    df.to_csv(RESULTS / "staffing.csv", index=False)
    if verbose:
        print(_hdr(rows, [("label", 34), ("staffed", 8), ("booth_capacity_veh_h", 18),
                          ("rho_peak_hour", 9), ("mean_wait_total_min", 12),
                          ("never_served_fraction", 13), ("verdict", 23)]))
        print("    The published booth counts are OBSERVED; how many of those booths")
        print("    actually have a person behind them is NOT, and the hours during which")
        print("    the on-demand complement is active are not published either.  The")
        print("    complement is therefore applied STATICALLY here, which is the most")
        print("    favourable reading for it; a time-varying version would help less.")
        print("    One unstaffed booth removes a full 1/7 = 14.3% of capacity, so")
        print("    staffing is not a detail at this crossing.")
    return df


# ---------------------------------------------------------------------------
# E5: opening hours and observed dated closures
# ---------------------------------------------------------------------------
def hours_and_closures(days: int = STAFF_DAYS, replicas: int = SCEN_REPLICAS,
                       verbose: bool = True) -> pd.DataFrame:
    print()
    print("    E5  OPENING-HOURS CONFLICT AND OBSERVED DATED CLOSURES")
    rows = []
    regimes = [("winter_press 09:00-21:00", "winter_press"),
               ("argentina 09:30-20:30", "argentina_0930_2030"),
               ("argentina 24 h", "argentina_24h")]
    for label, regime in regimes:
        cfg = Config(month="2025-06", start_date="2025-06-01", hours_regime=regime,
                     warmup_days=2.0, seed=1)
        rep = replicate(cfg, replicas, n_days=days)
        s = summarise(rep)
        rows.append(_row(label, s, kind="hours regime", detail=regime))

    closure_cases = [
        # n_days is set explicitly: a 34-day closure cannot be simulated inside a
        # 31-day month, and running it in one would report "nobody was ever served"
        # for the trivial reason that the gate never reopened.
        ("Feb 2026, 26 h closure", "2026-02", 28, dict(closed_intervals=(FEB_CLOSURE,))),
        ("Feb 2026, no closure", "2026-02", 28, dict()),
        ("Aug 2026, 27-day closure", "2026-08", 45, dict(extra_closed_days=tuple(range(0, 27)))),
        ("Aug 2026, 34-day closure", "2026-08", 55, dict(extra_closed_days=tuple(range(0, 34)))),
    ]
    # MOP had not published an August 2026 count when the 27- and 34-day closures
    # were reported, so no observed demand exists for those months.  Rather than
    # invent one, the scenarios declare the mean of the last six OBSERVED months
    # as a proxy and say so.  This is an assumption and is labelled as one.
    proxy_vpd = float(sum(DEMAND_PER_DAY[m] for m in
                          ("2025-12", "2026-01", "2026-02", "2026-03", "2026-04",
                           "2026-05")) / 6.0)
    for label, month, ndays, kw in closure_cases:
        # A dated closure is a one-off: a second replica adds cost, not information,
        # and a 27- or 34-day closure queues thousands of vehicles in any replica.
        reps = LONG_REPLICAS
        for ren in (True, False):
            cfg = Config(month=month, start_date=f"{month}-01",
                         demand_mode=("monthly_series" if month in DEMAND_PER_DAY
                                      else "explicit_vpd"),
                         veh_per_day=proxy_vpd, hours_regime="argentina_24h",
                         warmup_days=1.0, reneging_enabled=ren, seed=1, **kw)
            rep = replicate(cfg, reps, n_days=ndays)
            s = summarise(rep)
            rows.append(_row(f"{label}, renege={'on' if ren else 'off'}", s,
                             kind="dated closure", detail=label,
                             demand_source=("MOP observed" if month in DEMAND_PER_DAY
                                            else f"ASSUMPTION proxy {proxy_vpd:.0f}/day")))

    df = pd.DataFrame(rows)
    df.to_csv(RESULTS / "hours_and_closures.csv", index=False)
    if verbose:
        print(_hdr(df[df.kind == "hours regime"].to_dict("records"),
                   [("label", 26), ("open_hours_per_day", 18), ("rho_daily_open_window", 19),
                    ("mean_wait_total_min", 12), ("p95_wait_total_min", 12),
                    ("median_wait_total_min", 13), ("verdict", 22)]))
        print("    A gate that opens ONCE A DAY makes the system PERIODIC, not")
        print("    stationary: the queue is empty at opening and grows overnight, so no")
        print("    single mean wait is valid without saying when in the cycle it was")
        print("    measured.  That is why the restricted regimes are reported")
        print("    NON-STATIONARY even at rho_daily = 0.09..0.20.  The 24 h regime")
        print("    removes the cycle and is the only one that admits an equilibrium")
        print("    label.  The regime is not a modelling preference: the three sources")
        print("    disagree, and all three are carried through to the results.")
        print()
        print(_hdr(df[df.kind == "dated closure"].to_dict("records"),
                   [("label", 36), ("arrivals_mean", 13), ("throughput_veh_mean", 17),
                    ("max_queue_mean", 14), ("median_wait_total_min", 18),
                    ("p99_wait_total_min", 14), ("never_served_fraction", 18),
                    ("verdict", 23)]))
        print("    The MEDIAN matters more than the mean for a closure: a closure hits")
        print("    a minority of arrivals but those it hits wait tens of hours, so the")
        print("    mean is dragged by a cohort the majority never experiences.  All")
        print("    closure waits are conditional on a vehicle actually arriving.")
        print()
        print("    For the multi-week closures the abandonment figures are a property")
        print("    of the ASSUMED patience budget (30-day mean against a published")
        print("    timetable), not a measurement.  The press reports of drivers waiting")
        print("    out a 27 and a 34-day closure are evidence that the real budget is")
        print("    LONGER than 30 days, so the renege=on rows understate patience.")
        print("    The renege=off rows are the physical backlog if nobody gives up.")
    return df


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replicas", type=int, default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    v = not args.quiet
    RESULTS.mkdir(exist_ok=True)
    t0 = time.perf_counter()

    print("=" * 78)
    print("EXPERIMENTS - Paso Cristo Redentor light vehicles")
    print("=" * 78)

    print()
    print("  VERIFICATION")
    print("  " + "-" * 72)
    d2 = verify_event_driven_opening(verbose=v)
    cl = verify_closure_semantics(verbose=v)
    ren = verify_reneging_conclusion(verbose=v, n_days=3, n_replicas=2)

    sweep = utilisation_sweep(verbose=v)
    crit = _critical_rate(sweep)
    div = divergence_probe(verbose=v)
    legacy = legacy_invalidation(verbose=v)
    patio = patio_sweep(verbose=v)
    staff = staffing(verbose=v)
    hours = hours_and_closures(verbose=v)

    payload = {
        "verification": {"D2_opening": d2, "closure_semantics": cl,
                         "reneging_conclusion": ren},
        "critical_rate": crit,
        "divergence_probe": div.to_dict("records"),
        "legacy_rows": legacy.to_dict("records"),
        "sweep_levels": list(SWEEP_LEVELS),
        "sweep_days": SWEEP_DAYS,
        "sweep_replicas": SWEEP_REPLICAS,
        "feb2026_closure_hours": (FEB_CLOSURE[1] - FEB_CLOSURE[0]) / 60.0,
    }
    (RESULTS / "experiments.json").write_text(json.dumps(payload, indent=2, default=str))

    print()
    print(f"  wrote {RESULTS/'utilisation_sweep.csv'}")
    print(f"  wrote {RESULTS/'legacy_450.csv'}")
    print(f"  wrote {RESULTS/'patio_sweep.csv'}")
    print(f"  wrote {RESULTS/'staffing.csv'}")
    print(f"  wrote {RESULTS/'hours_and_closures.csv'}")
    print(f"  wrote {RESULTS/'experiments.json'}")
    print(f"  experiments finished in {time.perf_counter()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
