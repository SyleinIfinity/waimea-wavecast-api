from datetime import datetime, timedelta, timezone

from training.tl08c_retrain import split_examples, train_forward, verified_examples
from training.tl08b_short_horizons import FEATURES
from packages.ml.features import FEATURE_NAMES
from packages.gcs.models.local import LocalGCSClient
from packages.ml.artifacts import ModelStore
from packages.ml.gateway import FakeMLGateway


def test_forward_training_uses_qc_truth_not_its_own_prediction():
    anchor = datetime(2026,11,13,4,tzinfo=timezone.utc)
    row = {"horizon":"30min","forecast_id":7,"issued_at":(anchor+timedelta(minutes=35)).isoformat(),
           "anchor_at":anchor.isoformat(),"target_at":(anchor+timedelta(hours=1)).isoformat(),
           "features_snapshot":{name:1.0 for name in FEATURES},"predicted_value":99.0,
           "actual_value":2.0,"truth_sha256":"a"*64,"source_sha256":"b"*64}
    samples = verified_examples([row],"30min")
    assert len(samples)==1 and samples[0]["actual"]==2.0
    assert samples[0]["features"][0]==1.0
    assert verified_examples([{**row,"truth_sha256":None}],"30min")==[]


def test_late_daily_forecast_is_audited_but_excluded_from_retraining():
    target_end = datetime(2026,11,13,10,tzinfo=timezone.utc)
    row = {"horizon":"1d","forecast_id":1,
           "issued_at":(target_end-timedelta(minutes=48)).isoformat(),
           "anchor_at":(target_end-timedelta(days=1)).isoformat(),
           "target_at":target_end.isoformat(),
           "features_snapshot":{name:1.0 for name in FEATURE_NAMES},
           "predicted_value":1.3,"actual_value":1.5,
           "truth_sha256":"a"*64,"source_sha256":"b"*64}
    assert verified_examples([row],"1d")==[]


def test_forward_split_embargoes_target_from_next_fold():
    start = datetime(2025,1,1,tzinfo=timezone.utc)
    rows = [{"anchor_at":start+timedelta(hours=i),"target_at":start+timedelta(hours=i+1),
             "actual":1.0,"features":(1.0,)} for i in range(900)]
    train,validation,test = split_examples(rows,"30min")
    assert train[-1]["target_at"] < validation[0]["anchor_at"]
    assert validation[-1]["target_at"] < test[0]["anchor_at"]


def test_controlled_retrain_registers_draft_with_actual_labels(tmp_path):
    start = datetime(2025,1,1,tzinfo=timezone.utc)
    rows = []
    for i in range(300):
        anchor = start+timedelta(hours=3*i)
        rows.append({"horizon":"30min","forecast_id":i+1,
            "issued_at":(anchor+timedelta(hours=1)).isoformat(),"anchor_at":anchor.isoformat(),
            "target_at":(anchor+timedelta(minutes=90)).isoformat(),
            "features_snapshot":{name:1.0+(i%17)/10 for name in FEATURES},
            "predicted_value":99.0,"actual_value":1.0+(i%19)/10,
            "truth_sha256":f"{i+1:064x}","source_sha256":"a"*64})
    gateway = FakeMLGateway()
    store = ModelStore(LocalGCSClient(tmp_path),"fixture-models")
    result = train_forward(rows,"30min",gateway,store)
    assert result["status"]=="trained_draft"
    assert gateway.models[result["model_id"]]["status"]=="draft"
    assert gateway.dataset_splits[0]["label_definition"]["never_use_prediction_as_label"]
    assert max(example["actual_value"] for example in rows)<10
