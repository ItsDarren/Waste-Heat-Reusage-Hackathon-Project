"""Add the explicit feasibility proposal and deterministic robustness cases."""
import json
from model import ROOT,BASE,finance
from scenario_worker import isolated_site as run_site

def build(include_grid=True):
    data=json.loads((ROOT/'results.json').read_text());items={k:BASE['P_ask_'+k] for k in ['source_survey','customer_audit','route_design','contracts_equity','independent_review']}
    assert sum(items.values())==BASE['P_feasibility_ask']
    data['feasibility_programme']={'ask_usd':BASE['P_feasibility_ask'],'duration_days':BASE['P_validation_days'],'line_items_usd':items,'accounting':'Pre-FID risk capital; if built, credited within the construction engineering allowance, not added twice. If stopped, feasibility expenditure remains sunk.','construction_engineering_allowance_usd':data['sites']['U']['capex_items_usd']['engineering_permitting'],'team_role_count':BASE['P_team_roles'],'proposed_roles':['Project lead and systems integrator','Thermal and hydraulic engineer','Data-center operations and source interface lead','Data/model and software engineer','Commercial finance and contracting lead','Community engagement and affordability lead'],'people_status':'Functions proposed; no fabricated names, hires or partner commitments.'}
    data['evidence_gates']=[{'domain':x,'status':'UNVALIDATED','required_evidence':y} for x,y in [
        ('Source telemetry and rights','Interval primary-loop temperature/flow and safe capture envelope; circuit owner consent and DC operator control sign-off.'),
        ('Customer demand and tariffs','Interval bills and heat meters proving eligible water-side demand, avoidable variable prices, customer commitment, and housing pass-through.'),
        ('Vendor COP and capacity','Certified four-temperature performance maps, turndown and refrigerant specification.'),
        ('Route and installed cost','Surveyed branches, utility conflicts/easements, pressure and temperature design, firm scope and bids.'),
        ('Long-term contracts','Creditworthy source/offtake term aligned to project recovery, funded termination remedy, continuity and ownership obligations.')]]
    data['metadata']['input_sha256']=__import__('hashlib').sha256((ROOT/'inputs.json').read_bytes()).hexdigest()
    data['metadata']['model_sha256']=__import__('hashlib').sha256((ROOT/'model.py').read_bytes()).hexdigest()
    data['metadata']['validated_python_version']=__import__('sys').version.split()[0]
    data['metadata']['scenario_execution']='Fresh-process isolated scenarios; standard library only.'
    (ROOT/'results.json').write_text(json.dumps(data,indent=2)+'\n')
    if not include_grid:return
    rows=[]
    loadkeys=['U_commercial_dhw','U_commercial_space','U_homes','U_community_dhw','U_community_space']
    for el in [200,225,300]:
        for load in [.8,1,1.2]:
            ov={k:BASE[k]*load for k in loadkeys};ov['U_electricity']=el;d,_=run_site('U',ov,keep_rows=False)
            for cm in [.8,1,1.2]:
                cap=d['capex_usd']*cm;fixdelta=(cap-d['capex_usd'])*(BASE['P_om_fraction']+BASE['P_insurance_fraction'])
                f=finance(cap,d['operating_surplus_usd']-fixdelta,d['replacement_usd'],d['replacement_year'],d['project_years'],d['discount_rate'])
                rows.append(dict(electricity_usd_mwh=el,demand_multiplier=load,construction_capex_multiplier=cm,npv_usd=f['npv_usd'],positive_npv=f['npv_usd']>0))
    positive=sum(r['positive_npv'] for r in rows)
    robust={'cases':rows,'positive_npv_count':positive,'case_count':len(rows),'positive_fraction':positive/len(rows),'interpretation':'Fraction of explicitly assumed cases, NOT a probability, confidence interval, measurement accuracy or calibrated forecast. Equal case weights are arbitrary. Construction CAPEX/fixed O&M scale; year15 heat-pump refurbishment cost retains base value.'}
    (ROOT/'robustness.json').write_text(json.dumps(robust,indent=2)+'\n')
    print('Robustness:',positive,'/',len(rows),'positive NPV');print('Feasibility ask',sum(items.values()))
if __name__=='__main__':build()
