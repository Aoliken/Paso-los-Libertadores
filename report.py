#!/usr/bin/env python
"""Figures.  Every panel states whether its input is OBSERVED or ASSUMED.

Non-interactive backend only: this runs inside CI and inside WSL with no
display, and a figure that tries to open a window would hang the pipeline.

Legibility contract (this is a redraw for print, not a re-analysis):

* every figure is AUTHORED at exactly the width it is embedded at
  (``FIG_W`` == the ``figure(..., width=Inches(6.3))`` default of
  ``make_whitepaper.py``), so the Word scale factor is 1.0 and a declared
  font size IS the printed font size;
* no text artist is allowed below ``FS_FLOOR`` points, which ``--verify``
  proves mechanically instead of by eye;
* DPI is fixed at ``FIG_DPI`` for crisp reproduction, not for legibility.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib
matplotlib.use("Agg")          # non-interactive: never open a window
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.text import Text

from .model import Config, DEMAND_PER_DAY, PROFILE_HOURLY_PRIOR, closure_interval

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
FIGURES = ROOT / "figures"

FEB_CLOSURE = closure_interval("2026-02-01", "2026-02-19T10:00", "2026-02-20T12:00")

# Distinguishes measured from assumed at a glance, in every panel that mixes them.
C_OBS = "#1f3b73"      # observed
C_SIM = "#c1121f"      # simulated
C_ASSUM = "#8d8d8d"    # assumed
C_BAND = "#9ecae1"     # replica spread
C_WARN = "#f4a261"     # non-stationary region

# --- print geometry -------------------------------------------------------
# FIG_W must stay in sync with the embed width used by make_whitepaper.py.
FIG_W = 6.3            # inches; authored == embedded, so scale factor is 1.0
FIG_DPI = 300          # 6.3 in x 300 = 1890 px

# --- type scale (points, literal because the scale factor is 1.0) ----------
FS_PANEL = 10.5        # panel title
FS_PROSE = 8.5         # subtitle / prose
FS_LABEL = 9.0         # axis labels
FS_TICK = 8.5          # tick labels
FS_LEGEND = 8.5        # legend entries
FS_ANNOT = 8.0         # inline annotations
FS_FLOOR = 8.0         # hard floor, enforced by --verify
SUB_LEADING = 1.3      # subtitle line spacing (multiplier of the font size)

# --- verification tolerances (points) -------------------------------------
# Overlap and clipping need DIFFERENT slack.  A small penetration between two
# neighbouring tick labels is a graze, not a defect, so overlap tolerates a
# couple of points.  Clipping never has a legitimate cause: a glyph pushed
# past the frame is a truncated word, so that check stays near-zero.  Sharing
# one loose tolerance here is what previously hid a real 1.9 pt clipped title.
TOL_OVERLAP_PT = 2.0
TOL_CLIP_PT = 0.25

_VERIFY = False
_CHECKS: list["CheckResult"] = []


def _rc() -> None:
    """Apply the central type scale.  Called by every ``fig_*`` entry point."""
    plt.rcParams.update({
        "font.family": "DejaVu Sans",   # ships with matplotlib: no missing-font risk
        "font.size": FS_LABEL,
        "axes.titlesize": FS_PANEL,
        "axes.titleweight": "bold",
        "axes.labelsize": FS_LABEL,
        "xtick.labelsize": FS_TICK,
        "ytick.labelsize": FS_TICK,
        "legend.fontsize": FS_LEGEND,
        "figure.dpi": FIG_DPI,
        "savefig.dpi": FIG_DPI,
    })


def _style(ax, title: str, xlabel: str, ylabel: str, subtitle: str = "") -> None:
    """Panel title, pre-wrapped subtitle, axis labels, ticks, grid.

    The subtitle is anchored a fixed number of POINTS above the axes (an axes
    fraction would drift with the panel height) and the title is padded above it,
    so the two blocks can never collide however short the panel is.
    """
    lines = subtitle.count("\n") + 1 if subtitle else 0
    sub_h = lines * FS_PROSE * SUB_LEADING
    ax.set_title(title, loc="left", fontweight="bold",
                 pad=6.0 + (sub_h + 3.0 if lines else 0.0))
    if subtitle:
        ax.annotate(subtitle, xy=(0.0, 1.0), xycoords="axes fraction",
                    xytext=(0, 3), textcoords="offset points",
                    fontsize=FS_PROSE, color="#444444", va="bottom",
                    linespacing=SUB_LEADING, annotation_clip=False)
    ax.set_xlabel(xlabel, fontsize=FS_LABEL)
    ax.set_ylabel(ylabel, fontsize=FS_LABEL)
    ax.tick_params(labelsize=FS_TICK)
    ax.grid(alpha=0.25, linewidth=0.6)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def _load(name: str) -> pd.DataFrame:
    p = RESULTS / name
    if not p.exists():
        raise SystemExit(f"missing {p}; run calibrate.py and experiments.py first")
    return pd.read_csv(p)


# ---------------------------------------------------------------------------
# legibility verification
# ---------------------------------------------------------------------------
@dataclass
class CheckResult:
    """Outcome of the automated legibility audit for one figure."""

    name: str
    texts: int = 0
    min_font: float = float("nan")
    max_overlap_pt: float = 0.0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def fail(self, msg: str) -> None:
        self.problems.append(msg)


def _texts(fig) -> list[Text]:
    """Every Text artist of the figure, by construction rather than by hand.

    ``findobj`` walks the whole artist tree, so titles, axis labels, in-axes
    annotations, tick labels, offset texts and legend entries are all included
    and no future matplotlib re-parenting can silently shrink the audit.  Two
    classes are excluded because a reader cannot collide with them:

    * a hidden axis -- ``twinx`` draws the shared x axis once, so the twin's
      duplicate labels sit exactly on top of the real ones;
    * a tick outside its view interval -- matplotlib keeps those labels alive
      with extrapolated positions and still reports them as visible, but
      ``Axis.draw`` never emits them.
    """
    excluded: set[int] = set()

    def drop(t) -> None:
        if t is not None:
            excluded.add(id(t))

    for ax in fig.axes:
        for axis in (ax.xaxis, ax.yaxis):
            lo, hi = axis.get_view_interval()
            slack = abs(hi - lo) * 1e-9 + 1e-12
            hidden = not axis.get_visible()
            drop(axis.get_offset_text())
            for ticks in (axis.get_major_ticks(), axis.get_minor_ticks()):
                for tick in ticks:
                    outside = not (lo - slack <= float(tick.get_loc()) <= hi + slack)
                    if hidden or outside or not tick.get_visible():
                        drop(tick.label1)
                        drop(tick.label2)
            if hidden:
                drop(axis.label)
    return [t for t in fig.findobj(match=Text) if id(t) not in excluded]


def _shown(t: Text) -> bool:
    """True when the artist actually prints something."""
    return bool(t.get_visible()) and bool(str(t.get_text()).strip())


def _font_pt(t: Text) -> float:
    """Effective font size in points, falling back to the rcParam default."""
    size = t.get_fontsize()
    try:
        return float(size)
    except (TypeError, ValueError):
        return float(matplotlib.rcParams["font.size"])


def _tag(t: Text) -> str:
    s = " ".join(str(t.get_text()).split())
    return (s[:44] + "...") if len(s) > 44 else (s or "<empty>")


def _penetration(a, b) -> float:
    """Depth (px) at which two display-space boxes overlap.

    Uses the SHALLOWER of the two axes: boxes that merely touch along one axis
    are adjacent rows (a tick label and its axis label), not a collision.
    """
    dx = min(a.x1, b.x1) - max(a.x0, b.x0)
    dy = min(a.y1, b.y1) - max(a.y0, b.y0)
    if dx <= 0 or dy <= 0:
        return 0.0
    return min(dx, dy)


def _check_figure(fig, name: str) -> CheckResult:
    """Font floor, pairwise text collision and containment audit of one figure."""
    fig.set_dpi(FIG_DPI)          # audit the pixels that are actually saved
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    pt_per_px = 72.0 / fig.dpi
    res = CheckResult(name)

    boxes: list[tuple[Text, object]] = []
    for t in _texts(fig):
        if not _shown(t):
            continue
        res.texts += 1
        fs = _font_pt(t)
        res.min_font = fs if np.isnan(res.min_font) else min(res.min_font, fs)
        if fs < FS_FLOOR - 1e-6:
            res.fail(f"font {fs:.1f} pt below floor {FS_FLOOR:.1f} pt: {_tag(t)!r}")
        bbox = t.get_window_extent(renderer=renderer)
        if bbox.width <= 0 or bbox.height <= 0:
            continue
        boxes.append((t, bbox))

    frame = fig.bbox
    clip_tol_px = TOL_CLIP_PT / pt_per_px
    for t, b in boxes:
        over = max(0.0, max(frame.x0 - b.x0, b.x1 - frame.x1),
                   max(frame.y0 - b.y0, b.y1 - frame.y1))
        if over > clip_tol_px:
            res.fail(f"clipped {over * pt_per_px:.2f} pt outside the figure: {_tag(t)!r}")

    overlap_tol_px = TOL_OVERLAP_PT / pt_per_px
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            pen = _penetration(boxes[i][1], boxes[j][1])
            if pen <= overlap_tol_px:
                continue
            res.max_overlap_pt = max(res.max_overlap_pt, pen * pt_per_px)
            res.fail(f"overlap {pen * pt_per_px:.1f} pt: "
                     f"{_tag(boxes[i][0])!r} <> {_tag(boxes[j][0])!r}")
    return res


def _finish(fig, out: Path, name: str) -> None:
    """Audit when asked, then save at the print DPI and release the figure."""
    if _VERIFY:
        _CHECKS.append(_check_figure(fig, name))
    fig.savefig(out, dpi=FIG_DPI)
    plt.close(fig)


def _print_verify() -> bool:
    print()
    print("  LEGIBILITY VERIFY")
    print(f"    authored width {FIG_W:.1f} in == embedded width {FIG_W:.1f} in "
          f"(scale 1.00), font floor {FS_FLOOR:.1f} pt, "
          f"tolerances overlap {TOL_OVERLAP_PT:.2f} pt / clip {TOL_CLIP_PT:.2f} pt")
    print("  " + "-" * 78)
    for r in _CHECKS:
        head = (f"{r.texts:>3} texts, min font {r.min_font:4.1f} pt, "
                f"max overlap {r.max_overlap_pt:4.1f} pt")
        print(f"    {'PASS' if r.ok else 'FAIL'}  {r.name:<34} {head}")
        for p in r.problems:
            print(f"            - {p}")
    print("  " + "-" * 78)
    bad = [r.name for r in _CHECKS if not r.ok]
    if bad:
        print(f"    FAIL: {len(bad)} of {len(_CHECKS)} figures: {', '.join(bad)}")
    else:
        print(f"    PASS: {len(_CHECKS)}/{len(_CHECKS)} figures pass every check")
    return not bad


# ---------------------------------------------------------------------------
def fig_monthly(df: pd.DataFrame, out: Path) -> None:
    _rc()
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(FIG_W, 6.4), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1]})
    x = np.arange(len(df))

    # The replica band is only honest if it has width.  With one replica per
    # month sim_p05 == sim_p95, so an invisible fill_between would be a lie told
    # by the legend; draw the single value as a visible series and say so.
    degenerate = bool(np.allclose(df["sim_p05"].to_numpy(dtype=float),
                                  df["sim_p95"].to_numpy(dtype=float)))
    if degenerate:
        # p05 == p95 == the simulated mean, so nothing drawn at those
        # coordinates would ever be seen.  An open ring of a clearly larger
        # diameter stays visible around the mean marker and reads as what it
        # is: a spread of exactly zero width.
        ax.plot(x, df["sim_p05"], linestyle="none", marker="o", ms=11,
                mfc="none", mec=C_BAND, mew=2.2,
                label="simulated p05 = p95 (replica spread DEGENERATE: 0 wide)")
    else:
        ax.fill_between(x, df["sim_p05"], df["sim_p95"], color=C_BAND, alpha=0.75,
                        label="simulated 5-95% replica spread (stochastic only)")
    ax.plot(x, df["sim_served_mean"], "o-", color=C_SIM, ms=4, lw=1.6,
            label="simulated vehicles served")
    ax.plot(x, df["observed_veh"], "s--", color=C_OBS, ms=4, lw=1.6,
            label="OBSERVED MOP light vehicles")

    split = df["split"].to_numpy()
    change = np.flatnonzero(np.diff((split == "test").astype(int)) != 0)
    for c in change:
        ax.axvline(c + 0.5, color="#333333", lw=1.2, ls=":")
        ax2.axvline(c + 0.5, color="#333333", lw=1.2, ls=":")
        ax.text(c + 0.6, ax.get_ylim()[1] * 0.97, "TEST", fontsize=FS_ANNOT,
                color="#333333", va="top")
    bad = df["never_served_fraction"] > 0.01
    if bad.any():
        ax2.bar(x[bad.values], df.loc[bad, "never_served_fraction"], color=C_WARN,
                width=0.7, label="fraction of arrivals never served")
        ax2.bar(x[~bad.values], df.loc[~bad, "never_served_fraction"],
                color="#cbd5e1", width=0.7)
    ax2.axhline(0.01, color=C_OBS, lw=1.0, ls=":")
    ax2.set_ylim(0, max(0.2, float(df["never_served_fraction"].max()) * 1.15))

    _style(ax, "Monthly validation: observed vs simulated crossings",
           "", "light vehicles per month",
           "Demand is anchored to the observed monthly count, so a close fit is\n"
           "partly built in. The honest error metric is the panel below.")
    ax.legend(fontsize=FS_LEGEND, loc="upper left", framealpha=0.95)
    _style(ax2, "", "month (train 2024-10..2025-12  |  test 2026-01..2026-05)",
           "never served")
    ax2.legend(fontsize=FS_LEGEND, loc="upper left", framealpha=0.95)
    ax.set_xticks(x)
    # The labels must be set on the axes that DRAWS them: sharex shares the
    # locator and the formatter, not the label artists, so setting them on the
    # top panel would style the hidden copies and leave the printed ones
    # horizontal and overlapping.
    ax2.set_xticklabels(df["month"], rotation=90, fontsize=FS_TICK)
    fig.tight_layout()
    _finish(fig, out, "01_monthly_validation.png")


def fig_sweep(df: pd.DataFrame, crit: dict, out: Path) -> None:
    _rc()
    fig, ax = plt.subplots(figsize=(FIG_W, 4.6))
    stable = df["frac_non_stationary"] == 0.0
    ax.plot(df.loc[stable, "peak_rate_veh_h"], df.loc[stable, "mean_wait_total_min"],
            "o-", color=C_SIM, ms=5, label="mean wait, no queue divergence")
    # Only advertise the diverging marker set when the subset actually has rows:
    # frac_non_stationary is 0.0 for every swept level, so an always-added entry
    # would be a legend handle with nothing behind it.
    if (~stable).any():
        ax.plot(df.loc[~stable, "peak_rate_veh_h"],
                df.loc[~stable, "mean_wait_total_min"],
                "o", mfc="none", mec=C_WARN, mew=1.8, ms=8,
                label="mean wait, queue diverging (NON-STATIONARY)")
    ax2 = ax.twinx()
    ax2.plot(df["peak_rate_veh_h"], df["never_served_fraction"], "^:",
             color="#2a9d8f", ms=5, label="fraction of arrivals never served")
    ax2.set_ylabel("never served (fraction)", fontsize=FS_LABEL, color="#2a9d8f")
    ax2.tick_params(labelsize=FS_TICK, colors="#2a9d8f")
    ax2.spines["top"].set_visible(False)

    _style(ax, "Utilisation sweep: where the crossing saturates",
           "peak-hour arrival rate (veh/h)  --  OFFERED, not served",
           "mean total wait (min), served vehicles only",
           "7 OBSERVED booths, 1 person each (ASSUMED); service 3.5 min mean\n"
           "(ASSUMED). Wait statistics exclude vehicles that never got through.")

    # Annotations are placed against the final ylim, in three separate bands, so
    # that the narrow axes cannot stack them on top of each other.
    cap = crit["capacity_veh_h"]
    ax.axvline(cap, color=C_OBS, lw=1.4, ls="--")
    for peak, booths, rho in ((450, 13, 2.019), (450, 18, 1.458)):
        ax.axvline(peak, color="#6a4c93", lw=1.0, ls="-", alpha=0.7)
    lo, hi = ax.get_ylim()
    ax.text(cap + 6, lo + 0.72 * (hi - lo), "7 booths\n120 veh/h (rho=1)",
            fontsize=FS_ANNOT, color=C_OBS, ha="left", va="center",
            linespacing=1.3)
    for frac, label in ((0.40, "legacy 450 veh/h\n13 booths, rho=2.02"),
                        (0.17, "legacy 450 veh/h\n18 booths, rho=1.46")):
        ax.text(444, lo + frac * (hi - lo), label, fontsize=FS_ANNOT,
                color="#6a4c93", ha="right", va="center", linespacing=1.3)

    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=FS_LEGEND, loc="upper left",
              framealpha=0.95)
    fig.tight_layout()
    _finish(fig, out, "02_utilisation_sweep.png")


def fig_patio(df: pd.DataFrame, out: Path) -> None:
    _rc()
    # Stacked: side by side at 6.3 in would leave ~3 in per panel, which a log
    # axis cannot carry.
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(FIG_W, 7.4))
    for ren, colour, marker, name in ((False, C_OBS, "o", "OFF"),
                                      (True, C_SIM, "s", "ON")):
        sub = df[df["reneging"] == ren].sort_values("patio")
        ax.plot(sub["patio"], sub["max_q_upstream_mean"], marker, color=colour,
                ms=5, label=f"{name}: queue on the gate road")
        ax.plot(sub["patio"], sub["max_q_inside_mean"], marker, color=colour,
                ms=5, mfc="none", ls="--",
                label=f"{name}: queue inside the complex")
        ax2.plot(sub["patio"], sub["never_served_fraction"], marker, color=colour,
                 ms=5, label=f"abandonment {name}")
    _style(ax, "Patio capacity relocates the queue, it does not reduce it",
           "patio (yard) capacity, vehicles  [ASSUMED range]",
           "peak queue, vehicles",
           "January peak demand. The sum of the two curves is constant\n"
           "at every patio size.")
    ax.set_xscale("log")
    ax.set_xticks(df["patio"].unique())
    ax.get_xaxis().set_major_formatter(matplotlib.ticker.ScalarFormatter())
    # Explicit major ticks already carry the scale; the auto minor ticks of a
    # log axis label 2,3,4...x10^k and collide at 6.3 in.
    ax.minorticks_off()
    ax.set_xlim(11, 830)                      # padding: keep the 600 tick readable
    _style(ax2, "So the only thing that reduces the wait is giving up",
           "patio capacity, vehicles", "never served (fraction)",
           "A lower wait here means fewer drivers were served,\nnot a faster crossing.")
    ax2.legend(fontsize=FS_LEGEND, loc="center", framealpha=0.95)
    ax2.set_xscale("log")
    ax2.set_xticks(df["patio"].unique())
    ax2.get_xaxis().set_major_formatter(matplotlib.ticker.ScalarFormatter())
    ax2.minorticks_off()
    ax2.set_xlim(11, 830)
    fig.subplots_adjust(left=0.155, right=0.975, top=0.905, bottom=0.115,
                        hspace=0.70)
    # Four entries do not fit inside a 2.1 in panel without covering the
    # crossing, so the top legend goes under its own axis label instead.
    ax.legend(fontsize=FS_LEGEND, loc="upper center", ncols=2, framealpha=0.95,
              bbox_to_anchor=(0.5, -0.155), columnspacing=2.0)
    _finish(fig, out, "03_patio_vs_abandonment.png")


def fig_intraday(out: Path, exponent: float = 1.0) -> None:
    _rc()
    base = np.asarray(PROFILE_HOURLY_PRIOR, dtype=float)
    tilted = base ** float(exponent)
    tilted = tilted / tilted.mean()
    fig, ax = plt.subplots(figsize=(FIG_W, 4.2))
    ax.bar(np.arange(24), tilted, color=C_ASSUM, edgecolor="white", linewidth=0.6,
           label="ASSUMED hourly arrival profile (no public hourly series)")
    ax.plot(np.arange(24), base / base.mean(), "o-", color=C_OBS, ms=4, lw=1.2,
            label="prior profile before the fitted exponent")
    # Headroom only: the two band labels need a clear strip inside the axes.
    ax.set_ylim(0, float(ax.get_ylim()[1]) * 1.15)
    lo, hi = ax.get_ylim()
    # Short labels, staggered heights: at 6.3 in the old long strings overlapped.
    for o, c, name, frac in ((9, 21, "press 09-21", 0.99),
                             (9.5, 20.5, "gob.ar 09:30-20:30", 0.89)):
        ax.axvspan(o - 0.5, c - 0.5, color="#ffe8cc", alpha=0.55, zorder=0)
        ax.text((o + c) / 2 - 0.5, lo + frac * (hi - lo), name,
                fontsize=FS_ANNOT, ha="center", va="top", color="#a05a00")
    # Title wrapped on two lines: at 6.3 in the single-line form overflowed the
    # right frame by 1.9 pt and clipped the tail of "measurement".
    _style(ax, "The intraday arrival profile is\nan ASSUMPTION, not a measurement",
           "hour of day", "relative arrival rate (mean = 1)",
           "Shaded bands: the two CONFLICTING published winter opening-hour regimes.")
    ax.set_xticks(np.arange(24))
    fig.subplots_adjust(left=0.125, right=0.985, top=0.855, bottom=0.245)
    ax.legend(fontsize=FS_LEGEND, loc="upper center", framealpha=0.95,
              bbox_to_anchor=(0.5, -0.16))
    _finish(fig, out, "04_intraday_profile_ASSUMED.png")


def fig_closure(out: Path) -> None:
    """One trace showing a real dated closure, reproduced to the minute."""
    _rc()
    from .model import simulate
    cfg = Config(month="2026-02", start_date="2026-02-01", hours_regime="argentina_24h",
                 closed_intervals=(FEB_CLOSURE,), warmup_days=0.0, seed=7,
                 drain_after_arrivals_min=0.0)
    r = simulate(cfg, seed=7)
    mon = r.monitor
    d0, d1 = FEB_CLOSURE
    hours = (mon["t_min"] - d0) / 60.0
    fig, ax = plt.subplots(figsize=(FIG_W, 5.0))
    ax.axvspan(0, (d1 - d0) / 60.0, color="#ffd6d6", alpha=0.8, zorder=0,
               label="OBSERVED closure: 19 Feb 10:00 -> 20 Feb 12:00 (26 h)")
    ax.plot(hours, mon["q_upstream"], lw=1.3, color=C_SIM,
            label="queue on the gate road")
    ax.plot(hours, mon["q_inside"], lw=1.3, color="#2a9d8f",
            label="queue inside the complex")
    ax.plot(hours, mon["q_total"], lw=2.0, color=C_OBS, label="total queue")
    _style(ax, "A dated closure: what the model does and the old model could not",
           "hours relative to the start of the 26 h closure",
           "vehicles waiting",
           "Arrivals continue during a closure; only ADMISSION stops. Zero services\n"
           "start inside the window (asserted by verify_closure_semantics).")
    fig.subplots_adjust(left=0.105, right=0.985, top=0.88, bottom=0.28)
    # A 52-character legend entry cannot sit inside a 5 in axes without covering
    # the trace, so the legend goes below the axis label.
    ax.legend(fontsize=FS_LEGEND, loc="upper center", framealpha=0.95,
              bbox_to_anchor=(0.5, -0.142))
    _finish(fig, out, "05_feb2026_closure.png")


def main(argv=None) -> int:
    global _VERIFY
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--verify", action="store_true",
                    help="audit every text artist: font floor, text collisions, "
                         "on-canvas containment; non-zero exit on failure")
    args = ap.parse_args(argv)
    _VERIFY = bool(args.verify)
    FIGURES.mkdir(exist_ok=True)
    written = []

    monthly = _load("monthly_validation.csv")
    fig_monthly(monthly, FIGURES / "01_monthly_validation.png")
    written.append(FIGURES / "01_monthly_validation.png")

    sweep = _load("utilisation_sweep.csv")
    crit = json.loads((RESULTS / "experiments.json").read_text())["critical_rate"]
    fig_sweep(sweep, crit, FIGURES / "02_utilisation_sweep.png")
    written.append(FIGURES / "02_utilisation_sweep.png")

    patio = _load("patio_sweep.csv")
    fig_patio(patio, FIGURES / "03_patio_vs_abandonment.png")
    written.append(FIGURES / "03_patio_vs_abandonment.png")

    exponent = 1.0
    cj = RESULTS / "calibration.json"
    if cj.exists():
        exponent = float(json.loads(cj.read_text())["best_intraday_peak_exponent"])
    fig_intraday(FIGURES / "04_intraday_profile_ASSUMED.png", exponent)
    written.append(FIGURES / "04_intraday_profile_ASSUMED.png")

    fig_closure(FIGURES / "05_feb2026_closure.png")
    written.append(FIGURES / "05_feb2026_closure.png")

    if _VERIFY and not _print_verify():
        return 1

    if not args.quiet:
        print()
        print("  FIGURES")
        print("  " + "-" * 72)
        for p in written:
            size = p.stat().st_size if p.exists() else 0
            flag = "OK " if size > 10_000 else "!! "
            print(f"    {flag}{p.relative_to(ROOT)}  ({size:,} bytes)")
            if size <= 10_000:
                raise SystemExit(f"figure {p} is suspiciously small: {size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())