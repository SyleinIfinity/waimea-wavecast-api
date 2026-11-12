"""Read-only deployment inspection. Credentials come only from environment."""
import psycopg

from services.database_api.app.config import Settings


def main():
    settings = Settings()
    with psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
        sslmode=settings.db_sslmode,
        connect_timeout=5,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user, current_setting('server_version')")
            print("connection:", cur.fetchone())
            cur.execute("SELECT table_schema, count(*) FROM information_schema.tables WHERE table_schema IN ('bronze','silver','gold') AND table_type='BASE TABLE' GROUP BY table_schema ORDER BY table_schema")
            print("tables:", cur.fetchall())
            cur.execute("SELECT table_schema, count(*) FROM information_schema.views WHERE table_schema IN ('bronze','silver','gold') GROUP BY table_schema ORDER BY table_schema")
            print("views:", cur.fetchall())
            cur.execute("SELECT count(DISTINCT trigger_name) FROM information_schema.triggers WHERE event_object_schema='silver' AND event_object_table='wave_observations'")
            print("silver revision triggers:", cur.fetchone()[0])
            cur.execute("SELECT has_table_privilege(current_user, 'silver.wave_observations', 'INSERT,UPDATE'), has_table_privilege(current_user, 'gold.time_series_metrics', 'INSERT,UPDATE')")
            print("API write privileges (silver, gold):", cur.fetchone())


if __name__ == "__main__":
    main()
