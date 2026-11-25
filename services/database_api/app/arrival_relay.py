"""Forward committed NOAA arrivals to the private Cloud Run mail function."""

import logging
import os
import time
from contextlib import closing
from urllib.parse import urlsplit

from google.auth.compute_engine import IDTokenCredentials
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession, Request
from psycopg import Error as DatabaseError
from requests import RequestException

from services.database_api.app.config import Settings
from services.database_api.app.db.session import Database
from services.database_api.app.repositories.common import json_safe

logger = logging.getLogger(__name__)


def deliver_next(database: Database, session: AuthorizedSession, url: str) -> bool:
    # The lock prevents a second relay from sending this row concurrently.
    with database.connection() as conn:
        row = conn.execute(
            "SELECT station_id, observed_at, first_seen_at FROM gold.observation_arrival_outbox "
            "WHERE emailed_at IS NULL AND next_attempt_at <= now() "
            "ORDER BY next_attempt_at, first_seen_at LIMIT 1 FOR UPDATE SKIP LOCKED"
        ).fetchone()
        if row is None:
            return False
        try:
            response = session.post(url, json=json_safe(row), timeout=(5, 50), allow_redirects=False)
            delivered = response.status_code == 204
            if not delivered:
                logger.warning("NOAA arrival email deferred after HTTP %s", response.status_code)
        except (GoogleAuthError, RequestException) as exc:
            delivered = False
            logger.warning("NOAA arrival email deferred: %s", type(exc).__name__)
        if delivered:
            conn.execute(
                "UPDATE gold.observation_arrival_outbox SET emailed_at = clock_timestamp() "
                "WHERE station_id = %s AND observed_at = %s", (row["station_id"], row["observed_at"]),
            )
        else:
            conn.execute(
                "UPDATE gold.observation_arrival_outbox SET next_attempt_at = clock_timestamp() + interval '30 seconds' "
                "WHERE station_id = %s AND observed_at = %s", (row["station_id"], row["observed_at"]),
            )
    if delivered:
        logger.info("NOAA arrival email accepted for %s at %s", row["station_id"], row["observed_at"])
    return True


def main():
    logging.basicConfig(level=logging.INFO)
    url = os.environ["WAIMEA_ARRIVAL_FUNCTION_URL"].rstrip("/")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".run.app")
            or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.port):
        raise ValueError("WAIMEA_ARRIVAL_FUNCTION_URL must be the default Cloud Run service URL")
    database = Database(Settings())
    credentials = IDTokenCredentials(Request(), target_audience=url, use_metadata_identity_endpoint=True)
    with AuthorizedSession(credentials) as session:
        while True:
            try:
                with database.pool().connection() as listener:
                    listener.execute("LISTEN waimea_observation_arrival")
                    listener.commit()
                    try:
                        while True:
                            while deliver_next(database, session, url):
                                pass
                            # LISTEN wakes after commit. The timeout also recovers missed signals.
                            with closing(listener.notifies(timeout=5, stop_after=1)) as notifications:
                                next(notifications, None)
                    finally:
                        listener.execute("UNLISTEN waimea_observation_arrival")
            except (DatabaseError, GoogleAuthError, RequestException) as exc:
                logger.warning("NOAA arrival relay will retry: %s", type(exc).__name__)
                time.sleep(5)


if __name__ == "__main__":
    main()
