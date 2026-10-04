"""Physical invariants and failure handling, not implementation mirror tests."""
import unittest
import numpy as np
from ml.dispatch import config, plan_horizon, rule_step, execute_step, validate_step, aggregate


def observation(site='U', **updates):
    row=dict(timestamp='2025-01-14T12:00:00+00:00',site_id=site,it_mw=3.6,cooling_mw=.42,other_mw=.22,
             source_mw=2.4,source_c=30 if site=='U' else 45,source_flow_kg_s=85,source_available=1,
             demand_housing_mw=.45 if site=='U' else 1.5,demand_commercial_mw=1.4 if site=='U' else 0.,
             demand_community_mw=.18,electricity_price=180,grid_kgco2_kwh=.3,sensor_quality='good')
    row.update(updates); return row


class DispatchTests(unittest.TestCase):
    def test_shared_capacity_and_accounting_randomized(self):
        rng=np.random.default_rng(613)
        for site in ('U','R'):
            soc=0.
            for k in range(140):
                row=observation(site,source_mw=float(rng.uniform(0,2.8)),source_available=int(rng.random()>.12),
                                electricity_price=float(rng.uniform(30,900)),source_c=float(rng.uniform(8,55)),
                                source_flow_kg_s=float(rng.uniform(0,100)),
                                demand_housing_mw=float(rng.uniform(0,6)),demand_commercial_mw=float(rng.uniform(0,4)),
                                demand_community_mw=float(rng.uniform(0,.8)),backup_capacity_mw=float(rng.uniform(0,5)))
                result=rule_step(row,site,soc); soc=result['storage_soc_mwh']
                self.assertEqual(result['physics_violations'],[])
                self.assertLess(result['energy_balance_error_mwh'],1e-7)
                self.assertAlmostEqual(result['facility_electricity_mw'],4.24)
                self.assertAlmostEqual(result['hp_heat_mw'],result['source_heat_mw']+result['compressor_electricity_mw'])

    def test_outage_does_not_make_heat(self):
        r=observation(source_available=0,backup_capacity_mw=.01)
        result=rule_step(r)
        self.assertEqual(result['source_heat_mw'],0)
        self.assertEqual(result['hp_heat_mw'],0)
        self.assertGreater(result['unmet_demand_mw'],1)

    def test_community_precedes_discretionary_sales(self):
        r=observation(source_mw=.12,electricity_price=1200)
        result=rule_step(r)
        self.assertGreater(result['delivered_community_mw'],0.)
        self.assertEqual(result['delivered_commercial_mw'],0.)

    def test_temperature_and_disconnect(self):
        for row in (observation(network_supply_c=55),observation(connected_housing=0,connected_commercial=0,connected_community=0)):
            result=rule_step(row)
            self.assertEqual(result['delivered_heat_mw'],0)
            self.assertEqual(result['source_heat_mw'],0)

    def test_empty_and_full_storage_are_bounded(self):
        for site in ('U','R'):
            for opening in (0.,config(site)['storage_capacity_mwh']):
                r=observation(site,electricity_price=20)
                result=rule_step(r,site,opening)
                self.assertLessEqual(result['storage_soc_mwh'],config(site)['storage_capacity_mwh']+1e-8)
                self.assertGreaterEqual(result['storage_soc_mwh'],0.)

    def test_expensive_electricity_rejects_heat(self):
        r=observation(electricity_price=1500)
        plan=plan_horizon([dict(r,horizon=i+1) for i in range(24)])
        self.assertEqual(plan['status'],'optimized')
        self.assertGreater(sum(r['rejected_source_mw'] for r in plan['records'])/24,1)
        self.assertAlmostEqual(plan['records'][0]['delivered_commercial_mw'],0.,places=6)

    def test_stale_and_invalid_forecasts_fallback(self):
        for r in (observation(sensor_quality='stale'),observation(source_mw=float('nan'))):
            plan=plan_horizon([r]*24)
            self.assertEqual(plan['status'],'fallback')
            self.assertTrue(all(x['source_heat_mw']==0 for x in plan['records']))

    def test_solver_timeout_is_feasible_fallback(self):
        r=observation()
        plan=plan_horizon([r]*24,time_limit=0.)
        self.assertEqual(plan['status'],'fallback')
        self.assertTrue(all(not row['physics_violations'] for row in plan['records']))
        self.assertTrue(all(row['controller']=='deterministic_fallback' for row in plan['records']))

    def test_negative_forecast_is_not_optimized(self):
        plan=plan_horizon([observation(demand_housing_mw=-1)]*24)
        self.assertEqual(plan['status'],'fallback')
        self.assertTrue(all(row['delivered_housing_mw']==0 for row in plan['records']))

    def test_storage_and_terminal_no_free_energy(self):
        for site in ('U','R'):
            cfg=config(site); opening=cfg['storage_capacity_mwh']*.5
            inputs=[observation(site,electricity_price=50 if i<12 else 500,horizon=i+1) for i in range(24)]
            plan=plan_horizon(inputs,site,opening)
            self.assertEqual(plan['status'],'optimized')
            totals=aggregate(plan['records'])
            self.assertLess(totals['max_conservation_error_mwh'],1e-7)
            self.assertGreaterEqual(plan['records'][-1]['storage_soc_mwh']+plan['terminal_reserve_shortfall_mwh']+1e-5,opening*(1-cfg['storage_loss_fraction'])**24)
            self.assertTrue(all(not(r['storage_charge_mw']>1e-7 and r['storage_discharge_mw']>1e-7) for r in plan['records']))

    def test_forecast_error_repaired_before_execution(self):
        r=observation(); plan=plan_horizon([r]*24)
        actual=observation(source_available=0,demand_community_mw=.5,backup_capacity_mw=.5)
        result=execute_step(plan['records'][0],actual)
        self.assertEqual(result['source_heat_mw'],0.)
        self.assertEqual(result['hp_heat_mw'],0.)
        self.assertGreater(result['unmet_demand_mw'],0.)
        self.assertEqual(validate_step(result,actual),[])


if __name__=='__main__': unittest.main()
