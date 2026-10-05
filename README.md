# Paso Cristo Redentor — light vehicles, Mendoza → Chile

A research-grade, fully event-driven discrete-event simulation of the light-vehicle
border crossing at Paso Internacional Cristo Redentor (Los Libertadores), written in
SimPy and validated against the only public quantitative series that exists for this
crossing.

This package **rebuilds** an earlier script. It does not repair it. That script states no
conclusions of its own — it prints tables and one PNG — so nothing is being refuted here.
What it does is report numbers that cannot bear the weight they appear to carry, and those
are re-derived from source rather than inherited. Where a value could not be verified from
a citable source, it is labelled `ASSUMPTION-NOT-OBSERVED` rather than quietly presented
as fact.

---

## 1. What this model is, and what it is not

**It is** a discrete-event simulation of a single-server queueing network:

```
arrival → [gate: open/closed schedule] → [internal yard] → [control area] → [booth service]
                    wait_to_gate                    wait_inside          svc
```

Every state change is an event. There is no polling loop, no fixed 5-minute step and no
sleep. A vehicle arriving at 08:59 under a 09:00 opening waits **exactly** 1.0 minute;
this is asserted in code, not asserted in prose (`model.verify_event_driven_opening`).

**It is not** a forecast, and it does not predict a specific date's wait. Three limits are
structural, not fixable by more careful coding:

1. **No public hourly series exists.** MOP publishes monthly light-vehicle totals only.
   The intraday arrival profile is therefore an assumption. Monthly data cannot identify
   it — see §6.
2. **The demand total is anchored to the observed monthly count.** Monthly throughput
   therefore fits well *by construction*. That fit is not independent validation of the
   intraday shape, and the model never claims otherwise.
3. **Abandonment is unobserved.** Driver patience has no measurement. Results that depend
   on it are reported with abandonment both on and off, and the difference is stated as a
   property of the assumption, not of the crossing.

## 2. Data and provenance

Everything lives in `data/`, one row per fact, each with a source URL.

| file | rows | content |
|---|---|---|
| `mop_monthly_light_vehicles.csv` | 20 | MOP monthly light vehicles into Chile, 2024-10 … 2026-05 |
| `published_operating_parameters.csv` | 22 | booths, capacity, operating parameters, opening-hour conflict, out-of-scope figures |
| `press_wait_reports.csv` | 10 | press-reported waits, closures and capacities, **all marked `ANECDOTAL`** |

Every row in every file carries a source URL and a confidence class. Of the 22 parameter
rows, 19 are `OBSERVED`, 1 `DERIVED` and 2 `CONFLICTING` (the opening hours). Four rows are
deliberately marked **SCOPE MISMATCH** — the Argentine `SALIDA` series is the *wrong
direction* and aggregates three crossings, so it is recorded for transparency and used
for nothing.

Primary sources:

- MOP monthly statistics — <https://www.argentina.gob.ar/migraciones/estadisticas-movimientos-migratorios>
- Border opening hours — <https://www.argentina.gob.ar/interior/seguridadvial/horarios-de-fronteras>
- Vehicle/occupant ratio (3.199) — MOP 2018, 1,203,488 persons / 376,198 vehicles
- Press reports — see `press_wait_reports.csv` for per-row URLs

Re-derived from the raw MOP counts:

| quantity | value |
|---|---|
| total light vehicles, 608 days | 470,210 |
| mean per day | 773.37 |
| busiest month | 2025-01, 1615.61/day |
| quietest month | 2025-06, 265.77/day |
| peak/trough ratio | 6.08× |

**Scope caveat, stated up front.** This is the *light-vehicle* crossing only. Argentina's
published border statistics mix light vehicles, buses and heavy freight into a single
headline number, so the MOP totals used here are the light-vehicle series and nothing else.
Persons are reported separately using the derived 3.2 persons/vehicle and are never mixed
into vehicle counts.

## 3. Assumptions, and what happens if they are wrong

| parameter | value | status | if wrong |
|---|---|---|---|
| service time, mean | 3.5 min | **ASSUMPTION** (inherited) | scales all waits linearly |
| service time, CV | 0.6 | **ASSUMPTION** (inherited) | sets queue length, not mean |
| persons per vehicle | 3.2 | DERIVED (2018) | persons only; vehicles unaffected |
| staffing, persons per booth | 1.0 | **ASSUMPTION** | one empty booth = −14% capacity |
| internal yard ("patio") | 200 | **ASSUMPTION** | provably inert — see §5 |
| intraday arrival profile | prior + fitted exponent | **ASSUMPTION** | not identified by data |
| day-of-week factors | prior | **ASSUMPTION** | absorbed by month anchoring |
| abandonment, base patience | 45 min | **ASSUMPTION** | changes *who* is served |
| abandonment, closed-gate patience | 30 days | **ASSUMPTION**, press-anchored | understates multi-week patience |
| abandonment, queue sensitivity | 0.8 per 20 vehicles | **ASSUMPTION** | shapes the abandonment hazard |
| drain after arrivals | 2,880 min | **ASSUMPTION** | too short ⇒ backlog reported, not hidden |

Observed, not assumed: 7 car booths; 7 booths extended to 13 by an on-demand complement;
5 bus booths (excluded); 6 additional booths in the "mixed patio"; 1,203,488 persons and
376,198 vehicles; 15 simultaneous vehicles inside the complex (up from 3–5); upstream
waiting lots at Uspallata, Penitentes, Punta de Vacas and Puente del Inca.

## 4. The opening-hours conflict

Three sources disagree, and the model refuses to pick one silently:

| regime | window | source |
|---|---|---|
| `winter_press` | 09:00–21:00 | press reports for the May–September regime |
| `argentina_0930_2030` | 09:30–20:30 | argentina.gob.ar |
| `argentina_24h` | 24 h | argentina.gob.ar "época estival" |

All three are carried into the results. Note the consequence in §6: a gate that opens once
a day makes the system **periodic, not stationary**, so the restricted regimes cannot yield
an equilibrium wait no matter how low the load.

## 5. The patio question, answered properly

The previous script's only patio test was a scenario table row, `patio de 400 vehiculos`
against a `patio_capacity=200` default. Three things make that row uninformative, and all
three are verifiable in the source:

1. **There is no abandonment mechanism anywhere in that model.** Grepping it for
   `reneg|abandon|impacien|desiste` returns exactly one hit, and that hit is about cutting
   *entry*. So total queue is invariant by construction, and a patio comparison can only
   ever reveal relocation, never relief.
2. **The patio was a single 200-vehicle resource.** There was no separate control-area
   occupancy limit, so the one observed physical constraint — 15 simultaneous vehicles —
   was not represented at all.
3. **The reported queue is a single `cola_max` column**, with no upstream/inside split, so
   a queue that had simply moved from the booths to the gate road reads as unchanged.

The rebuild sweeps the patio over a range that binds and repeats every value with and
without abandonment. The result is unambiguous:

- **Total queue is identical at every patio size.** 719.5 vehicles at patio 15 and at patio
  600 alike. A larger patio relocates the queue from the booths to the gate road; it
  cannot reduce it. This is the queue moving, not congestion improving.
- **Abandonment is what changes the reported wait** — by shrinking *who* was served, not by
  making the crossing faster. Without abandonment: mean wait 181.2 min, 97.5% served. With
  it: mean wait 9.3 min, 78.7% served, 21.3% never cross at all.

Same offered load, same capacity, same number of booths. The only thing that moved the
mean from three hours to nine minutes was the volume of drivers who gave up and went home.

## 6. What the data can and cannot identify

The one calibratable parameter is the intraday peak exponent. The grid objective is
**monotone** and its minimum sits on the edge of the grid, which means the monthly totals
produce a *bound*, not an estimate:

> the arrival profile must be flatter than the prior, because any peaking makes the assumed
> patience budget abandon vehicles the real crossing apparently did not lose.

Monthly totals constrain *how many* vehicles crossed, not *when*. Only an hourly series can
identify the shape. Until one exists, the fitted value is an assumption anchored to the
observed daily total, and the model says so in its own output.

## 7. Invalidation of the previous configuration

Reproduced verbatim in `results/legacy_450.csv`:

| booths | capacity | ρ at 450 veh/h | verdict |
|---|---|---|---|
| 13 | 222.9 veh/h | **2.02** | NON-STATIONARY |
| 18 | 308.6 veh/h | **1.46** | NON-STATIONARY |

ρ > 1 at the peak hour means a queue necessarily builds. A daily load ratio below 1 does
**not** rescue this: the day can be cleared in aggregate only after every driver who queued
during the peak has already absorbed the entire delay. Those waits are a transient of a
peak-hour overload, never an equilibrium.

The previous code compounded this two ways, both verifiable in its source:

- **No drain.** It ends at `env.run(until=cfg.n_days * 24 * 60)` — the horizon is the
  stop condition, so whatever the last day queued is simply not counted.
- **Censored vehicles booked as served.** Line 160 reads
  `df["espera_min"] = df["t_serv"].fillna(horizonte) - df["t_arr"]`. A vehicle that never
  got a booth has `t_serv = NaN`, so `fillna(horizonte)` gives it a *finite* wait equal to
  the time remaining in the run. It then lands in the same mean as vehicles that genuinely
  crossed. That is a lower bound reported as a result, and it is invisible in the output
  because the column is just called `espera_min`.
- **No stationarity test.** `rho`, equilibrium and utilisation never appear in the file at
  all; `peak_rate_veh_h: float = 450.0` is a single hardcoded default.

The rebuild adds a drain, separates `never_served` from served, reports a censored
fraction on its own, and refuses to print a wait when the verdict is not `STATIONARY`.

## 8. Reproducing

```bash
/home/easyrpg/venv/bin/python -m paso_los_libertadores.run_all
```

Three to four minutes. Measured across repeated runs on the same machine: **200–251 s**,
and the spread is machine load, not the pipeline — calibration alone ranged 59–91 s across
identical runs. It is dominated by validating all 20 observed months at full calendar
length, which is irreducible if the monthly series is to be validated at all. Writes
`results/*.csv`, `results/*.json` and `figures/*.png`, and fails loudly if any artefact is
missing or implausibly small.

Individual stages:

```bash
/home/easyrpg/venv/bin/python -m paso_los_libertadores.calibrate
/home/easyrpg/venv/bin/python -m paso_los_libertadores.experiments
/home/easyrpg/venv/bin/python -m paso_los_libertadores.report
```

Every replica seed is fixed and recorded, so every number here is reproducible.

### Hourly validation hook

`calibrate.py --obs hourly.csv` exists so an hourly series can be plugged in if one is ever
obtained. **No such series has been obtained**, so that code path has never been exercised
against real data, and running it without a file prints a warning rather than a result.

## 9. Verification

The defects are fixed by assertion, not by inspection:

| check | what it proves |
|---|---|
| `verify_event_driven_opening` | waits are exact to the minute; no service starts outside opening hours |
| `verify_closure_semantics` | a dated closure blocks admission to the minute, does **not** delete demand, and its queue is visible |
| `verify_reneging_conclusion` | the patio conclusion flips once abandonment is modelled |

## 10. Obtaining better data

**PasAr has no usable archive.** `mxm.mendoza.gov.ar` exposes no historical API and no
archived series, so PasAr cannot answer the hourly question. That is a dead end, not a
source to keep querying.

What is worth requesting, in order of value:

1. **Hourly or 15-minute vehicle counts by lane**, 24 months, via a freedom-of-information
   request under **Ley 25.633** (decree 1,581/2010). This is the single request that would
   identify the intraday profile and convert §6 from a bound into an estimate.
2. **Customs service-time observations** per lane. The 3.5-minute mean is inherited from
   the previous script and is the largest unverified driver of every wait reported here.
3. **Lane opening and staffing logs.** §3 shows staffing swings capacity by 17% per booth,
   and the active hours of the on-demand complement are unpublished.
4. **Abandonment counts**, ideally from the upstream lots or an official traffic survey.
   Every abandonment result in this package is currently assumption-driven.

A further series worth requesting under **Ley 20.285** (personal data protection) is
aggregated lane-level throughput, so that any future hourly analysis can be published
without identifying individual travellers.

## 11. Layout

```
paso_los_libertadores/
  model.py         event-driven model, Config, verification
  calibrate.py     monthly validation, train/test split, --obs hook
  experiments.py   sweep, legacy invalidation, patio, staffing, closures
  report.py        figures (non-interactive backend)
  run_all.py       calibrate -> experiments -> figures -> summary
  data/            three CSVs, every row with a source URL
  results/         CSVs and JSON written by the run
  figures/         five PNGs
```
