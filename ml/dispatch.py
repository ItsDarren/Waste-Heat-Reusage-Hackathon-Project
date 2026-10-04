"""Heat Commons causal dispatch: a shared MILP plus exact physical recourse.
All powers are MW, hourly energy MWh, temperatures degC, money real 2026 USD.
The baseline model is loaded read-only as the nonlinear physical validator.
"""
from __future__ import annotations
import bootstrap  # dependency paths and process-local native runtime guard
import importlib.util
import math
import time
import warnings
from pathlib import Path
import numpy as np
from scipy.optimize import Bounds, LinearConstraint
from .solver import solve_milp as milp
from scipy.sparse import lil_matrix

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("heat_commons_original_physics", ROOT / "baseline/model/model.py")
physics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(physics)
COHORTS = ("housing", "commercial", "community")
COMMUNITY_FRACTION = 0.70
VARIABLE_OM_USD_MWH = 3.0
UNSERVED_CONTRACT_USD_MWH = 1000.0


def number(row, key, default=0.):
    try:
        value = float(row.get(key, default))
        return value if math.isfinite(value) else float(default)
    except (TypeError, ValueError):
        return float(default)


def config(site_id):
    p = physics.site_params(site_id)
    capacity = p['storage_m3'] * p['P_rho'] * p['P_cp'] * (p['P_supply']-p['P_return']) / 3600000 * p['P_storage_usable']
    return dict(site_id=site_id, p=p, storage_capacity_mwh=capacity,
                storage_charge_mw=p['hp_mw']*.65, storage_discharge_mw=p['hp_mw']*.65,
                charge_efficiency=.97, discharge_efficiency=.97,
                storage_loss_fraction=p['P_storage_loss_hour'],
                storage_min_temperature_c=63., storage_temperature_c=65.,
                connection_mw={'housing': 1.4 if site_id=='U' else 2.4,
                               'commercial': 3.0 if site_id=='U' else 0.,
                               'community': .65 if site_id=='U' else .8},
                network_flow_limit_kg_s=1.20*p['hp_mw']*1000/(p['P_cp']*(p['P_supply']-p['P_return'])),
                tariffs={'housing':p['public_baseline']*(1-p['public_discount']),
                         'commercial':p['commercial_baseline']*(1-p['commercial_discount']),
                         'community':p['public_baseline']*(1-p['public_discount'])},
                avoided_costs={'housing':p['public_baseline'], 'commercial':p['commercial_baseline'], 'community':p['public_baseline']},
                baseline_ef={'housing':p['P_gas_ef']/p['P_boiler_eff'],
                             'community':p['P_gas_ef']/p['P_boiler_eff'],
                             'commercial':p['steam_share']*p['P_steam_ef']+(1-p['steam_share'])*p['grid_ef']})


def physical_state(row, site_id='U', conservative=False):
    cfg=config(site_id); p=dict(cfg['p'])
    p['P_supply']=number(row,'network_supply_c',p['P_supply'])
    p['P_return']=number(row,'network_return_c',p['P_return'])
    src_c=number(row,'source_c',p['source_c'])
    source_return=number(row,'source_return_c',src_c-p['source_dt'])
    # Recovery cannot cool the capture return below the original design outlet
    # difference or the explicit minimum return in the capture-loop agreement.
    source_dt=max(0.,min(p['source_dt'],src_c-source_return))
    p['source_dt']=max(source_dt,.01)
    source=max(0.,number(row,'source_mw'))
    if conservative:
        low=max(0.,number(row,'source_mw_lower',source))
        source=max(0.,source-.25*max(0.,source-low))
    availability=min(1.,max(0.,number(row,'source_available',1.)))
    flow=max(0.,number(row,'source_flow_kg_s',p['source_mw']*1000/(p['P_cp']*max(.01,source_dt))))
    source=min(source,p['source_mw']*availability,number(row,'capture_capacity_mw',p['source_mw']),
               number(row,'exchanger_capacity_mw',p['source_mw']),flow*p['P_cp']*source_dt/1000)
    if availability<.5 or src_c<p['P_source_min'] or source_dt<=0 or str(row.get('sensor_quality','good')).lower() in ('missing','stale','suspect','invalid','failed'):
        source=0.
    if number(row,'cooling_interlock_ok',1.)<.5:
        source=0.
    try: cop=physics.cop(src_c,p)
    except ValueError: cop=1.01; source=0.
    hp_cap=max(0.,min(p['hp_mw']*min(1.,max(0.,number(row,'hp_available',1.))),source/max(1e-9,1-1/cop)))
    demands={g:max(0.,number(row,'demand_'+g+'_mw')) for g in COHORTS}
    caps={g:min(demands[g],max(0.,number(row,'capacity_'+g+'_mw',cfg['connection_mw'][g]))) if number(row,'connected_'+g,1.)>=.5 else 0. for g in COHORTS}
    flags=[]
    try:
        physics.network(.001,p['route_m'],p)
    except (ValueError, ZeroDivisionError, OverflowError):
        caps={g:0. for g in COHORTS}; flags.append('DELIVERY_TEMPERATURE_INELIGIBLE')
    for g in COHORTS:
        if number(row,'delivery_required_'+g+'_c',p['P_dhw'])+p['P_hx_approach']>p['P_supply']:
            caps[g]=0.; flags.append(g.upper()+'_TEMPERATURE_INELIGIBLE')
    storage_eligible=(p['P_supply']>=cfg['storage_min_temperature_c'] and number(row,'storage_temperature_c',cfg['storage_temperature_c'])>=max(cfg['storage_min_temperature_c'],p['P_supply']))
    if not storage_eligible: flags.append('STORAGE_TEMPERATURE_INELIGIBLE')
    if source<=1e-9: flags.append('SOURCE_UNAVAILABLE')
    return dict(cfg=cfg,p=p,source=source,cop=cop,hp_cap=hp_cap,demands=demands,caps=caps,
                price=max(0.,number(row,'electricity_price',p['electricity'])),
                grid=max(0.,number(row,'grid_kgco2_kwh',p['grid_ef'])),
                backup_cap=max(0.,number(row,'backup_capacity_mw',p['backup_mw'])),
                storage_eligible=storage_eligible,flags=flags)


def network_result(q, state):
    return physics.network(max(0.,q)*1000,state['p']['route_m'],state['p'])


def max_network_delivery(state):
    total=sum(state['caps'].values())
    if total<=1e-10: return 0.
    limit=state['cfg']['network_flow_limit_kg_s']
    if network_result(1e-8,state)['mass_flow_kg_s']>limit: return 0.
    if network_result(total,state)['mass_flow_kg_s']<=limit: return total
    lo=0.; hi=total
    for _ in range(45):
        mid=(lo+hi)/2
        if network_result(mid,state)['mass_flow_kg_s']>limit: hi=mid
        else: lo=mid
    return lo


def network_envelope(state):
    """Conservative fixed loss + affine pump envelope, checked on 65 physical loads.
    Exact original physics verifies every executed action, independently of this fit.
    """
    maximum=max_network_delivery(state)
    if maximum<=1e-9: return (0.,0.,0.,0.)
    xs=np.linspace(1e-8,maximum,65)
    ns=[network_result(q,state) for q in xs]
    loss=max(n['loss_kw']/1000 for n in ns)*1.005
    pump0=ns[0]['pump_kw']/1000
    slope=max(0.,(ns[-1]['pump_kw']/1000-pump0)/maximum)
    intercept=max(n['pump_kw']/1000-slope*q for q,n in zip(xs,ns))*1.005
    return maximum,loss,intercept,slope*1.005


def _source_pump_factor(state):
    p=state['p']
    return (1-1/state['cop'])*p['P_source_dp']/(p['P_cp']*p['source_dt']*p['P_rho']*p['P_pump_eta']*1000)


def _empty_plan(row):
    return dict(row,hp_heat_mw=0.,storage_charge_mw=0.,storage_discharge_mw=0.,
                **{'delivered_'+g+'_mw':0. for g in COHORTS})


def plan_horizon(records, site_id='U', initial_soc=0., conservative=True, terminal_target=None, time_limit=10.):
    """Forecast then optimize. Records are future issue-time-available inputs only.
    Uses an hourly MILP (HiGHS), lexicographic community recovery obligation then USD.
    On invalid input/solver failure, returns a clearly labelled feasible rule forecast.
    """
    if isinstance(records,dict): records=records.get('records',[])
    records=list(records)[:24]
    if not records: raise ValueError('At least one future hour is required')
    required=['source_mw']+['demand_'+g+'_mw' for g in COHORTS]
    invalid=any(any(k not in r or not math.isfinite(number(r,k,float('nan'))) or number(r,k)<0 for k in required) for r in records)
    stale=any(str(r.get('sensor_quality','good')).lower() in ('stale','missing','invalid','failed') for r in records)
    if invalid or stale:
        return fallback_horizon(records,site_id,initial_soc,'INVALID_OR_STALE_FORECAST')
    states=[physical_state(r,site_id,conservative) for r in records]
    cfg=states[0]['cfg']; p=cfg['p']; H=len(records)
    initial_soc=float(np.clip(initial_soc,0,cfg['storage_capacity_mwh']))
    # Preserve opening stored energy, after unavoidable standby losses. This stops
    # free horizon-end depletion; future outage can force an explicit relaxation.
    target=initial_soc*(1-cfg['storage_loss_fraction'])**H if terminal_target is None else float(terminal_target)
    names=['hp','charge','discharge','soc','hp_on','net_on','charge_mode','community_shortfall']
    names += [x+'_'+g for g in COHORTS for x in ('deliver','backup','unmet')]
    n=len(names); N=H*n+1; terminal_slack=N-1
    def ix(t,k): return t*n+names.index(k)
    lb=np.zeros(N); ub=np.full(N,np.inf); integer=np.zeros(N,int)
    objective=np.zeros(N); community=np.zeros(N); rows=[]; lower=[]; upper=[]
    envelopes=[]
    def constraint(entries,lo=-np.inf,hi=np.inf):
        rows.append(entries); lower.append(lo); upper.append(hi)
    for t,s in enumerate(states):
        maximum,loss,pump0,pumpslope=network_envelope(s); envelopes.append((maximum,loss,pump0,pumpslope))
        ub[ix(t,'hp')]=s['hp_cap']; ub[ix(t,'charge')]=cfg['storage_charge_mw'] if s['storage_eligible'] else 0.
        ub[ix(t,'discharge')]=cfg['storage_discharge_mw'] if s['storage_eligible'] else 0.
        ub[ix(t,'soc')]=cfg['storage_capacity_mwh']
        for name in ('hp_on','net_on','charge_mode'):
            integer[ix(t,name)]=1; ub[ix(t,name)]=1
        constraint({ix(t,'hp'):1,ix(t,'hp_on'):-s['hp_cap']},hi=0.)
        constraint({ix(t,'charge'):1,ix(t,'charge_mode'):-cfg['storage_charge_mw']},hi=0.)
        constraint({ix(t,'discharge'):1,ix(t,'charge_mode'):cfg['storage_discharge_mw']},hi=cfg['storage_discharge_mw'])
        qrow={ix(t,'deliver_'+g):1. for g in COHORTS}; qrow[ix(t,'net_on')]=-maximum
        constraint(qrow,hi=0.)
        # Net thermal bus closes in conservative model. Pump electricity is separate.
        balance={ix(t,'hp'):1,ix(t,'discharge'):1,ix(t,'charge'):-1,ix(t,'net_on'):-loss}
        for g in COHORTS: balance[ix(t,'deliver_'+g)]=-1
        constraint(balance,lo=0.,hi=0.)
        energy={ix(t,'soc'):1,ix(t,'charge'):-cfg['charge_efficiency'],ix(t,'discharge'):1/cfg['discharge_efficiency']}
        rhs=initial_soc*(1-cfg['storage_loss_fraction']) if t==0 else 0.
        if t: energy[ix(t-1,'soc')]=-(1-cfg['storage_loss_fraction'])
        constraint(energy,lo=rhs,hi=rhs)
        constraint({ix(t,'backup_'+g):1 for g in COHORTS},hi=s['backup_cap'])
        obligation=s['caps']['community']*COMMUNITY_FRACTION
        constraint({ix(t,'deliver_community'):1,ix(t,'community_shortfall'):1},lo=obligation)
        community[ix(t,'community_shortfall')]=1
        for g in COHORTS:
            ub[ix(t,'deliver_'+g)]=s['caps'][g]
            ub[ix(t,'backup_'+g)]=s['demands'][g]; ub[ix(t,'unmet_'+g)]=s['demands'][g]
            constraint({ix(t,'deliver_'+g):1,ix(t,'backup_'+g):1,ix(t,'unmet_'+g):1},lo=s['demands'][g],hi=s['demands'][g])
            objective[ix(t,'deliver_'+g)]=-cfg['tariffs'][g]+p['P_community_fund']+VARIABLE_OM_USD_MWH+s['price']*(pumpslope+p['P_booster_fraction'])
            objective[ix(t,'unmet_'+g)]=UNSERVED_CONTRACT_USD_MWH
        objective[ix(t,'hp')]=s['price']*(1/s['cop']+_source_pump_factor(s))+(1-1/s['cop'])*p['P_source_fee']
        objective[ix(t,'hp_on')]=s['price']*p['P_aux_kw']/1000
        objective[ix(t,'net_on')]=s['price']*pump0
    # Allow terminal reserve relaxation only as explicit penalized MWh, separately
    # from the dollar objective. It cannot hide storage creation in physical recourse.
    constraint({ix(H-1,'soc'):1,terminal_slack:1},lo=target)
    terminal=np.zeros(N); terminal[terminal_slack]=1.
    A=lil_matrix((len(rows),N))
    for r,entries in enumerate(rows):
        for c,v in entries.items(): A[r,c]=v
    A=A.tocsr(); constraints=LinearConstraint(A,lower,upper)
    opts={'time_limit':float(time_limit),'mip_rel_gap':1e-6,'threads':1,'parallel':False}
    # SciPy forwards these documented HiGHS options; one thread avoids global
    # native scheduler contention when the demo server handles simultaneous users.
    warnings.filterwarnings('ignore',message='Unrecognized options detected.*',category=RuntimeWarning)
    started=time.perf_counter()
    try:
        first=milp(community,integrality=integer,bounds=Bounds(lb,ub),constraints=constraints,options=opts)
        if not first.success: return fallback_horizon(records,site_id,initial_soc,'COMMUNITY_SOLVER_'+str(first.message))
        # Sequential lexicographic energy stage: no arbitrary dollar/energy score.
        obligation_constraint=LinearConstraint(community.reshape(1,-1),-np.inf,float(first.fun)+1e-7)
        reserve=milp(terminal,integrality=integer,bounds=Bounds(lb,ub),constraints=[constraints,obligation_constraint],options=opts)
        if not reserve.success: return fallback_horizon(records,site_id,initial_soc,'TERMINAL_SOLVER_'+str(reserve.message))
        reserve_constraint=LinearConstraint(terminal.reshape(1,-1),-np.inf,float(reserve.fun)+1e-7)
        result=milp(objective,integrality=integer,bounds=Bounds(lb,ub),constraints=[constraints,obligation_constraint,reserve_constraint],options=opts)
        if not result.success: return fallback_horizon(records,site_id,initial_soc,'ECONOMIC_SOLVER_'+str(result.message))
    except Exception as error:
        return fallback_horizon(records,site_id,initial_soc,'SOLVER_EXCEPTION_'+type(error).__name__)
    solved=[]; soc=initial_soc
    for t,(raw,s) in enumerate(zip(records,states)):
        planned=dict(raw,hp_heat_mw=max(0.,result.x[ix(t,'hp')]),storage_charge_mw=max(0.,result.x[ix(t,'charge')]),storage_discharge_mw=max(0.,result.x[ix(t,'discharge')]),
                     **{'delivered_'+g+'_mw':max(0.,result.x[ix(t,'deliver_'+g)]) for g in COHORTS})
        # Forecast display receives the same exact physics validation as execution.
        # The reserved source bound, rather than its mean, governs planned flows.
        forecast_actual=dict(raw,source_mw=s['source'])
        executed=execute_step(planned,forecast_actual,site_id,soc,controller='forecast_optimized' if conservative else 'perfect_information')
        executed['optimizer_soc_mwh']=float(result.x[ix(t,'soc')])
        executed['forecast_horizon']=int(raw.get('horizon',t+1)); executed['planned']=True
        executed['optimization_loss_reserve_mw']=envelopes[t][1] if result.x[ix(t,'net_on')]>.5 else 0.
        solved.append(executed); soc=executed['storage_soc_mwh']
    return dict(status='optimized',solver_status=str(result.message),site_id=site_id,synthetic=True,
                objective_usd=float(-result.fun),community_shortfall_mwh=sum(r['community_obligation_shortfall_mw'] for r in solved),
                terminal_target_mwh=target,terminal_reserve_shortfall_mwh=float(result.x[terminal_slack]),
                solve_seconds=round(time.perf_counter()-started,5),records=solved,
                totals=aggregate(solved),metadata=dict(method='24-hour shared-equipment MILP; HiGHS; exact baseline recourse',
                source_reserve='25% of mean-to-nominal-90%-lower-interval distance' if conservative else 'perfect future information',
                objective='Lexicographic community shortfall MWh, then terminal reserve shortfall MWh, then operator USD margin less unserved-service contract penalties',
                terminal_policy='Opening SOC less standby losses retained; unavoidable relaxation reported',
                network='Conservative serial-equivalent common network; surveyed branches not established',
                decision_support_only=True))


def _deliver_for_budget(budget,state,desired):
    """Preserve community minimum, then scale the requested discretionary portfolio."""
    maximum=max_network_delivery(state)
    desired={g:min(state['caps'][g],max(0.,desired.get(g,0.))) for g in COHORTS}
    obligation=min(state['caps']['community']*COMMUNITY_FRACTION,desired['community'])
    def feasible(q):
        return sum(q.values())<=maximum+1e-9 and (not sum(q.values()) or network_result(sum(q.values()),state)['hp_output_kw']/1000<=budget+1e-10)
    base={g:(obligation if g=='community' else 0.) for g in COHORTS}
    if not feasible(base):
        lo=0.;hi=obligation
        for _ in range(42):
            mid=(lo+hi)/2
            if feasible(dict(housing=0.,commercial=0.,community=mid)): lo=mid
            else: hi=mid
        return dict(housing=0.,commercial=0.,community=lo)
    if feasible(desired): return desired
    lo=0.;hi=1.
    for _ in range(42):
        mid=(lo+hi)/2; q={g:base[g]+mid*(desired[g]-base[g]) for g in COHORTS}
        if feasible(q): lo=mid
        else: hi=mid
    return {g:base[g]+lo*(desired[g]-base[g]) for g in COHORTS}


def execute_step(plan_record, actual, site_id='U', initial_soc=0., controller='forecast_optimized'):
    """Execute only one hour after fresh measurements arrive; no future is read here.
    Exact original network losses/COP replace the MILP approximations. Actual demand,
    flow/temperature limits, connection state and source availability always prevail.
    """
    s=physical_state(actual,site_id); cfg=s['cfg']; p=s['p']; flags=list(s['flags'])
    opening=max(0.,min(float(initial_soc),cfg['storage_capacity_mwh']))
    standby=opening*cfg['storage_loss_fraction']; usable=opening-standby
    hp=min(s['hp_cap'],max(0.,number(plan_record,'hp_heat_mw')))
    discharge=min(cfg['storage_discharge_mw'],usable*cfg['discharge_efficiency'],max(0.,number(plan_record,'storage_discharge_mw'))) if s['storage_eligible'] else 0.
    charge=min(cfg['storage_charge_mw'],max(0.,number(plan_record,'storage_charge_mw'))) if s['storage_eligible'] else 0.
    if discharge>1e-8: charge=0.
    desired={g:min(s['caps'][g],max(0.,number(plan_record,'delivered_'+g+'_mw'))) for g in COHORTS}
    obligation=s['caps']['community']*COMMUNITY_FRACTION
    desired['community']=max(desired['community'],obligation)
    # Safety recourse can call available equipment to satisfy the explicit minimum
    # community recovery obligation, even when the forecast underpredicted demand.
    if obligation>1e-9:
        required=network_result(obligation,s)['hp_output_kw']/1000
        if hp+discharge<required:
            hp=min(s['hp_cap'],max(hp,required-discharge))
            if hp+discharge<required and s['storage_eligible']:
                discharge=min(cfg['storage_discharge_mw'],usable*cfg['discharge_efficiency'],required-hp)
                if discharge>1e-8: charge=0.
    allocations=_deliver_for_budget(hp+discharge,s,desired)
    total=sum(allocations.values()); net=network_result(total,s)
    heat=net['hp_output_kw']/1000
    # Any conservative loss reserve released by exact physics is curtailed. We do
    # not manufacture a sale or silently add it to SOC.
    discharge=min(discharge,heat)
    direct=max(0.,heat-discharge)
    charge=min(charge,max(0.,hp-direct),max(0.,(cfg['storage_capacity_mwh']-usable)/cfg['charge_efficiency']))
    hp=direct+charge
    soc=usable+charge*cfg['charge_efficiency']-discharge/cfg['discharge_efficiency']
    extracted=hp*(1-1/s['cop']); compressor=hp/s['cop']
    sourcepump=hp*_source_pump_factor(s)
    netpump=net['pump_kw']/1000
    auxiliary=p['P_aux_kw']/1000 if hp>1e-9 else 0.
    booster=total*p['P_booster_fraction']
    electric=compressor+sourcepump+netpump+auxiliary+booster
    residual={g:max(0.,s['demands'][g]-allocations[g]) for g in COHORTS}
    backups={g:0. for g in COHORTS}; backup_left=s['backup_cap']
    backups['community']=min(residual['community'],backup_left); backup_left-=backups['community']
    other=residual['housing']+residual['commercial']
    for g in ('housing','commercial'): backups[g]=min(residual[g],backup_left*residual[g]/other) if other else 0.
    unmet={g:max(0.,residual[g]-backups[g]) for g in COHORTS}
    revenue=sum(allocations[g]*cfg['tariffs'][g] for g in COHORTS)
    royalty=extracted*p['P_source_fee']; fund=total*p['P_community_fund']; om=total*VARIABLE_OM_USD_MWH
    cost=electric*s['price']+royalty+fund+om
    savings={g:allocations[g]*(cfg['avoided_costs'][g]-cfg['tariffs'][g]) for g in COHORTS}
    backup_spend=sum(backups[g]*cfg['avoided_costs'][g] for g in COHORTS)
    baseline_spend=sum(s['demands'][g]*cfg['avoided_costs'][g] for g in COHORTS)
    heat_emissions=electric*s['grid']+sum(backups[g]*cfg['baseline_ef'][g] for g in COHORTS)
    # kg/kWh numerically equals tonnes/MWh. Facility carbon is never credited to heat.
    facility=sum(max(0.,number(actual,k)) for k in ('it_mw','cooling_mw','other_mw'))
    facility_emissions=facility*s['grid']
    storage_conversion=charge*(1-cfg['charge_efficiency'])+discharge*(1/cfg['discharge_efficiency']-1)
    balance=hp+opening-total-net['loss_kw']/1000-standby-storage_conversion-soc
    repaired=any(abs(allocations[g]-number(plan_record,'delivered_'+g+'_mw'))>1e-5 for g in COHORTS) or abs(hp-number(plan_record,'hp_heat_mw'))>1e-5
    if repaired: flags.append('EXACT_PHYSICS_RECOURSE')
    if hp>=s['hp_cap']-1e-5 and hp>1e-8: flags.append('HEAT_PUMP_OR_SOURCE_LIMIT')
    if sum(backups.values())>=s['backup_cap']-1e-6 and sum(unmet.values())>1e-8: flags.append('BACKUP_CAPACITY_UNMET_DEMAND')
    if sum(allocations.values())<1e-8 and s['source']>1e-8: flags.append('RECOVERY_NOT_DISPATCHED')
    if obligation-allocations['community']>1e-5: flags.append('COMMUNITY_RECOVERY_SHORTFALL')
    if soc<=1e-5: flags.append('STORAGE_EMPTY')
    if soc>=cfg['storage_capacity_mwh']-1e-5: flags.append('STORAGE_FULL')
    row=dict(timestamp=str(actual.get('timestamp',plan_record.get('timestamp',''))),site_id=site_id,synthetic=True,controller=controller,
             source_heat_mw=extracted,source_available_mw=s['source'],rejected_source_mw=max(0.,s['source']-extracted),
             source_cap_mw=s['source'],hp_capacity_mw=s['hp_cap'],hp_heat_mw=hp,heating_cop=s['cop'],
             compressor_electricity_mw=compressor,network_pump_electricity_mw=netpump,source_pump_electricity_mw=sourcepump,
             auxiliary_electricity_mw=auxiliary,booster_electricity_mw=booster,incremental_electricity_mw=electric,
             facility_electricity_mw=facility,it_mw=max(0.,number(actual,'it_mw')),cooling_mw=max(0.,number(actual,'cooling_mw')),other_mw=max(0.,number(actual,'other_mw')),
             combined_electricity_mw=facility+electric,delivered_heat_mw=total,demand_mw=sum(s['demands'].values()),
             backup_heat_mw=sum(backups.values()),unmet_demand_mw=sum(unmet.values()),network_loss_mw=net['loss_kw']/1000,
             network_flow_kg_s=net['mass_flow_kg_s'],delivered_temperature_c=net['delivered_temp_c'] if total>1e-8 else None,
             storage_open_mwh=opening,storage_soc_mwh=max(0.,soc),storage_capacity_mwh=cfg['storage_capacity_mwh'],
             storage_charge_mw=charge,storage_discharge_mw=discharge,storage_standby_loss_mw=standby,storage_conversion_loss_mw=storage_conversion,
             revenue_usd=revenue,electricity_cost_usd=electric*s['price'],source_royalty_usd=royalty,community_fund_usd=fund,variable_om_usd=om,
             operating_cost_usd=cost,operating_margin_usd=revenue-cost,customer_savings_usd=sum(savings.values()),
             customer_backup_spend_usd=backup_spend,customer_service_baseline_spend_usd=baseline_spend,
             customer_heat_spend_usd=revenue+backup_spend,community_allocation_mw=allocations['community'],
             community_obligation_mw=obligation,community_obligation_shortfall_mw=max(0.,obligation-allocations['community']),
             heat_service_carbon_t=heat_emissions,facility_carbon_t=facility_emissions,operational_carbon_t=heat_emissions+facility_emissions,
             heat_service_baseline_carbon_t=sum((allocations[g]+backups[g])*cfg['baseline_ef'][g] for g in COHORTS),
             energy_balance_error_mwh=abs(balance),hp_identity_error_mwh=abs(hp-extracted-compressor),
             ambient_c=number(actual,'ambient_c',number(actual,'ambient_forecast_c')),source_c=number(actual,'source_c',p['source_c']),
             electricity_price=s['price'],grid_kgco2_kwh=s['grid'],constraints=sorted(set(flags)),
             physics_repaired=repaired,sensor_quality=str(actual.get('sensor_quality','good')))
    for g in COHORTS:
        row.update({f'delivered_{g}_mw':allocations[g],f'demand_{g}_mw':s['demands'][g],f'backup_{g}_mw':backups[g],
                    f'unmet_{g}_mw':unmet[g],f'revenue_{g}_usd':allocations[g]*cfg['tariffs'][g],f'savings_{g}_usd':savings[g],
                    f'connected_{g}':number(actual,'connected_'+g,1.)})
    violations=validate_step(row,actual,site_id)
    row['physics_violations']=violations
    if violations: raise ArithmeticError('Physical invariant failure: '+', '.join(violations))
    return row


def validate_step(row,actual,site_id='U',tolerance=1e-6):
    s=physical_state(actual,site_id); cfg=s['cfg']; bad=[]
    def check(test,message):
        if test: bad.append(message)
    check(row['energy_balance_error_mwh']>tolerance,'thermal conservation')
    check(row['hp_identity_error_mwh']>tolerance,'HP source plus compressor')
    check(row['source_heat_mw']>s['source']+tolerance,'source capacity/flow/availability')
    check(row['hp_heat_mw']>s['hp_cap']+tolerance,'shared HP capacity')
    check(row['storage_soc_mwh']>cfg['storage_capacity_mwh']+tolerance or row['storage_soc_mwh']<-tolerance,'storage capacity')
    check(row['storage_charge_mw']>tolerance and row['storage_discharge_mw']>tolerance,'simultaneous charge discharge')
    check(row['network_flow_kg_s']>cfg['network_flow_limit_kg_s']+tolerance,'network flow capacity')
    check(row['backup_heat_mw']>s['backup_cap']+tolerance,'shared backup capacity')
    check(abs(row['delivered_heat_mw']+row['backup_heat_mw']+row['unmet_demand_mw']-row['demand_mw'])>tolerance,'demand reconciliation')
    for g in COHORTS: check(row['delivered_'+g+'_mw']>s['caps'][g]+tolerance,g+' demand/connection')
    check(row['delivered_heat_mw']>tolerance and row['delivered_temperature_c']<s['p']['P_dhw']+s['p']['P_hx_approach']-tolerance,'delivery temperature')
    check(abs(row['facility_electricity_mw']-row['it_mw']-row['cooling_mw']-row['other_mw'])>tolerance,'facility reconciliation')
    return bad


def rule_step(actual,site_id='U',initial_soc=0.,controller='rule_proportional'):
    """Original instantaneous economic gate and proportional allocations, extended
    with the same community priority, physical constraints and storage efficiencies.
    No future demand, weather, price or source data are accessible to this controller.
    """
    s=physical_state(actual,site_id); cfg=s['cfg']; p=s['p']; desired=dict(s['caps'])
    total=sum(desired.values()); gate=False
    if total>1e-9:
        net=network_result(total,s); required=net['hp_output_kw']/1000
        electric=required/s['cop']+required*_source_pump_factor(s)+net['pump_kw']/1000+p['P_aux_kw']/1000+total*p['P_booster_fraction']
        gate=sum(desired[g]*cfg['tariffs'][g] for g in COHORTS)>=electric*s['price']+required*(1-1/s['cop'])*p['P_source_fee']+total*(p['P_community_fund']+VARIABLE_OM_USD_MWH)
    hp=s['hp_cap'] if gate else 0.
    obligation=s['caps']['community']*COMMUNITY_FRACTION
    if not gate: desired={g:obligation if g=='community' else 0. for g in COHORTS}
    direct_need=network_result(sum(desired.values()),s)['hp_output_kw']/1000 if sum(desired.values()) else 0.
    discharge=min(cfg['storage_discharge_mw'],max(0.,direct_need-hp),initial_soc*(1-cfg['storage_loss_fraction'])*cfg['discharge_efficiency'])
    charge=min(cfg['storage_charge_mw'],max(0.,hp-direct_need)) if not discharge else 0.
    plan=dict(actual,hp_heat_mw=hp,storage_charge_mw=charge,storage_discharge_mw=discharge,**{'delivered_'+g+'_mw':desired[g] for g in COHORTS})
    return execute_step(plan,actual,site_id,initial_soc,controller)


def fallback_horizon(records,site_id,initial_soc,reason):
    rows=[]; soc=initial_soc
    for actual in records:
        r=rule_step(actual,site_id,soc,'deterministic_fallback');r['constraints'].append(reason)
        rows.append(r);soc=r['storage_soc_mwh']
    return dict(status='fallback',solver_status=reason,site_id=site_id,synthetic=True,records=rows,totals=aggregate(rows),
                metadata={'decision_support_only':True,'fallback_reason':reason})


def aggregate(rows):
    if not rows:return {}
    keys=['facility_electricity_mw','incremental_electricity_mw','source_heat_mw','hp_heat_mw','delivered_heat_mw','rejected_source_mw','network_loss_mw','backup_heat_mw','unmet_demand_mw','storage_charge_mw','storage_discharge_mw','storage_standby_loss_mw','storage_conversion_loss_mw','community_allocation_mw','community_obligation_shortfall_mw']
    totals={k.replace('_mw','_mwh'):sum(r[k] for r in rows) for k in keys}
    for k in ['revenue_usd','operating_cost_usd','operating_margin_usd','customer_savings_usd','source_royalty_usd','community_fund_usd','electricity_cost_usd','variable_om_usd','customer_backup_spend_usd','heat_service_carbon_t','facility_carbon_t','operational_carbon_t','heat_service_baseline_carbon_t']:
        totals[k]=sum(r[k] for r in rows)
    for g in COHORTS:
        totals['sold_'+g+'_mwh']=sum(r['delivered_'+g+'_mw'] for r in rows)
        totals['savings_'+g+'_usd']=sum(r['savings_'+g+'_usd'] for r in rows)
    totals.update(hours=len(rows),peak_facility_electricity_mw=max(r['facility_electricity_mw'] for r in rows),
                  peak_incremental_electricity_mw=max(r['incremental_electricity_mw'] for r in rows),peak_combined_electricity_mw=max(r['combined_electricity_mw'] for r in rows),
                  storage_initial_mwh=rows[0]['storage_open_mwh'],storage_final_mwh=rows[-1]['storage_soc_mwh'],
                  max_conservation_error_mwh=max(r['energy_balance_error_mwh'] for r in rows),
                  physics_violation_count=sum(len(r['physics_violations']) for r in rows),recourse_hours=sum(r['physics_repaired'] for r in rows),
                  avoided_heat_service_carbon_t=sum(r['heat_service_baseline_carbon_t']-r['heat_service_carbon_t'] for r in rows))
    return totals
