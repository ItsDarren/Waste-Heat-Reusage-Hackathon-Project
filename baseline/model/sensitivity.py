"""Deterministic scenario range: not probabilities, not measured prediction intervals."""
import json,math
from pathlib import Path
from model import ROOT,BASE,write_csv,finance
from scenario_worker import isolated_site as run_site
FIELDS=['delivered_mwh','demand_annual_mwh','electricity_mwh','heat_coverage_fraction','system_cop','capex_usd','opex_usd','revenue_usd','operating_surplus_usd','npv_usd','irr_fraction','simple_payback_years','discounted_payback_years','lcoh_usd_mwh','avoided_operational_emissions_t','peak_backup_mw','source_availability_fraction']
def main():
    cases=[
      ('Base urban','U',{},None),('Base rural','R',{},None),
      ('Urban electricity $200/MWh','U',{'U_electricity':200},None),('Urban electricity $280/MWh','U',{'U_electricity':280},None),('Urban electricity $300/MWh','U',{'U_electricity':300},None),
      ('Urban conservative COP derate 0.44','U',{'P_carnot_eta':.44},None),('Urban favorable COP derate 0.60','U',{'P_carnot_eta':.60},None),
      ('Urban trench 1,400 m','U',{'U_route_m':1400},None),('Urban trench 400 m','U',{'U_route_m':400},None),
      ('Urban 20% capex overrun','U',{},'capex_20'),
      ('Urban 20% less all subscribed demand','U',{'U_commercial_dhw':5200,'U_commercial_space':2800,'U_homes':720,'U_community_dhw':160,'U_community_space':240},None),
      ('Urban commercial anchor removed','U',{'U_commercial_dhw':0,'U_commercial_space':0},None),
      ('Urban commercial cheap gas baseline $100/MWh','U',{'U_commercial_baseline':100},'cheap_gas'),
      ('Urban 10-year firm source, zero terminal value','U',{'P_years':10},None),
      ('Urban extra 72-hour winter source outage','U',{},'outage_72h'),('Urban 50% source capacity','U',{},'half_source'),
      ('Urban 10 C colder seven-day winter spell','U',{},'cold_spell'),
      ('Urban stratified tank 70% usable','U',{'P_storage_usable':.70},None),
      ('Urban negative operating margin electricity $1,000/MWh','U',{'U_electricity':1000},None),
      ('Urban source temperature reversed to5C','U',{},'reversed_temperature'),
      ('Urban combined downside','U',{'U_electricity':300,'P_carnot_eta':.44,'U_route_m':1400,'U_commercial_dhw':5200,'U_commercial_space':2800,'U_homes':720,'U_community_dhw':160,'U_community_space':240,'P_contingency':.35},None),
      ('Urban high grid emissions 0.60t/MWh','U',{'U_grid_ef':.60},None),
      ('Urban all commercial baseline steam','U',{'U_steam_share':1},None),
      ('Rural favorable grid 0.05t/MWh','R',{'R_grid_ef':.05},None),
    ]
    out=[]
    for name,site,overrides,stress in cases:
        d,_=run_site(site,overrides,stress if stress!='capex_20' else None,False)
        if stress=='capex_20':
            capital=d['capex_usd']*1.2;additionalfixed=d['capex_usd']*.2*(BASE['P_om_fraction']+BASE['P_insurance_fraction']);d.update(finance(capital,d['operating_surplus_usd']-additionalfixed,d['replacement_usd'],d['replacement_year'],d['project_years'],d['discount_rate']));d['capex_usd']=capital;d['opex_usd']+=additionalfixed;d['operating_surplus_usd']-=additionalfixed
            annuity=sum(1/(1+d['discount_rate'])**t for t in range(1,int(d['project_years'])+1));d['lcoh_usd_mwh']=(capital+d['opex_usd']*annuity+d['replacement_usd']/(1+d['discount_rate'])**d['replacement_year'])/(d['delivered_mwh']*annuity)
        row=dict(name=name,site_id=site,**{k:d[k] for k in FIELDS});out.append(row);print(name,round(d['npv_usd']))
    # Paid load threshold preserves price/cohort proportions and installed capex: not a new customer count forecast.
    lo,hi=.5,1.5
    loadkeys=['U_commercial_dhw','U_commercial_space','U_homes','U_community_dhw','U_community_space']
    lowrun,_=run_site('U',{k:BASE[k]*lo for k in loadkeys},None,False)
    highrun,_=run_site('U',{k:BASE[k]*hi for k in loadkeys},None,False)
    if not (lowrun['npv_usd']<0<highrun['npv_usd']):raise ValueError('Paid-load zero NPV not bracketed; report no crossing in tested range')
    for _ in range(15):
        scale=(lo+hi)/2;ov={k:BASE[k]*scale for k in ['U_commercial_dhw','U_commercial_space','U_homes','U_community_dhw','U_community_space']};d,_=run_site('U',ov,None,False)
        if d['npv_usd']<0:lo=scale
        else:hi=scale
    scale=(lo+hi)/2;d,_=run_site('U',{k:BASE[k]*scale for k in loadkeys},None,False)
    baseannual=BASE['U_homes']*(BASE['U_housing_dhw']+BASE['U_housing_space'])+BASE['U_commercial_dhw']+BASE['U_commercial_space']+BASE['U_community_dhw']+BASE['U_community_space']
    threshold={'paid_demand_scale_zero_npv':(lo+hi)/2,'paid_demand_annual_mwh_zero_npv':baseannual*(lo+hi)/2,'delivered_mwh_zero_npv':d['delivered_mwh'],'method':'Scale all cohort loads at unchanged per-MWh tariffs, profile shapes, installed CAPEX and other base assumptions; 15-step bisection.'}
    data={'interpretation':'Deterministic stress tests, not probability or prediction-accuracy claims. Each changes stated inputs; base uncertainty is not resolved by computation.','scenarios':out,'thresholds':threshold}
    (ROOT/'sensitivities.json').write_text(json.dumps(data,indent=2)+'\n');write_csv(ROOT/'sensitivities.csv',out);print(threshold)
if __name__=='__main__':main()


