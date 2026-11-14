from datetime import datetime, timedelta, timezone

from training.calisthenics_v1 import Event, OnlineWaveModel
from training.calisthenics_15 import continue_2026
from training.calisthenics_v1 import SHORT_FEATURES
import training.calisthenics_15 as continuation
import pytest


def _seed():
    start=datetime(2025,1,1,tzinfo=timezone.utc)
    return [Event(start+timedelta(days=i),start+timedelta(days=i,hours=2),
                  start+timedelta(days=i+1),(1.0+i/100,),1.2+i/100)
            for i in range(120)]


def test_2026_continuation_scores_before_learning_and_updates_after_20():
    model=OnlineWaveModel(_seed())
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    events=[Event(start+timedelta(days=i),start+timedelta(days=i,hours=2),
                  start+timedelta(days=i+1),(1.5+i/100,),1.7+i/100)
            for i in range(30)]
    records,metrics=continue_2026(model,events,start+timedelta(days=40))
    assert len(records)==30
    assert records[0]["learned_count_before_issue"]==120
    assert records[20]["learned_count_before_issue"]>120
    assert metrics["final_learned_count"]==150


def test_2026_continuation_excludes_unmatured_target():
    model=OnlineWaveModel(_seed())
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    events=[Event(start,start+timedelta(hours=2),start+timedelta(days=1),(1.5,),1.7),
            Event(start+timedelta(days=1),start+timedelta(days=1,hours=2),
                  start+timedelta(days=2),(1.6,),1.8)]
    records,metrics=continue_2026(model,events,start+timedelta(days=1,hours=12))
    assert len(records)==1
    assert metrics["final_learned_count"]==121


@pytest.mark.parametrize("available,expected", [(19,"waiting_for_shadow_truth"),(20,"registered")])
def test_watch_waits_for_20_evaluated_pairs_then_updates(monkeypatch,available,expected):
    start=datetime(2026,11,13,tzinfo=timezone.utc)
    features=tuple(1.0+i/10 for i in range(len(SHORT_FEATURES)))
    seed=[Event(start+timedelta(minutes=30*i),start+timedelta(minutes=30*i),
                start+timedelta(minutes=30*i+30),features,1.4) for i in range(120)]
    model=OnlineWaveModel(seed)
    current={"model_id":25,"created_at":start,"hyperparameters":{
        "last_forward_forecast_id":77,"last_forward_evaluation_id":50,
        "last_shadow_forecast_id":0,"last_shadow_evaluation_id":50}}
    rows=[{"forecast_id":99-i,"evaluation_id":51+i,"features_snapshot":dict(zip(SHORT_FEATURES,features)),
           "actual_value":1.5,"predicted_value":1.7,"model_id":25,"model_version":"v1.5-base",
           "anchor_at":start+timedelta(minutes=30*i),
           "issued_at":start+timedelta(minutes=30*i+60),
           "target_at":start+timedelta(minutes=30*i+90),
           "evaluated_at":start+timedelta(minutes=30*i+100),
           "truth_sha256":f"{i:064x}"}
          for i in range(available)]

    class Result:
        def __init__(self,one=None,many=None): self.one,self.many=one,many
        def fetchone(self): return self.one
        def fetchall(self): return self.many

    class Connection:
        def __enter__(self): return self
        def __exit__(self,*_): pass
        def execute(self,sql,args=None):
            if "FROM gold.model_registry" in sql: return Result(one=current)
            if "AND EXISTS" in sql: return Result(one=None)
            if "FROM gold.calisthenics_shadow_forecasts f" in sql:
                assert args==(50,20)
                return Result(many=rows)
            raise AssertionError(sql)

    class Store:
        def load(self,_): return model,{"horizon":"30min"}

    monkeypatch.setattr(continuation,"_connect",lambda:Connection())
    monkeypatch.setattr(continuation,"_store_gateway",lambda:(Store(),object()))
    registered=[]
    monkeypatch.setattr(continuation,"_register",lambda *args,**kwargs:registered.append((args,kwargs)) or {"status":"registered"})
    outcome=continuation.watch_30min()
    assert outcome["status"]==expected
    assert model.learned_count==120+(20 if available==20 else 0)
    assert len(registered)==(1 if available==20 else 0)
    if registered:
        assert registered[0][1]["last_shadow_evaluation_id"]==70
        assert registered[0][0][5][0]["predicted"]==1.7  # frozen, issued prediction


def test_watch_stops_on_correction_to_consumed_shadow_truth(monkeypatch):
    class Result:
        def __init__(self,row): self.row=row
        def fetchone(self): return self.row
    class Connection:
        def __enter__(self): return self
        def __exit__(self,*_): pass
        def execute(self,sql,args=None):
            if "FROM gold.model_registry" in sql:
                return Result({"model_id":25,"hyperparameters":{"last_shadow_evaluation_id":50}})
            if "AND EXISTS" in sql:
                assert args==(50,50)
                return Result({"forecast_id":9})
            raise AssertionError(sql)
    monkeypatch.setattr(continuation,"_connect",lambda:Connection())
    monkeypatch.setattr(continuation,"_store_gateway",lambda:(object(),object()))
    with pytest.raises(RuntimeError,match="forecast_id=9"):
        continuation.watch_30min()
