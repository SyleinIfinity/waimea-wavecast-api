from datetime import datetime, timedelta, timezone

from training.calisthenics_v1 import Event, replay


def test_replay_trains_only_after_target_and_scores_immediately():
    start = datetime(2007, 1, 1, tzinfo=timezone.utc)
    events = []
    for i in range(150):
        anchor = start + timedelta(days=i-110)
        issue = anchor + timedelta(hours=2)
        target = anchor + timedelta(days=1)
        events.append(Event(anchor, issue, target, (1.0 + i/100, None), 1.2 + i/100))
    model, outputs, metrics = replay(events, "1d", batch_size=10)
    assert outputs
    assert outputs[0]["learned_count_before_issue"] == metrics["seed_count"]
    assert outputs[0]["target_at"] > outputs[0]["issued_at_simulated"]
    assert outputs[0]["actual"] == events[110].actual
    assert metrics["final_learned_count"] == len(events)
    assert model.update_count > 0


def test_replay_rejects_insufficient_seed():
    now = datetime(2007, 1, 1, tzinfo=timezone.utc)
    events = [Event(now-timedelta(days=2), now-timedelta(days=1), now, (1.0,), 1.0)]
    try:
        replay(events, "1d")
    except ValueError as exc:
        assert "seed" in str(exc)
    else:
        raise AssertionError("expected seed-size guard")


def test_fast_single_prediction_matches_sklearn_transform():
    start = datetime(2006, 1, 1, tzinfo=timezone.utc)
    seed = [Event(start+timedelta(days=i),start+timedelta(days=i,hours=2),
                  start+timedelta(days=i+1),(1.0+i/100,None if i%3 else 2.0),
                  1.2+i/100) for i in range(120)]
    from training.calisthenics_v1 import OnlineWaveModel
    model = OnlineWaveModel(seed)
    row = (1.9,None)
    reference = model.estimator.predict(model.scaler.transform(model.imputer.transform([row])))[0]
    assert abs(model.predict([row])[0]-max(0,reference)) < 1e-10


def test_week_ahead_truth_is_not_learned_before_period_closes():
    start = datetime(2007, 1, 1, tzinfo=timezone.utc)
    seed = [Event(start-timedelta(days=110-i),start-timedelta(days=110-i,hours=-2),
                  start-timedelta(days=103-i),(1.0+i/100,),1.2+i/100)
            for i in range(100)]
    live = [Event(start+timedelta(days=i),start+timedelta(days=i,hours=2),
                  start+timedelta(days=i+8),(1.5+i/100,),1.7+i/100)
            for i in range(20)]
    _, outputs, metrics = replay(seed+live,"7d")
    assert len(outputs)==20
    assert all(item["learned_count_before_issue"]==metrics["seed_count"] for item in outputs[:17])
    assert outputs[17]["learned_count_before_issue"] > metrics["seed_count"]


def test_calisthenics_artifact_loads_from_canonical_module(tmp_path):
    from packages.gcs.models.local import LocalGCSClient
    from packages.ml.artifacts import ModelStore
    from training.calisthenics_v1 import OnlineWaveModel
    start = datetime(2006, 1, 1, tzinfo=timezone.utc)
    seed = [Event(start+timedelta(days=i),start+timedelta(days=i,hours=2),
                  start+timedelta(days=i+1),(1.0+i/100,),1.2+i/100) for i in range(110)]
    store = ModelStore(LocalGCSClient(tmp_path),"private")
    artifact = store.save(OnlineWaveModel(seed),task="calisthenics-test",version="v1.0",
                          manifest={"horizon":"1d"})
    metadata = {**artifact,"hyperparameters":{"manifest_sha256":artifact["manifest_sha256"],
                                                "manifest_generation":artifact["manifest_generation"]}}
    loaded,manifest = store.load(metadata)
    assert type(loaded) is OnlineWaveModel
    assert manifest["horizon"] == "1d"
