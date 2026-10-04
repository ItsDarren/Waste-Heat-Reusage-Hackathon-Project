"""Validate a telemetry CSV, load saved models, forecast and recommend dispatch.

Example: python -m ml.infer --csv artifacts/telemetry_ingestion_example.csv
  --site U --issue 2023-01-10T12:00:00Z --soc 0.5
No training, external credentials, or physical equipment commands are involved.
"""
import bootstrap
import argparse
import hashlib
import json
from pathlib import Path
import pandas as pd
from .schema import ARTIFACTS, validate_csv
from .forecasting import ForecastService, MODEL_PATH

def infer(csv_path,site_id,issue_timestamp=None,initial_soc=0.,include_dispatch=True,output=None):
    path=Path(csv_path).resolve();data=validate_csv(path)
    selected=data[data.site_id==site_id]
    if selected.empty: raise ValueError(f'No rows for site {site_id}')
    issue=pd.Timestamp(issue_timestamp) if issue_timestamp else selected.timestamp.max()
    if issue.tzinfo is None: raise ValueError('Issue timestamp must have an explicit UTC offset')
    history=selected[selected.timestamp<=issue]
    if len(history)<169: raise ValueError('Supply at least 169 hourly records through issue time for previous-week features')
    service=ForecastService(data=data)
    forecast=service.forecast(site_id,issue)
    forecast['basis']='CSV INPUT · FORECAST MODEL TRAINED ON SYNTHETIC DATA'
    result={'schema_version':'2.0','decision_support_only':True,
        'basis':'CSV INPUT; synthetic-trained prototype, not site-validated control',
        'input_file':path.name,'input_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'model_sha256':hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest(),
        'input_rows':len(data),'site_id':site_id,'forecast':forecast,
        'future_exogenous_basis':'Causal demonstration weather/tariff forecast from issue-time observations and case-study calendar. No external forecast service or contracted tariff schedule is connected.',
        'cautions':['The included example CSV is synthetic. A user-supplied CSV is not automatically evidence of measured field performance.',
                    'Models remain trained on synthetic data. Validate sensors, retrain and recalibrate on representative real history before operational use.',
                    'Customer routes, equipment, economics, maintenance calendar and storage parameters retain the documented case-study assumptions unless explicitly configured.']}
    if include_dispatch:
        from .dispatch import config,plan_horizon
        capacity=config(site_id)['storage_capacity_mwh']
        if initial_soc<0 or initial_soc>capacity: raise ValueError(f'Initial SOC must be within 0..{capacity:.6f} MWh')
        result['dispatch']=plan_horizon(forecast['records'],site_id,initial_soc,conservative=True)
        result['dispatch']['basis']='RECOMMENDATION ON PREDICTED CONDITIONS · CSV input / synthetic-trained models / assumed equipment'
    destination=Path(output) if output else ARTIFACTS/'csv_inference.json'
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    return result,destination

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv',required=True,help='Telemetry CSV satisfying artifacts/data_schema.json')
    parser.add_argument('--site',choices=['U','R'],required=True)
    parser.add_argument('--issue',help='UTC issue timestamp with Z/offset; defaults to last available row')
    parser.add_argument('--soc',type=float,default=0.,help='Initial useful thermal storage state, MWh')
    parser.add_argument('--no-dispatch',action='store_true')
    parser.add_argument('--output',help='Output JSON path; defaults to artifacts/csv_inference.json')
    args=parser.parse_args()
    result,destination=infer(args.csv,args.site,args.issue,args.soc,not args.no_dispatch,args.output)
    print(json.dumps({'output':str(destination.resolve()),'forecast_status':result['forecast']['status'],
        'forecast_hours':len(result['forecast']['records']),'dispatch_status':result.get('dispatch',{}).get('status','not_requested'),
        'basis':result['basis']},indent=2))
