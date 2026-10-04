"""Reproducible three-year synthetic trajectories with independent annual/site seeds.

The named buildings provide case-study context, not measured workload information.
The source tranche remains a 2.5 MW thermal assumption. Electrical loads are new,
explicit synthetic assumptions and must never be inferred from that tranche.
"""
import bootstrap
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd
from .schema import ARTIFACTS, ROOT, SITES, COHORTS, write_schema

SEED = 73129
DATA_PATH = ARTIFACTS / 'synthetic_hourly.csv.gz'

def parameters(site):
    assumptions=json.loads((ROOT/'baseline/model/inputs.json').read_text(encoding='utf-8'))['assumptions']
    p={a['id']:a['value'] for a in assumptions}
    p.update({k[2:]:v for k,v in list(p.items()) if k.startswith(site+'_')})
    return p

def ar_noise(rng,n,rho,scale):
    x=np.empty(n); x[0]=rng.normal(0,scale)
    shocks=rng.normal(0,scale*np.sqrt(1-rho*rho),n)
    for i in range(1,n): x[i]=rho*x[i-1]+shocks[i]
    return x

def calendar_terms(timestamps,site):
    local=pd.DatetimeIndex(timestamps).tz_convert(SITES[site]['timezone'])
    day=local.dayofyear.to_numpy(); hour=local.hour.to_numpy(); weekday=local.dayofweek.to_numpy()<5
    yearlen=np.where(local.is_leap_year,366,365)
    p=parameters(site)
    climate=p['weather_mean']-p['weather_amp']*np.cos(2*np.pi*(day-18)/yearlen)+p['weather_diurnal']*np.sin(2*np.pi*(hour-9)/24)
    tariff=p['electricity']*(.77+.30*((hour>=16)&(hour<=21))+.12*((hour>=8)&(hour<16))+.07*weekday)
    grid=p['grid_ef']*(.89+.17*((hour>=16)&(hour<=21))+.08*(np.cos(2*np.pi*day/yearlen)**2))
    return local,climate,tariff,grid

def generate_dataset(force=False):
    if DATA_PATH.exists() and not force: return load_data()
    ARTIFACTS.mkdir(exist_ok=True); frames=[]; seed_registry={}
    for si,site in enumerate(SITES):
        p=parameters(site)
        for year in (2023,2024,2025):
            seed=SEED+si*100000+(year-2023)*1009; seed_registry[f'{site}-{year}']=seed
            rng=np.random.default_rng(seed)
            ts=pd.date_range(f'{year}-01-01',f'{year+1}-01-01',inclusive='left',freq='h',tz='UTC'); n=len(ts)
            local,climate,tariff,gridbase=calendar_terms(ts,site)
            h=local.hour.to_numpy(); day=local.dayofyear.to_numpy(); weekday=local.dayofweek.to_numpy()<5
            # Synoptic multi-day persistence + fronts and sporadic ramps. Not a repeated yearly waveform.
            weather=climate+ar_noise(rng,n,.985,4.2)+ar_noise(rng,n,.66,.7)
            weather+=np.repeat(rng.normal(0,1.5,(n+167)//168),168)[:n]
            util=np.clip(.69+.055*weekday+.035*np.cos(2*np.pi*(h-13)/24)+ar_noise(rng,n,.96,.075),.22,.98)
            # Batch jobs create uncertain non-sinusoidal electrical demand.
            for _ in range(100):
                start=int(rng.integers(0,n-24)); length=int(rng.integers(2,18)); util[start:start+length]+=rng.uniform(-.11,.14)
            util=np.clip(util,.18,.99)
            it=(4.9 if site=='U' else 5.3)*(.19+.81*util)+rng.normal(0,.025,n)
            cooling=it*(.087+.0042*np.maximum(weather-7,0))+rng.normal(0,.012,n)
            other=.18+.04*weekday+.025*((h>=7)&(h<=20))+ar_noise(rng,n,.8,.014)
            source_c=p['source_c']+p['source_amp']*(util-.7)+.10*(weather-p['weather_mean'])+ar_noise(rng,n,.92,.5)
            delta=np.clip(p['source_dt']+.5*(util-.7)+rng.normal(0,.1,n),4,12)
            flow=(88 if site=='U' else 70)*(.90+.12*util)+ar_noise(rng,n,.9,2)
            scheduled=np.ones(n)
            # Work orders are announced ahead of time: 48h in spring and 24h in fall.
            scheduled[(day>=102)&(day<104)]=0; scheduled[(day==280)]=0
            available=scheduled.copy()
            for _ in range(7):
                start=int(rng.integers(0,n-48)); available[start:start+int(rng.integers(3,21))]=0
            occ=np.maximum(.5,1+ar_noise(rng,n,.96,.08))
            dhwseason=1+.10*np.cos(2*np.pi*(day-18)/365.2425)
            patterns={'housing_dhw':dhwseason*(.4+1.5*np.exp(-((h-7)/2)**2)+1.1*np.exp(-((h-19)/2.5)**2)),
                      'commercial_dhw':dhwseason*(.7+.8*((h>=7)&(h<23)))*np.where(weekday,1.05,.875),
                      'community_dhw':dhwseason*(.4+.9*((h>=7)&(h<21)))}
            degree=np.maximum(0,p['P_balance_temp']-weather)
            demands={}
            for cohort in COHORTS:
                factor={'housing':np.where((h<8)|(h>=17),1.15,.8),'commercial':np.where((h>=6)&(h<23),1.05,.7),'community':np.where((h>=7)&(h<21),1.1,.6)}[cohort]
                # Fixed climate normalization, not yearly renormalization that could suppress cold-year energy.
                refdegree=np.maximum(0,p['P_balance_temp']-climate)*factor
                annual_dhw=p[f'{cohort}_dhw']*(p['homes'] if cohort=='housing' else 1)
                annual_space=p[f'{cohort}_space']*(p['homes'] if cohort=='housing' else 1)
                dhw=patterns[f'{cohort}_dhw']/patterns[f'{cohort}_dhw'].mean()*annual_dhw/8760
                space=degree*factor/max(refdegree.mean(),.1)*annual_space/8760
                demands[cohort]=np.maximum(0,(dhw+space)*occ*(1+rng.normal(0,.025,n)))
            price=np.maximum(20,tariff+.9*np.maximum(weather-25,0)+.8*np.maximum(0,-weather)+ar_noise(rng,n,.91,13))
            grid=np.maximum(.025,gridbase+ar_noise(rng,n,.97,.018))
            regime=np.full(n,'ordinary',dtype=object)
            connected={c:np.ones(n,dtype=int) for c in COHORTS}
            if year==2025:
                cold=(ts>=pd.Timestamp('2025-01-14',tz='UTC'))&(ts<pd.Timestamp('2025-01-18',tz='UTC'))
                weather[cold]-=13; regime[cold]='heldout_cold_spell'
                for cohort in COHORTS:
                    if p[f'{cohort}_space']>0: demands[cohort][cold]*=1.65
                cooling[cold]=it[cold]*.087; price[cold]*=1.4
                expensive=(ts>=pd.Timestamp('2025-07-16',tz='UTC'))&(ts<pd.Timestamp('2025-07-19',tz='UTC'))
                price[expensive]*=3.8; regime[expensive]='heldout_price_spike'
                outage=(ts>=pd.Timestamp('2025-10-12',tz='UTC'))&(ts<pd.Timestamp('2025-10-14',tz='UTC'))
                available[outage]=0; regime[outage]='heldout_unannounced_outage'
                tempbad=(ts>=pd.Timestamp('2025-04-18',tz='UTC'))&(ts<pd.Timestamp('2025-04-19',tz='UTC'))
                source_c[tempbad]=10; regime[tempbad]='heldout_source_temperature'
                disconnect=(ts>=pd.Timestamp('2025-09-10',tz='UTC'))&(ts<pd.Timestamp('2025-09-12',tz='UTC'))
                connected['commercial' if site=='U' else 'housing'][disconnect]=0; regime[disconnect]='heldout_customer_disconnect'
            source=np.minimum.reduce([np.full(n,2.5),it*(.55 if site=='U' else .62),flow*.004186*delta])*available*(source_c>=12)
            quality=np.full(n,'good',dtype=object)
            for _ in range(12):
                start=int(rng.integers(168,n-24)); quality[start:start+int(rng.integers(1,7))]='missing'
            if year==2025:
                missing=(ts>=pd.Timestamp('2025-02-13T06:00Z'))&(ts<pd.Timestamp('2025-02-13T14:00Z'))
                quality[missing]='stale'; regime[missing]='heldout_stale_sensor'
            frame=pd.DataFrame(dict(timestamp=ts,site_id=site,it_mw=it,cooling_mw=np.maximum(cooling,0),other_mw=np.maximum(other,0),source_mw=source,
                ambient_c=weather,source_c=source_c,source_return_c=source_c-delta,source_flow_kg_s=flow,source_available=available,scheduled_available=scheduled,
                cpu_utilization=util,gpu_utilization=np.clip(util+ar_noise(rng,n,.9,.06),0,1),electricity_price=price,grid_kgco2_kwh=grid,sensor_quality=quality,regime=regime))
            frame['facility_mw']=frame.it_mw+frame.cooling_mw+frame.other_mw
            for c in COHORTS: frame[f'demand_{c}_mw']=demands[c]; frame[f'connected_{c}']=connected[c]
            frames.append(frame)
    result=pd.concat(frames,ignore_index=True).sort_values(['site_id','timestamp']).reset_index(drop=True)
    result.to_csv(DATA_PATH,index=False,float_format='%.8f',compression={'method':'gzip','mtime':0})
    write_schema()
    metadata={'basis':'SYNTHETIC DEMONSTRATION','measured_telemetry':False,'generator_seed':SEED,'independent_site_year_seeds':seed_registry,
        'rows':len(result),'utc_start':str(result.timestamp.min()),'utc_end':str(result.timestamp.max()),'hours_by_site_year':{f'{s}-{y}':len(g) for (s,y),g in result.groupby(['site_id',result.timestamp.dt.year])},
        'dataset_sha256':hashlib.sha256(DATA_PATH.read_bytes()).hexdigest(),'electrical_assumptions':'Nominal IT scale 4.9 MW NYC / 5.3 MW Lansing, synthetic utilization; temperature-dependent cooling and 0.18 MW base other facility power. No site electrical measurements.',
        'thermal_assumption':'Original 2.5 MW interface tranche retained, independently capped by IT heat fraction, measured/simulated flow*cp*deltaT, availability and source temperature.',
        'realized_ground_truth':'Power columns are synthetic latent realized truth even during sensor-quality incidents. Forecaster hides bad-quality target observations; evaluation may use latent truth.',
        'heldout_regimes':'2025 only: Jan14-18 cold spell; Feb13 stale sensors; Apr18 low source temperature; Jul16-19 price spike; Sep10-12 connection loss; Oct12-14 unannounced outage.',
        'weather_issue_method':'At issue time climate + decay of observed current weather anomaly + independently seeded issue/horizon forecast error. No realized future weather is read.'}
    (ARTIFACTS/'dataset_metadata.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    return result

def load_data():
    if not DATA_PATH.exists(): return generate_dataset()
    frame=pd.read_csv(DATA_PATH); frame['timestamp']=pd.to_datetime(frame.timestamp,utc=True)
    return frame

if __name__=='__main__':
    frame=generate_dataset(force=True)
    print(f'Generated {len(frame):,} hourly synthetic rows at {DATA_PATH}',flush=True)
