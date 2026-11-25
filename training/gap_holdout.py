"""Read-only masked-observation check for candidate gap handling.

Linear interpolation uses a later observation and is therefore suitable only
for delayed repair candidates, never as a realtime input or NOAA truth.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from math import isfinite

from packages.ml.gateway import HttpMLGateway
from training.tl08c_forward_runner import utc

SIZES=(1,2,3,6)


def validate(rows:list[dict], sizes=SIZES, stride:int=41) -> dict:
    if stride<2 or any(size<1 for size in sizes):
        raise ValueError("positive gap sizes and stride required")
    clean={utc(row["observed_at"]):float(row["wave_height_m"])
           for row in rows if row.get("quality_status")=="valid"
           and row.get("source_kind")=="realtime"
           and row.get("wave_height_m") is not None
           and isfinite(float(row["wave_height_m"]))
           and float(row["wave_height_m"])>=0}
    times=sorted(clean)
    result={}
    for size in sizes:
        baseline=[];interpolated=[]
        for index in range(1,len(times)-size,stride):
            window=times[index-1:index+size+1]
            if len(window)!=size+2 or any(b-a!=timedelta(minutes=30)
                    for a,b in zip(window,window[1:])):
                continue
            left,right=clean[window[0]],clean[window[-1]]
            for offset,target in enumerate(window[1:-1],1):
                actual=clean[target]
                baseline.append(abs(left-actual))
                interpolated.append(abs(left+(right-left)*offset/(size+1)-actual))
        result[str(size)]={"masked_points":len(baseline),
            "causal_persistence_mae_m":sum(baseline)/len(baseline) if baseline else None,
            "delayed_linear_interpolation_mae_m":sum(interpolated)/len(interpolated) if interpolated else None}
    return {"status":"RESEARCH_ONLY","valid_realtime_rows":len(clean),
            "stride":stride,"methods":result,
            "policy":"No observed rows are modified; delayed interpolation is not realtime truth."}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--start",default="2026-09-28")
    parser.add_argument("--end")
    args=parser.parse_args()
    start=datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end=datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc) if args.end else datetime.now(timezone.utc)
    if end<=start or end-start>timedelta(days=90):
        parser.error("bounded UTC window of at most 90 days required")
    rows=HttpMLGateway().get_observations(start,end)
    print(json.dumps({**validate(rows),"start_utc":start.isoformat(),"end_utc":end.isoformat()},sort_keys=True))


if __name__=="__main__":
    main()
