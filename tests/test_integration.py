"""Verify the actual server-to-interface contract using saved models, no training."""
import unittest
import bootstrap
import server
from ml.data import load_data
from ml.forecasting import ForecastService

class IntegratedExperience(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.DATA=load_data();server.SERVICE=ForecastService(server.DATA)

    def test_saved_inference_and_exact_dispatch_share_24_timestamps(self):
        for site in ('U','R'):
            d=server.experience(site,'2025-08-15T17:00:00Z')
            self.assertEqual(len(d['forecast']['records']),24)
            self.assertEqual(len(d['plan']['records']),24)
            self.assertEqual(d['plan']['status'],'optimized')
            for f,r in zip(d['forecast']['records'],d['plan']['records']):
                self.assertEqual(f['timestamp'],r['timestamp'])
                self.assertAlmostEqual(f['facility_mw'],f['it_mw']+f['cooling_mw']+f['other_mw'])
                self.assertLess(r['energy_balance_error_mwh'],1e-7)
                self.assertEqual(r['physics_violations'],[])

    def test_scenario_is_explicit_override_not_fabricated_ml_prediction(self):
        for site,cohort in [('U','commercial'),('R','housing')]:
            ordinary=server.experience(site,'2025-01-15T17:00:00Z')
            disconnected=server.experience(site,'2025-01-15T17:00:00Z','disconnected')
            outage=server.experience(site,'2025-01-15T17:00:00Z','outage')
            self.assertEqual(ordinary['forecast'],disconnected['forecast'])
            self.assertEqual(ordinary['forecast'],outage['forecast'])
            self.assertTrue(all(r['delivered_'+cohort+'_mw']==0 for r in disconnected['plan']['records']))
            self.assertTrue(all(r['source_heat_mw']==0 for r in outage['plan']['records']))

    def test_full_calendar_and_year_end_forecast_without_invented_truth(self):
        for site in ('U','R'):
            days=server.calendar_data(site)['days']
            self.assertEqual(len(days),365)
            self.assertEqual(days[0]['day'],'2025-01-01');self.assertEqual(days[-1]['day'],'2025-12-31')
            d=server.experience(site,'2025-12-31T23:00:00Z')
            self.assertEqual(len(d['realized']),0)
            self.assertEqual(len(d['forecast']['records']),24)
            self.assertTrue(d['forecast']['records'][-1]['timestamp'].startswith('2026-01-01'))

    def test_evidence_table_uses_operational_candidate_and_both_sites(self):
        e=server.evidence()
        rows=[r for r in e['metrics'] if r['model']=='selected_operational']
        self.assertEqual(len(rows),16)
        for site in ('U','R'):
            self.assertEqual(sum(r['site_id']==site for r in rows),8)
        self.assertEqual(e['backtest']['verification']['physics_violation_count'],0)

if __name__=='__main__':unittest.main()
