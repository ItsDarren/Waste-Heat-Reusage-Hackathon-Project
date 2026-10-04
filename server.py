"""Local decision-support server. No control outputs, credentials, or external telemetry."""
import bootstrap
import argparse
import csv
import gc
import json
import math
import mimetypes
import threading
import traceback
from functools import lru_cache
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
import pandas as pd
from ml.data import load_data
from ml.forecasting import ForecastService
from ml.dispatch import plan_horizon, config, COHORTS, physics

ROOT=Path(__file__).resolve().parent
LOCK=threading.Lock()
DATA=None
SERVICE=None

def clean(value):
    if isinstance(value, dict): return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [clean(v) for v in value]
    if isinstance(value,float): return value if math.isfinite(value) else None
    if hasattr(value,'isoformat'): return value.isoformat()
    if hasattr(value,'item'): return clean(value.item())
    return value

def read_json(name):
    p=ROOT/'artifacts'/name
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else None

def read_csv(name):
    p=ROOT/'artifacts'/name
    return pd.read_csv(p).to_dict('records') if p.exists() else []

def evidence():
    return clean(dict(basis='SYNTHETIC DEMONSTRATION',dataset=read_json('dataset_metadata.json'),
      forecast=read_json('forecast_metadata.json'),metrics=read_csv('forecast_metrics.csv'),
      intervals=read_csv('forecast_interval_coverage.csv'),reload=read_json('fresh_process_reload.json'),
      backtest=read_json('backtest.json'),stress=read_json('stress_test.json'),
      verification=read_json('integration_verification.json'),
      baseline=json.loads((ROOT/'baseline/model/results.json').read_text(encoding='utf-8')),
      sources={'gradient_boosting':'https://scikit-learn.org/stable/auto_examples/applications/plot_time_series_lagged_features.html','optimizer':'https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.milp.html'}))

@lru_cache(maxsize=2)
def calendar_data(site):
    frame=DATA[(DATA.site_id==site)&(DATA.timestamp.dt.year==2025)].copy()
    frame['day']=frame.timestamp.dt.strftime('%Y-%m-%d')
    frame['demand_mw']=frame[[f'demand_{g}_mw' for g in COHORTS]].sum(axis=1)
    grouped=frame.groupby('day')[['facility_mw','source_mw','demand_mw','ambient_c']].mean().reset_index()
    return clean(dict(site_id=site,basis='Simulated daily mean, UTC calendar, 2025',days=grouped.to_dict('records')))

@lru_cache(maxsize=128)
def experience(site,issue,scenario='normal'):
    issue=pd.Timestamp(issue)
    if issue.tzinfo is None: raise ValueError('Issue timestamp must include a UTC offset.')
    issue=issue.tz_convert('UTC')
    if issue.year!=2025: raise ValueError('The interactive untouched test-year explorer is limited to 2025.')
    with LOCK:
        gc_was_enabled=gc.isenabled()
        gc.disable()
        try:
            forecast=SERVICE.forecast(site,issue.isoformat())
            records=[dict(r) for r in forecast['records']]
            for r in records:
                if scenario=='outage': r['source_available']=0; r['source_mw']=0
                elif scenario=='expensive': r['electricity_price']=max(650.,float(r['electricity_price'])*3)
                elif scenario=='cold':
                    for g in COHORTS:r[f'demand_{g}_mw']*=1.8
                elif scenario=='disconnected':r['connected_commercial' if site=='U' else 'connected_housing']=0
                elif scenario=='temperature':r['source_c']=8.;r['source_return_c']=5.
            plan=plan_horizon(records,site_id=site,initial_soc=0.)
        finally:
            if gc_was_enabled:gc.enable()
    cfg=config(site)
    selected=DATA[(DATA.site_id==site)&(DATA.timestamp>issue)&(DATA.timestamp<=issue+pd.Timedelta(hours=24))]
    current=DATA[(DATA.site_id==site)&(DATA.timestamp==issue)]
    return clean(dict(site_id=site,issue_timestamp=issue.isoformat(),basis='SYNTHETIC DEMONSTRATION',
      scenario=scenario,forecast=forecast,dispatch_inputs=records,plan=plan,
      realized=selected.to_dict('records'),current=current.iloc[0].to_dict() if len(current) else {},
      configuration={k:v for k,v in cfg.items() if k!='p'},
      initial_storage='Each independently selected demonstration opens empty; rolling backtests carry actual SOC between hours.',
      scenario_note='Original issued ML forecasts are unchanged; selected what-if overrides affect optimization inputs only.' if scenario!='normal' else 'Causal issued forecasts; future realized values shown only for retrospective inspection.'))

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT/'web'),**kwargs)
    def log_message(self,format,*args):
        if '/api/health' not in str(args):super().log_message(format,*args)
    def end_headers(self):
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Cache-Control','no-cache')
        super().end_headers()
    def send_json(self,value,status=200,download=False):
        raw=json.dumps(clean(value),allow_nan=False,separators=(',',':')).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        if download:self.send_header('Content-Disposition','attachment; filename="heat_commons_synthetic_evidence.json"')
        self.end_headers();self.wfile.write(raw)
    def translate_path(self,path):
        path=unquote(urlparse(path).path)
        if path=='/present.html':return str(ROOT/'presentation'/'PRESENT.html')
        if path=='/ml/CSV_INGESTION.md':return str(ROOT/'ml'/'CSV_INGESTION.md')
        if path=='/README.md':return str(ROOT/'README.md')
        if path=='/web/vendor/THREE-LICENSE.txt':return str(ROOT/'web'/'vendor'/'THREE-LICENSE.txt')
        if path.startswith(('/baseline/','/docs/','/artifacts/','/presentation/','/media/')):
            target=(ROOT/path.lstrip('/')).resolve()
            allowed=ROOT/path.split('/')[1]
            if not target.is_relative_to(allowed): return str(ROOT/'web'/'404')
            # Never serve serialized executable models or source internals through the browser.
            if target.suffix.lower() in ('.pkl','.pyc'):return str(ROOT/'web'/'404')
            return str(target)
        return super().translate_path(path)
    def do_GET(self):
        u=urlparse(self.path); q={k:v[0] for k,v in parse_qs(u.query).items()}
        try:
            if u.path=='/api/health':return self.send_json(dict(ready=SERVICE is not None,synthetic=True,model_loaded=SERVICE is not None))
            if u.path.startswith('/api/evidence'):return self.send_json(evidence(),download=u.path.endswith('/download'))
            if u.path in ('/api/calendar','/api/experience'):
                site=q.get('site','U')
                if site not in ('U','R'):raise ValueError('site must be U or R')
                if SERVICE is None:return self.send_json({'error':'Saved models not found. Run python run.py train, then restart.'},503)
                if u.path=='/api/calendar':return self.send_json(calendar_data(site))
                scenario=q.get('scenario','normal')
                if scenario not in ('normal','outage','expensive','cold','disconnected','temperature'):raise ValueError('Unknown scenario')
                return self.send_json(experience(site,q.get('issue','2025-01-15T17:00:00Z'),scenario))
            if u.path=='/api/distance':
                site=q.get('site','U');p=physics.site_params(site)
                return self.send_json(physics.distance_engine(route_length_m=float(q.get('distance',800)),delivered_requirement_kw=float(q.get('duty',1000)),source_inlet_c=p['source_c'],electricity_price_per_mwh=p['electricity'],parameters=dict(physics.BASE,U_source_mw=p['source_mw'],U_hp_mw=p['hp_mw'],U_source_dt=p['source_dt'],U_pipe_usd_m=p['pipe_usd_m'])))
            if u.path.startswith('/api/'):return self.send_json({'error':'Not found'},404)
            return super().do_GET()
        except (ValueError,KeyError,IndexError) as error:self.send_json({'error':str(error)},400)
        except Exception as error:
            traceback.print_exc();self.send_json({'error':f'{type(error).__name__}: {error}'},500)

def main():
    global DATA,SERVICE
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765);args=parser.parse_args()
    DATA=load_data()
    try:SERVICE=ForecastService(DATA)
    except FileNotFoundError:print('No trained models. Run python run.py train before using the explorer.',flush=True)
    mimetypes.add_type('text/javascript','.js')
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'Heat Commons: http://127.0.0.1:{args.port} | saved models loaded: {SERVICE is not None}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass

if __name__=='__main__':main()
