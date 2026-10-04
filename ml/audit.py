"""Re-audit persisted dispatch output without trusting stored PASS/error fields."""
from pathlib import Path
import gzip,hashlib,json
from collections import defaultdict
from .dispatch import COHORTS, config
ROOT=Path(__file__).resolve().parents[1]

def run():
    artifact=ROOT/'artifacts/backtest_hourly.jsonl.gz'
    with gzip.open(artifact,'rt',encoding='utf-8') as f: rows=[json.loads(line) for line in f]
    errors=defaultdict(float);last={};counts=defaultdict(int)
    def check(name,delta): errors[name]=max(errors[name],abs(float(delta)))
    for r in rows:
        cfg=config(r['site_id']);p=cfg['p']; key=(r['site_id'],r['window'],r['controller'])
        check('hp_identity_mwh',r['hp_heat_mw']-r['source_heat_mw']-r['compressor_electricity_mw'])
        check('thermal_bus_mwh',r['hp_heat_mw']+r['storage_open_mwh']-r['delivered_heat_mw']-r['network_loss_mw']-r['storage_standby_loss_mw']-r['storage_conversion_loss_mw']-r['storage_soc_mwh']-r.get('terminal_storage_rejected_mwh',0))
        check('source_capture_rejection_mwh',r['source_available_mw']-r['source_heat_mw']-r['rejected_source_mw'])
        check('storage_dynamics_mwh',r['storage_soc_mwh']+r.get('terminal_storage_rejected_mwh',0)-r['storage_open_mwh']*(1-cfg['storage_loss_fraction'])-r['storage_charge_mw']*cfg['charge_efficiency']+r['storage_discharge_mw']/cfg['discharge_efficiency'])
        check('facility_components_mwh',r['facility_electricity_mw']-r['it_mw']-r['cooling_mw']-r['other_mw'])
        check('heat_electric_components_mwh',r['incremental_electricity_mw']-sum(r[k] for k in ('compressor_electricity_mw','source_pump_electricity_mw','network_pump_electricity_mw','auxiliary_electricity_mw','booster_electricity_mw')))
        check('demand_service_mwh',r['demand_mw']-r['delivered_heat_mw']-r['backup_heat_mw']-r['unmet_demand_mw'])
        check('shared_allocation_mwh',r['delivered_heat_mw']-sum(r['delivered_'+g+'_mw'] for g in COHORTS))
        check('thermal_revenue_usd',r['revenue_usd']-sum(r['delivered_'+g+'_mw']*cfg['tariffs'][g] for g in COHORTS))
        check('customer_savings_usd',r['customer_savings_usd']-sum(r['delivered_'+g+'_mw']*(cfg['avoided_costs'][g]-cfg['tariffs'][g]) for g in COHORTS))
        check('margin_usd',r['operating_margin_usd']-r['revenue_usd']+r['operating_cost_usd'])
        check('operating_cost_usd',r['operating_cost_usd']-r['incremental_electricity_mw']*r['electricity_price']-r['source_royalty_usd']-r['community_fund_usd']-r['variable_om_usd'])
        check('community_fund_usd',r['community_fund_usd']-r['delivered_heat_mw']*p['P_community_fund'])
        if key in last:check('state_continuity_mwh',r['storage_open_mwh']-last[key])
        else:check('identical_zero_initial_state_mwh',r['storage_open_mwh'])
        last[key]=r['storage_soc_mwh'];counts[key]+=1
    for value in last.values():check('identical_zero_terminal_state_mwh',value)
    report=dict(basis='SYNTHETIC DEMONSTRATION',method='Independent persisted-output recomputation; does not trust stored physics flags',
                executed_rows=len(rows),site_window_controller_groups=len(last),max_absolute_errors=dict(errors),passed=max(errors.values())<1e-7,
                hourly_artifact_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
                input_hashes=json.loads((ROOT/'artifacts/backtest.json').read_text())['input_hashes'])
    (ROOT/'artifacts/accounting_audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    if not report['passed']:raise AssertionError(report)
    print(json.dumps(report,indent=2))
    return report

if __name__=='__main__':run()
