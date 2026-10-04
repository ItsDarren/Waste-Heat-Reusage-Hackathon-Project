"""Fresh-process scenario execution for repeatable scenario sweeps on this host."""
import json,sys,subprocess
from pathlib import Path

def isolated_site(site='U',overrides=None,stress=None,keep_rows=False):
    if keep_rows:raise ValueError('Scenario worker intentionally exports aggregate results only')
    payload=json.dumps(dict(site=site,overrides=overrides,stress=stress))
    bootstrap='import runtime_guard,runpy,sys;runtime_guard.configure();runpy.run_path(sys.argv[1],run_name="__main__")'
    completed=subprocess.run([sys.executable,'-X','faulthandler','-c',bootstrap,str(Path(__file__).resolve())],input=payload,text=True,capture_output=True,check=True,timeout=30,cwd=Path(__file__).parent)
    return json.loads(completed.stdout),[]

if __name__=='__main__':
    from model import run_site
    request=json.loads(sys.stdin.read())
    result,_=run_site(**request,keep_rows=False)
    print(json.dumps(result))
