"""Issue-time features and saved direct multi-horizon forecasting models.

Every feature lookup is either <= the issue index or an issued/known exogenous
quantity. Realized future rows are only read by train.py for target evaluation.
"""
import bootstrap
import hashlib
import json
import pickle
import warnings
from pathlib import Path
import numpy as np
import pandas as pd
from .schema import ARTIFACTS, TARGETS, COHORTS, SITES
from .data import load_data, parameters, calendar_terms

MODEL_PATH=ARTIFACTS/'forecast_models.pkl'

def season(month):
    return np.select([np.isin(month,[12,1,2]),np.isin(month,[3,4,5]),np.isin(month,[6,7,8])],['winter','spring','summer'],default='autumn')

def bucket(horizon):
    return np.where(horizon==1,0,np.where(horizon<=6,1,np.where(horizon<=12,2,3)))

class FeatureBuilder:
    def __init__(self,data):
        self.sites={s:g.sort_values('timestamp').reset_index(drop=True).copy() for s,g in data.groupby('site_id')}
        self.observed={}
        self.sensor_observed={}
        for site,g in self.sites.items():
            g['timestamp']=pd.to_datetime(g.timestamp,utc=True)
            values=g[TARGETS].copy()
            values.loc[g.sensor_quality.ne('good'),:]=np.nan
            # Forward fill only; initial missing values use 0 and trigger fallback.
            self.observed[site]=values.ffill().fillna(0).to_numpy()
            sensor_columns=['ambient_c','source_c','source_return_c','source_flow_kg_s','source_available']
            sensors=g[sensor_columns].copy()
            sensors.loc[g.sensor_quality.ne('good'),:]=np.nan
            self.sensor_observed[site]=sensors.ffill().fillna(0)

    def locate(self,site,issue_timestamp):
        ts=pd.Timestamp(issue_timestamp)
        if ts.tzinfo is None: raise ValueError('Issue timestamp must include a UTC offset')
        index=self.sites[site].timestamp.searchsorted(ts.tz_convert('UTC'),side='right')-1
        if index<0: raise ValueError('No telemetry at or before issue time')
        return int(index)

    def features(self,site,origins,horizons=None):
        origins=np.asarray(origins,dtype=int)
        if horizons is None:
            oi=np.repeat(origins,24); h=np.tile(np.arange(1,25),len(origins))
        else:
            oi=origins; h=np.asarray(horizons,dtype=int)
        if ((h<1)|(h>24)).any(): raise ValueError('Forecast horizon must be 1..24')
        if (oi<0).any() or (oi>=len(self.sites[site])).any(): raise ValueError('Issue index outside available history')
        g=self.sites[site]; obs=self.observed[site]; p=parameters(site)
        issued=pd.DatetimeIndex(g.timestamp.iloc[oi]); valid=issued+pd.to_timedelta(h,unit='h')
        local,climate,tariff,gridbase=calendar_terms(valid,site)
        _,issue_climate,issue_tariff,issue_grid=calendar_terms(issued,site)
        current=g.iloc[oi].copy()
        for column in self.sensor_observed[site]:
            current[column]=self.sensor_observed[site][column].iloc[oi].to_numpy()
        # A causal synthetic weather forecast. The future realized ambient array is not referenced.
        anomaly=current.ambient_c.to_numpy()-issue_climate
        # Deterministic independent pseudo-random issue noise, unrelated to generator weather innovations.
        key=(issued.asi8//3_600_000_000_000).astype(np.int64)
        innovation=np.sin(key*12.9898+h*78.233+(0 if site=='U' else 9.17))*43758.5453
        innovation=(innovation-np.floor(innovation)-.5)*2
        weather=climate+anomaly*np.exp(-h/48)+innovation*(.25+.055*h)
        price=tariff+(current.electricity_price.to_numpy()-issue_tariff)*np.exp(-h/12)
        grid=gridbase+(current.grid_kgco2_kwh.to_numpy()-issue_grid)*np.exp(-h/18)
        day=local.dayofyear.to_numpy(); hour=local.hour.to_numpy(); dow=local.dayofweek.to_numpy()
        yearlen=np.where(local.is_leap_year,366,365)
        scheduled=((day<102)|(day>=104))&(day!=280)
        # Known current fault held for the first 6h, then no unsupported claim of repair: persist availability.
        current_available=current.source_available.to_numpy()
        availability=scheduled.astype(float)*current_available
        # Planned downtime ending in horizon is known; planned issue outage can be released on schedule.
        issue_scheduled=current.get('scheduled_available',pd.Series(np.ones(len(current)))).to_numpy()
        availability=np.where(issue_scheduled==0,scheduled.astype(float),availability)
        source_c=current.source_c.to_numpy()+.08*(weather-current.ambient_c.to_numpy())
        exog={'timestamp':valid,'issue_timestamp':issued,'horizon':h,'ambient_forecast_c':weather,
              'source_c':source_c,'source_flow_kg_s':current.source_flow_kg_s.to_numpy(),
              'source_return_c':source_c-(current.source_c.to_numpy()-current.source_return_c.to_numpy()),
              'source_available':availability,'scheduled_available':scheduled.astype(float),
              'electricity_price':np.maximum(0,price),'grid_kgco2_kwh':np.maximum(0,grid),
              'capture_fraction':np.full(len(oi),.55 if site=='U' else .62),
              'sensor_quality':current.sensor_quality.to_numpy()}
        for cohort in COHORTS:
            exog[f'connected_{cohort}']=current.get(f'connected_{cohort}',pd.Series(np.ones(len(current)))).to_numpy()
        names=['site_R','horizon','hour_sin','hour_cos','year_sin','year_cos','weekday','forecast_ambient_c','forecast_heating_degree','forecast_cooling_degree',
               'current_ambient_c','forecast_source_c','current_source_flow','known_source_available','forecast_price','quality_good']
        columns=[np.full(len(oi),float(site=='R')),h,np.sin(hour*2*np.pi/24),np.cos(hour*2*np.pi/24),np.sin(day*2*np.pi/yearlen),np.cos(day*2*np.pi/yearlen),(dow<5).astype(float),
                 weather,np.maximum(p['P_balance_temp']-weather,0),np.maximum(weather-7,0),current.ambient_c.to_numpy(),source_c,current.source_flow_kg_s.to_numpy(),availability,price,current.sensor_quality.eq('good').to_numpy().astype(float)]
        for ti,target in enumerate(TARGETS):
            for label,idx in [('current',oi),('previous_hour',np.maximum(0,oi-1)),('previous_day',np.maximum(0,oi+h-24)),('previous_week',np.maximum(0,oi+h-168))]:
                if (idx>oi).any(): raise AssertionError('Feature leakage')
                names.append(f'{target}_{label}'); columns.append(obs[idx,ti])
        self.feature_names=names
        return np.column_stack(columns).astype(np.float32),exog,oi

    def baseline(self,site,origins,horizons,name):
        obs=self.observed[site]; oi=np.asarray(origins); h=np.asarray(horizons)
        if name=='persistence': idx=oi
        elif name=='previous_day': idx=np.maximum(0,oi+h-24)
        elif name=='previous_week': idx=np.maximum(0,oi+h-168)
        else: raise ValueError(name)
        return obs[idx].copy()

def physical_clip(values,exog):
    result=np.maximum(0,np.asarray(values,dtype=float))
    # Protect source prediction by the physical interface and known condition envelope.
    cap=np.minimum.reduce([np.full(len(result),2.5),exog['source_flow_kg_s']*.004186*np.maximum(0,exog['source_c']-exog['source_return_c']),result[:,0]*exog['capture_fraction']])
    cap*=exog['source_available']*(exog['source_c']>=12)
    result[:,3]=np.minimum(result[:,3],cap)
    return result

class ForecastService:
    def __init__(self,data=None,model_path=MODEL_PATH):
        if not Path(model_path).exists(): raise FileNotFoundError('Train first: python -m ml.train')
        # Model files are local trusted build artifacts, never uploaded pickle input.
        with open(model_path,'rb') as handle: self.bundle=pickle.load(handle)
        self.data=load_data() if data is None else data
        self.builder=FeatureBuilder(self.data)

    def forecast_many(self,site_id,origins):
        origins=np.asarray(origins,dtype=int)
        x,exog,oi=self.builder.features(site_id,origins); h=exog['horizon']
        result=np.empty((len(x),len(TARGETS))); cache={}
        for ti,target in enumerate(TARGETS):
            name=self.bundle['selection'][site_id][target]
            if name in ['persistence','previous_day','previous_week']:
                if name not in cache: cache[name]=self.builder.baseline(site_id,oi,h,name)
                result[:,ti]=cache[name][:,ti]
            else:
                try: result[:,ti]=self.bundle['models'][target][name].predict(x)
                except (ValueError,RuntimeError,FloatingPointError): result[:,ti]=np.nan
        invalid=~np.isfinite(result).all(axis=1)
        if invalid.any():
            result[invalid]=self.builder.baseline(site_id,oi,h,'previous_day')[invalid]
            exog['sensor_quality'][invalid]='suspect'
        result=physical_clip(result,exog)
        quality=self.builder.sites[site_id].sensor_quality.iloc[oi].to_numpy()
        bad=(quality!='good')|invalid
        # No pretending model operation is healthy when current telemetry is missing.
        if bad.any():
            fallback=physical_clip(self.builder.baseline(site_id,oi,h,'previous_day'),exog)
            result[bad]=fallback[bad]
            result[bad,3]=0
            exog['source_available'][bad]=0
        widths=np.empty_like(result)
        for ti,target in enumerate(TARGETS):
            widths[:,ti]=np.asarray(self.bundle['calibration'][site_id][target])[bucket(h)]
        widths[bad]*=1.5
        lo=np.maximum(0,result-widths); hi=result+widths
        hi[:,3]=np.minimum(2.5,hi[:,3]); lo[:,3]=np.minimum(lo[:,3],hi[:,3])
        return result,lo,hi,exog,oi,bad

    def forecast(self,site_id,issue_timestamp):
        if site_id not in self.builder.sites: raise ValueError('Unknown site')
        issue=pd.Timestamp(issue_timestamp)
        if issue.tzinfo is None: raise ValueError('Issue timestamp must include UTC offset')
        origin=self.builder.locate(site_id,issue)
        latest=self.builder.sites[site_id].timestamp.iloc[origin]
        stale=(issue-latest)>pd.Timedelta(hours=2)
        result,lo,hi,exog,oi,bad=self.forecast_many(site_id,[origin])
        # Request beyond latest observed hour: refuse to imply newly issued inference.
        status='stale_telemetry_fallback' if stale or self.builder.sites[site_id].sensor_quality.iloc[origin]!='good' else 'invalid_prediction_fallback' if bad.any() else 'model_forecast'
        current=self.builder.sites[site_id].iloc[origin]
        if status=='model_forecast' and (current.ambient_c < -22 or current.ambient_c > 40 or current.electricity_price>600):
            status='out_of_distribution_conservative'
            hi=result+(hi-result)*1.5; lo=np.maximum(0,result-(result-lo)*1.5)
            hi[:,3]=np.minimum(hi[:,3],2.5)
        if stale:
            result[:,3]=0;lo[:,3]=0;hi[:,3]=0
            exog['source_available'][:]=0
        totalwidth=np.asarray(self.bundle['calibration'][site_id]['facility_mw'])[bucket(exog['horizon'])]
        rows=[]
        for k in range(24):
            row={name:(str(value[k].isoformat()) if name in ('timestamp','issue_timestamp') else str(value[k]) if name=='sensor_quality' else float(value[k])) for name,value in exog.items()}
            row['horizon']=int(row['horizon'])
            for ti,target in enumerate(TARGETS):
                row[target]=float(result[k,ti]);row[target+'_lower']=float(lo[k,ti]);row[target+'_upper']=float(hi[k,ti])
            row['facility_mw']=sum(row[t] for t in TARGETS[:3])
            row['facility_mw_lower']=max(0,row['facility_mw']-totalwidth[k]); row['facility_mw_upper']=row['facility_mw']+totalwidth[k]
            rows.append(row)
        return {'site_id':site_id,'issue_timestamp':latest.isoformat(),'requested_issue_timestamp':issue.isoformat(),'basis':'SYNTHETIC DEMONSTRATION' if 'regime' in self.data else 'EXTERNAL TELEMETRY · model trained on synthetic data',
                'status':status,'interval_nominal':.90,'interval_method':'Split conformal absolute residual; per site, target and horizon group, calibration Oct-Dec 2024. Coverage under distribution shift is measured, not guaranteed.',
                'selected_models':self.bundle['selection'][site_id], 'records':rows}
