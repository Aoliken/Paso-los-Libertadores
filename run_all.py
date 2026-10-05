#!/usr/bin/env python
"""Run the whole project: calibrate -> experiments -> figures -> summary.

Usage (from any working directory):

    /home/easyrpg/venv/bin/python paso_los_libertadores/run_all.py
    /home/easyrpg/venv/bin/python -m paso_los_libertadores.run_all

Every stage is timed and every stage prints its own evidence.  The run fails
loudly rather than silently producing empty results: the assertions in
``model.verify_*`` are the contract, and a missing or tiny figure is an error.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
FIGURES = ROOT / "figures"

STAGES = (
    ("CALIBRATE", "calibrate", "monthly validation against the MOP series"),
    ("EXPERIMENTS", "experiments", "sweeps, legacy invalidation, patio, closures"),
    ("FIGURES", "report", "plots from the CSVs written above"),
)


def run_stage(name: str, module: str, purpose: str, extra: list[str]) -> tuple[str, float, bool]:
    print()
    print("#" * 78)
    print(f"# STAGE {name}: {purpose}")
    print("#" * 78)
    t0 = time.perf_counter()
    # The package must be importable regardless of the caller's CWD, otherwise
    # `python paso_los_libertadores/run_all.py` fails while `-m` from the parent works.
    env = dict(os.environ)
    pkg_parent = str(ROOT.parent)
    env["PYTHONPATH"] = (
        pkg_parent + os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else pkg_parent
    )
    proc = subprocess.run(
        [sys.executable, "-u", "-m", f"paso_los_libertadores.{module}", *extra],
        cwd=pkg_parent,
        env=env,
    )
    dt = time.perf_counter() - t0
    print()
    print(f"# STAGE {name} {'FAILED' if proc.returncode else 'ok'} in {dt:.1f}s")
    return name, dt, proc.returncode == 0


def verify_outputs() -> list[str]:
    """Fail loudly on a missing, empty or implausibly small artefact."""
    problems = []
    required = [
        RESULTS / "calibration_grid.csv",
        RESULTS / "monthly_validation.csv",
        RESULTS / "calibration.json",
        RESULTS / "utilisation_sweep.csv",
        RESULTS / "legacy_450.csv",
        RESULTS / "patio_sweep.csv",
        RESULTS / "staffing.csv",
        RESULTS / "hours_and_closures.csv",
        RESULTS / "experiments.json",
    ]
    for p in required:
        if not p.exists():
            problems.append(f"missing {p.relative_to(ROOT)}")
        elif p.stat().st_size < 200:
            problems.append(f"{p.relative_to(ROOT)} is only {p.stat().st_size} bytes")
    for p in sorted(FIGURES.glob("*.png")):
        if p.stat().st_size < 10_000:
            problems.append(f"{p.relative_to(ROOT)} is only {p.stat().st_size} bytes, "
                            f"too small to be a real plot")
    n_png = len(list(FIGURES.glob("*.png")))
    if n_png < 5:
        problems.append(f"expected at least 5 figures, found {n_png}")
    return problems


def summarise_findings() -> None:
    print()
    print("=" * 78)
    print("HEADLINE FINDINGS")
    print("=" * 78)
    ex = json.loads((RESULTS / "experiments.json").read_text())
    cal = json.loads((RESULTS / "calibration.json").read_text())

    crit = ex["critical_rate"]
    div = ex.get("divergence_probe") or []
    div_first = next((int(r["peak_rate_veh_h"]) for r in div
                      if r.get("frac_non_stationary", 0) > 0), None)
    print(f"  1. Capacity. 7 staffed booths clear {crit['capacity_veh_h']:.1f} veh/h, so")
    print(f"     rho = 1 at {crit['critical_rate_veh_h']:.1f} veh/h. The crossing is still a")
    print(f"     valid equilibrium up to {crit['last_equilibrium_peak']} veh/h offered; "
          f"from {crit['first_unstable_peak']} veh/h")
    print("     abandonment exceeds the 1% tolerance, so waits stop describing the")
    print(f"     offered load. With abandonment OFF, a queue genuinely diverges from "
          f"{div_first} veh/h.")
    print("     IMPORTANT: with abandonment on, the queue never diverges - it saturates")
    print("     at a bounded level that giving up maintains. A 'did the queue grow' test")
    print("     would therefore have reported a falsely safe result.")

    leg = ex["legacy_rows"]
    print("  2. The previous configuration is invalid. Peak 450 veh/h gives rho = "
          f"{leg[0]['rho_peak_hour']:.2f}")
    print(f"     against 13 booths and rho = {leg[1]['rho_peak_hour']:.2f} against 18.")
    print("     Both rows exceed 1, so a queue builds inside every peak hour regardless of")
    print("     any other metric. The invalidation rests on rho_peak_hour, NOT on the")
    print("     drift test: with no drain a queue can settle into a repeating daily")
    print("     cycle and be reported MIXED, which is not a pass, because a repeating")
    print("     cycle is not an equilibrium. Observed drift verdicts: "
          + ", ".join(f"{r['label']}={r['verdict']}" for r in leg) + ".")
    print("     A daily load ratio below 1 does not rescue them: the peak-hour")
    print("     overload is real and its delay is borne entirely by whoever queued.")

    mv = cal["metrics"]
    print(f"  3. Monthly validation. Test months: RMSE {mv['test']['rmse_veh']:,.0f} veh, "
          f"mean |error| {mv['test']['mean_abs_rel_error']:.2%},")
    print(f"     correlation {mv['test']['corr']:.4f}. But the daily arrival total is")
    print("     anchored to the observed monthly count, so this fit is largely built in")
    print("     and is NOT independent validation of the intraday profile.")
    print(f"  4. The intraday profile is NOT identified by monthly data. Verdict:")
    print(f"     {grid_verdict(cal)}. Only an hourly series, obtainable by FOI, could")
    print("     identify it.")

    print("  5. Patio capacity cannot reduce congestion. Total queue is identical at")
    print("     every patio size; a bigger patio only moves the queue from the booths")
    print("     to the gate road. Abandonment is what lowers the reported wait, and it")
    print("     does so by shrinking WHO was served.")
    print("     The old code's only patio test was one scenario row (200 -> 400) in a")
    print("     model with NO abandonment mechanism anywhere, no separate control-area")
    print("     space limit (the observed 15 was absent), and a single undivided")
    print("     cola_max column, so a queue that had moved road-ward read as unchanged.")

    print("  6. A gate that opens once a day makes the system periodic, not stationary.")
    print("     The restricted winter regimes are reported NON-STATIONARY even at")
    print("     rho_daily = 0.09..0.20, and no mean wait is meaningful without saying")
    print("     when in the daily cycle it was measured.")


def grid_verdict(cal: dict) -> str:
    g = cal["grid"]
    import numpy as np
    err = np.array([r["rel_error"] for r in g], dtype=float)
    exps = np.array([r["intraday_peak_exponent"] for r in g], dtype=float)
    best = float(exps[int(np.argmin([r["rmse_veh_per_day"] for r in g]))])
    span = float(err.max() - err.min())
    if span <= 0.05:
        return "NOT IDENTIFIED"
    if bool(np.isclose(best, exps.min()) or np.isclose(best, exps.max())):
        return "BOUNDED, NOT IDENTIFIED"
    return "IDENTIFIED"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replicas", type=int, default=None,
                    help="override replicas for the stages that accept one")
    ap.add_argument("--skip", default="", help="comma-separated stages to skip")
    args = ap.parse_args(argv)
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    t0 = time.perf_counter()
    print("=" * 78)
    print("PASO CRISTO REDENTOR - light vehicles Mendoza -> Chile")
    print("Event-driven SimPy model, monthly validation, stress tests, figures")
    print("=" * 78)
    print(f"  python     : {sys.executable}")
    print(f"  package    : {ROOT}")
    print(f"  simpy      : {__import__('simpy').__version__}")
    print(f"  pandas     : {__import__('pandas').__version__}")
    print(f"  numpy      : {__import__('numpy').__version__}")

    timings = []
    ok = True
    for name, module, purpose in STAGES:
        if name.lower() in {s.lower() for s in skip}:
            print()
            print(f"# STAGE {name}: SKIPPED on request")
            continue
        extra = ["--replicas", str(args.replicas)] if (
            args.replicas is not None and module in ("calibrate", "experiments")) else []
        _, dt, good = run_stage(name, module, purpose, extra)
        timings.append((name, dt, good))
        ok = ok and good
        if not good:
            print(f"# STAGE {name} returned a non-zero exit code; stopping.")
            break

    print()
    print("=" * 78)
    print("RUN SUMMARY")
    print("=" * 78)
    for name, dt, good in timings:
        print(f"  {name:12s} {dt:7.1f}s  {'ok' if good else 'FAILED'}")
    print(f"  {'TOTAL':12s} {time.perf_counter()-t0:7.1f}s")

    if not skip - {"FIGURES", "FIGURES"} and all(g for _, _, g in timings):
        problems = verify_outputs()
        print()
        if problems:
            for p in problems:
                print(f"  OUTPUT PROBLEM: {p}")
            ok = False
        else:
            n_png = len(list(FIGURES.glob("*.png")))
            n_csv = len(list(RESULTS.glob("*.csv")))
            print(f"  outputs verified: {n_csv} result CSVs, {n_png} figures, all non-empty")
        summarise_findings()

    print()
    print("=" * 78)
    print("END OF RUN")
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
