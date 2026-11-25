"""Bounded, read-only UTC time-series aggregates over canonical Silver rows."""
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from psycopg import sql

from services.database_api.app.repositories.common import json_safe


router = APIRouter(prefix="/api/v1/analytics/time-series", tags=["public"])
FIELDS = {
    "WVHT": "wave_height_m",
    "DPD": "dominant_period_s",
    "APD": "average_period_s",
    "MWD": "mean_wave_direction_deg",
    "WTMP": "sea_surface_temp_c",
}
MAX_DAYS = 9000  # Covers the 2004-present station archive; still bounds public work.
SCATTER_LIMIT = 1500


@router.get("/overview")
def overview(request: Request, start: datetime, end: datetime,
             metric: Literal["WVHT", "DPD", "APD", "MWD", "WTMP"] = "WVHT",
             qc_only: bool = False):
    if (start.tzinfo is None or end.tzinfo is None or end < start
            or end - start > timedelta(days=MAX_DAYS)):
        raise HTTPException(422, "invalid or oversized UTC time window")
    column = sql.Identifier(FIELDS[metric])
    where = ("station_id='51201' AND observed_at >= %s AND observed_at <= %s "
             "AND (%s = false OR quality_status='valid')")
    params = (start, end, qc_only)
    summary = {}
    with request.app.state.db.connection() as conn:
        for period in ("day", "week", "month"):
            query = sql.SQL(
                "SELECT date_trunc(%s, observed_at AT TIME ZONE 'UTC')::date AS period_start, "
                "count(*) AS sample_count, avg({field}) AS mean_value, "
                "percentile_cont(0.5) WITHIN GROUP (ORDER BY {field}) AS median_value "
                "FROM silver.wave_observations WHERE " + where + " AND {field} IS NOT NULL "
                "GROUP BY 1 ORDER BY 1"
            ).format(field=column)
            summary[period] = json_safe(conn.execute(query, (period, *params)).fetchall())

        totals = conn.execute(sql.SQL(
            "SELECT count(*) AS observation_count, count({field}) AS metric_count, "
            "avg({field}) AS mean_value, "
            "percentile_cont(0.5) WITHIN GROUP (ORDER BY {field}) AS median_value, "
            "count(DISTINCT (observed_at AT TIME ZONE 'UTC')::date) AS day_count "
            "FROM silver.wave_observations WHERE " + where
        ).format(field=column), params).fetchone()

        histogram = conn.execute(
            "SELECT least(11, floor(wave_height_m / 0.5)::int) AS bin, count(*) AS sample_count "
            "FROM silver.wave_observations WHERE " + where +
            " AND wave_height_m IS NOT NULL AND wave_height_m >= 0 "
            "GROUP BY 1 ORDER BY 1", params,
        ).fetchall()

        scatter = conn.execute(
            "WITH numbered AS (SELECT wave_height_m, dominant_period_s, "
            "row_number() OVER (ORDER BY observed_at) AS row_number, count(*) OVER () AS total_pairs "
            "FROM silver.wave_observations WHERE " + where +
            " AND wave_height_m IS NOT NULL AND dominant_period_s IS NOT NULL) "
            "SELECT wave_height_m,dominant_period_s,total_pairs FROM numbered "
            "WHERE (row_number-1) %% greatest(1,ceil(total_pairs::numeric/%s)::int)=0 "
            "ORDER BY row_number LIMIT %s", (*params, SCATTER_LIMIT, SCATTER_LIMIT),
        ).fetchall()

    return {
        "status": "ok" if totals["observation_count"] else "empty",
        "metric": metric,
        "timezone": "UTC",
        "qc_only": qc_only,
        "summary": summary,
        "totals": json_safe(totals),
        "histogram": json_safe(histogram),
        "scatter": {
            "total_pairs": scatter[0]["total_pairs"] if scatter else 0,
            "points": json_safe([{"wave_height_m": row["wave_height_m"],
                                  "dominant_period_s": row["dominant_period_s"]}
                                 for row in scatter]),
        },
    }
