"""Run: python -m ml.train [--regenerate]. CPU-only reproducible training and audit."""
import os
import bootstrap  # project-local dependencies and host-specific process affinity
os.environ.setdefault('OMP_NUM_THREADS','2')
os.environ.setdefault('OPENBLAS_NUM_THREADS','2')
import argparse
import hashlib
import json
import pickle
import platform
from pathlib import Path
import subprocess
import sys
import time
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from .data import generate_dataset, DATA_PATH, SEED
from .forecasting import FeatureBuilder, ForecastService, MODEL_PATH, physical_clip, bucket, season
from .schema import ARTIFACTS, TARGETS, SITES, validate_csv

SPLITS={'train':('2023-01-08','2024-07-01',12),'validation':('2024-07-01','2024-10-01',23),'calibration':('2024-10-01','2025-01-01',23),'test':('2025-01-01','2026-01-01',23)}
CANDIDATES=['persistence','previous_day','previous_week','ridge','gradient_boosting']

def batch(builder,site,split):
    start,end,stride=SPLITS[split]; g=builder.sites[site]
    eligible=np.flatnonzero((g.timestamp>=pd.Timestamp(start,tz='UTC'))&(g.timestamp+pd.Timedelta(hours=24)<pd.Timestamp(end,tz='UTC')))
    origins=eligible[::stride]
    x,exog,oi=builder.features(site,origins)
    y=g[TARGETS].to_numpy()[oi+exog['horizon']]
    return dict(x=x,y=y,exog=exog,oi=oi,origins=origins)

def all_predictions(builder,site,b,models):
    out={}
    for candidate in CANDIDATES:
        if candidate in CANDIDATES[:3]: pred=builder.baseline(site,b['oi'],b['exog']['horizon'],candidate)
        else: pred=np.column_stack([models[t][candidate].predict(b['x']) for t in TARGETS])
        out[candidate]=physical_clip(pred,b['exog'])
    return out

def selected_predictions(builder,site,b,predictions,selection):
    y=np.column_stack([predictions[selection[site][target]][:,i] for i,target in enumerate(TARGETS)])
    bad=builder.sites[site].sensor_quality.iloc[b['oi']].ne('good').to_numpy()
    y[bad]=predictions['previous_day'][bad]
    y[bad,3]=0
    return y

def metrics(actual,pred,denom):
    error=pred-actual; mae=float(np.abs(error).mean());rmse=float(np.sqrt(np.mean(error*error)))
    return {'mae_mw':mae,'rmse_mw':rmse,'nmae_training_mean':mae/denom if denom>1e-6 else None,'normalization_denominator_mw':float(denom),'n':len(actual)}

def quantile90(errors):
    if not len(errors): return 0.
    q=min(1,np.ceil((len(errors)+1)*.90)/len(errors))
    return float(np.quantile(errors,q,method='higher'))

def verify_pipeline(data,service):
    checks={}
    issue=pd.Timestamp('2025-01-14T12:00:00Z');site='U'
    origin=service.builder.locate(site,issue)
    original_x,original_exog,_=service.builder.features(site,[origin])
    changed=data.copy(); future=(changed.site_id==site)&(changed.timestamp>issue)
    for column in changed.select_dtypes(include=np.number).columns:
        changed.loc[future,column]=changed.loc[future,column]*7+31
    changed.loc[future,'sensor_quality']='missing'
    changed_x,changed_exog,_=FeatureBuilder(changed).features(site,[origin])
    assert np.array_equal(original_x,changed_x)
    for name in original_exog:
        assert np.array_equal(np.asarray(original_exog[name]),np.asarray(changed_exog[name])),name
    checks['future_mutation_invariance']=True
    checks['feature_sha256']=hashlib.sha256(original_x.tobytes()).hexdigest()
    frame=service.builder.sites[site]
    assert len(frame[frame.timestamp.dt.year==2024])==8784
    local=frame.timestamp.dt.tz_convert('America/New_York')
    # In UTC each hour is unique; the local spring day has23, fall day25.
    assert int((local.dt.strftime('%Y-%m-%d')=='2024-03-10').sum())==23
    assert int((local.dt.strftime('%Y-%m-%d')=='2024-11-03').sum())==25
    checks['leap_year_hours']=8784; checks['dst_spring_hours']=23; checks['dst_fall_hours']=25
    missing=service.forecast(site,'2025-02-13T08:00:00Z')
    assert missing['status']=='stale_telemetry_fallback'
    assert all(row['source_mw']==0 and row['source_available']==0 for row in missing['records'])
    checks['missing_sensor_failclosed']=True
    class InvalidEstimator:
        def predict(self,x): return np.full(len(x),np.nan)
    chosen=service.bundle['selection']['U']['it_mw']
    if chosen not in CANDIDATES[:3]:
        saved=service.bundle['models']['it_mw'][chosen]
        service.bundle['models']['it_mw'][chosen]=InvalidEstimator()
        failed=service.forecast('U',issue)
        assert failed['status']=='invalid_prediction_fallback'
        assert all(row['source_mw']==0 and np.isfinite(row['it_mw']) for row in failed['records'])
        service.bundle['models']['it_mw'][chosen]=saved
        checks['nonfinite_estimator_output_failclosed']=True
    for site in SITES:
        forecast=service.forecast(site,issue)
        assert all(abs(row['facility_mw']-sum(row[t] for t in TARGETS[:3]))<1e-9 for row in forecast['records'])
        assert all(0<=row['source_mw']<=2.5 and row['source_mw']<=row['it_mw']*row['capture_fraction']+1e-7 for row in forecast['records'])
        assert all(0<=row[t+'_lower']<=row[t]<=row[t+'_upper'] for row in forecast['records'] for t in TARGETS)
    checks['electrical_reconciliation_and_nonnegative_intervals']=True
    checks['thermal_source_forecast_limits']=True
    sample=data.groupby('site_id',group_keys=False).head(240)
    sample.to_csv(ARTIFACTS/'telemetry_ingestion_example.csv',index=False)
    loaded=validate_csv(ARTIFACTS/'telemetry_ingestion_example.csv')
    assert len(loaded)==480
    checks['csv_ingestion_roundtrip_rows']=480
    bad=sample.copy();bad['timestamp']=bad.timestamp.dt.tz_localize(None)
    bad.to_csv(ARTIFACTS/'_invalid_csv_test.csv',index=False)
    try: validate_csv(ARTIFACTS/'_invalid_csv_test.csv')
    except ValueError: checks['naive_timestamp_rejected']=True
    else: raise AssertionError('Naive timestamps accepted')
    (ARTIFACTS/'_invalid_csv_test.csv').unlink()
    (ARTIFACTS/'forecast_verification.json').write_text(json.dumps(checks,indent=2),encoding='utf-8')
    return checks

def run(regenerate=False):
    started=time.time(); data=generate_dataset(force=regenerate);builder=FeatureBuilder(data)
    print(f'Data: {len(data):,} rows, 2023-2025 incl leap year. Building causal features.',flush=True)
    train={site:batch(builder,site,'train') for site in SITES}
    x=np.concatenate([b['x'] for b in train.values()]); y=np.concatenate([b['y'] for b in train.values()])
    models={};train_means={s:dict(zip(TARGETS,b['y'].mean(axis=0).tolist())) for s,b in train.items()}
    for ti,target in enumerate(TARGETS):
        print(f'Training {target} ({len(x):,} examples; {x.shape[1]} issue-time features)',flush=True)
        models[target]={}
        models[target]['ridge']=make_pipeline(StandardScaler(),Ridge(alpha=50.))
        models[target]['gradient_boosting']=HistGradientBoostingRegressor(max_iter=130,max_leaf_nodes=23,learning_rate=.075,l2_regularization=8,min_samples_leaf=45,early_stopping=False,random_state=SEED+ti)
        for model in models[target].values(): model.fit(x,y[:,ti])
    selection={};validation=[];calibration={};selected_cal={}
    for site in SITES:
        b=batch(builder,site,'validation'); preds=all_predictions(builder,site,b,models)
        selection[site]={}
        for ti,target in enumerate(TARGETS):
            scores={candidate:float(np.abs(pred[:,ti]-b['y'][:,ti]).mean()) for candidate,pred in preds.items()}
            # Stable insertion order favors the simplest model in exact ties.
            selection[site][target]=min(scores,key=scores.get)
            for candidate in CANDIDATES: validation.append(dict(site_id=site,target=target,model=candidate,**metrics(b['y'][:,ti],preds[candidate][:,ti],train_means[site][target])))
        cal=batch(builder,site,'calibration');calpreds=all_predictions(builder,site,cal,models)
        point=selected_predictions(builder,site,cal,calpreds,selection);errors=np.abs(point-cal['y'])
        calibration[site]={}
        for ti,target in enumerate(TARGETS): calibration[site][target]=[quantile90(errors[bucket(cal['exog']['horizon'])==bi,ti]) for bi in range(4)]
        facility_errors=np.abs(point[:,:3].sum(axis=1)-cal['y'][:,:3].sum(axis=1))
        calibration[site]['facility_mw']=[quantile90(facility_errors[bucket(cal['exog']['horizon'])==bi]) for bi in range(4)]
    bundle={'schema_version':'2.0','models':models,'selection':selection,'calibration':calibration,'feature_names':builder.feature_names,'targets':TARGETS,'seed':SEED}
    with open(MODEL_PATH,'wb') as f: pickle.dump(bundle,f,protocol=pickle.HIGHEST_PROTOCOL)
    pd.DataFrame(validation).to_csv(ARTIFACTS/'forecast_validation.csv',index=False)
    service=ForecastService(data); rows=[];summaries=[];interval_rows=[];test_predictions=[]
    for site in SITES:
        b=batch(builder,site,'test');preds=all_predictions(builder,site,b,models)
        selected,lower,upper,exog,oi,bad=service.forecast_many(site,b['origins']);preds['selected_operational']=selected
        seasons=season(pd.DatetimeIndex(exog['timestamp']).tz_convert(SITES[site]['timezone']).month)
        for candidate,pred in preds.items():
            for ti,target in enumerate(TARGETS+['facility_mw']):
                actual=b['y'][:,ti] if ti<len(TARGETS) else b['y'][:,:3].sum(axis=1)
                predicted=pred[:,ti] if ti<len(TARGETS) else pred[:,:3].sum(axis=1)
                denom=train_means[site][target] if target in train_means[site] else sum(train_means[site][t] for t in TARGETS[:3])
                summaries.append(dict(site_id=site,target=target,model=candidate,**metrics(actual,predicted,denom)))
                for horizon in range(1,25):
                    for season_name in ['winter','spring','summer','autumn','all']:
                        mask=(exog['horizon']==horizon)&((seasons==season_name) if season_name!='all' else np.ones(len(seasons),dtype=bool))
                        if mask.any(): rows.append(dict(site_id=site,target=target,model=candidate,horizon=horizon,season=season_name,**metrics(actual[mask],predicted[mask],denom)))
        for ti,target in enumerate(TARGETS+['facility_mw']):
            if target=='facility_mw':
                actual=b['y'][:,:3].sum(axis=1);point=selected[:,:3].sum(axis=1);w=np.asarray(calibration[site][target])[bucket(exog['horizon'])];lo=np.maximum(0,point-w);hi=point+w
            else: actual=b['y'][:,ti];lo=lower[:,ti];hi=upper[:,ti]
            for horizon in list(range(1,25))+[0]:
                for season_name in ['winter','spring','summer','autumn','all']:
                    mask=((exog['horizon']==horizon) if horizon else np.ones(len(actual),dtype=bool))&((seasons==season_name) if season_name!='all' else np.ones(len(actual),dtype=bool))
                    if mask.any(): interval_rows.append(dict(site_id=site,target=target,horizon=horizon or 'all',season=season_name,n=int(mask.sum()),nominal_coverage=.9,actual_coverage=float(((actual[mask]>=lo[mask])&(actual[mask]<=hi[mask])).mean()),mean_interval_width_mw=float((hi[mask]-lo[mask]).mean())))
        sample=pd.DataFrame({'site_id':site,'issue_timestamp':exog['issue_timestamp'],'timestamp':exog['timestamp'],'horizon':exog['horizon'],'season':seasons,'fallback':bad})
        for ti,target in enumerate(TARGETS): sample[target+'_actual']=b['y'][:,ti];sample[target+'_predicted']=selected[:,ti];sample[target+'_lower']=lower[:,ti];sample[target+'_upper']=upper[:,ti]
        sample['ambient_forecast_c']=exog['ambient_forecast_c'];sample['ambient_realized_c']=builder.sites[site].ambient_c.to_numpy()[oi+exog['horizon']]
        test_predictions.append(sample)
    pd.DataFrame(rows).to_csv(ARTIFACTS/'forecast_metrics_by_horizon_season.csv',index=False)
    summary=pd.DataFrame(summaries);summary.to_csv(ARTIFACTS/'forecast_metrics.csv',index=False)
    intervals=pd.DataFrame(interval_rows); intervals.to_csv(ARTIFACTS/'forecast_interval_coverage.csv',index=False)
    pd.concat(test_predictions).to_csv(ARTIFACTS/'forecast_test_predictions.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    all_intervals=intervals[(intervals.horizon=='all')&(intervals.season=='all')]
    scorecard=[]
    for site in SITES:
        for target in TARGETS+['facility_mw']:
            group=summary[(summary.site_id==site)&(summary.target==target)]
            selected=group[group.model=='selected_operational'].iloc[0]
            bestbase=group[group.model.isin(CANDIDATES[:3])].sort_values('mae_mw').iloc[0]
            interval=all_intervals[(all_intervals.site_id==site)&(all_intervals.target==target)].iloc[0]
            scorecard.append({'site_id':site,'target':target,'selected_model':selection[site].get(target,'component_reconciliation'),'mae_mw':float(selected.mae_mw),'rmse_mw':float(selected.rmse_mw),'nmae_training_mean':float(selected.nmae_training_mean) if pd.notna(selected.nmae_training_mean) else None,'best_test_baseline':bestbase.model,'best_baseline_mae_mw':float(bestbase.mae_mw),'mae_improvement_vs_best_test_baseline_pct':100*(1-float(selected.mae_mw)/float(bestbase.mae_mw)) if bestbase.mae_mw>1e-8 else None,'interval_coverage':float(interval.actual_coverage),'mean_interval_width_mw':float(interval.mean_interval_width_mw)})
    metadata={'basis':'SYNTHETIC DEMONSTRATION; held-out synthetic accuracy, not validated site performance','created_by':'python -m ml.train','python_version':platform.python_version(),'numpy_version':np.__version__,'pandas_version':pd.__version__,'sklearn_version':sklearn.__version__,
       'code_sha256':{name:hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() for name in ['data.py','forecasting.py','schema.py','train.py']},
       'dataset_sha256':hashlib.sha256(DATA_PATH.read_bytes()).hexdigest(),'model_sha256':hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest(),'splits':{k:{'issue_start_utc':v[0],'target_exclusive_end_utc':v[1],'origin_stride_hours':v[2]} for k,v in SPLITS.items()},
       'train_rows':len(x),'feature_schema':builder.feature_names,'targets':TARGETS,'seeds':{'generator':SEED,'tree':{target:SEED+i for i,target in enumerate(TARGETS)}},'selection':selection,
       'model_hyperparameters':{'gradient_boosting':{'max_iter':130,'max_leaf_nodes':23,'learning_rate':.075,'l2_regularization':8,'min_samples_leaf':45,'early_stopping':False},'ridge_alpha':50},
       'selection_rule':'Lowest validation MAE per site/target; 2025 never used to select models. Exact ties prefer persistence, then previous day, previous week, ridge, tree.',
       'normalization':'nMAE denominator = that site/target mean realized MW in training examples; undefined for zero-demand cohorts.',
       'interval_method':'Split conformal absolute residual; finite-sample ceiling quantile of calibration residuals at nominal90%, per site,target and horizon buckets1/2-6/7-12/13-24. Nonnegative and source 2.5MW clipping. Time dependence and heldout distribution shift mean coverage is empirical, not guaranteed.',
       'weather_forecast':'Causal issue-time anomaly decay plus independent deterministic forecast noise; never reads realized future weather. This is a simulated forecast service.',
       'evaluation':'Rolling origins every23h through untouched2025, all24 horizons; stride23 samples every time of day. Predictions and realized weather exported for inspection.',
       'limitations':['No field telemetry or validated site electrical capacity.','Conformal intervals may under-cover unseen regimes; observed coverage is published.','Synthetic training does not establish operational readiness or generalization to real buildings.','Forecasts do not command physical equipment.','CSV ingestion validates schema, but deployment requires sensor mapping, real issued exogenous forecasts and retraining.'],
       'elapsed_seconds':round(time.time()-started,2),'scorecard':scorecard}
    (ARTIFACTS/'forecast_metadata.json').write_text(json.dumps(metadata,indent=2,allow_nan=False),encoding='utf-8')
    # Verify a separate interpreter loads persisted estimators and predicts without calling train.
    probe="from ml.forecasting import ForecastService; import json; s=ForecastService(); a=s.forecast('U','2025-01-14T12:00:00Z'); assert len(a['records'])==24; assert abs(a['records'][0]['facility_mw']-sum(a['records'][0][t] for t in ('it_mw','cooling_mw','other_mw')))<1e-9; print(json.dumps({'fresh_process_reload':True,'horizon_records':len(a['records']),'it_next_hour_mw':a['records'][0]['it_mw']}))"
    check=subprocess.run([sys.executable,'-c',probe],text=True,capture_output=True,check=True)
    proof=json.loads(check.stdout.strip());proof['model_sha256']=metadata['model_sha256']
    (ARTIFACTS/'fresh_process_reload.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
    verify_pipeline(data,service)
    print(json.dumps({'elapsed_seconds':metadata['elapsed_seconds'],'selection':selection,'fresh_process_reload':proof},indent=2),flush=True)
    return metadata

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--regenerate',action='store_true');args=parser.parse_args()
    run(args.regenerate)
