"""Independent leakage audit: mutate unseen trajectories, retain issued predictions."""
import unittest
import numpy as np
import pandas as pd
from ml.data import DATA_PATH, load_data
from ml.forecasting import MODEL_PATH, ForecastService, FeatureBuilder

@unittest.skipUnless(MODEL_PATH.exists() and DATA_PATH.exists(),'requires saved training artifacts')
class CausalAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=load_data()

    def test_future_mutation_cannot_change_issued_forecast(self):
        for site in ('U','R'):
            issue=pd.Timestamp('2025-01-14T12:00:00Z')
            changed=self.data.copy(deep=True)
            future=(changed.site_id==site)&(changed.timestamp>issue)
            for name in ('it_mw','cooling_mw','other_mw','source_mw','ambient_c','source_c','source_return_c','source_flow_kg_s','electricity_price','grid_kgco2_kwh','demand_housing_mw','demand_commercial_mw','demand_community_mw'):
                changed.loc[future,name]=99999.
            changed.loc[future,'source_available']=0
            changed.loc[future,'sensor_quality']='stale'
            a=ForecastService(self.data).forecast(site,issue)
            b=ForecastService(changed).forecast(site,issue)
            self.assertEqual(a,b,'Future realized data leaked into an issued forecast')

    def test_dst_and_leap_hours_are_canonical(self):
        site=self.data[self.data.site_id=='U']
        for year,count in ((2023,8760),(2024,8784),(2025,8760)):
            self.assertEqual(int(site.timestamp.dt.year.eq(year).sum()),count)
        self.assertTrue(site.timestamp.diff().dropna().eq(pd.Timedelta(hours=1)).all())
        fall=site[(site.timestamp>=pd.Timestamp('2025-11-02T00:00Z'))&(site.timestamp<pd.Timestamp('2025-11-03T00:00Z'))]
        local=fall.timestamp.dt.tz_convert('America/New_York')
        self.assertEqual(int(local.dt.hour.eq(1).sum()),2)

if __name__=='__main__': unittest.main()
