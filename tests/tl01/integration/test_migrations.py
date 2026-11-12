import os

import psycopg
import pytest

from services.database_api.app.config import Settings


@pytest.mark.skipif(not os.getenv("DB_PASSWORD"), reason="DB_PASSWORD required for live PostgreSQL test")
def test_v2_schema_and_seed():
    settings = Settings()
    with psycopg.connect(host=settings.db_host, port=settings.db_port, dbname=settings.db_name, user=settings.db_user, password=settings.db_password, sslmode=settings.db_sslmode, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT table_schema, count(*) FROM information_schema.tables WHERE table_schema IN ('bronze','silver','gold') AND table_type='BASE TABLE' GROUP BY table_schema")
            assert dict(cur.fetchall()) == {"bronze": 6, "silver": 5, "gold": 14}
            cur.execute("SELECT count(*) FROM information_schema.views WHERE table_schema IN ('bronze','silver','gold')")
            assert cur.fetchone()[0] == 5
            cur.execute("SELECT count(DISTINCT trigger_name) FROM information_schema.triggers WHERE event_object_schema='silver' AND event_object_table='wave_observations'")
            assert cur.fetchone()[0] == 2
            cur.execute("SELECT count(*) FROM bronze.measurement_definitions")
            assert cur.fetchone()[0] == 14
            cur.execute("SELECT count(*) FROM bronze.source_feeds WHERE station_id='51201'")
            assert cur.fetchone()[0] == 3
