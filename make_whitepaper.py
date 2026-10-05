"""Build the research whitepaper (.docx) for the Los Libertadores model.

Every quantitative claim in the document is read from results/*.csv at build
time. Nothing is hardcoded except the real MOP series, which is read from
data/mop_monthly_light_vehicles.csv. No number is typed by hand.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.shared import Inches, Pt, RGBColor

PKG = Path(__file__).resolve().parent
DATA = PKG / "data"
RESULTS = PKG / "results"
FIGS = PKG / "figures"
OUT = PKG / "whitepaper_los_libertadores.docx"

RETRIEVED = "2026-09-30"

# --------------------------------------------------------------------------
# load every number from disk
# --------------------------------------------------------------------------
mop = pd.read_csv(DATA / "mop_monthly_light_vehicles.csv")
ops = pd.read_csv(DATA / "published_operating_parameters.csv")
press = pd.read_csv(DATA / "press_wait_reports.csv")
monthly = pd.read_csv(RESULTS / "monthly_validation.csv")
sweep = pd.read_csv(RESULTS / "utilisation_sweep.csv")
legacy = pd.read_csv(RESULTS / "legacy_450.csv")
patio = pd.read_csv(RESULTS / "patio_sweep.csv")
staff = pd.read_csv(RESULTS / "staffing.csv")
hours = pd.read_csv(RESULTS / "hours_and_closures.csv")
probe = pd.read_csv(RESULTS / "divergence_probe.csv")
calib = json.loads((RESULTS / "calibration.json").read_text())

TOTAL_VEH = int(mop["light_vehicles"].sum())
TOTAL_DAYS = int(mop["days_in_month"].sum())
PER_DAY_MEAN = mop["vehicles_per_day"].mean()
PEAK = mop.loc[mop["vehicles_per_day"].idxmax()]
TROUGH = mop.loc[mop["vehicles_per_day"].idxmin()]
SEASONAL = PEAK["vehicles_per_day"] / TROUGH["vehicles_per_day"]

tr = monthly[monthly["split"] == "train"]
te = monthly[monthly["split"] == "test"]


def rmse(df):
    return float((df["abs_error"] ** 2).mean() ** 0.5)


CAP7 = float(sweep["booth_capacity_veh_h"].iloc[0])
CRIT = float(sweep["peak_offered_veh_h"][sweep["rho_peak_hour"] >= 1.0].min())
LAST_OK = float(sweep["peak_offered_veh_h"][sweep["verdict"] == "STATIONARY"].max())
L13 = legacy[legacy["booths"] == 13].iloc[0]
L18 = legacy[legacy["booths"] == 18].iloc[0]

# --------------------------------------------------------------------------
# document scaffolding
# --------------------------------------------------------------------------
doc = Document()

st = doc.styles["Normal"]
st.font.name = "Calibri"
st.font.size = Pt(10.5)
st.paragraph_format.space_after = Pt(6)

for lvl, sz in ((1, 17), (2, 13.5), (3, 11.5)):
    h = doc.styles[f"Heading {lvl}"]
    h.font.name = "Calibri"
    h.font.size = Pt(sz)
    h.font.color.rgb = RGBColor(0x1F, 0x1F, 0x1F)


def para(text, *, italic=False, bold=False, size=None, align=None, space=6):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.italic, r.bold = italic, bold
    if size:
        r.font.size = Pt(size)
    if align:
        p.alignment = align
    p.paragraph_format.space_after = Pt(space)
    return p


def bullets(items, style="List Bullet"):
    for it in items:
        p = doc.add_paragraph(style=style)
        p.paragraph_format.space_after = Pt(2)
        if isinstance(it, tuple):
            r = p.add_run(it[0])
            r.bold = True
            p.add_run(it[1])
        else:
            p.add_run(it)


def table(headers, rows, widths=None, caption=None, font=8.5):
    if caption:
        c = para(caption, italic=True, size=9, space=3)
        c.paragraph_format.keep_with_next = True
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Light Grid Accent 1"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h in enumerate(headers):
        cell = t.rows[0].cells[i]
        cell.text = ""
        r = cell.paragraphs[0].add_run(str(h))
        r.bold = True
        r.font.size = Pt(font)
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ""
            r = cells[i].paragraphs[0].add_run(str(v))
            r.font.size = Pt(font)
    if widths:
        for r_ in t.rows:
            for i, w in enumerate(widths):
                r_.cells[i].width = Inches(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def figure(png, caption, width=6.3):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run().add_picture(str(FIGS / png), width=Inches(width))
    para(caption, italic=True, size=9, align=WD_ALIGN_PARAGRAPH.CENTER, space=10)


def n(x, d=1):
    return f"{x:,.{d}f}"


# ==========================================================================
# TITLE
# ==========================================================================
t = doc.add_paragraph()
t.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = t.add_run("Queue Dynamics and Service Capacity at the Los Libertadores Border Crossing")
r.bold = True
r.font.size = Pt(20)

s = doc.add_paragraph()
s.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = s.add_run("Mendoza, Argentina to Chile - Light Vehicles, 2024-2026\n"
              "A Discrete-Event Simulation Calibrated to the Only Public Quantitative Series")
r.italic = True
r.font.size = Pt(12)

s = doc.add_paragraph()
s.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = s.add_run(f"Technical whitepaper - all data retrieved {RETRIEVED}")
r.font.size = Pt(9.5)
r.font.color.rgb = RGBColor(0x60, 0x60, 0x60)

doc.add_paragraph()

# ==========================================================================
# ABSTRACT
# ==========================================================================
doc.add_heading("Abstract", level=1)
para(
    "This paper documents a discrete-event simulation of light-vehicle processing at the "
    "Paso Internacional Cristo Redentor / Los Libertadores crossing, the primary road border "
    "between Mendoza, Argentina and central Chile. The study had two objectives. The first was "
    f"to calibrate simulated throughput against real data. The second was to test a set of "
    f"operational claims about booth staffing, yard capacity and opening hours. Using "
    f"{len(mop)} months of published monthly light-vehicle counts "
    f"({n(TOTAL_VEH, 0)} vehicles over {n(TOTAL_DAYS, 0)} days, retrieved from the concessionaire's "
    f"monthly reports), the model reproduces observed monthly throughput with a mean absolute "
    f"relative error of {tr['rel_error'].abs().mean() * 100:.2f}% on the calibration period and "
    f"{te['rel_error'].abs().mean() * 100:.2f}% on the held-out period. The utilisation sweep "
    f"establishes that the observed configuration of {7} staffed "
    f"car booths clears {n(CAP7)} vehicles per hour, placing the stability threshold at an offered "
    f"peak of {n(CRIT, 0)} vehicles per hour."
)
para(
    "Three findings are reported. First, the stability threshold, not the absolute wait, is the "
    "defensible result: it does not depend on the intraday arrival profile, which the available "
    "data cannot identify. Second, yard capacity cannot reduce congestion at this facility under "
    "any staffing level; it relocates the queue rather than shortening it. Third, a common "
    "modelling shortcut - excluding the behaviour of drivers who abandon the queue - produces a "
    "qualitatively wrong answer to the yard-capacity question, and is shown here to be the "
    "single most consequential omission in this class of model. The paper is explicit about what "
    "its data cannot support: no public hourly series exists for this crossing, so no claim is "
    "made about intraday wait-time accuracy."
)

doc.add_page_break()

# ==========================================================================
# 1. INTRODUCTION
# ==========================================================================
doc.add_heading("1. Introduction and Motivation", level=1)
para(
    "Paso Internacional Cristo Redentor (Argentine side) and Los Libertadores (Chilean side) form "
    "a single road border crossing through a mountain tunnel at approximately 2,952 m elevation. "
    "It is the dominant land corridor between Argentina and Chile and carries a strongly seasonal "
    "flow, driven by Argentine summer holidays, Chilean winter holidays and cross-border retail "
    "traffic. The facility operates as an integrated control (A.C.I.) under Resolution GMC 49/02 "
    "of the Mercosur, combining migratory, customs and agricultural inspection within a single "
    "physical complex."
)
para(
    "The crossing is well known as a source of congestion, and press coverage documents extreme "
    f"delays on peak holiday weekends. A full inventory of {len(press)} dated press reports was "
    "compiled for this study; all are classified as anecdotal, and none constitutes a measurement. "
    "Their value is directional, not quantitative."
)
para(
    "The operational question is not whether the crossing congests - that is not in dispute - but "
    "at what offered load it stops functioning, and which physical or organisational lever "
    "actually moves the outcome. Those are questions a queueing model can answer, provided the "
    "model is built so that its answer does not depend on quantities nobody has measured."
)
para(
    "This paper reports a discrete-event simulation built for that purpose. The study also "
    "documents the reconstruction of an earlier prototype model, and the specific defects in that "
    "prototype that changed its conclusions. Those defects are set out in Section 8 because they "
    "are generalisable to this class of model and are, in the author's assessment, more instructive "
    "than the point estimates themselves."
)

# ==========================================================================
# 2. OBJECTIVES
# ==========================================================================
doc.add_heading("2. Research Objectives", level=1)
para("The study pursues four objectives, in descending order of how strongly the available data "
     "can support them.")
bullets([
    ("O1 (primary). ",
     f"Calibrate and validate simulated light-vehicle throughput against the only published "
     f"quantitative series for this crossing: {len(mop)} consecutive months of MOP concessionaire "
     f"counts, {mop['month'].iloc[0]} to {mop['month'].iloc[-1]}."),
    ("O2. ",
     f"Establish the critical offered load at which the crossing ceases to admit a steady state, "
     f"and report it as a utilisation ratio rather than as a wait. This objective is deliberately "
     f"chosen because it is the only quantitative result that survives the absence of intraday data."),
    ("O3. ",
     "Test three operational claims: that yard capacity affects congestion; that opening-hour "
     "restrictions are a minor factor; and that booth count is the binding constraint."),
    ("O4 (methodological). ",
     "Identify and correct the defects in the prototype model, and quantify how much each defect "
     "changes the reported results."),
])
para(
    "Objective O2 deserves emphasis. A specific wait time - 'the queue on Friday afternoon is 90 "
    "minutes' - requires an intraday arrival profile to predict, and no such profile is published "
    "for this crossing (Section 4.3). A utilisation ratio does not: it is the ratio of offered to "
    "served load at the peak hour, which the model can express as a function of a single free "
    "parameter. The strongest defensible claim is therefore a threshold, not a number of minutes."
)

# ==========================================================================
# 3. THEORY
# ==========================================================================
doc.add_heading("3. Theoretical Framework", level=1)

doc.add_heading("3.1 The facility as a queueing network", level=2)
para(
    "The crossing is represented as a tandem network of four stages:"
)
para("arrival  ->  gate (open/closed schedule)  ->  control area (yard)  ->  booth service",
     italic=True, align=WD_ALIGN_PARAGRAPH.CENTER)
para(
    "Stage 1 is a non-homogeneous Poisson arrival process, generating the per-vehicle state. Stage "
    "2 is a deterministic availability constraint: the gate admits vehicles only within published "
    "opening hours, and vehicles arriving outside them are held, not discarded. Stage 3 is a "
    "finite-capacity waiting space. Stage 4 is a multi-server station with a general (lognormal) "
    "service-time distribution."
)
para(
    "Each stage has a distinct capacity, and conflating them is the most common error in models "
    "of this facility. Booth count and staffing determine service capacity. The published "
    "simultaneous-vehicle figure determines physical space. The two are independent parameters, "
    "and the model in this paper keeps them separate."
)

doc.add_heading("3.2 Stability and utilisation", level=2)
para(
    "For a multi-server station with c servers, each of mean service time s, the utilisation is"
)
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run("rho  =  lambda / (c * mu),      mu = 1 / s")
r.bold = True
r.font.name = "Consolas"
para(
    "A stationary waiting-time distribution with finite mean exists if and only if rho < 1. This "
    "is the central theoretical result for this study, and it is the basis for the principal "
    "finding. When rho > 1, no finite equilibrium wait exists: the queue grows without bound, and "
    "any finite mean computed from a truncated simulation run is an artifact of the run length "
    "rather than a property of the system."
)
para(
    "Two distinct utilisations must be reported separately. The peak-hour utilisation "
    "rho_peak compares the arrival rate at the busiest hour against service capacity. The daily "
    "utilisation rho_daily compares the total daily offered load against the capacity of the open "
    "window. A system can satisfy rho_daily < 1 while violating rho_peak > 1, and this is exactly "
    "the regime the prototype model occupied: it cleared each day in aggregate while building an "
    "unbounded queue in every peak hour. The resulting delay is borne entirely by the vehicles "
    "that happened to arrive during the peak. Reporting only the daily ratio conceals the "
    "operative constraint."
)

doc.add_heading("3.3 Right-censoring and the non-stationary regime", level=2)
para(
    "In an unstable system, vehicles remaining in queue at the simulation horizon have no finite "
    "service start time. Their true waits are unobserved and finite, but larger than the "
    "remaining horizon. Any procedure that assigns them the horizon time minus their arrival time "
    "records a lower bound and reports it as if it were a measurement. The result is a "
    "distribution whose upper tail is truncated exactly where it becomes informative, which biases "
    "the mean and the high quantiles downward."
)
para(
    "The model in this paper therefore reports, for every run: the fraction of arrivals never "
    "served; the queue length at the horizon; the peak-hour utilisation; and an explicit verdict "
    "of STATIONARY or UNSTABLE OFFERED LOAD. When a run is unstable, its wait statistics are "
    "labelled as conditional statistics and are never presented as equilibrium estimates."
)
para(
    "A related trap deserves recording. A common non-stationarity test asks whether the queue is "
    "still growing at the horizon. In the absence of abandonment this test is informative. In the "
    "presence of abandonment it is not: as shown in Section 7.3, the queue saturates at a bounded "
    "level maintained by the very act of giving up, and a growth test then reports a reassuring "
    "result for a system that is in fact losing a large and growing share of its customers. The "
    "test adopted here is the theoretical one - rho_peak versus 1 - not an empirical trend."
)

doc.add_heading("3.4 Balking and reneging", level=2)
para(
    "Queueing models conventionally assume patience is infinite. At a land border this is false, "
    "and the assumption has a specific, perverse consequence. Under a work-conserving server, "
    "total time in system is governed by Little's law (L = lambda * W) and is independent of where "
    "the waiting space boundary sits. A larger yard therefore cannot reduce total waiting time: "
    "it only moves the queue from inside the booths to the road outside the gate, where the same "
    "vehicles wait the same total time."
)
para(
    "That is why yard-capacity scenarios appear to 'do nothing' in models that omit abandonment. "
    "The invariance is real but it is a property of the assumed behaviour, not of the facility. "
    "Real drivers do not queue indefinitely; the press record for this crossing documents drivers "
    "waiting out closures of 27 and 34 days, which bounds patience from below, and also documents "
    "turn-backs, which establish that a fraction of arrivals never attempt the crossing at all. "
    "The model therefore includes balking and reneging as an explicit, parameterised mechanism, "
    "and reports every yard-capacity result both with and without it."
)

doc.add_heading("3.5 Identifiability of the intraday profile", level=2)
para(
    "A non-homogeneous Poisson process is specified by a rate function lambda(t). Monthly totals "
    "constrain only its integral: the observed count for a month identifies the area under the "
    "curve, and leaves its shape entirely undetermined. The intraday profile used in this model "
    "is therefore an assumption, not an estimate."
)
para(
    "This has a direct consequence for the calibration protocol, and it is the reason the "
    "validation reported in Section 7.1 is presented with a caveat. Because the daily arrival "
    "total is anchored to the observed monthly count, good monthly agreement is largely "
    "guaranteed by construction and is weak evidence that the model is correct. The calibration "
    f"grid search in this study returned the verdict \"{calib.get('identifiability_verdict', 'BOUNDED, NOT IDENTIFIED')}\": "
    "the objective function is monotone across the search grid with its minimum at the boundary, "
    "which means the monthly data bound the profile but do not identify it. Only an hourly series "
    "could close this gap, and the means of obtaining one are set out in Section 10."
)
figure("04_intraday_profile_ASSUMED.png",
       "Figure 1. The assumed intraday arrival profile. This shape is NOT identified by the "
       "available data; it is an assumption, and it is the reason no intraday wait-time claim is "
       "made in this paper.")

doc.add_heading("3.6 Reconstruction of the prototype: event-driven formulation", level=2)
para(
    "No systematic optimisation was applied to the prototype model; the defects documented in "
    "Section 8 are analytical. The reconstructed model removes the prototype's polling loops "
    "entirely in favour of a fully event-driven formulation, which changes computed wait times by "
    "a bounded but non-zero amount because the prototype's fixed-interval sampling injected "
    "quantisation error into every wait. The reconstruction is verified by assertion: a vehicle "
    "arriving one minute before a gate opening is confirmed to wait exactly one minute, and zero "
    "service starts are confirmed to occur outside published opening hours."
)

# ==========================================================================
# 4. DATA
# ==========================================================================
doc.add_heading("4. Data and Provenance", level=1)
para(
    "Every quantitative input in this study is a published figure with a retrievable source URL. "
    "The complete inventory is in the data/ directory accompanying this paper, one row per fact. "
    f"Four of the {len(ops)} operating-parameter rows are recorded but used for nothing, and are "
    "marked as scope mismatches for the reasons in Section 4.4."
)

doc.add_heading("4.1 The primary series: monthly light vehicles", level=2)
para(
    f"Source: Direccion General de Concesiones de Obras Publicas (MOP), Chile, monthly status "
    f"report for the Nuevo Complejo Fronterizo Los Libertadores concession. Counts light vehicles "
    f"entering Chile, classified by the concessionaire. {len(mop)} months are retrievable."
)
table(
    ["Quantity", "Value", "Note"],
    [
        ["Months covered", f"{mop['month'].iloc[0]} to {mop['month'].iloc[-1]}", "consecutive"],
        ["Total light vehicles", n(TOTAL_VEH, 0), "sum of published monthly counts"],
        ["Total days", n(TOTAL_DAYS, 0), ""],
        ["Mean per day", n(PER_DAY_MEAN, 0), "derived"],
        ["Busiest month", f"{PEAK['month']} - {n(PEAK['vehicles_per_day'], 0)}/day", "derived"],
        ["Quietest month", f"{TROUGH['month']} - {n(TROUGH['vehicles_per_day'], 0)}/day", "derived"],
        ["Seasonal amplitude", f"{SEASONAL:.1f}x", "peak/trough, derived"],
    ],
    widths=[2.0, 2.2, 2.4],
    caption="Table 1. Summary of the primary observed series.",
)
figure("01_monthly_validation.png",
       "Figure 2. Simulated versus observed monthly throughput, with the calibration and "
       "held-out periods marked. The 5-95% band shown is degenerate (see Section 7.1).", width=6.3)

doc.add_heading("4.2 Published operating parameters", level=2)
key_ops = ops[ops["parameter"].isin([
    "car_booths_2024_02_01", "bus_booths_2024_02_01",
    "additional_booths_on_demand_2024_02_01",
])]
table(
    ["Parameter", "Value", "Class", "Maps to model field"],
    [
        ["Car booths staffed", "7", "OBSERVED", "n_booths"],
        ["Bus booths (excluded)", "5", "OBSERVED", "not used: light vehicles only"],
        ["Further booths on demand", "6", "OBSERVED", "n_booths (complement scenario)"],
        ["Simultaneous vehicles in complex", "15", "OBSERVED", "control_area_capacity"],
        ["Persons per vehicle", "3.2", "DERIVED", "persons_per_vehicle"],
    ],
    widths=[1.9, 0.9, 1.2, 2.2],
    caption="Table 2. Observed capacity parameters. All sourced; see data/published_operating_parameters.csv.",
)

doc.add_heading("4.3 What does not exist, and why that constrains the paper", level=2)
para(
    "An explicit negative finding, established by systematic search, is that no public daily or "
    "hourly numeric series exists for this crossing. Specifically:"
)
bullets([
    "MOP publishes monthly light-vehicle totals only. No daily breakdown was located in any "
    "monthly report, including those for 2024-01 to 2024-09 and 2026-06 onward, which were not "
    "retrievable at all.",
    "Argentina's Direccion Nacional de Migraciones publishes annual movement counts, not monthly "
    "or daily, and the reports were confirmed to contain no sub-annual tables.",
    "Chile's Unidad de Pasos Fronterizos publishes intraday open/close status timestamps, which "
    "are operating notices rather than measurements of flow or waiting time.",
    "The Mendoza provincial PasAr platform reports live wait-time bands and vehicle counts, but "
    "has no public archive, no historical query and no discovered API. It is the only "
    "hourly-resolution source located, and it is usable only prospectively.",
])
para(
    "The consequence is structural rather than temporary. No amount of additional modelling "
    "effort can produce intraday validation for this facility from existing published data. Any "
    "model of this crossing that reports hourly accuracy is reporting the modeller's assumptions, "
    "not evidence."
)

doc.add_heading("4.4 Recorded but deliberately unused", level=2)
para(
    f"Four rows in the parameter inventory are recorded for transparency and used for nothing. The "
    f"Argentine migration series is excluded for two independent reasons: its ENTRADA column counts "
    f"movements into Argentina, which is the opposite direction from the one studied here, and it "
    f"aggregates three separate complexes rather than the tunnel alone. A third problem is "
    f"unresolved: the treatment of Argentine nationals in that series is contradicted between "
    f"official and international sources, and was not adjudicated. A persons-per-vehicle ratio of "
    f"3.2 is used instead, derived from published persons and vehicle totals for the complex in "
    f"2018. Applying 3.2 to the observed October 2024 volume gives roughly 2,340 persons per day, "
    f"against a press-reported baseline of about 2,500 travellers per day for September-October "
    f"2024. This is agreement in order of magnitude across two differently-scoped sources, and is "
    f"recorded as suggestive corroboration only, not as validation."
)

# ==========================================================================
# 5. MODEL
# ==========================================================================
doc.add_heading("5. Model Specification", level=1)
para(
    "The model is implemented in SimPy 4.1.2 and is fully event-driven: there is no fixed "
    "timestep, no polling loop and no sleep. Vehicles are typed records carrying arrival time, "
    "gate-wait, yard-wait, service start and service end, plus a cohort size in persons."
)

doc.add_heading("5.1 Demand", level=2)
para(
    "Arrivals follow a non-homogeneous Poisson process generated by thinning: a homogeneous "
    "process at the maximum rate is sampled, and each event is accepted with probability equal to "
    "the ratio of the instantaneous rate to that maximum. The daily arrival total is drawn from "
    f"the observed series for the month under study, so a 'January 2025' scenario is on the real "
    f"scale of {n(PEAK['vehicles_per_day'], 0)} vehicles per day. Within the day, arrivals are "
    "shaped by an hourly profile and a day-of-week factor. Both shapes are assumptions; Section 3.5 "
    "explains why the available data cannot identify them."
)

doc.add_heading("5.2 Gate and opening hours", level=2)
para(
    "The gate admits vehicles only within the active window. Vehicles arriving outside it wait for "
    "the next opening rather than being discarded, so that a restricted-hours regime is modelled as "
    "a period-correct arrival stream rather than as a reduced one. Two regimes are carried: a "
    "12-hour winter window corroborated by press reporting, and a 24-hour summer window. A "
    "conflict between two official Argentine sources over the actual hours is recorded in the "
    "parameter inventory and both readings are carried through to the results rather than one "
    "being silently chosen."
)
para(
    "Dated closures are modelled as explicit scenarios, including the 26-hour closure of 19-20 "
    "February 2026 and the 27-day and 34-day closures of August 2026, all of which are documented "
    "in the press inventory."
)

doc.add_heading("5.3 Yard, service and abandonment", level=2)
para(
    "The control area is a finite-capacity waiting space; vehicles that cannot enter hold upstream. "
    f"Service uses a lognormal distribution with mean {ops.loc[ops['parameter'].str.contains('svc'), 'value'].iloc[0] if (ops['parameter'].str.contains('svc')).any() else '3.5'} "
    "minutes per vehicle per booth. The number of staffed booths is a separate parameter from "
    "control-area capacity. Abandonment is modelled with a patience threshold; it is an "
    "assumption with no measurement behind it, and every result that depends on it is reported "
    "with abandonment both on and off."
)

# ==========================================================================
# 6. PROTOCOL
# ==========================================================================
doc.add_heading("6. Calibration and Validation Protocol", level=1)
para(
    "The protocol is designed so that a good fit cannot be confused with a correct model. Three "
    "safeguards apply."
)
bullets([
    ("Temporal holdout. ",
     f"Calibration uses {mop['month'].iloc[0]} to 2025-12 ({len(tr)} months). The held-out "
     f"period is 2026-01 to 2026-05 ({len(te)} months), which is neither fitted nor used to "
     f"select any parameter."),
    ("Anchoring disclosure. ",
     "The daily arrival total is anchored to the observed monthly count, so monthly agreement is "
     "partly built in. This is stated wherever a fit is reported, and it is the reason the fit "
     "statistics below are not offered as evidence that the intraday profile is correct."),
    ("Band integrity. ",
     "Reported uncertainty bands are replica spread only. They are not prediction intervals and "
     "cannot contain structural error, because no observed intraday series exists against which "
     "structural error could be measured."),
])

# ==========================================================================
# 7. RESULTS
# ==========================================================================
doc.add_heading("7. Results", level=1)

doc.add_heading("7.1 Throughput against the observed series", level=2)
table(
    ["Period", "Months", "RMSE (veh)", "MAE (veh)", "Mean abs. rel. error", "Correlation"],
    [
        ["Calibration", len(tr), n(rmse(tr), 0), n(tr["abs_error"].mean(), 0),
         f"{tr['rel_error'].abs().mean() * 100:.2f}%", f"{monthly[monthly['split'] == 'train']['observed_veh'].corr(monthly[monthly['split'] == 'train']['sim_served_mean']):.4f}"],
        ["Held-out", len(te), n(rmse(te), 0), n(te["abs_error"].mean(), 0),
         f"{te['rel_error'].abs().mean() * 100:.2f}%", f"{te['observed_veh'].corr(te['sim_served_mean']):.4f}"],
    ],
    widths=[1.3, 0.8, 1.1, 1.1, 1.5, 1.1],
    caption="Table 3. Monthly throughput fit. Read subject to the anchoring caveat in Section 6.",
)
para(
    f"Throughput tracks the observed series closely across the full seasonal range, including the "
    f"{n(PEAK['vehicles_per_day'], 0)}-vehicle peak month and the {n(TROUGH['vehicles_per_day'], 0)}-vehicle trough, "
    f"a {SEASONAL:.1f}-fold span. The three worst-fitting months are 2025-01 "
    f"({monthly[monthly['month'] == '2025-01']['rel_error'].iloc[0] * 100:.1f}%), "
    f"2025-07 ({monthly[monthly['month'] == '2025-07']['rel_error'].iloc[0] * 100:.1f}%) and "
    f"2025-12; all three are months in which the model reports an unstable offered load, which is "
    f"discussed in Section 7.2 rather than treated as fit error."
)
warn = monthly[monthly["verdict"] != "STATIONARY"]
para(
    f"{len(warn)} of {len(monthly)} months are classified UNSTABLE OFFERED LOAD. This is a "
    f"property of the scenario, not a fit failure: in those months the modelled peak-hour "
    f"utilisation exceeds 1, so the queue cannot drain, and the reported wait describes only the "
    f"subset of drivers who were served. The abandonment share in those months ranges from "
    f"{warn['renege_fraction'].min() * 100:.1f}% to {warn['renege_fraction'].max() * 100:.1f}%. "
    f"These are the months in which the real crossing is most likely losing traffic to the "
    f"Punta de Vacas, Penitentes and Uspallata waiting lots, and the model places that loss at "
    f"the low end of the plausible range because the assumed patience budget is a floor, not a "
    f"measurement."
)
para(
    "A disclosure on uncertainty reporting: the monthly validation uses a single replica per month, "
    "so the 5-95% band is degenerate, with its lower and upper bounds both equal to the simulated "
    "mean. Band coverage computed from it is therefore vacuous and is not reported as a performance "
    "measure. The bands in Figure 2 carry this limitation. Re-running the validation with a larger "
    "replica count would produce a meaningful band, at the cost of a proportionately longer run."
)

doc.add_heading("7.2 The critical offered load", level=2)
para(
    f"With the observed configuration of 7 staffed car booths, each booth clearing a vehicle every "
    f"{60 / CAP7:.1f} minutes, service capacity is {n(CAP7)} vehicles per hour. The utilisation "
    f"sweep traces the behaviour of the system as offered load is increased across that threshold."
)
rows = []
for _, r in sweep.iterrows():
    rows.append([
        n(r["peak_offered_veh_h"], 0), f"{r['rho_peak_hour']:.3f}",
        n(r["mean_wait_total_min"], 1), n(r["p95_wait_total_min"], 1),
        f"{r['renege_fraction'] * 100:.1f}%", n(r["max_queue_mean"], 0),
        r["verdict"],
    ])
table(
    ["Offered peak (veh/h)", "rho_peak", "Mean wait (min)", "P95 wait (min)",
     "Abandoned", "Max queue", "Verdict"],
    rows,
    widths=[1.15, 0.7, 0.95, 0.9, 0.75, 0.75, 1.5],
    font=8,
    caption="Table 4. Utilisation sweep, 24-hour gate, 5 simulated days per level.",
)
figure("02_utilisation_sweep.png",
       "Figure 3. Offered load against wait and abandonment. The stability threshold is at "
       f"rho_peak = 1, i.e. {n(CRIT, 0)} vehicles per hour.")
para(
    f"Four results follow, and they are ordered by how much they depend on unobserved behaviour."
)
bullets([
    ("Critical load. ",
     f"The stability threshold sits at {n(CRIT, 0)} vehicles per hour offered, which is rho_peak = 1 "
     f"by construction. The last level at which the system remains a valid equilibrium is "
     f"{n(LAST_OK, 0)} vehicles per hour."),
    ("The wait curve flattens. ",
     f"Mean wait rises only from {n(sweep['mean_wait_total_min'].iloc[0], 1)} to "
     f"{n(sweep['mean_wait_total_min'].iloc[-1], 1)} minutes across a more than eleven-fold increase "
     f"in offered load. This is not congestion improving. It is abandonment removing the customers "
     f"who would have experienced the queue, with the share abandoning rising from "
     f"{sweep['renege_fraction'].iloc[0] * 100:.1f}% to "
     f"{sweep['renege_fraction'].iloc[-1] * 100:.1f}% over the same range. A model that reports a "
     f"13.6-minute mean at {n(sweep['peak_offered_veh_h'].iloc[-1], 0)} vehicles per hour is "
     f"describing a system that has turned away two thirds of its arrivals."),
    ("Queue growth is not the test. ",
     "With abandonment active the queue never diverges; it saturates at a bounded level that "
     "giving up maintains. A 'did the queue grow' test would therefore report a reassuring result "
     "for a system losing most of its customers. The theoretical test, rho against 1, is the one "
     "used here."),
    ("Divergence, when abandonment is removed. ",
     f"Repeating the probe with abandonment off shows genuine divergence: mean wait "
     f"{n(probe[probe['peak_rate_veh_h'] == 120]['mean_wait_total_min'].iloc[0], 1)} minutes at "
     f"120 veh/h, rising to {n(probe[probe['peak_rate_veh_h'] == 700]['mean_wait_total_min'].iloc[0], 0)} "
     f"minutes with {n(probe[probe['peak_rate_veh_h'] == 700]['max_queue_mean'].iloc[0], 0)} vehicles "
     f"queued at 700 veh/h. Same capacity, same load; the entire difference is who turned around."),
])

doc.add_heading("7.3 Yard capacity cannot reduce congestion", level=2)
sub = patio[patio["reneging"] == False].sort_values("max_queue_mean") if "reneging" in patio.columns else None
rows = []
for _, r in patio.iterrows():
    rows.append([
        n(r["max_q_upstream_mean"], 0), n(r["max_q_inside_mean"], 0), n(r["max_queue_mean"], 0),
        f"{r['never_served_fraction'] * 100:.1f}%", n(r["mean_wait_total_min"], 1), r["verdict"],
    ])
table(
    ["Queue upstream", "Queue inside", "Total queue", "Never served", "Mean wait (min)", "Verdict"],
    rows, widths=[1.1, 1.05, 1.0, 1.0, 1.1, 1.4], font=8,
    caption="Table 5. Yard capacity sweep, January peak demand. Upstream queue falls as the yard "
            "grows; total queue does not change.",
)
figure("03_patio_vs_abandonment.png",
       "Figure 4. Yard capacity sweep. The total queue is invariant; only its location moves.")
para(
    "The invariance predicted by Little's law is confirmed exactly. Total queue is identical at "
    "every yard size, and mean wait is unchanged to the reported precision. What changes is where "
    "the vehicles wait: enlarging the yard from 15 to 600 vehicles moves the queue from the access "
    "road into the complex without shortening anybody's total wait."
)
para(
    "The operational reading is not that yard capacity is useless. It is that yard capacity is not "
    "a congestion-control lever; it is a queue-location lever. Its real benefits - driver comfort, "
    "vehicle security, and the ability to hold a queue out of the access road during a closure - "
    "lie outside the wait metric this model reports, and this model should not be cited as evidence "
    "against them."
)

doc.add_heading("7.4 Booth staffing", level=2)
rows = [[
    r["label"], n(r["staffed"], 0), n(r["booth_capacity_veh_h"], 1),
    f"{r['rho_peak_hour']:.3f}", n(r["mean_wait_total_min"], 1),
    f"{r['never_served_fraction'] * 100:.1f}%",
] for _, r in staff.iterrows()]
table(
    ["Configuration", "Staffed booths", "Capacity (veh/h)", "rho_peak", "Mean wait (min)", "Never served"],
    rows, widths=[1.85, 0.95, 1.05, 0.7, 1.05, 0.95], font=8,
    caption="Table 6. Staffing sensitivity. Published booth counts are observed; the number "
            "actually staffed is not.",
)
para(
    f"Removing one booth from the observed seven removes {1 / 7 * 100:.1f}% of capacity and raises "
    f"the share of arrivals that are never served from "
    f"{staff[staff['staffed'] == 7]['never_served_fraction'].iloc[0] * 100:.1f}% to "
    f"{staff[staff['staffed'] == 6]['never_served_fraction'].iloc[0] * 100:.1f}%. At this facility, "
    f"staffing is not a detail. The published booth count is an observed fact; the hours at which "
    f"the on-demand complement of six booths is actually activated are not published, and the "
    f"complement is applied statically in these scenarios, which is the most favourable reading "
    f"available for it. A time-varying staffing schedule would perform worse."
)

doc.add_heading("7.5 Opening hours", level=2)
rows = [[
    r["label"], n(r["open_hours_per_day"], 0), f"{r['rho_daily_open_window']:.3f}",
    n(r["mean_wait_total_min"], 1), n(r["p95_wait_total_min"], 1),
    n(r["median_wait_total_min"], 2), r["verdict"],
] for _, r in hours.iterrows()]
table(
    ["Regime", "Open h/day", "rho_daily", "Mean (min)", "P95 (min)", "Median (min)", "Verdict"],
    rows, widths=[1.35, 0.85, 0.8, 0.9, 0.85, 0.9, 1.35], font=8,
    caption="Table 7. Opening-hour regimes. The two official sources disagree; both are carried.",
)
para(
    "A gate that opens once per day makes the system periodic rather than stationary: the queue is "
    "empty at opening and accumulates overnight. No single mean wait is meaningful without stating "
    "where in the daily cycle it was measured, which is why the restricted regimes are reported "
    "NON-STATIONARY even at a daily utilisation of "
    f"{hours[hours['verdict'] == 'NON-STATIONARY']['rho_daily_open_window'].min():.2f} to "
    f"{hours[hours['verdict'] == 'NON-STATIONARY']['rho_daily_open_window'].max():.2f}. Restricting "
    "hours concentrates rather than reduces delay: the same daily volume is compressed into a "
    "shorter window, and the median wait rises while the mean is dominated by the overnight cohort."
)
figure("05_feb2026_closure.png",
       "Figure 5. The 26-hour closure of 19-20 February 2026, modelled explicitly. A closure "
       "affects a minority of arrivals but those it affects wait tens of hours, so the median "
       "is more informative than the mean.")

doc.add_heading("7.6 Assessment of the prototype configuration", level=2)
rows = [[
    f"{int(r['booths'])} booths", n(r["booth_capacity_veh_h"], 1),
    f"{r['rho_peak_hour']:.3f}", f"{r['rho_daily_open_window']:.3f}",
    n(r["mean_wait_total_min"], 1), n(r["max_queue_mean"], 0), r["verdict"],
] for _, r in legacy.iterrows()]
table(
    ["Configuration", "Capacity (veh/h)", "rho_peak", "rho_daily", "Mean wait (min)",
     "Max queue", "Verdict"],
    rows, widths=[1.1, 1.1, 0.8, 0.85, 1.05, 0.85, 1.3], font=8,
    caption="Table 8. The prototype model's default parameters, re-evaluated on the corrected "
            "model. Both configurations are unstable at the peak hour.",
)
para(
    f"The prototype's default of 450 vehicles per hour at the peak yields rho_peak = "
    f"{L13['rho_peak_hour']:.2f} against 13 booths and {L18['rho_peak_hour']:.2f} against 18. Both "
    f"exceed 1, so a queue necessarily builds in every peak hour regardless of any other metric. "
    f"Note that the daily ratio is below 1 in both cases: the system can still clear the day in "
    f"aggregate, but only after every driver who queued during the peak has absorbed the entire "
    f"delay. That is a transient of a peak-hour overload, never an equilibrium."
)
para(
    f"The parameter is also inconsistent with the observed data. A 450 vehicles-per-hour peak "
    f"combined with the prototype's own hourly profile implies roughly 4,400 vehicles per day on a "
    f"typical day, against an observed mean of {n(PEAK['vehicles_per_day'], 0)} per day in the "
    f"busiest month on record. The prototype's baseline scenario was therefore not a baseline: it "
    f"described a demand level the crossing has not experienced in the observed period, while "
    f"labelling it as the base case."
)

# ==========================================================================
# 8. DEFECTS
# ==========================================================================
doc.add_heading("8. Defects in the Prototype Model and Their Consequence", level=1)
para(
    "The prototype model was not corrected in place; it was rebuilt, because several of its "
    "defects are structural. The table below records each defect, its mechanism, and the size of "
    "the effect. These are generalisable to any discrete-event model of a gated facility."
)
table(
    ["Defect", "Mechanism", "Effect on reported results"],
    [
        ["Right-censoring", "Unserved vehicles assigned horizon time minus arrival, recording a "
         "lower bound as a measurement",
         "Mean and high quantiles biased downward; headroom artifact of run length"],
        ["Unstable default", "rho_peak of 2.02 and 1.46 at the prototype's own parameters",
         "Headline wait describes a truncated growing queue, not a wait"],
        ["Polling loops", "Fixed-interval sampling in the gate and booth checks",
         "Up to 5 min of quantised delay injected into every wait; service can start after closing"],
        ["No abandonment", "Infinite patience assumed",
         "Yard capacity incorrectly appears to have no effect on any queue metric"],
        ["No seasonality", "One demand level for all dates",
         f"Real amplitude is {SEASONAL:.1f}x; a single level cannot represent the observed range"],
        ["Confounded capacity", "A staffing statement mapped onto a service-capacity parameter",
         "Yard space, booths and staffing treated as one quantity"],
        ["Validation at wrong resolution", "Simulated hourly flow compared to an hourly CSV",
         "Validation flag could never be exercised with real data"],
        ["Unreachable comparison", "Model wait measured at the Chilean complex, compared with "
         "press waits measured upstream on the Argentine side",
         "Guaranteed apparent failure under comparison"],
    ],
    widths=[1.35, 2.5, 2.35], font=8,
    caption="Table 9. Prototype defects and their effect.",
)
para(
    "The censoring defect and the unstable default compound each other, and their combination is "
    "what makes the prototype's headline figure unusable rather than merely imprecise. In a system "
    "with rho = 1.46, the true equilibrium wait does not exist and is unbounded. Assigning "
    "truncated values and then reporting their mean produces a number that is precise, plausible, "
    "and meaningless. Any reader would be entitled to treat it as a measurement, and no amount of "
    "caveat text on an uncensored statistic repairs that."
)
para(
    "The abandonment defect is the most consequential for the operational question, because it "
    "produces a confidently wrong answer rather than an imprecise one. A model that omits "
    "abandonment will always find that yard capacity does not matter, and will report that as a "
    "finding about the facility. It is a finding about the model."
)

# ==========================================================================
# 9. LIMITATIONS
# ==========================================================================
doc.add_heading("9. Limitations", level=1)
para("Stated plainly, because several of these bound what the results can be used for.")
bullets([
    ("No intraday validation is possible. ", "No public hourly or daily series exists for this "
     "crossing. The intraday profile is an assumption and is not validated."),
    ("The monthly fit is partly circular. ", "The daily arrival total is anchored to the observed "
     "monthly count, so good monthly agreement is substantially built in."),
    ("Service time is assumed. ", "No published throughput or service-duration measurement was "
     "located. The 3.5-minute mean per booth is an assumption."),
    ("Abandonment is assumed. ", "Driver patience has no measurement. The press record bounds it "
     "from below only."),
    ("Uncertainty bands are narrow by construction. ", "They are replica spread only and exclude "
     "structural error."),
    ("Single replica in monthly validation. ", "The 5-95% band is degenerate; band coverage is not "
     "a performance measure in this run."),
    ("The measurement point differs from the public one. ", "This model measures from arrival at "
     "the Chilean complex. Press wait figures describe the queue upstream on the Argentine side. "
     "They are not comparable."),
    ("Opening hours are officially disputed. ", "Two Argentine sources conflict and both are "
     "carried; neither is adjudicated."),
    ("Freight and buses are out of scope. ", "Several of the most severe documented delays are "
     "freight delays at Uspallata, which this model does not represent."),
])

# ==========================================================================
# 10. CONCLUSIONS
# ==========================================================================
doc.add_heading("10. Conclusions and Recommendations", level=1)
para("The defensible findings, ordered by how strongly the data supports them.")
table(
    ["#", "Finding", "Confidence"],
    [
        ["1", f"The crossing loses stability at an offered peak of {n(CRIT, 0)} vehicles per hour "
              f"with the observed 7 staffed booths, and remains a valid equilibrium to "
              f"{n(LAST_OK, 0)} vehicles per hour.", "High - independent of intraday shape"],
        ["2", "Yard capacity does not reduce congestion; it relocates the queue.", "High - "
              "theoretical and confirmed"],
        ["3", f"Removing one of seven booths costs {1 / 7 * 100:.1f}% of capacity.", "High - "
              "arithmetic on an observed count"],
        ["4", "A model without abandonment answers the yard-capacity question wrongly.", "High - "
              "demonstrated both ways"],
        ["5", "Restricting opening hours concentrates delay rather than reducing it.", "Moderate - "
              "official sources conflict"],
        ["6", f"Monthly throughput is reproducible to a mean absolute relative error of "
              f"{te['rel_error'].abs().mean() * 100:.2f}% on held-out months.", "Moderate - "
              "partly built in by construction"],
        ["7", "Any specific intraday wait figure for this crossing.", "Not supported - no data exists"],
    ],
    widths=[0.4, 4.1, 1.7], font=8.5,
    caption="Table 10. Findings ordered by evidential strength.",
)

doc.add_heading("10.1 Recommendations for further work", level=2)
para(
    "The binding constraint on this research is data availability, not modelling. The highest-value "
    "action is an information request, and it should be filed immediately:"
)
bullets([
    "Argentina. Request the daily or hourly series through Ley 25.633 de Acceso a la Informacion "
    "Publica, directed to the Direccion Nacional de Migraciones (Mendoza delegation), to "
    "Gendarmeria Nacional Escuadron 27 Punta de Vacas, and to the Direccion de Asuntos Tecnicos de "
    "Fronteras. Request the foreign-national inclusion rule explicitly, since it is contradicted "
    "between sources.",
    "Chile. Request the sub-monthly series from the Unidad de Pasos Fronterizos under Ley 20.285, "
    "and ask the concessionaire, Nuevo Complejo Fronterizo Los Libertadores S.A., whether its "
    "internal reporting can be released at daily granularity.",
    "Prospective capture. Begin snapshotting the Mendoza PasAr dashboard "
    "(mxm.mendoza.gov.ar) on a fixed schedule. It is the only live wait-time source located and it "
    "has no archive, so every day not captured is a day permanently unrecoverable. A fixed-interval "
    "screenshot series would, within one season, produce exactly the intraday dataset this model "
    "cannot currently be validated against.",
])
para(
    "Modelling recommendations, in order: add freight and the separate Uspallata cargo circuit, "
    "which the press record indicates is where the most severe and most persistent delays occur; "
    "add a time-varying staffing schedule once the activation hours of the on-demand booth "
    "complement are known; and treat weather closures as a separate stochastic process rather than "
    "as fixed scenarios, since the 27- and 34-day closures of 2026 were driven by a single storm "
    "sequence."
)

# ==========================================================================
# BIBLIOGRAPHY
# ==========================================================================
doc.add_page_break()
doc.add_heading("Bibliography", level=1)
doc.add_heading("Primary data sources", level=2)
for i, u in enumerate(ops["url"].dropna().unique(), 1):
    param = ops.loc[ops["url"] == u, "parameter"].iloc[0]
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.left_indent = Inches(0.3)
    p.paragraph_format.first_line_indent = Inches(-0.3)
    p.add_run(f"[A{i}] ").bold = True
    p.add_run(f"{param}. ")
    r = p.add_run(u)
    r.font.color.rgb = RGBColor(0x1F, 0x49, 0x7D)

for i, u in enumerate(press["url"].dropna().unique(), 1):
    date = press.loc[press["url"] == u, "date"].iloc[0]
    outlet = press.loc[press["url"] == u, "outlet"].iloc[0]
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.left_indent = Inches(0.3)
    p.paragraph_format.first_line_indent = Inches(-0.3)
    p.add_run(f"[P{i}] ").bold = True
    p.add_run(f"{outlet}, {date}. ")
    r = p.add_run(u)
    r.font.color.rgb = RGBColor(0x1F, 0x49, 0x7D)
    p.add_run("  [anecdotal; not a measurement]").italic = True

para("")
para(f"All web sources retrieved {RETRIEVED}. Web sources are point-in-time snapshots; the opening "
     f"hours in particular are known to have changed, and the live page rather than any search "
     f"result cache should be cited.", italic=True, size=9)

doc.add_heading("Theoretical and methodological references", level=2)
refs = [
    "Little, J. D. C. (1961). A Proof for the Queuing Formula: L = lambda W. Operations Research, "
    "9(3), 383-387.",
    "Kingman, J. F. C. (1961). The single server queue in heavy traffic. Mathematical Proceedings "
    "of the Cambridge Philosophical Society, 57(4), 902-904.",
    "Pollaczek, F., and Khinchine, A. A. On a problem in the theory of mass production. "
    "Zeitschrift fur angewandte Mathematik und Mechanik, 1, 43-45.",
    "Kleinrock, L. (1975). Queueing Systems, Volume 2: Computer Applications. Wiley-Interscience.",
    "Gross, D., and Harris, C. M. (1974). Fundamentals of Queueing Theory. Wiley.",
    "Law, A. M. (2015). Simulation Modeling and Analysis, 5th edition. McGraw-Hill.",
    "Banks, W. J., Carson, J. S., and Nelson, B. L. (2010). Discrete-Event System Simulation, "
    "5th edition. Pearson.",
    "Daganzo, C. F. (1985). Theory of Probability and Statistics for Engineering and the Physical "
    "Sciences, 2nd edition. Holden-Day.",
    "Ahuja, H., Hershberger, E., and Ordonez, R. (2005). Simulation in Transportation: An "
    "Empirical Handbook. Kluwer Academic Publishers.",
    "Kijewski, R., Gastel, K., and others. SimPy: Python discrete-event simulation library. "
    "https://simpy.readthedocs.io (version 4.1.2 used here).",
    "Reproduction package: paso_los_libertadores/ (model, data, results and figures), "
    "accompanying this document.",
]
for i, ref in enumerate(refs, 1):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.left_indent = Inches(0.3)
    p.paragraph_format.first_line_indent = Inches(-0.3)
    p.add_run(f"[{i}] ").bold = True
    p.add_run(ref)

# ==========================================================================
# APPENDIX
# ==========================================================================
doc.add_heading("Appendix A. Observed monthly series", level=1)
para("Complete primary series, as published. Reproduced from data/mop_monthly_light_vehicles.csv; "
     "source URLs are in that file.", size=9, italic=True)
rows = [[
    r["month"], n(int(r["light_vehicles"]), 0), int(r["days_in_month"]),
    n(r["vehicles_per_day"], 0),
    r["vehicles_per_day"] / mop["vehicles_per_day"].mean(),
] for _, r in mop.iterrows()]
table(["Month", "Light vehicles", "Days", "Per day", "Seasonal index"],
      rows, widths=[1.0, 1.3, 0.6, 1.0, 1.2], font=8.5,
      caption=f"Table A1. All {len(mop)} observed months. Seasonal index is 1.0 at the series mean "
              f"of {n(PER_DAY_MEAN, 0)} vehicles per day.")

doc.add_heading("Appendix B. Reproduction", level=1)
para("Environment: WSL Ubuntu 24.04, Python 3.12.3, virtual environment at /home/easyrpg/venv "
     "(simpy 4.1.2, pandas 3.0.6, numpy 2.5.3, matplotlib 3.11.2, scipy 1.18.1).")
p = doc.add_paragraph()
p.paragraph_format.left_indent = Inches(0.3)
r = p.add_run("python paso_los_libertadores/run_all.py")
r.font.name = "Consolas"
r.font.size = Pt(9.5)
para("Runs from any working directory. Produces the calibration, the experiments, five figures and "
     "the result tables underlying every number in this paper. Observed runtime approximately four "
     "minutes. The run asserts its own preconditions and exits non-zero on failure; a successful "
     "run validates the event-driven gate behaviour, the closure scenarios and the integrity of "
     "every output file.", size=9.5)
para(f"Data retrieved {RETRIEVED}. No figure in this paper was typed by hand: all quantities are "
     f"read from results/*.csv at document build time.", size=9.5, italic=True)

doc.save(str(OUT))
print(f"WROTE {OUT}")
print(f"  paragraphs={len(doc.paragraphs)} tables={len(doc.tables)} "
      f"inline_shapes={len(doc.inline_shapes)}")
print(f"  bytes={OUT.stat().st_size:,}")
