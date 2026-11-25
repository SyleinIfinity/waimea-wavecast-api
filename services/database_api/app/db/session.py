from contextlib import contextmanager
from typing import Iterator

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from services.database_api.app.config import Settings


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._pool: ConnectionPool | None = None

    def pool(self) -> ConnectionPool:
        if not self.settings.db_password:
            raise RuntimeError("DB_PASSWORD is not configured")
        if self._pool is None:
            self._pool = ConnectionPool(
                conninfo="",
                kwargs={
                    "host": self.settings.db_host,
                    "port": self.settings.db_port,
                    "dbname": self.settings.db_name,
                    "user": self.settings.db_user,
                    "password": self.settings.db_password,
                    "sslmode": self.settings.db_sslmode,
                    "connect_timeout": 5,
                    "row_factory": dict_row,
                },
                min_size=0,
                max_size=self.settings.db_pool_max,
                open=True,
            )
        return self._pool

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        with self.pool().connection(timeout=7) as conn:
            with conn.transaction():
                yield conn

    def ready(self) -> bool:
        with self.connection() as conn:
            return conn.execute("SELECT 1").fetchone()["?column?"] == 1

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
