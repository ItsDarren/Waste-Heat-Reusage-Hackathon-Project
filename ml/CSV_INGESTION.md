# CSV inference

From the project root, after installing the documented dependencies:

```powershell
python -m ml.infer --csv artifacts/telemetry_ingestion_example.csv --site U --issue 2023-01-10T12:00:00Z --soc 0.5 --output artifacts/csv_inference.json
```

This validates the CSV, loads the saved estimators without retraining, creates 24 hourly forecasts and their calibrated intervals, and solves the physically constrained dispatch horizon. `--no-dispatch` emits forecasts alone. `--issue` defaults to the last available row. `--soc` is useful thermal MWh, with bounds checked against the site's assumed storage capacity.

The included 480-row example contains synthetic observations for both sites. Replace it with your mapped telemetry using `artifacts/data_schema.json`; provide at least 169 contiguous hourly observations through the requested issue time. Timestamps must have `Z` or an explicit offset. The loader converts them to UTC, rejects duplicates, unknown sites, nonfinite/negative required power values, and inconsistent component accounting. Missing hours must be represented explicitly. Bad-quality rows must contain finite prior estimates and use `sensor_quality=missing`, `stale` or `suspect`; the feature builder uses earlier good readings and dispatch disables recovery while the current state is unreliable. It never backfills from future observations.

The CSV path is data only: uploaded pickle/model files are never loaded. Returned JSON records the input hash, model hash, provenance, forecast, dispatch recommendation and status. All predictions still come from a synthetic-trained prototype. CSV ingestion does not establish field accuracy, approved controls or contractual equipment availability.

Future weather and prices in this first implementation are causal demonstration forecasts based on issue-time information. No live external forecast service is connected. Customer routes, tariffs, maintenance calendar, connection capacities and equipment retain the case-study assumptions in the original model and `ml/dispatch.py`; changing to another real system requires mapping and validating those parameters, retraining, recalibration and engineering review. The schema documents separate weather, exogenous, customer and storage tables for that integration.

Facility electricity is IT + cooling + other facility electrical power. Recovery source power is thermal, capped by the assumed 2.5 MW tranche and physical conditions. Incremental heat-system electricity appears in dispatch output separately.
