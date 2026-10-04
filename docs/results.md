# Measured results from the synthetic demonstration

The complete rolling test did **not** show an aggregate operating-margin improvement from the causal forecasting controller. Preserve the deterministic rule as the operating reference while the forecast-driven controller remains an experimental decision-support option. This result does not invalidate forecasting; it identifies the cost of forecast error, conservative reserves, outages and selected control policies in this particular synthetic system. It is not a conclusion about real equipment or either site's actual economics.

## Closed-loop dispatch

The held-out 2025 evaluation used nine 48–72-hour seasonal and stress windows per site: **504 hours per site**, **1,008 operating hours**, and **3,024 executed controller-hours**. These are selected unseen windows, not a full operational year. All three controllers faced the same realized trajectories and equipment, customer, storage and service constraints. No short window was annualized.

| Operational result, synthetic holdout | NYC: rule | NYC: forecast + optimization | NYC: perfect-information rolling | Lansing: rule | Lansing: forecast + optimization | Lansing: perfect-information rolling |
|---|---:|---:|---:|---:|---:|---:|
| Operating margin / USD | 46,353.48 | 45,228.45 | 50,518.32 | 7,182.02 | 7,016.22 | 8,037.66 |
| Useful heat delivered / MWh | 608.37 | 562.83 | 608.49 | 261.52 | 244.71 | 260.71 |
| Incremental heat-system electricity / MWh | 181.78 | 162.41 | 175.26 | 69.52 | 63.98 | 66.97 |
| Facility electricity / MWh | 2,344.51 | 2,344.51 | 2,344.51 | 2,410.23 | 2,410.23 | 2,410.23 |
| Customer variable-cost savings / USD | 11,334.26 | 10,540.60 | 11,429.32 | 3,922.74 | 3,670.66 | 3,910.58 |
| Unmet total customer demand / MWh | 0 | 0 | 0 | 0 | 0 | 0 |

The causal controller's margin change was **−$1,125.02 (−2.43%) in NYC** and **−$165.80 (−2.31%) in Lansing**, relative to the rule. It improved margin in 9 of 18 individual windows and reduced it in 9. Examples include +$221.10 during NYC's 48-hour summer low-demand window and −$927.32 during NYC's 48-hour stale-sensor window. Every window, including negative cases, remains in `artifacts/backtest.json`.

Customer savings use actually delivered recovered heat multiplied by the difference between the original assumed avoided-heat price and HeatCo's thermal tariff. Thermal revenue is separate. Operating margin excludes capital, fixed O&M, financing and tax, and is not project profit. Retained backup supplies customer residual demand and its costs remain on the customer side. These distinctions explain why lower heat-system electricity alone is not proof of a better customer or operator result.

## Physical and service verification

All **3,024 executed actions passed physical validation**, with maximum thermal-balance residual **8.89 × 10⁻¹⁶ MWh**. An independent second audit reloaded the persisted hourly artifact and recomputed sixteen conservation/accounting identities without trusting its recorded PASS flags. Facility electrical components, heat-system electrical components, source capture/rejection, storage dynamics, shared allocations, thermal revenue, margin and customer savings reconcile. Across all 54 site/window/controller groups, opening and closing storage is exactly zero; unused terminal storage is explicitly rejected, never sold or credited.

No total demand was unmet in these selected windows because retained backup was sufficient. This is different from the **community recovered-heat obligation**, which was sometimes physically unavailable. The forecast controller recorded 2.664 MWh of community recovery shortfall in NYC and 2.483 MWh in Lansing. Backup can maintain service without making recovered-heat obligations disappear.

The demonstration contract targets 70% of connected, connection-capacity-limited community demand from recovered heat. The optimizer first minimizes horizon-wide community shortfall, then terminal-reserve shortfall, then optimizes USD. The execution step gives the actual current community minimum priority and can increase planned HP/storage use to meet it, subject to actual physical limits. It does not promise 70% recovery during source outages, low-temperature operation, empty storage or other infeasible conditions. This contract is a new explicit assumption, not an existing site agreement.

The separate **34-case stress suite** covers 17 regimes at each site, including source/HP outages, temperature failure, full/empty/cold storage, missing/stale sensors, cooling interlock failure, zero source flow, expensive electricity, high demand, insufficient backup and disconnected customers. Every case conserves energy. Some deliberately produce rejection, community recovery shortfall or unmet demand; these are correct reported outcomes rather than reasons to manufacture a sale. An explicit zero-time solver test confirms deterministic feasible fallback, and negative/invalid forecast tests confirm safe handling.

## Perfect information is a benchmark, not a field claim

The perfect-information rolling controller uses actual future data and is therefore an **anticipative benchmark**, not deployable forecasting performance. It preserves the same equipment and physics. Its realized outcome is not guaranteed to dominate every other rolling controller because of finite horizons, lexicographic priorities and nonlinear physical recourse.

The report also contains a genuine mathematical margin ceiling from a full-window perfect-information **relaxed LP**. It relaxes distribution losses, pumping/auxiliary/booster costs and community obligations while retaining demand, source, HP and storage limits. This is an upper bound, **not a fair controller or executable schedule**. Every reported controller margin is automatically checked against that ceiling. The distinction prevents an optimistic relaxation from being presented as a feasible operating plan.

## Reproducibility and limits

The trained model was loaded in a fresh process. Independent mutation of all future realized electrical, thermal, weather, price, source and sensor values leaves an already-issued forecast unchanged for both sites. UTC continuity, the 8,784-hour leap year and repeated local DST hour are verified. Forecast accuracy and interval coverage are evaluated by target, site, horizon and season; the nominal 90% label is not a guaranteed coverage claim under stress. This compact repository retains the model card, summary metrics and interval coverage. Run `python run.py train` to regenerate the detailed `forecast_metrics_by_horizon_season.csv` and `forecast_test_predictions.csv.gz` files, which are omitted from the package.

The evaluated model SHA-256 is `e487a1b7cb952fa78dba91fa62f7f3a059816cd53b814764d38f1ef505a607ac`. Dataset SHA-256 is `81e1a6380d13f6e462991ffd842c29338548b2407da49b06678d4cc41bbdd23d`. Both were unchanged between inspection before service construction and final completion. Run `python -m ml.backtest` to regenerate the dispatch evidence and `python -m ml.audit` to re-audit its saved accounting.

Source temperature, flow, electrical capacity, load shape, customer demand, prices and carbon are synthetic. The 2.5 MW tranche remains an assumed thermal maximum. Branch surveys, measured telemetry, vendor performance maps, cooling interlocks, tariffs, customer contracts and field service acceptance remain open. The system sends no commands to physical plant.

