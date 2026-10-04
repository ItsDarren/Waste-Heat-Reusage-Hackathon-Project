"""Verify packaged model, accounting, annual results and selected presentation assets.

Run from this repository with: python verify_project.py
The audit uses only repository files and the active Python environment. It writes
artifacts/integration_verification.json. This is a software and synthetic-data
verification, not a claim of field validation or a browser interaction test.
"""
import bootstrap
import gzip
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone
from ml.dispatch import physics, config, COHORTS

ROOT = Path(__file__).resolve().parent
ART = ROOT / 'artifacts'
SELECTED_ASSETS = {
    'presentation/PRESENT.html': 'a2c86986ca1f6ecfb07848bfbeef3894cff0b1dee27da82a1a31393952d2407a',
    'media/Demo Video.mp4': 'e48ac09d5de7f650055967f04993e7e6a4b09a941a989762086bf478c029f0ac',
}


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read(name):
    return json.loads((ART / name).read_text(encoding='utf-8'))


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def verify_fresh_inference():
    """Load trusted saved estimators and solve a new plan in another interpreter."""
    probe = '''
import json
from ml.forecasting import ForecastService
from ml.dispatch import plan_horizon
service = ForecastService()
results = []
for site in ('U', 'R'):
    forecast = service.forecast(site, '2025-01-14T12:00:00Z')
    assert len(forecast['records']) == 24
    for row in forecast['records']:
        assert abs(row['facility_mw'] - row['it_mw'] - row['cooling_mw'] - row['other_mw']) < 1e-9
    plan = plan_horizon(forecast['records'], site_id=site, initial_soc=0.)
    assert len(plan['records']) == 24
    assert plan['status'] == 'optimized', plan['status']
    for forecast_row, dispatch_row in zip(forecast['records'], plan['records']):
        assert forecast_row['timestamp'] == dispatch_row['timestamp']
        assert dispatch_row['physics_violations'] == []
        assert abs(dispatch_row['energy_balance_error_mwh']) < 1e-7
    results.append({'site_id': site, 'forecast_hours': 24, 'optimized_hours': 24,
                    'status': plan['status'], 'physical_violations': 0,
                    'next_hour_it_mw': forecast['records'][0]['it_mw']})
print(json.dumps(results))
'''
    completed = subprocess.run(
        [sys.executable, '-B', '-c', probe], cwd=ROOT,
        text=True, capture_output=True, timeout=180,
    )
    check(completed.returncode == 0,
          'Fresh saved-model inference/optimization failed:\n' + completed.stderr)
    return json.loads(completed.stdout)


def run():
    report = {
        'basis': 'SYNTHETIC DEMONSTRATION; software verification, not field certification',
        'verified_utc': datetime.now(timezone.utc).isoformat(),
        'python_version': sys.version.split()[0],
        'checks': {},
    }
    meta = read('forecast_metadata.json')
    back = read('backtest.json')
    prior_reload = read('fresh_process_reload.json')
    model_hash = sha(ART / 'forecast_models.pkl')
    data_hash = sha(ART / 'synthetic_hourly.csv.gz')
    check(model_hash == meta['model_sha256'] == prior_reload['model_sha256'], 'Model hash mismatch')
    check(data_hash == meta['dataset_sha256'], 'Dataset hash mismatch')
    check(model_hash == back['input_hashes']['forecast_models.pkl'], 'Backtest model hash mismatch')
    check(data_hash == back['input_hashes']['synthetic_hourly.csv.gz'], 'Backtest dataset hash mismatch')
    report['checks']['saved_model_and_dataset_hashes_match'] = True
    report['input_hashes'] = {'model_sha256': model_hash, 'dataset_sha256': data_hash}

    # These are recorded held-out audits; fresh inference and accounting run below.
    for name, passed in read('forecast_verification.json').items():
        if isinstance(passed, bool):
            check(passed, 'Recorded forecast audit failed: ' + name)
    check(read('stress_test.json')['passed'], 'Recorded stress test failed')
    report['checks']['recorded_causal_feature_and_stress_audits_pass'] = True
    report['fresh_process_inference_and_dispatch'] = verify_fresh_inference()
    report['checks']['fresh_saved_model_inference_and_optimization_both_sites'] = True

    count = 0
    maxima = dict.fromkeys(('hp_identity_mwh', 'system_balance_mwh',
        'facility_reconciliation_mwh', 'demand_balance_mwh',
        'operating_margin_usd', 'cohort_revenue_usd'), 0.)
    groups, endings = {}, {}
    with gzip.open(ART / 'backtest_hourly.jsonl.gz', 'rt', encoding='utf-8') as handle:
        for line in handle:
            row = json.loads(line)
            count += 1
            cfg = config(row['site_id'])
            key = (row['site_id'], row['window'], row['controller'])
            if key not in groups:
                check(abs(row['storage_open_mwh']) < 1e-8, 'Nonzero free opening inventory')
                groups[key] = 0
            groups[key] += 1
            endings[key] = row['storage_soc_mwh']
            residuals = {
                'hp_identity_mwh': abs(row['hp_heat_mw'] - row['source_heat_mw'] - row['compressor_electricity_mw']),
                'system_balance_mwh': abs(row['hp_heat_mw'] + row['storage_open_mwh'] - row['delivered_heat_mw'] - row['network_loss_mw'] - row['storage_standby_loss_mw'] - row['storage_conversion_loss_mw'] - row['storage_soc_mwh'] - row.get('terminal_storage_rejected_mwh', 0)),
                'facility_reconciliation_mwh': abs(row['facility_electricity_mw'] - row['it_mw'] - row['cooling_mw'] - row['other_mw']),
                'demand_balance_mwh': abs(row['demand_mw'] - row['delivered_heat_mw'] - row['backup_heat_mw'] - row['unmet_demand_mw']),
                'operating_margin_usd': abs(row['operating_margin_usd'] - row['revenue_usd'] + row['operating_cost_usd']),
                'cohort_revenue_usd': abs(row['revenue_usd'] - sum(row[f'delivered_{g}_mw'] * cfg['tariffs'][g] for g in COHORTS)),
            }
            for name, value in residuals.items():
                maxima[name] = max(maxima[name], value)
                check(value < 1e-6, f'{name} residual {value}')
            check(not row['physics_violations'], 'Reported physical violation')
            check(row['source_heat_mw'] <= min(2.5, row['source_cap_mw']) + 1e-6, 'Source double-allocation')
            check(row['hp_heat_mw'] <= row['hp_capacity_mw'] + 1e-6, 'Heat-pump capacity exceeded')
            check(-1e-7 <= row['storage_soc_mwh'] <= cfg['storage_capacity_mwh'] + 1e-7, 'Storage bounds')
            check(not (row['storage_charge_mw'] > 1e-7 and row['storage_discharge_mw'] > 1e-7), 'Simultaneous storage cycling')
            for cohort in COHORTS:
                check(row[f'delivered_{cohort}_mw'] <= row[f'demand_{cohort}_mw'] + 1e-7, 'Invented heat sale')
    check(all(abs(value) < 1e-8 for value in endings.values()), 'Nonzero closing inventory')
    check(count == back['verification']['total_executed_controller_hours'], 'Backtest row count mismatch')
    report['checks']['independent_hourly_accounting_rows'] = count
    report['checks']['common_zero_storage_endpoints'] = True
    report['checks']['site_window_controller_groups'] = len(groups)
    report['independent_maximum_residuals'] = maxima

    stored = json.loads((ROOT / 'baseline/model/results.json').read_text(encoding='utf-8'))
    baseline = []
    for site in ('U', 'R'):
        actual, _ = physics.run_site(site, keep_rows=False)
        for name in ('delivered_mwh', 'electricity_mwh', 'revenue_usd', 'capex_usd', 'npv_usd'):
            reference = stored['sites'][site][name]
            delta = actual[name] - reference
            tolerance = max(1e-5, abs(reference) * 1e-8)
            check(abs(delta) <= tolerance, f'Annual reproduction {site} {name}: {delta}')
            baseline.append({'site_id': site, 'metric': name, 'stored_value': reference,
                'recomputed_value': actual[name], 'difference': delta,
                'cross_runtime_tolerance': tolerance})
    report['baseline_reproduction'] = baseline
    report['checks']['annual_model_reproduction_both_sites'] = True

    asset_hashes = {}
    for relative, expected in SELECTED_ASSETS.items():
        path = ROOT / relative
        check(path.is_file(), 'Selected presentation asset missing: ' + relative)
        actual = sha(path)
        check(actual == expected, 'Selected presentation asset changed: ' + relative)
        asset_hashes[relative] = {'sha256': actual, 'bytes': path.stat().st_size}
    report['selected_assets'] = asset_hashes
    report['checks']['selected_video_and_presentation_byte_identical'] = True
    report['scope'] = 'Fresh saved-model inference and optimization for both sites, independent persisted-flow accounting, annual-model recomputation, and asset hashes. Recorded causal/stress evidence is checked; browser interaction and field validation are separate.'
    report['verdict'] = 'PASS within declared simulation and numerical boundaries; field validation remains OPEN'
    (ART / 'integration_verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'verdict': report['verdict'], 'checks': report['checks'], 'maxima': maxima}, indent=2))
    return report


if __name__ == '__main__':
    run()
