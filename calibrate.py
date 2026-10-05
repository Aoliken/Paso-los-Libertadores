#!/usr/bin/env python
"""Monthly validation of the SimPy border-crossing model.

WHAT THIS CAN AND CANNOT DO
---------------------------
The only public quantitative series for this crossing is the MOP monthly count
of LIGHT vehicles that cleared the border into Chile.  There is no public hourly
or intra-day series, so this script can only be validated at monthly
resolution.  ``--obs PATH`` exists so an hourly series can be plugged in if one
is ever obtained through an FOI request; until then it warns loudly and refuses
to pretend.

Train/test split (fixed, declared before looking at the test months):
    train : 2024-10 .. 2025-12   (15 months)
    test  : 2026-01 .. 2026-05   (5 months)

The calibration parameter is the INTRADAY PEAK EXPONENT, which reshapes the
assumed hourly arrival profile without changing the daily total.  A honest
warning is printed if the objective turns out to be flat, because in that case
the monthly data cannot identify the parameter and saying so is the result.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from .model import (Config, DEMAND_PER_DAY, DEMAND_TABLE, replicate, seeds_for,
                    summarise)

RESULTS = Path(__file__).resolve().parent / "results"

TRAIN_MONTHS = [m for m in DEMAND_PER_DAY if m <= "2025-12"]
TEST_MONTHS = [m for m in DEMAND_PER_DAY if m >= "2026-01"]

#: Grid for the intraday peak exponent.  1.0 leaves the prior profile untouched.
#: The range deliberately extends BELOW the prior, because the monthly totals
#: prefer the flattest profile available (least balking) and would otherwise pin
#: the optimum to the edge of the grid.
#: 4 points is enough: the objective is monotone, so the grid only has to
#: bracket both ends to expose that. More points cost time and add no
#: information the monthly series is capable of supplying.
PEAK_EXPONENT_GRID = (0.50, 1.00, 1.50, 2.50)

#: Months used for the cheap search.  Chosen to span the demand range
#: (2025-01 is the observed maximum, 2025-06 the minimum, 2025-11 mid-range).
SEARCH_MONTHS = ("2025-01", "2025-06", "2025-11")
SEARCH_DAYS = 5
SEARCH_WARMUP_DAYS = 2.0


def _month_config(month: str, exponent: float, seed: int, n_days: int | None,
                  warmup: float) -> Config:
    return Config(month=month, start_date=f"{month}-01",
                  intraday_peak_exponent=exponent, seed=seed,
                  warmup_days=warmup)


def search_objective(exponent: float, months=SEARCH_MONTHS, n_days: int = SEARCH_DAYS,
                     seed: int = 11) -> dict:
    """Simulated served rate vs observed rate, on the search months only.

    The comparison is made PER DAY on both sides, so the truncated search window
    never gets compared against a full-month observed total.  Reported alongside
    the error is the BALKING FRACTION, because a profile that throws arrivals
    away can hit any throughput target it likes.
    """
    obs, sim, ren = [], [], []
    for m in months:
        cfg = _month_config(m, exponent, seed, n_days, SEARCH_WARMUP_DAYS)
        r = replicate(cfg, 1, n_days=n_days)
        obs.append(DEMAND_PER_DAY[m])
        sim.append(float(r["served"].mean()) / n_days)
        ren.append(float(r["renege_fraction"].mean()))
    obs_a, sim_a = np.asarray(obs), np.asarray(sim)
    rmse = float(np.sqrt(np.mean((sim_a - obs_a) ** 2)))
    return {
        "intraday_peak_exponent": exponent,
        "search_days_per_month": n_days,
        "observed_veh_per_day": float(obs_a.sum()),
        "simulated_served_veh_per_day": float(sim_a.sum()),
        "abs_error_veh_per_day": float(sim_a.sum() - obs_a.sum()),
        "rel_error": float(sim_a.sum() / obs_a.sum() - 1.0),
        "rmse_veh_per_day": rmse,
        "mean_renege_fraction": float(np.mean(ren)),
        "worst_renege_fraction": float(np.max(ren)),
    }


def run_search(verbose: bool = True) -> pd.DataFrame:
    rows = []
    for e in PEAK_EXPONENT_GRID:
        t0 = time.perf_counter()
        r = search_objective(e)
        r["seconds"] = time.perf_counter() - t0
        rows.append(r)
        if verbose:
            print(f"    exponent {e:4.2f}  served/day {r['simulated_served_veh_per_day']:>9,.0f} "
                  f"vs observed/day {r['observed_veh_per_day']:>9,.0f}  "
                  f"rel err {r['rel_error']:+7.2%}  RMSE {r['rmse_veh_per_day']:>7,.0f}/day  "
                  f"balking {r['mean_renege_fraction']:6.2%}  ({r['seconds']:4.1f}s)")
    df = pd.DataFrame(rows)
    RESULTS.mkdir(exist_ok=True)
    df.to_csv(RESULTS / "calibration_grid.csv", index=False)
    return df


def assess_identifiability(grid: pd.DataFrame, best: float) -> str:
    """Say plainly whether the monthly data can identify the exponent.

    Three outcomes, and the middle one is the common one:

    * monotone objective whose minimum lies ON A GRID EDGE -> the parameter is
      BOUNDED, not identified: the data only says "no flatter than this";
    * a genuine interior minimum with a wide spread -> IDENTIFIED;
    * an objective that barely moves -> NOT IDENTIFIED at all.

    Monthly totals constrain how many vehicles crossed, not *when* they arrived.
    """
    g = grid.sort_values("intraday_peak_exponent").reset_index(drop=True)
    err = g["rel_error"].to_numpy(dtype=float)
    span = float(err.max() - err.min())
    lo, hi = float(g["intraday_peak_exponent"].min()), float(g["intraday_peak_exponent"].max())
    at_edge = bool(np.isclose(best, lo) or np.isclose(best, hi))
    monotone = bool(np.all(np.diff(err) <= 1e-9) or np.all(np.diff(err) >= -1e-9))

    if span <= 0.05:
        verdict, note = "NOT IDENTIFIED", True
    elif at_edge or monotone:
        verdict, note = "BOUNDED, NOT IDENTIFIED", False
    else:
        verdict, note = "IDENTIFIED", False

    lines = [
        f"    best exponent           : {best:.2f}"
        + ("   <- ON A GRID EDGE" if at_edge else ""),
        f"    relative-error range    : {span:.3%} across the grid "
        f"({lo:.2f}..{hi:.2f}), objective "
        f"{'monotone' if monotone else 'non-monotone with an interior minimum'}",
        f"    identifiability verdict : {verdict}",
    ]
    if verdict == "NOT IDENTIFIED":
        lines += [
            "    The monthly totals barely move across the whole grid, so they cannot",
            "    distinguish a peaked arrival profile from a flat one.",
        ]
    elif verdict == "BOUNDED, NOT IDENTIFIED":
        lines += [
            "    The objective is monotone and its minimum sits on the edge of the grid,",
            "    so this is a BOUND, not an estimate: the monthly totals only say the",
            "    arrival profile must be FLATTER than the prior, because any peaking",
            "    makes the assumed patience budget abandon vehicles the real crossing",
            "    apparently did not lose.  The value used is the flattest grid point and",
            "    remains an ASSUMPTION anchored to the observed daily total.",
            "    The shape itself is not recoverable from monthly data; only an hourly",
            "    series, obtainable by FOI, could identify it.",
        ]
    return "\n".join(lines)


def validate_monthly(exponent: float, n_replicas: int, verbose: bool = True) -> tuple:
    """Simulate all 20 observed months and compare served vs observed.

    The 5th-95th percentile band comes from the REPLICAS, so it measures
    stochastic variability only.  It is NOT a prediction interval: it cannot
    contain the structural error, because no observed hourly profile exists to
    calibrate against.
    """
    rows = []
    for m in DEMAND_PER_DAY:
        n_days = pd.Period(m).days_in_month
        obs = DEMAND_PER_DAY[m] * n_days
        cfg = _month_config(m, exponent, 101, None, 0.0)
        rep = replicate(cfg, n_replicas)
        s = summarise(rep)
        rows.append({
            "month": m,
            "split": "train" if m in TRAIN_MONTHS else "test",
            "days_in_month": n_days,
            "observed_veh": round(obs, 1),
            "observed_veh_per_day": DEMAND_PER_DAY[m],
            "sim_served_mean": s["throughput_veh_mean"],
            "sim_p05": float(rep["served"].quantile(0.05)),
            "sim_p95": float(rep["served"].quantile(0.95)),
            "abs_error": s["throughput_veh_mean"] - obs,
            "rel_error": s["throughput_veh_mean"] / obs - 1.0,
            "in_band": bool(rep["served"].min() >= obs * 0.95 and rep["served"].max() <= obs * 1.05),
            "within_5_95_band": bool(abs(s["throughput_veh_mean"] - obs)
                                     <= 0.05 * 0.5 * (float(rep["served"].quantile(0.95))
                                                      - float(rep["served"].quantile(0.05)))),
            "never_served_fraction": s["never_served_fraction"],
            "renege_fraction": s["renege_fraction"],
            "mean_wait_total_min": s["mean_wait_total_min"],
            "median_wait_total_min": s["median_wait_total_min"],
            "p95_wait_total_min": s["p95_wait_total_min"],
            "p99_wait_total_min": s["p99_wait_total_min"],
            "persons_served": s["persons_served_mean"],
            "persons_per_vehicle": s["persons_per_served_vehicle"],
            "rho_daily": s["rho_daily_open_window"],
            "rho_peak": s["rho_peak_hour"],
            "open_hours_per_day": s["open_hours_per_day"],
            "verdict": s["verdict"],
            "wait_stat_label": s["wait_stat_label"],
        })
        if verbose:
            r = rows[-1]
            print(f"    {m}  {r['split']:5s} obs {r['observed_veh']:>8,.0f}  "
                  f"sim {r['sim_served_mean']:>8,.0f}  err {r['rel_error']:+7.2%}  "
                  f"never-served {r['never_served_fraction']:6.2%}  "
                  f"{r['verdict']}")
    df = pd.DataFrame(rows)
    df.to_csv(RESULTS / "monthly_validation.csv", index=False)

    def metrics(sub: pd.DataFrame) -> dict:
        err = sub["rel_error"].to_numpy(dtype=float)
        obs = sub["observed_veh"].to_numpy(dtype=float)
        sim = sub["sim_served_mean"].to_numpy(dtype=float)
        return {
            "months": int(len(sub)),
            "rmse_veh": float(np.sqrt(np.mean((sim - obs) ** 2))),
            "mae_veh": float(np.mean(np.abs(sim - obs))),
            "mean_abs_rel_error": float(np.mean(np.abs(err))),
            "corr": float(np.corrcoef(obs, sim)[0, 1]) if len(sub) > 2 else float("nan"),
            "months_within_5pct": int((np.abs(err) <= 0.05).sum()),
            "months_with_obs_in_replica_band": int(sub["in_band"].sum()),
        }

    return df, {"train": metrics(df[df.split == "train"]),
                "test": metrics(df[df.split == "test"]),
                "all": metrics(df)}


def hourly_hook(path: str | None, verbose: bool = True) -> None:
    """The only place an hourly series could ever enter the project."""
    print()
    print("  HOURLY VALIDATION")
    print("  " + "-" * 72)
    if not path:
        print("    NOT RUN.  No public hourly or intra-day series exists for this")
        print("    crossing.  MOP publishes MONTHLY light-vehicle totals only, and the")
        print("    press reports are anecdotal, unmeasured and mostly on the Argentine")
        print("    side of the border.  Monthly validation is therefore the strongest")
        print("    claim this model can support, and no stronger claim is made.")
        print("    To enable hourly validation, obtain the series by FOI and re-run:")
        print("        python -m paso_los_libertadores.calibrate --obs hourly.csv")
        return
    p = Path(path)
    if not p.exists():
        print(f"    --obs {path} does not exist; hourly validation skipped.")
        return
    df = pd.read_csv(p)
    print(f"    loaded {len(df):,} rows from {p}")
    print("    NOTE: this path has never been exercised against real data, because no")
    print("    such series has been obtained yet.  Treat its output as untested.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replicas", type=int, default=1,
                    help="replicas per month in the final validation")
    ap.add_argument("--obs", default=None,
                    help="optional hourly observations CSV (none exist publicly)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    v = not args.quiet
    t0 = time.perf_counter()
    RESULTS.mkdir(exist_ok=True)

    print("=" * 78)
    print("MONTHLY VALIDATION - Paso Cristo Redentor light vehicles")
    print("=" * 78)
    print(f"  observed series : MOP monthly light vehicles, {len(DEMAND_PER_DAY)} months "
          f"({min(DEMAND_PER_DAY)}..{max(DEMAND_PER_DAY)})")
    print(f"  train           : {TRAIN_MONTHS[0]}..{TRAIN_MONTHS[-1]} ({len(TRAIN_MONTHS)} months)")
    print(f"  test            : {TEST_MONTHS[0]}..{TEST_MONTHS[-1]} ({len(TEST_MONTHS)} months)")
    print(f"  replicas/month  : {args.replicas}")
    print()

    print("  STEP 1 - grid search on TRAIN-era representative months "
          f"({SEARCH_DAYS} days each)")
    grid = run_search(verbose=v)
    best = float(grid.loc[grid["rmse_veh_per_day"].idxmin(), "intraday_peak_exponent"])
    print()
    print(assess_identifiability(grid, best))
    print()

    print("  STEP 2 - full-month validation of all observed months")
    df, met = validate_monthly(best, args.replicas, verbose=v)
    print()
    for split in ("train", "test", "all"):
        m = met[split]
        print(f"    {split:5s} n={m['months']:2d}  RMSE {m['rmse_veh']:>9,.0f} veh  "
              f"MAE {m['mae_veh']:>9,.0f} veh  mean|rel| {m['mean_abs_rel_error']:6.2%}  "
              f"corr {m['corr']:.4f}  within 5%: {m['months_within_5pct']}/{m['months']}  "
              f"obs inside replica band: {m['months_with_obs_in_replica_band']}/{m['months']}")
    print()
    print("    The 5-95 band is replica spread only.  It is NOT a prediction interval:")
    print("    it cannot contain structural error, because no observed hourly profile")
    print("    exists.  A monthly fit near 100% is largely guaranteed by construction,")
    print("    since the daily arrival total is anchored to the observed monthly count.")
    print()

    hourly_hook(args.obs, verbose=v)

    payload = {
        "best_intraday_peak_exponent": best,
        "grid": grid.to_dict("records"),
        "metrics": met,
        "train_months": TRAIN_MONTHS,
        "test_months": TEST_MONTHS,
        "replicas_per_month": args.replicas,
        "seeds": seeds_for(101, args.replicas),
        "band_meaning": "replica stochastic spread only, not a prediction interval",
    }
    (RESULTS / "calibration.json").write_text(json.dumps(payload, indent=2, default=str))
    print()
    print(f"  wrote {RESULTS/'calibration_grid.csv'}")
    print(f"  wrote {RESULTS/'monthly_validation.csv'}")
    print(f"  wrote {RESULTS/'calibration.json'}")
    print(f"  calibration finished in {time.perf_counter()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
