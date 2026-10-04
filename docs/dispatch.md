# Heat dispatch: implementation and accounting

This is an executable **synthetic decision-support demonstration**. It sends no commands to physical equipment. The two cases retain the original input registry: **111 8th Avenue, NYC (U)** and **Lake Hawkeye, former Cayuga Power Plant, Lansing, NY (R)**. The 2.5 MW recovery tranche is an assumed **thermal** capacity. It is not a measurement of data-center electricity.

## Run and inspect

From the project root after dependency installation and forecasting training:

```powershell
python -m ml.backtest
python -m unittest discover -s tests -p test_dispatch.py -v
```

`ml/dispatch.py` exports `plan_horizon(records, site_id, initial_soc)`, `execute_step(plan_record, actual, site_id, initial_soc)` and `rule_step(actual, site_id, initial_soc)`. Every interval is one hour; average MW and interval MWh are therefore numerically equal. The functions reject an empty forecast horizon and never infer a full facility load from recovered heat.

`artifacts/backtest.json` contains seasonal windows, every controller's operational totals, USD changes, provenance and limitations. `backtest_hourly.jsonl.gz` preserves every executed action, physical component and active constraint. `stress_test.json` preserves deliberately difficult operating cases. These artifacts are regenerated, not authored results.

## Shared physical plant

The heat-pump plant is 3 MW thermal in NYC and 1.5 MW thermal in Lansing, with the original 2.5 MW source tranche in both cases. The original 800 m / 3,500 m serial-equivalent networks, 65°C supply, 40°C nominal return, temperature-lift COP, exponential supply/return losses, variable circulation, hydraulic pumping and 63°C remote supply minimum are preserved by importing `baseline/model/model.py` read-only.

The branch topology is not surveyed. A single conservative equivalent trunk carries the aggregate load, and every customer competes for it. The prototype does **not** turn three independent distance-calculator answers into three overlapping source allocations. Cohort connection limits are explicit new demonstration assumptions: NYC housing/commercial/community 1.4 / 3.0 / 0.65 MW; Lansing 2.4 / 0 / 0.8 MW. Maximum trunk flow is 120% of the original heat-pump design flow; this requires engineering/vendor validation.

Available source heat is the minimum of the forecast/actual thermal source, capture tranche derated by availability, exchanger capacity, and `mass flow × cp × allowable capture temperature drop`. The drop is capped at the original source-loop design drop, and source inlet below the original 12°C minimum blocks capture. Cooling interlock failure blocks recovery. Source and compressor energy are separate:

`HP output = recovered source + compressor electricity`

`compressor electricity = HP output / heating COP`

Source recovery never includes compressor energy. Heat-pump maintenance availability, customer connection status, delivery-temperature requirements, network flow, source flow, backup capacity and temperature eligibility constrain the same portfolio.

Storage capacity derives from the original tank volumes, water properties, temperature span and 85% usable fraction: 3.706 MWh NYC, 1.853 MWh Lansing. New dispatch assumptions are 97% charge efficiency, 97% discharge efficiency, hourly standing loss from the original model, and charge/discharge limits at 65% of HP nameplate. A binary mode prohibits simultaneous charge/discharge. Storage at insufficient temperature is ineligible. There is no thermal-energy creation:

`SOC[t+1] = SOC[t] × (1 − standing loss) + charge × 0.97 − discharge / 0.97`

## Forecast versus decision

The forecasting service issues the next 1–24 hours using only information available at issue time. It predicts electrical components, source heat and cohort demand. The MILP then chooses HP output, delivery, charge, discharge, backup and unmet demand. Forecasts are not a controller, and the optimizer is not an ML model.

A conservative source reserve removes 25% of the distance between the point estimate and the nominal 90% lower interval. This is an explicit engineering heuristic, **not a 90% reliability guarantee**. Price and grid forecasts are issued estimates; unannounced outages are not inserted into future ML inputs. The deterministic rule controller reads only current observations. All controllers receive the same current physical observations at execution.

## MILP and exact physical repair

SciPy's bundled HiGHS solves a shared 24-hour MILP. A small pinned adapter (`ml/solver.py`) reads only primal solutions/status/objectives and independently validates linear constraints and integrality. This avoids unnecessary LP-basis extraction after a MIP solve; a native fault was observed there on this workstation. The host also requires the original submission's process-local CPU-affinity guard and a bounded-run cyclic-GC policy for reliable numerical execution. No machine-wide settings or physics assumptions change. Binary variables represent plant operation, network circulation and storage mode. Network thermal losses use a conservative fixed-loss envelope checked against 65 loads; pumping uses a conservative affine envelope. These approximations preserve minimum-temperature circulation costs at small delivered load. They can be conservative and can change economic decisions.

Every suggested action is recalculated by the original nonlinear network and COP functions. At execution, fresh actual demand, temperatures, connections, source and flow limits override forecasts. A feasible recourse step clips demand, prioritizes community service, respects available source/equipment/storage, calls retained backup, and reports unmet demand if backup is insufficient. Unused source heat is rejected. Conservative loss reserve released by the exact physics is curtailed rather than sold or silently placed in storage.

Each executed step verifies thermal conservation, the HP identity, shared source/HP/backup capacities, network flow, minimum delivery temperature, connection/demand limits, nonnegative/bounded SOC and facility-electricity reconciliation. Any actual invariant failure raises an exception; it does not produce a convincing-looking chart.

## Community priority and the dollar objective

The **new demonstration contract** asks for recovered heat equal to 70% of connected, connection-capacity-limited community demand, whenever physically achievable. This is not a historical agreement or a field-validated service commitment. There are three lexicographic solves:

1. Minimize community recovery shortfall, in MWh.
2. With that optimum retained, minimize terminal storage reserve shortfall, in MWh.
3. With both retained, maximize operating USD value.

The dollar solve maximizes thermal sales minus incremental electricity, source royalty, the original community contribution and $3/MWh delivered variable O&M. A separately disclosed $1,000/MWh unserved-service contract penalty values unmet demand. This penalty is a demonstration assumption; it is not booked as cash operating cost in displayed results. The objectives never add dollars, energy and carbon into an unscaled score.

The forecast horizon ordinarily retains opening SOC less unavoidable standing loss, avoiding cost-free horizon-end liquidation. An infeasible terminal reserve relaxation is explicitly reported and takes priority only after community service. In a finite backtest, the known endpoint truncates the optimization. Every independent comparison window starts and ends at zero SOC. Remaining thermal energy after its last hour is explicitly rejected and reported, with no sale or salvage credit. That boundary is artificial but identical across controllers; no selected day is annualized.

## Economics and carbon boundaries

- **Facility electricity:** IT + cooling + other facility electrical loads from the synthetic telemetry. These components reconcile exactly.
- **Incremental heat-system electricity:** HP compressor + source pump + network pump + plant auxiliaries + original booster allowance. It is additional to facility electricity.
- **Thermal revenue:** delivered recovered heat by cohort × the original conditional thermal tariffs. It is not an electricity sale.
- **Operating margin:** thermal revenue less incremental heat electricity, variable O&M, source royalty and community contribution. CAPEX, fixed O&M, tax, debt, grants, replacement and lifecycle NPV are outside this short-term dispatch boundary. Margin is not profit.
- **Customer savings:** actually delivered recovered heat × (the original avoided-heat price − HeatCo tariff). The baseline is explicit, conditional and unverified. Unmet demand receives no savings credit.
- **Retained backup:** customer-owned existing backup, consistent with the original model. Its expense and emissions are reported separately; its fuel cost is not silently moved to HeatCo's operating account. Backup competes for the original shared installed capacity.
- **Carbon:** incremental heat electricity plus backup emissions are compared with the original assumed baseline for the same heat actually served. Facility electricity emissions are reported separately and are never claimed as eliminated by heat recovery. Grid `kg/kWh` is numerically equal to `t/MWh`. This is operational carbon, not lifecycle assessment.

The original annual financial and lifecycle screening remains in `baseline/` with its original assumptions and limitations; it is not overwritten by dispatch economics.

## Fair comparison and the upper-bound distinction

The complete backtest executes the same realized 2025 trajectories under three controllers:

1. Original economic gate and proportional rule, adapted only to the common community and physical constraints.
2. Causal forecast-driven rolling optimization, executing one action then updating SOC and observations.
3. The same rolling optimizer given perfect future information, clearly marked anticipative.

The perfect-information rolling result is a useful benchmark but **not a mathematical guarantee that its realized margin is always highest**. Finite horizons, lexicographic objectives, conservative network approximations and exact recourse can all change rankings. Negative observed improvements are retained.

A separate full-window, perfect-information **relaxed LP economic ceiling** supplies a genuine upper bound on operator operating margin: it keeps actual demand, source, HP and storage capacities, but relaxes network losses, pumping/auxiliaries/booster costs and community obligations. Every physically feasible comparison trajectory lies inside this relaxation. The ceiling is explicitly not an executable plan or a fair controller. An automated assertion checks every reported controller margin against it.

Backtest percentage changes divide the USD margin difference by absolute baseline margin; the primary result is the USD difference. Nine seasonal/stress windows per site are not annual financial forecasts.

## Failure behavior and open validation

Missing/stale measurements fail source recovery closed. Invalid/nonfinite/negative predictions or solver failure/timeout select the labelled deterministic feasible controller. Community recovery shortfall, unavailable temperature service, backup shortfall and rejected heat remain visible. This favors feasible operation rather than invented sales.

Vendor COP/part-load maps, surveyed branching and hydraulics, actual source and customer telemetry, heat-quality measurements, metering uncertainty, utility price structures, actual customer contracts, equipment/cooling interlocks and production control security remain open. A constant booster fraction and equivalent trunk are screening approximations. Passing synthetic stress tests does not close field-validation requirements.
