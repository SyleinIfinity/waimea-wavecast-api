from datetime import datetime,timedelta,timezone

from training.gap_holdout import validate


def test_masked_gap_evaluation_never_relabels_observations():
    start=datetime(2026,11,1,tzinfo=timezone.utc)
    rows=[{"observed_at":(start+timedelta(minutes=30*i)).isoformat(),
           "wave_height_m":float(i),"quality_status":"valid","source_kind":"realtime"}
          for i in range(10)]
    before=[dict(row) for row in rows]
    result=validate(rows,sizes=(1,2),stride=3)
    assert result["methods"]["1"]["masked_points"]>0
    assert result["methods"]["1"]["delayed_linear_interpolation_mae_m"]==0
    assert result["methods"]["1"]["causal_persistence_mae_m"]==1
    assert rows==before
