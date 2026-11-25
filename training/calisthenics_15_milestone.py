"""One-time 300-target Calisthenics 1.5 shadow retraining checkpoint."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from math import isfinite

from packages.ml.metrics import regression
from training.calisthenics_v1 import Event, OnlineWaveModel, SHORT_FEATURES
from training.calisthenics_15 import _connect, _register, _store_gateway, _timestamp, chronological_shadow_pairs

MILESTONE = 300
VERSION_PREFIX = 'v1.5-shadow300-'


def run_once() -> dict:
    with _connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        existing = conn.execute(
            "SELECT model_id FROM gold.model_registry WHERE model_name='calisthenics 1.5 / 30min' "
            "AND model_version LIKE 'v1.5-shadow300-%' ORDER BY model_id DESC LIMIT 1"
        ).fetchone()
        if existing:
            return {'status': 'already_trained', 'model_id': existing['model_id'], 'required': MILESTONE}
        rows = chronological_shadow_pairs(conn, MILESTONE)
        if len(rows) < MILESTONE:
            return {'status': 'waiting_for_300_targets', 'unique_targets': len(rows),
                    'required': MILESTONE}
        base = conn.execute("SELECT * FROM gold.model_registry "
                            "WHERE model_name='calisthenics 1.5 / 30min' "
                            "AND model_version LIKE 'v1.5-base-%' AND status='draft' "
                            "ORDER BY created_at DESC,model_id DESC LIMIT 1").fetchone()
        if base is None:
            raise RuntimeError('Calisthenics 1.5 base model unavailable')
        last_evaluation_id = conn.execute(
            "SELECT COALESCE(max(e.evaluation_id),0) AS n "
            "FROM gold.calisthenics_shadow_evaluations e "
            "JOIN gold.calisthenics_shadow_forecasts f USING(forecast_id) "
            "WHERE f.horizon='30min' AND f.gcs_uri IS NOT NULL"
        ).fetchone()['n']

    store, gateway = _store_gateway()
    model, manifest = store.load(base)
    if type(model) is not OnlineWaveModel or manifest.get('horizon') != '30min' \
            or manifest.get('feature_names') != list(SHORT_FEATURES):
        raise RuntimeError('Calisthenics 1.5 base artifact mismatch')
    records = []
    events = []
    for row in rows:
        snapshot = row['features_snapshot']
        if set(snapshot) != set(SHORT_FEATURES):
            raise ValueError('shadow feature schema mismatch')
        features = tuple(snapshot[name] for name in SHORT_FEATURES)
        predicted, actual, baseline = (float(row['predicted_value']),
                                       float(row['actual_value']), float(features[0]))
        if _timestamp(row['issued_at']) >= _timestamp(row['target_at']) \
                or _timestamp(row['evaluated_at']) < _timestamp(row['target_at']) \
                or not all(isfinite(value) and value >= 0 for value in
                           (predicted, actual, baseline)):
            raise ValueError('invalid prospective shadow evidence')
        events.append(Event(_timestamp(row['anchor_at']), _timestamp(row['issued_at']),
                            _timestamp(row['target_at']), features, actual))
        records.append({'forecast_id': row['forecast_id'], 'issued_model_id': row['model_id'],
                        'issued_model_version': row['model_version'],
                        'issued_at': _timestamp(row['issued_at']).isoformat(),
                        'target_at': _timestamp(row['target_at']).isoformat(),
                        'predicted': predicted, 'actual': actual,
                        'persistence': baseline, 'absolute_error': abs(predicted-actual),
                        'truth_sha256': row['truth_sha256'],
                        'learned_count_before_issue': model.learned_count})
    seed_count = model.learned_count
    model.estimator.set_params(shuffle=False)
    model.update(events)
    gaps = sum(right.target - left.target > timedelta(minutes=30)
               for left, right in zip(events, events[1:]))
    actuals = [row['actual'] for row in records]
    metrics = {'model': regression(actuals, [row['predicted'] for row in records]),
               'persistence': regression(actuals, [row['persistence'] for row in records]),
               'count': len(records), 'final_learned_count': model.learned_count,
               'first_target_at': records[0]['target_at'], 'last_target_at': records[-1]['target_at'],
               'gaps_over_30min': gaps}
    digest = hashlib.sha256(json.dumps([(row['forecast_id'], row['truth_sha256'])
                                        for row in records]).encode()).hexdigest()
    version = f'{VERSION_PREFIX}{digest[:16]}'
    params = base.get('hyperparameters') or {}
    return _register(model, '30min', version, base['model_id'], datetime.now(timezone.utc),
                     records, metrics, int(params.get('last_forward_forecast_id', 0)), 'shadow300',
                     seed_count, int(params.get('last_forward_evaluation_id', 0)), store, gateway,
                     last_shadow_forecast_id=max(row['forecast_id'] for row in rows),
                     last_shadow_evaluation_id=last_evaluation_id,
                     last_shadow_target_at=records[-1]['target_at'])
