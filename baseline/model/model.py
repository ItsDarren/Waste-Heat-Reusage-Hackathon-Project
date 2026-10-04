"""HEATCOMMONS: deterministic screening model, Python standard library only.
All measured-looking time series are synthetic. See inputs.json for parameter provenance.
Run: python model.py. Outputs are regenerated from this file and inputs.json.
"""
import calendar,csv,datetime,json,math,hashlib,copy
from pathlib import Path
ROOT=Path(__file__).parent
REG=json.loads((ROOT/'inputs.json').read_text(encoding='utf-8'))
BASE={a['id']:a['value'] for a in REG['assumptions']}

def cop(source_c,p):
    tc=p['P_supply']+p['P_cond_approach']+273.15
    te=source_c-p.get('source_dt',8)/2-p['P_capture_hx_approach']-p['P_evap_approach']+273.15
    if te>=tc: raise ValueError('Non-positive refrigerant temperature lift')
    return max(1.01,p['P_carnot_eta']*tc/(tc-te))

def network(qkw,length,p):
    """Single equivalent pair; exponential loss both pipes. q is metered customer heat.
    Variable circulation maintains >=DHW target+HX approach at remote supply;
    return rises at low load. Total trench is conservative serial-equivalent annually.
    """
    if qkw<=0: return dict(hp_output_kw=0,loss_kw=0,delivered_temp_c=p['P_supply'],mass_flow_kg_s=0,pump_kw=0,return_at_customer_c=p['P_return'],return_at_plant_c=p['P_return'])
    ts,tr,tg=p['P_supply'],p['P_return'],p['P_ground']
    minimum=p['P_dhw']+p['P_hx_approach']
    if ts<=tr or ts<minimum or (length>0 and ts<=minimum): raise ValueError('Temperature requirement cannot be met')
    k=p['P_u_pipe']*length
    x=qkw*1000/(ts-tr)+4*k
    for _ in range(14):
        e=math.exp(-k/x) if x else 1
        f=x*((ts-tg)*e-(tr-tg))-qkw*1000
        d=(ts-tg)*e*(1+k/x)-(tr-tg)
        xn=x-f/d
        x=max(xn,x/2) if xn>0 else x/2
    minx=k/math.log((ts-tg)/(minimum-tg)) if k>0 else 0
    x=max(x,minx)
    e=math.exp(-k/x)
    tout=tg+(ts-tg)*e
    rcustomer=tout-qkw*1000/x
    rplant=tg+(rcustomer-tg)*e
    heat=x*(ts-rplant)/1000
    flow=x/(p['P_cp']*1000)
    designflow=p.get('hp_mw',3)*1000/(p['P_cp']*(ts-tr))
    dp=2*length*p['P_pressure_gradient']*(flow/designflow)**2+p['P_station_dp']
    pump=flow/p['P_rho']*dp/p['P_pump_eta']/1000
    return dict(hp_output_kw=heat,loss_kw=heat-qkw,delivered_temp_c=tout,mass_flow_kg_s=flow,pump_kw=pump,return_at_customer_c=rcustomer,return_at_plant_c=rplant)

def delivery_for_heat(available_kw,demand_kw,length,p):
    if available_kw<=0 or demand_kw<=0:return 0,network(0,length,p)
    need=network(demand_kw,length,p)
    if need['hp_output_kw']<=available_kw:return demand_kw,need
    lo,hi=0,demand_kw
    for _ in range(24):
        q=(lo+hi)/2
        if network(q,length,p)['hp_output_kw']>available_kw:hi=q
        else:lo=q
    return lo,network(lo,length,p)

def distance_engine(route_length_m=800,delivered_requirement_kw=1000,network_supply_c=65,network_return_c=40,source_inlet_c=30,electricity_price_per_mwh=225,supply_available=True,parameters=None):
    p=dict(BASE);p.update(parameters or {});p.update(P_supply=network_supply_c,P_return=network_return_c,source_dt=p['U_source_dt'],hp_mw=p['U_hp_mw'])
    flags=[]
    if route_length_m<0 or delivered_requirement_kw<0: raise ValueError('Length and load must be non-negative')
    if not supply_available or source_inlet_c<p['P_source_min']:
        return dict(delivered_temp_c=None,hp_output_kw=0,source_heat_kw=0,electricity_kw=0,loss_kw=0,loss_percent=0,mass_flow_kg_s=0,pump_kw=0,heating_cop=0,system_cop=0,capex_pipe_usd=route_length_m*p['U_pipe_usd_m'],marginal_cost_per_mwh=None,feasibility_flags=['SOURCE_UNAVAILABLE_BACKUP_REQUIRED'],delivered_kw=0)
    try:n=network(delivered_requirement_kw,route_length_m,p);c=cop(source_inlet_c,p)
    except ValueError:return dict(delivered_temp_c=None,hp_output_kw=0,source_heat_kw=0,electricity_kw=0,loss_kw=0,loss_percent=0,mass_flow_kg_s=0,pump_kw=0,heating_cop=0,system_cop=0,capex_pipe_usd=route_length_m*p['U_pipe_usd_m'],marginal_cost_per_mwh=None,feasibility_flags=['TEMPERATURE_INFEASIBLE_BACKUP_REQUIRED'],delivered_kw=0)
    qh=n['hp_output_kw'];source=qh*(1-1/c);spump=source/(p['P_cp']*p['source_dt'])/p['P_rho']*p['P_source_dp']/p['P_pump_eta']/1000
    pump=n['pump_kw']+spump+(p['P_aux_kw'] if qh else 0);el=qh/c+pump
    if qh>p['U_hp_mw']*1000:flags.append('HEAT_PUMP_CAPACITY_EXCEEDED')
    if source>p['U_source_mw']*1000:flags.append('SOURCE_CAPACITY_EXCEEDED')
    if not flags:flags.append('SCREENING_FEASIBLE_UNVERIFIED_INPUTS')
    return dict(result_basis='REQUIRED_DUTY_NOT_DISPATCH',delivered_temp_c=n['delivered_temp_c'],hp_output_kw=qh,source_heat_kw=source,electricity_kw=el,loss_kw=n['loss_kw'],loss_percent=n['loss_kw']/qh*100 if qh else 0,mass_flow_kg_s=n['mass_flow_kg_s'],pump_kw=pump,heating_cop=c,system_cop=delivered_requirement_kw/el if el else 0,capex_pipe_usd=route_length_m*p['U_pipe_usd_m'],marginal_cost_per_mwh=(el*electricity_price_per_mwh+source*p['P_source_fee'])/delivered_requirement_kw+p['P_community_fund'] if delivered_requirement_kw else None,feasibility_flags=flags,delivered_kw=delivered_requirement_kw)

def site_params(site,overrides=None):
    p=dict(BASE);p.update(overrides or {})
    for k,v in list(p.items()):
        if k.startswith(site+'_'):p[k[2:]]=v
    return p

def make_profiles(p,stress=None):
    raw={k:[] for k in ('housing_dhw','housing_space','commercial_dhw','commercial_space','community_dhw','community_space')}; weather=[]
    for h in range(8760):
        day=h//24;hod=h%24;weekday=(day+3)%7<5
        t=p['weather_mean']-p['weather_amp']*math.cos(2*math.pi*(day-18)/365)+p['weather_diurnal']*math.sin(2*math.pi*(hod-9)/24)
        weather.append(t)
        dhwseason=1+.10*math.cos(2*math.pi*(day-18)/365)
        raw['housing_dhw'].append(dhwseason*(.4+1.5*math.exp(-((hod-7)/2)**2)+1.1*math.exp(-((hod-19)/2.5)**2)))
        raw['commercial_dhw'].append(dhwseason*(.7+.8*(7<=hod<23))*(1.05 if weekday else .875))
        raw['community_dhw'].append(dhwseason*(.4+.9*(7<=hod<21)))
        degree=max(0,p['P_balance_temp']-t)
        raw['housing_space'].append(degree*(1.15 if hod<8 or hod>=17 else .8))
        raw['commercial_space'].append(degree*(1.05 if 6<=hod<23 else .7))
        raw['community_space'].append(degree*(1.1 if 7<=hod<21 else .6))
    annual=dict(housing_dhw=p['homes']*p['housing_dhw'],housing_space=p['homes']*p['housing_space'],commercial_dhw=p['commercial_dhw'],commercial_space=p['commercial_space'],community_dhw=p['community_dhw'],community_space=p['community_space'])
    totals={k:sum(values) for k,values in raw.items()}
    normalized={k:[v/totals[k]*annual[k] for v in raw[k]] for k in raw}
    if stress=='cold_spell':
        for h in range(24*24,31*24):
            old=max(.1,p['P_balance_temp']-weather[h]);weather[h]-=10
            for k in ('housing_space','commercial_space','community_space'):normalized[k][h]*=max(0,p['P_balance_temp']-weather[h])/old
    return weather,normalized

def capex(p):
    items=dict(heat_pumps=p['hp_mw']*1000*p['hp_usd_kw'],capture_and_isolation=p['capture_cost'],twin_pipe_civils=p['route_m']*p['pipe_usd_m'],customer_ets=p['ets_cost'],customer_interface_retrofits=p['retrofit_cost'],stratified_storage=p['storage_m3']*p['tank_usd_m3'],electrical=p['electric_cost'],controls_metering=p['controls_cost'],retained_backup_readiness=p['backup_readiness'])
    direct=sum(items.values());items['engineering_permitting']=direct*p['P_civils_eng'];items['contingency']=(direct+items['engineering_permitting'])*p['P_contingency'];return items

def finance(capital,net,replacement,year,life,r):
    flows=[-capital]+[net-(replacement if t==year else 0) for t in range(1,int(life)+1)]
    npv=sum(v/(1+r)**t for t,v in enumerate(flows));cum=-capital;dcum=-capital;simple=None;discounted=None
    for t,v in enumerate(flows[1:],1):
        old=cum;cum+=v
        if simple is None and cum>=0:simple=t-1+(-old/v)
        old=dcum;pv=v/(1+r)**t;dcum+=pv
        if discounted is None and dcum>=0:discounted=t-1+(-old/pv)
    def f(rate):return sum(v/(1+rate)**t for t,v in enumerate(flows))
    irr=None
    if net>0 and f(-.5)>0 and f(2)<0:
        lo,hi=-.5,2
        for _ in range(80):
            mid=(lo+hi)/2
            if f(mid)>0:lo=mid
            else:hi=mid
        irr=(lo+hi)/2
    return dict(npv_usd=npv,irr_fraction=irr,simple_payback_years=simple,discounted_payback_years=discounted,cashflows_usd=flows)

def run_site(site='U',overrides=None,stress=None,keep_rows=True):
    p=site_params(site,overrides);weather,profiles=make_profiles(p,stress)
    demand=[sum(v[h] for v in profiles.values()) for h in range(8760)]
    groups={g:[profiles[g+'_dhw'][h]+profiles[g+'_space'][h] for h in range(8760)] for g in ('housing','commercial','community')}
    tariffs={'housing':p['public_baseline']*(1-p['public_discount']),'community':p['public_baseline']*(1-p['public_discount']),'commercial':p['commercial_baseline']*(1-p['commercial_discount'])}
    baselines={'housing':p['public_baseline'],'community':p['public_baseline'],'commercial':p['commercial_baseline']}
    baselineef={'housing':p['P_gas_ef']/p['P_boiler_eff'],'community':p['P_gas_ef']/p['P_boiler_eff'],'commercial':p['steam_share']*p['P_steam_ef']+(1-p['steam_share'])*p['grid_ef']}
    if stress=='cheap_gas':baselineef['commercial']=p['P_gas_ef']/p['P_boiler_eff']
    storage=p['storage_m3']*p['P_rho']*p['P_cp']*(p['P_supply']-p['P_return'])/3600000*p['P_storage_usable']
    prepared=[]
    for h in range(8760):
        day=h//24; source_c=p['source_c']-p['source_amp']*math.cos(2*math.pi*(day-18)/365)
        source=p['source_mw']*(.96+.025*math.cos(2*math.pi*(h%24-15)/24)+.015*math.cos(2*math.pi*day/365))
        # Exactly 168 baseline downtime hours, 72 contiguous in January.
        outage_hours=int(p['source_outage_h']); first=min(72,outage_hours); second=min(48,max(0,outage_hours-first)); third=max(0,outage_hours-first-second)
        unavailable=(10*24<=h<10*24+first or 130*24<=h<130*24+second or 250*24<=h<250*24+third)
        if stress=='outage_72h':unavailable=unavailable or 28*24<=h<31*24
        if stress=='half_source':source*=.5
        if stress=='reversed_temperature':source_c=5
        if unavailable or source_c<p['P_source_min']:source=0
        c=cop(source_c,p);maxhp=min(p['hp_mw'],source/(1-1/c)) if source else 0
        n=network(demand[h]*1000,p['route_m'],p)
        rev_per_mwh=sum(groups[g][h]*tariffs[g] for g in groups)/demand[h] if demand[h] else 0
        # Conservative marginal gate includes all auxiliary power at requested load.
        source_req=n['hp_output_kw']*(1-1/c)
        srcpump=source_req/(p['P_cp']*p['source_dt'])/p['P_rho']*p['P_source_dp']/p['P_pump_eta']/1000
        variable=(n['hp_output_kw']/c+n['pump_kw']+srcpump+p['P_aux_kw'])*p['electricity']/1000+source_req/1000*p['P_source_fee']+demand[h]*p['P_community_fund']
        if variable>rev_per_mwh*demand[h]:maxhp=0
        prepared.append((source_c,source,c,maxhp,n))
    def dispatch(initial,record=False):
        soc=initial;rows=[];agg={k:0.0 for k in ('demand_mwh','delivered_mwh','backup_mwh','hp_heat_mwh','source_heat_mwh','compressor_mwh','network_pump_mwh','source_pump_mwh','auxiliary_mwh','booster_mwh','electricity_mwh','pipe_loss_mwh','storage_loss_mwh','storage_charge_mwh','storage_discharge_mwh','source_available_mwh','revenue_usd','baseline_heat_emissions_t','source_fee_usd','community_fund_usd')};coh={g:dict(demand_mwh=0.,delivered_mwh=0.,backup_mwh=0.,savings_usd=0.,revenue_usd=0.,avoided_heat_emissions_t=0.) for g in groups};peakback=peakflow=peakel=0;min_temp=100;max_closure=0
        for h,(st,src,c,maxhp,n) in enumerate(prepared):
            opening=soc;standby=soc*p['P_storage_loss_hour'];soc-=standby
            need=n['hp_output_kw']/1000
            direct=min(need,maxhp);discharge=min(soc,max(0,need-direct));budget=direct+discharge
            q,actual=delivery_for_heat(budget*1000,demand[h]*1000,p['route_m'],p)
            delivered=q/1000;used=actual['hp_output_kw']/1000
            # Numerical inversion can return slightly below budget: never discard storage silently.
            discharge=max(0,min(discharge,used-direct));direct=min(direct,used)
            soc-=discharge
            charge=min(max(0,maxhp-direct),storage-soc);soc+=charge
            hp=direct+charge;extract=hp*(1-1/c);comp=hp/c
            sourcepump=extract*1000/(p['P_cp']*p['source_dt'])/p['P_rho']*p['P_source_dp']/p['P_pump_eta'] # MW->kW gives kg/s; hydraulic expression then gives W.
            sourcepump/=1000000
            netpump=actual['pump_kw']/1000;aux=p['P_aux_kw']/1000 if hp>1e-9 else 0
            dhw=sum(profiles[g+'_dhw'][h] for g in groups)
            booster=delivered*dhw/demand[h]*p['P_booster_fraction'] if demand[h] else 0
            el=comp+sourcepump+netpump+aux+booster;loss=actual['loss_kw']/1000;backup=demand[h]-delivered
            closure=abs(hp+opening-delivered-loss-standby-soc);max_closure=max(max_closure,closure)
            if record:
                vals=dict(demand_mwh=demand[h],delivered_mwh=delivered,backup_mwh=backup,hp_heat_mwh=hp,source_heat_mwh=extract,compressor_mwh=comp,network_pump_mwh=netpump,source_pump_mwh=sourcepump,auxiliary_mwh=aux,booster_mwh=booster,electricity_mwh=el,pipe_loss_mwh=loss,storage_loss_mwh=standby,storage_charge_mwh=charge,storage_discharge_mwh=discharge,source_available_mwh=src,source_fee_usd=extract*p['P_source_fee'],community_fund_usd=delivered*p['P_community_fund'])
                rev=baseem=0
                for g in groups:
                    gd=groups[g][h];served=delivered*gd/demand[h] if demand[h] else 0
                    for k,v in dict(demand_mwh=gd,delivered_mwh=served,backup_mwh=gd-served,savings_usd=served*(baselines[g]-tariffs[g]),revenue_usd=served*tariffs[g],avoided_heat_emissions_t=served*baselineef[g]).items():coh[g][k]+=v
                    rev+=served*tariffs[g];baseem+=served*baselineef[g]
                vals.update(revenue_usd=rev,baseline_heat_emissions_t=baseem)
                for k,v in vals.items():agg[k]+=v
                peakback=max(peakback,backup);peakflow=max(peakflow,actual['mass_flow_kg_s']);peakel=max(peakel,el);min_temp=min(min_temp,actual['delivered_temp_c']) if delivered>0 else min_temp
                if keep_rows:rows.append(dict(hour=h,timestamp=(datetime.datetime(2025,1,1)+datetime.timedelta(hours=h)).isoformat(),month=(datetime.datetime(2025,1,1)+datetime.timedelta(hours=h)).month,outdoor_c=weather[h],capture_inlet_c=st,heating_cop=c,storage_open_mwh=opening,storage_close_mwh=soc,delivered_network_c=actual['delivered_temp_c'] if delivered else 0,source_max_mw=src,hp_max_mw=maxhp,network_flow_kg_s=actual['mass_flow_kg_s'],**vals))
        return soc,rows,agg,coh,dict(peak_backup_mw=peakback,peak_electric_mw=peakel,peak_network_flow_kg_s=peakflow,min_delivered_network_c=min_temp if agg['delivered_mwh'] else None,max_hourly_heat_balance_error_mwh=max_closure)
    initial=0
    for _ in range(6):
        end,*_=dispatch(initial)
        if abs(end-initial)<1e-10:break
        initial=end
    end,rows,agg,coh,peaks=dispatch(initial,True)
    costs=capex(p);capital=sum(costs.values());fixed=capital*(p['P_om_fraction']+p['P_insurance_fraction'])+p['P_admin'];electriccost=agg['electricity_mwh']*p['electricity'];opex=fixed+electriccost+agg['source_fee_usd']+agg['community_fund_usd'];net=agg['revenue_usd']-opex
    replacement=costs['heat_pumps']*p['P_replacement_fraction'];fin=finance(capital,net,replacement,p['P_replacement_year'],p['P_years'],p['P_discount']);annuity=sum(1/(1+p['P_discount'])**t for t in range(1,int(p['P_years'])+1));pvreplace=replacement/(1+p['P_discount'])**p['P_replacement_year'] if p['P_replacement_year']<=p['P_years'] else 0;lcoh=(capital+opex*annuity+pvreplace)/(agg['delivered_mwh']*annuity) if agg['delivered_mwh'] else None
    gridem=agg['electricity_mwh']*p['grid_ef'];netco2=agg['baseline_heat_emissions_t']-gridem-p['P_leakage'];embodied=p['P_embodied']*(p['route_m']/800)**.45
    for g,v in coh.items():v.update(tariff_usd_mwh=tariffs[g],avoidable_baseline_usd_mwh=baselines[g],baseline_emissions_t_mwh=baselineef[g],coverage_fraction=v['delivered_mwh']/v['demand_mwh'] if v['demand_mwh'] else 0)
    d=dict(site='111 8th Avenue, NYC' if site=='U' else 'Lake Hawkeye, former Cayuga Power Plant, Lansing, NY',site_id=site,synthetic=True,demand_annual_mwh=sum(demand),demand_peak_mw=max(demand),source_capture_nominal_mw=p['source_mw'],source_capture_inlet_nominal_c=p['source_c'],source_capture_inlet_min_c=min(x[0] for x in prepared),source_capture_inlet_max_c=max(x[0] for x in prepared),source_capture_outlet_nominal_c=p['source_c']-p['source_dt'],hp_capacity_mw=p['hp_mw'],hp_modules=3,network_supply_c=p['P_supply'],network_return_design_c=p['P_return'],network_total_trench_m=p['route_m'],farthest_route_m=p['farthest_route_m'],target_homes=p['homes'],backup_installed_mw=p['backup_mw'],storage_geometric_m3=p['storage_m3'],storage_usable_mwh=storage,storage_initial_mwh=initial,storage_final_mwh=end,storage_cyclic_error_mwh=end-initial,storage_peak_load_hours=storage/max(demand),source_availability_fraction=sum(1 for x in prepared if x[1]>0)/8760,heat_coverage_fraction=agg['delivered_mwh']/sum(demand),heat_pump_capacity_factor=agg['hp_heat_mwh']/(p['hp_mw']*8760),heating_cop_seasonal=agg['hp_heat_mwh']/agg['compressor_mwh'] if agg['compressor_mwh'] else None,system_cop=agg['delivered_mwh']/agg['electricity_mwh'] if agg['electricity_mwh'] else None,pipe_loss_fraction=agg['pipe_loss_mwh']/(agg['delivered_mwh']+agg['pipe_loss_mwh']) if agg['delivered_mwh'] else 0,storage_loss_fraction=agg['storage_loss_mwh']/agg['hp_heat_mwh'] if agg['hp_heat_mwh'] else 0,electricity_price_usd_mwh=p['electricity'],grid_factor_t_mwh=p['grid_ef'],capex_items_usd=costs,capex_usd=capital,fixed_opex_usd=fixed,electricity_opex_usd=electriccost,opex_usd=opex,operating_surplus_usd=net,replacement_year=p['P_replacement_year'],replacement_usd=replacement,discount_rate=p['P_discount'],project_years=p['P_years'],lcoh_usd_mwh=lcoh,weighted_tariff_usd_mwh=agg['revenue_usd']/agg['delivered_mwh'] if agg['delivered_mwh'] else None,avoided_operational_emissions_t=netco2,new_electricity_emissions_t=gridem,refrigerant_allowance_t=p['P_leakage'],embodied_allowance_t=embodied,carbon_payback_years=embodied/netco2 if netco2>0 else None,community_bill_savings_usd=coh['community']['savings_usd'],housing_bill_savings_usd=coh['housing']['savings_usd'],housing_saving_usd_per_target_home=coh['housing']['savings_usd']/p['homes']*p['P_passthrough'],resident_passthrough_fraction=p['P_passthrough'],cohorts=coh,**agg,**fin,**peaks)
    d['required_electricity_price_for_zero_npv_usd_mwh']=(agg['revenue_usd']-fixed-agg['source_fee_usd']-agg['community_fund_usd']-(capital+pvreplace)/annuity)/agg['electricity_mwh'] if agg['electricity_mwh'] else None
    d['electric_load_factor']=agg['electricity_mwh']/(8760*peaks['peak_electric_mw']) if peaks['peak_electric_mw'] else 0
    d['tariff_context_mismatch']=d['electric_load_factor']<0.65
    d['tariff_validation_note']='S22 p146 all-in tariff context assumed 65-90% DC load factor; this heat-pump duty is lower, so the assumed rate requires a specific utility quote.'
    d['required_commercial_avoidable_baseline_for_zero_npv_usd_mwh']=((capital+pvreplace)/annuity+opex-coh['housing']['revenue_usd']-coh['community']['revenue_usd'])/(coh['commercial']['delivered_mwh']*(1-p['commercial_discount'])) if coh['commercial']['delivered_mwh'] else None
    d['required_weighted_tariff_for_zero_npv_usd_mwh']=lcoh
    d['commercial_dhw_equivalent_litres_per_day']=p['commercial_dhw']*3600000/(365*p['P_cp']*(p['P_dhw']-p['P_dhw_mains']))
    d['housing_dhw_litres_person_day']=p['housing_dhw']*3600000/(365*p['P_cp']*(p['P_dhw']-p['P_dhw_mains'])*p['P_dhw_occupants']*(1+p['P_dhw_loss_fraction']))
    # Existing backup remains responsible for its own electricity/fuel; not financed by HeatCo.
    d['backup_emissions_t']=sum(v['backup_mwh']*v['baseline_emissions_t_mwh'] for v in coh.values())
    d['whole_service_baseline_emissions_t']=sum(v['demand_mwh']*v['baseline_emissions_t_mwh'] for v in coh.values())
    d['whole_service_project_emissions_t']=d['backup_emissions_t']+gridem+p['P_leakage']
    designflow=p['hp_mw']*1000/(p['P_cp']*(p['P_supply']-p['P_return']));d['design_network_flow_kg_s']=designflow;diam=math.sqrt(4*designflow/p['P_rho']/math.pi/p['P_velocity'])*1000;d['screening_pipe_internal_diameter_mm']=next((x for x in [80,100,125,150,200,250,300,400] if x>=diam),400)
    return d,rows

def write_csv(path,rows):
    if not rows:return
    with path.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def main():
    results={'metadata':{'version':'1.0','reference_year':2025,'currency':REG['currency'],'profile_scope':REG['profile_scope'],'input_sha256':hashlib.sha256((ROOT/'inputs.json').read_bytes()).hexdigest(),'network_method':'Conservative serial-equivalent total trench with exponential supply/return losses and minimum-temperature circulation; actual branched route requires survey.'},'sites':{}}
    allrows=[];monthly=[]
    for s in ['U','R']:
        d,rows=run_site(s);results['sites'][s]=d
        allrows.extend([dict(site_id=s,**r) for r in rows])
        for m in range(1,13):
            rr=[r for r in rows if r['month']==m];monthly.append(dict(site_id=s,month=m,**{k:sum(r[k] for r in rr) for k in ['demand_mwh','delivered_mwh','backup_mwh','hp_heat_mwh','source_heat_mwh','electricity_mwh','pipe_loss_mwh','storage_loss_mwh','revenue_usd']}))
    (ROOT/'results.json').write_text(json.dumps(results,indent=2)+'\n');write_csv(ROOT/'hourly.csv',allrows);write_csv(ROOT/'monthly.csv',monthly);write_csv(ROOT/'assumptions.csv',REG['assumptions'])
    from robustness import build
    build(include_grid=False)
    print(json.dumps({s:{k:d[k] for k in ['demand_annual_mwh','delivered_mwh','hp_heat_mwh','system_cop','capex_usd','opex_usd','revenue_usd','npv_usd','simple_payback_years','avoided_operational_emissions_t','required_electricity_price_for_zero_npv_usd_mwh']} for s,d in results['sites'].items()},indent=2))

if __name__=='__main__':main()



