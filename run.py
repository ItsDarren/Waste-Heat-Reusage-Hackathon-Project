"""Reproducible project entry point; use saved models without retraining to serve."""
import bootstrap
import argparse
import runpy
import sys

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('command',choices=['data','train','backtest','test','serve','all'])
args,rest=parser.parse_known_args()
if args.command=='serve':
    sys.argv=['server.py']+rest
    runpy.run_module('server',run_name='__main__')
elif args.command=='test':
    import unittest
    import json
    from datetime import datetime,timezone
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover('tests'))
    (bootstrap.ROOT/'artifacts/test_report.json').write_text(json.dumps({'command':'python run.py test','completed_utc':datetime.now(timezone.utc).isoformat(),'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'passed':result.wasSuccessful()},indent=2),encoding='utf-8')
    sys.exit(not result.wasSuccessful())
else:
    modules={'data':['ml.data'],'train':['ml.train'],'backtest':['ml.backtest'],'all':['ml.data','ml.train','ml.backtest']}[args.command]
    for module in modules:
        sys.argv=[module]+rest
        runpy.run_module(module,run_name='__main__')
