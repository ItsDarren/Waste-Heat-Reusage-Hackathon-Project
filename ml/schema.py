"""Versioned CSV interface. Power is MW, hourly energy is MWh; timestamps are UTC."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'artifacts'
TARGETS = ['it_mw', 'cooling_mw', 'other_mw', 'source_mw',
           'demand_housing_mw', 'demand_commercial_mw', 'demand_community_mw']
COHORTS = ['housing', 'commercial', 'community']
SITES = {'U': {'name': '111 8th Avenue · NYC', 'timezone': 'America/New_York'},
         'R': {'name': 'Lake Hawkeye · Lansing, NY', 'timezone': 'America/New_York'}}
SCHEMA = {
 'schema_version': '2.0', 'basis': 'SYNTHETIC DEMONSTRATION; no measured site telemetry',
 'canonical_time': 'RFC3339 UTC with explicit offset. Unique (site_id,timestamp), hourly. Local display uses IANA zones; never assume 8760 rows/year.',
 'site_hourly.csv': {'required': ['timestamp','site_id'] + TARGETS + ['ambient_c','source_c','source_return_c','source_flow_kg_s','source_available','sensor_quality','electricity_price','grid_kgco2_kwh'],
  'optional': ['facility_mw','cpu_utilization','gpu_utilization','scheduled_available','regime','connected_housing','connected_commercial','connected_community'],
  'units': {'*_mw': 'MW average over hour, numerically MWh in one-hour intervals', '*_c':'degrees Celsius','source_flow_kg_s':'kg/s','electricity_price':'USD/MWh incremental electricity','grid_kgco2_kwh':'kgCO2e/kWh, numerically tCO2e/MWh','source_available':'fraction [0,1]','sensor_quality':'good|missing|stale|suspect'},
  'accounting': 'facility_mw = it_mw + cooling_mw + other_mw. Source_mw is thermal and capped by the original assumed 2.5 MW recovery tranche. Heat-plant compressor and pumping electricity are additional, never included in these three measured/simulated facility components.'},
 'customer_registry.csv': {'fields': ['site_id','cohort','route_m','connection_capacity_mw','delivery_c','return_c','tariff_usd_mwh','avoided_heat_usd_mwh','baseline_kgco2_kwh','minimum_service_fraction','connected'], 'notes':'Register routes and capacity separately for every simultaneous customer. Customer demand is delivered useful thermal power, not purchased electricity.'},
 'weather_forecasts.csv': {'fields':['issue_timestamp','valid_timestamp','site_id','ambient_forecast_c'], 'availability':'issue_timestamp <= decision issue; valid timestamp alone is insufficient. Realized future ambient_c is prohibited as a feature.'},
 'exogenous_forecasts.csv': {'fields':['issue_timestamp','valid_timestamp','site_id','electricity_price','grid_kgco2_kwh','scheduled_available','connected_housing','connected_commercial','connected_community'], 'availability':'Only previously issued forecasts or truly committed tariffs/maintenance may be used. Unannounced outages are unknown.'},
 'storage_registry.csv': {'fields':['site_id','capacity_mwh','soc_mwh','charge_limit_mw','discharge_limit_mw','charge_efficiency','discharge_efficiency','standing_loss_fraction_hour','minimum_delivery_c','tank_c'], 'notes':'SOC is useful thermal energy. Temperature eligibility is enforced by dispatch. Terminal SOC cannot be freely liquidated.'},
 'feature_rules': ['Decision at end of issue hour; forecast horizons are issue+1 through issue+24.', 'All telemetry feature indices must be <= issue index.', 'Seasonal lags use valid time minus 24 or 168 hours, guaranteed observed for horizons <=24.', 'Known calendar, independently issued weather predictions and maintenance schedules may describe future timestamps.', 'Quality-marked bad telemetry is forward filled from earlier good observations for lag features. Current stale/missing telemetry triggers a labeled conservative fallback.', 'Do not infer accuracy at either named site from synthetic holdout scores.'],
 'external_ingestion':'validate_csv() rejects naive timestamps, duplicates, non-hourly site sequences, impossible powers and facility-accounting mismatch. External ingestion is validated and preserved separately; it does not overwrite the reproducible synthetic training set or silently train a model.'
}

def write_schema():
    ARTIFACTS.mkdir(exist_ok=True)
    (ARTIFACTS/'data_schema.json').write_text(json.dumps(SCHEMA, indent=2), encoding='utf-8')

def validate_csv(path):
    import numpy as np
    import pandas as pd
    frame = pd.read_csv(path)
    required = SCHEMA['site_hourly.csv']['required']
    missing = sorted(set(required)-set(frame))
    if missing: raise ValueError(f'Missing columns: {missing}')
    # Explicit offset is mandatory. pd.to_datetime(..., utc=True) alone would silently accept naive inputs.
    if not frame.timestamp.astype(str).str.contains(r'(?:Z|[+-]\d{2}:?\d{2})$', regex=True).all():
        raise ValueError('Every timestamp must include Z or a numeric UTC offset')
    frame['timestamp'] = pd.to_datetime(frame.timestamp, utc=True, errors='raise')
    if frame.duplicated(['site_id','timestamp']).any(): raise ValueError('Duplicate site/timestamp')
    if not frame.site_id.isin(SITES).all(): raise ValueError('Unknown site_id: supported U/R')
    for site, part in frame.groupby('site_id'):
        if not part.timestamp.sort_values().diff().dropna().eq(pd.Timedelta(hours=1)).all():
            raise ValueError(f'{site}: expected contiguous hourly records; missing hours must be explicit with quality flags')
    for col in TARGETS + ['source_flow_kg_s']:
        values = pd.to_numeric(frame[col],errors='raise')
        if (~np.isfinite(values) | (values<0)).any(): raise ValueError(f'{col}: nonfinite or negative value')
    if not frame.sensor_quality.isin(['good','missing','stale','suspect']).all(): raise ValueError('Unknown sensor quality')
    for col in ['ambient_c','source_c','source_return_c','electricity_price','grid_kgco2_kwh','source_available']:
        if not np.isfinite(pd.to_numeric(frame[col], errors='raise')).all(): raise ValueError(f'{col}: nonfinite value')
    if ((frame.source_available<0)|(frame.source_available>1)).any(): raise ValueError('source_available outside [0,1]')
    total=frame[['it_mw','cooling_mw','other_mw']].sum(axis=1)
    if 'facility_mw' in frame and not np.allclose(frame.facility_mw,total,atol=1e-5): raise ValueError('Facility component accounting mismatch')
    frame['facility_mw']=total
    return frame.sort_values(['site_id','timestamp']).reset_index(drop=True)
