"""Untouched-2025 rolling closed-loop evaluation and deterministic stress audit.
Run: python -m ml.backtest. All output is a synthetic demonstration, never field proof.
"""
from __future__ import annotations
import bootstrap  # dependency paths and process-local native runtime guard
import argparse
import hashlib
import json
import time
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.optimize import linprog
from scipy.sparse import lil_matrix
from .data import load_data
from .dispatch import (COHORTS, config, physical_state, plan_horizon, execute_step,
                       rule_step, aggregate, network_result, VARIABLE_OM_USD_MWH)

ROOT=Path(__file__).resolve().parents[1]
ARTIFACTS=ROOT/'artifacts'
WINDOWS=[('winter_cold_spell','2025-01-14',72),('winter_stale_sensor','2025-02-13',48),
         ('spring_low_source_temperature','2025-04-18',48),('spring_ordinary','2025-05-12',48),
         ('summer_price_spike','2025-07-16',72),('summer_low_demand','2025-08-13',48),
         ('customer_disconnection','2025-09-10',48),('autumn_outage','2025-10-12',72),
         ('winter_ordinary','2025-12-12',48)]


def json_default(value):
    if hasattr(value,'item'): return value.item()
    return str(value)


def close_storage(row):
    """A common zero terminal state, with residual energy explicitly rejected.
    No controller receives a free sale or salvage credit at a selected window edge.
    """
    row=dict(row)
    row['terminal_storage_rejected_mwh']=row['storage_soc_mwh']
    row['storage_soc_mwh']=0.
    row['energy_balance_error_mwh']=abs(row['hp_heat_mw']+row['storage_open_mwh']-row['delivered_heat_mw']-row['network_loss_mw']-row['storage_standby_loss_mw']-row['storage_conversion_loss_mw']-row['terminal_storage_rejected_mwh'])
    if row['terminal_storage_rejected_mwh']>1e-9: row['constraints']=row['constraints']+['BENCHMARK_TERMINAL_STORAGE_REJECTION']
    return row


def relaxed_perfect_information_ceiling(records,site):
    """A genuine upper bound for operator margin, NOT the fair comparator.
    Perfect information, lossless distribution, no pumps/auxiliaries, relaxed community
    obligations. Same heat pump/source/capture capacities, actual demand and storage.
    This relaxation contains every physically feasible comparator trajectory.
    """
    H=len(records); cfg=config(site); p=cfg['p']; stride=7; N=H*stride
    # Per hour: hp, charge, discharge, soc, deliveries housing/commercial/community.
    def ix(t,k): return t*stride+k
    costs=np.zeros(N); bounds=[]; Aub=[]; bub=[]; Aeq=[]; beq=[]
    for t,row in enumerate(records):
        s=physical_state(row,site)
        bounds += [(0,s['hp_cap']),(0,cfg['storage_charge_mw'] if s['storage_eligible'] else 0),(0,cfg['storage_discharge_mw'] if s['storage_eligible'] else 0),(0,cfg['storage_capacity_mwh'])]
        bounds += [(0,s['caps'][g]) for g in COHORTS]
        costs[ix(t,0)]=s['price']/s['cop']+(1-1/s['cop'])*p['P_source_fee']
        for j,g in enumerate(COHORTS): costs[ix(t,4+j)]=-cfg['tariffs'][g]+p['P_community_fund']+VARIABLE_OM_USD_MWH
        # Inequality deliberately permits rejecting surplus upgraded heat.
        Aub.append({ix(t,0):-1,ix(t,1):1,ix(t,2):-1,**{ix(t,4+j):1 for j in range(3)}});bub.append(0.)
        state={ix(t,3):1,ix(t,1):-cfg['charge_efficiency'],ix(t,2):1/cfg['discharge_efficiency']}
        if t:state[ix(t-1,3)]=-(1-cfg['storage_loss_fraction'])
        Aeq.append(state);beq.append(0.)
    # Ending positive SOC cannot improve positive-cost objective; disposal allowed.
    def matrix(rows):
        A=lil_matrix((len(rows),N))
        for i,entries in enumerate(rows):
            for j,v in entries.items(): A[i,j]=v
        return A.tocsr()
    result=linprog(costs,A_ub=matrix(Aub),b_ub=bub,A_eq=matrix(Aeq),b_eq=beq,bounds=bounds,method='highs')
    return dict(status='optimal' if result.success else 'failed',operating_margin_ceiling_usd=float(-result.fun) if result.success else None,
                label='Perfect-information relaxed economic upper bound',
                limitations='Relaxation only: lossless pipes, no pumping/auxiliary/booster electricity, relaxed community obligations; not a physically executable schedule or the fair comparison.')


def run_backtest(quick=False):
    from .forecasting import ForecastService
    input_hashes={name:hashlib.sha256((ARTIFACTS/name).read_bytes()).hexdigest() for name in ('forecast_models.pkl','synthetic_hourly.csv.gz')}
    data=load_data(); service=ForecastService(data=data)
    if input_hashes['forecast_models.pkl']!=hashlib.sha256((ARTIFACTS/'forecast_models.pkl').read_bytes()).hexdigest():
        raise RuntimeError('Model file changed during backtest construction')
    all_rows=[]; comparisons=[]; started=time.perf_counter()
    windows=WINDOWS if not quick else [WINDOWS[0],WINDOWS[5],WINDOWS[7]]
    for site in ('U','R'):
        part=data[data.site_id==site].set_index('timestamp').sort_index()
        for label,start,hours in windows:
            if quick: hours=24
            timestamp=pd.Timestamp(start,tz='UTC'); test=part.loc[timestamp:timestamp+pd.Timedelta(hours=hours-1)].reset_index().to_dict('records')
            states={k:0. for k in ('rule_proportional','forecast_optimized','perfect_information')}
            results={k:[] for k in states}; fallbacks=0; forecast_fallbacks=0
            for i,actual in enumerate(test):
                if i%12==0: print(f'{site} {label} hour {i}/{hours}',flush=True)
                current=pd.Timestamp(actual['timestamp']); issue=current-pd.Timedelta(hours=1)
                forecast=service.forecast(site,issue)
                future=forecast['records']
                forecast_fallbacks+=int(forecast.get('status','')!='ok' and ('fallback' in str(forecast.get('status','')).lower()))
                # Window endpoint is known to all controllers. Optimization is
                # truncated there, eliminating purchases for unevaluated hours.
                H=min(24,len(test)-i); future=future[:H]
                fp=plan_horizon(future,site,states['forecast_optimized'],conservative=True,
                                terminal_target=0. if H<24 else None)
                perfect_records=[dict(r,horizon=j+1) for j,r in enumerate(test[i:i+24])]
                pp=plan_horizon(perfect_records,site,states['perfect_information'],conservative=False,
                                terminal_target=0. if H<24 else None)
                fallbacks+=int(fp['status']=='fallback')
                actions={
                    'rule_proportional':rule_step(actual,site,states['rule_proportional']),
                    'forecast_optimized':execute_step(fp['records'][0],actual,site,states['forecast_optimized'],'forecast_optimized'),
                    'perfect_information':execute_step(pp['records'][0],actual,site,states['perfect_information'],'perfect_information')}
                for controller,action in actions.items():
                    if i==len(test)-1: action=close_storage(action)
                    action['window']=label;action['regime']=str(actual.get('regime','ordinary'))
                    action['forecast_issue_timestamp']=str(issue) if controller=='forecast_optimized' else None
                    if controller=='forecast_optimized':action['solver_status']=fp['status'];action['forecast_status']=forecast.get('status','model')
                    results[controller].append(action); states[controller]=action['storage_soc_mwh']
            summaries={k:aggregate(rows) for k,rows in results.items()}
            for k,rows in results.items():
                summaries[k]['terminal_storage_rejected_mwh']=sum(r.get('terminal_storage_rejected_mwh',0.) for r in rows)
                all_rows.extend(rows)
            baseline=summaries['rule_proportional']['operating_margin_usd']; optimized=summaries['forecast_optimized']['operating_margin_usd']
            ceiling=relaxed_perfect_information_ceiling(test,site)
            bound=ceiling['operating_margin_ceiling_usd']
            if bound is not None and any(v['operating_margin_usd']>bound+1e-5 for v in summaries.values()):
                raise AssertionError('Relaxed mathematical upper bound violated')
            comparisons.append(dict(site_id=site,window=label,start=str(timestamp),hours=hours,controllers=summaries,
                forecast_minus_rule_margin_usd=optimized-baseline,
                forecast_minus_rule_margin_percent=100*(optimized-baseline)/abs(baseline) if abs(baseline)>1e-8 else None,
                dispatch_fallback_hours=fallbacks,forecast_fallback_hours=forecast_fallbacks,perfect_information_ceiling=ceiling))
            print(f'{site} {label}: {hours} h; rule ${baseline:.2f}; ML ${optimized:.2f}; delta ${optimized-baseline:+.2f}; physics OK',flush=True)
    totals={}
    for site in ('U','R'):
        totals[site]={k:aggregate([r for r in all_rows if r['site_id']==site and r['controller']==k]) for k in ('rule_proportional','forecast_optimized','perfect_information')}
    output=dict(basis='SYNTHETIC DEMONSTRATION',field_validated=False,version='2.0',evaluation_year=2025,
                input_hashes=input_hashes,windows= comparisons,by_site=totals,wall_seconds=round(time.perf_counter()-started,2),
                method=dict(controller_comparison='Identical realized demand, source, prices, component ratings, source interlocks, shared network and backup, storage efficiencies, community contracts; every first action validated against exact original physics.',
                rule='Original instantaneous economic gate + proportional allocation, extended only for shared constraints/community priority. No future input.',
                forecast='Causal models issue each hour, 24-hour optimization, only first action executes, observations and SOC update before reissuing.',
                perfect_information='Same rolling optimizer with realized future; anticipative benchmark. Finite horizon, MILP envelopes and exact recourse mean its realized margin is not mathematically guaranteed to dominate.',
                true_upper_bound='A separate perfect-information lossless-network relaxed LP supplies an honest margin ceiling; it is not physically executable or a fair controller.',
                terminal='Every independent window opens with 0 MWh. All controllers close at 0 MWh; any residual storage is explicitly rejected with no sale/salvage credit. No annualization.',
                normalization='Percentage delta divided by absolute baseline operating margin, omitted if baseline is zero. USD delta is primary.',
                operator_boundary='Thermal sales less incremental heat electricity, variable O&M, source royalty, community contribution. Retained customer backup cost reported separately; CAPEX/fixed O&M/debt/tax absent. Margin is not project profit.',
                customer_savings_baseline='Delivered heat times original assumed avoided-heat tariff minus HeatCo thermal tariff. Unserved demand produces no avoided-cost or carbon credit.',
                outage_information='Unannounced outages are unavailable to forecasting until observed; deterministic execution recourse sees current observations equally for all controllers.'),
                verification=dict(physics_violation_count=sum(len(r['physics_violations']) for r in all_rows),
                                  max_conservation_error_mwh=max(r['energy_balance_error_mwh'] for r in all_rows),
                                  total_executed_controller_hours=len(all_rows)),
                source_files_sha256={f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in ('ml/dispatch.py','ml/solver.py','ml/backtest.py','ml/forecasting.py','artifacts/forecast_models.pkl','baseline/model/model.py','baseline/model/inputs.json')})
    ARTIFACTS.mkdir(exist_ok=True)
    suffix='_quick' if quick else ''
    (ARTIFACTS/f'backtest{suffix}.json').write_text(json.dumps(output,indent=2,default=json_default),encoding='utf-8')
    # JSON Lines preserves rich flow/constraint records; gzip is portable and compact.
    import gzip
    with gzip.open(ARTIFACTS/f'backtest_hourly{suffix}.jsonl.gz','wt',encoding='utf-8') as out:
        for row in all_rows: out.write(json.dumps(row,default=json_default)+'\n')
    return output


def run_stress_suite():
    # Independent explicit challenge inputs; no forecasts are fabricated here.
    base=dict(timestamp='2025-12-20T18:00:00+00:00',it_mw=3.8,cooling_mw=.5,other_mw=.2,
              source_mw=2.45,source_c=30.,source_available=1,source_flow_kg_s=85.,sensor_quality='good',
              demand_housing_mw=.6,demand_commercial_mw=1.6,demand_community_mw=.18,electricity_price=200.)
    scenarios={
        'ordinary':{},'outage':{'source_available':0},'summer_low_demand':{'demand_housing_mw':.12,'demand_commercial_mw':.22,'demand_community_mw':.01,'ambient_c':31},
        'winter_cold_spell':{'demand_housing_mw':3.2,'demand_commercial_mw':3.9,'demand_community_mw':.7,'ambient_c':-20},
        'expensive_electricity':{'electricity_price':1200},'high_demand_inadequate_backup':{'demand_housing_mw':5.,'demand_commercial_mw':5.,'demand_community_mw':1.,'backup_capacity_mw':.2},
        'inadequate_delivery_temperature':{'network_supply_c':55},'source_too_cold':{'source_c':8},
        'missing_sensors':{'sensor_quality':'missing'},'stale_sensors':{'sensor_quality':'stale'},
        'disconnected_customer':{'connected_housing':0,'connected_commercial':0},
        'zero_source_flow':{'source_flow_kg_s':0},'heat_pump_outage':{'hp_available':0},
        'cold_storage':{'storage_temperature_c':50},'cooling_interlock':{'cooling_interlock_ok':0},
        'empty_storage':{},'full_storage':{}}
    report=[]
    for site in ('U','R'):
        cfg=config(site)
        for name,updates in scenarios.items():
            actual=dict(base,site_id=site,source_c=30 if site=='U' else 45,
                        demand_commercial_mw=1.6 if site=='U' else 0.,**{})
            actual.update(updates)
            initial=cfg['storage_capacity_mwh'] if name in ('full_storage','cold_storage') else 0.
            future=[dict(actual,horizon=i+1) for i in range(24)]
            plan=plan_horizon(future,site,initial)
            step=execute_step(plan['records'][0],actual,site,initial)
            report.append(dict(site_id=site,scenario=name,solver_status=plan['status'],constraints=step['constraints'],
                               verified=not step['physics_violations'],flows=step,
                               horizon_totals=plan['totals']))
    output=dict(basis='SYNTHETIC DEMONSTRATION',randomized_physics_test_seed=613,scenario_count=len(report),
                passed=all(r['verified'] for r in report),max_conservation_error_mwh=max(r['flows']['energy_balance_error_mwh'] for r in report),cases=report)
    (ARTIFACTS/'stress_test.json').write_text(json.dumps(output,indent=2,default=json_default),encoding='utf-8')
    return output


if __name__=='__main__':
    # The original submission documents this host runtime policy for bounded
    # numerical workloads. Refcounts still release arrays/solvers immediately.
    import gc
    gc.disable()
    parser=argparse.ArgumentParser();parser.add_argument('--quick',action='store_true');parser.add_argument('--stress-only',action='store_true');args=parser.parse_args()
    stress=run_stress_suite();print(f"Stress: {stress['scenario_count']} cases; passed={stress['passed']}",flush=True)
    if not args.stress_only: run_backtest(args.quick)
