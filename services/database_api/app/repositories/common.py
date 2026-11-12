from datetime import date, datetime
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb


JSONB_LIST_COLUMNS = {"feature_names", "columns_in_source", "feature_columns", "confusion_matrix"}


def adapted(value: Any, column: str = "") -> Any:
    if isinstance(value, dict) or (isinstance(value, list) and column in JSONB_LIST_COLUMNS):
        return Jsonb(value)
    return value


def insert_row(conn, schema: str, table: str, values: dict[str, Any], *, conflict: str = "", returning: str = ""):
    columns = list(values)
    statement = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
        sql.Identifier(schema), sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )
    if conflict:
        statement += sql.SQL(" ") + sql.SQL(conflict)
    if returning:
        statement += sql.SQL(" RETURNING ") + sql.Identifier(returning)
    return conn.execute(statement, [adapted(values[c], c) for c in columns])


def upsert_row(conn, schema: str, table: str, values: dict[str, Any], keys: tuple[str, ...]):
    columns = list(values)
    statement = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({}) ON CONFLICT ({}) DO UPDATE SET {}").format(
        sql.Identifier(schema), sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
        sql.SQL(", ").join(map(sql.Identifier, keys)),
        sql.SQL(", ").join(sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(c), sql.Identifier(c)) for c in columns if c not in keys),
    )
    return conn.execute(statement, [adapted(values[c], c) for c in columns])


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat().replace("+00:00", "Z")
    return value
