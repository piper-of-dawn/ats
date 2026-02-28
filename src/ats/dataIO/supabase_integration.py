import os
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import polars as pl
import psycopg
from dotenv import load_dotenv
from psycopg import sql

_ALLOWED_DSN_QUERY_KEYS = {
    "application_name",
    "channel_binding",
    "client_encoding",
    "connect_timeout",
    "dbname",
    "fallback_application_name",
    "gssencmode",
    "gsslib",
    "host",
    "hostaddr",
    "keepalives",
    "keepalives_count",
    "keepalives_idle",
    "keepalives_interval",
    "krbsrvname",
    "load_balance_hosts",
    "options",
    "passfile",
    "password",
    "port",
    "replication",
    "requirepeer",
    "service",
    "sslcert",
    "sslcompression",
    "sslcrl",
    "sslcrldir",
    "sslkey",
    "sslmode",
    "sslnegotiation",
    "sslpassword",
    "sslrootcert",
    "sslsni",
    "target_session_attrs",
    "tcp_user_timeout",
    "user",
}


def _sanitize_postgres_dsn(dsn: str) -> str | None:
    if not dsn:
        return None
    parts = urlsplit(dsn)
    if parts.scheme not in {"postgres", "postgresql"}:
        return None
    safe_qs = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k in _ALLOWED_DSN_QUERY_KEYS
    ]
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(safe_qs, doseq=True), parts.fragment)
    )


@lru_cache(maxsize=1)
def _get_conn_params():
    load_dotenv()
    for key in ("SUPABASE_DB_URL", "DATABASE_URL", "POSTGRES_URL", "POSTGRES_URL_NON_POOLING"):
        raw = os.getenv(key)
        dsn = _sanitize_postgres_dsn(raw) if raw else None
        if dsn:
            return dsn
    return dict(
        host="aws-1-eu-north-1.pooler.supabase.com",
        port=6543,
        dbname="postgres",
        user="postgres.fcbvypqyxoinuqbnkikx",
        password=os.getenv("SUPABASE_PASSWORD"),
        connect_timeout=10,
        prepare_threshold=None,
    )


def _connect():
    conn_info = _get_conn_params()
    if isinstance(conn_info, str):
        return psycopg.connect(conn_info, connect_timeout=10, prepare_threshold=None)
    return psycopg.connect(**conn_info)


def fetch_table(table_name: str) -> pl.DataFrame:
    with _connect() as conn:
        with conn.cursor() as cur:
            query = sql.SQL("SELECT * FROM {}").format(sql.Identifier(table_name))
            cur.execute(query)
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
    return pl.DataFrame(rows, schema=columns, orient="row")


def get_table_columns(table_name: str) -> list[str]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select column_name
                from information_schema.columns
                where table_schema = 'public' and table_name = %s
                order by ordinal_position
                """,
                (table_name,),
            )
            return [row[0] for row in cur.fetchall()]


def get_date_column_name(table_name: str) -> str:
    columns = get_table_columns(table_name)
    by_lower = {column.lower(): column for column in columns}
    for candidate in ("as_of_date", "date"):
        if candidate in by_lower:
            return by_lower[candidate]
    raise psycopg.errors.UndefinedColumn(
        f"No supported date column found in table '{table_name}'."
    )


def fetch_recent_dates(table_name: str, limit: int = 7) -> list[str]:
    date_column = get_date_column_name(table_name)
    with _connect() as conn:
        with conn.cursor() as cur:
            query = sql.SQL(
                """
                select distinct cast({date_col} as date) as trading_date
                from {table}
                where {date_col} is not null
                order by trading_date desc
                limit %s
                """
            ).format(
                date_col=sql.Identifier(date_column),
                table=sql.Identifier(table_name),
            )
            cur.execute(query, (limit,))
            return [row[0].isoformat() for row in cur.fetchall()]


def fetch_top_rows_for_date(
    table_name: str,
    selected_date: str,
    limit: int = 10,
) -> pl.DataFrame:
    columns = get_table_columns(table_name)
    date_column = get_date_column_name(table_name)
    order_by = []
    if "ltm" in columns:
        order_by.append(sql.SQL("{} desc nulls last").format(sql.Identifier("ltm")))
    if "stm" in columns:
        order_by.append(sql.SQL("{} desc nulls last").format(sql.Identifier("stm")))

    query = sql.SQL("select * from {table} where cast({date_col} as date) = %s").format(
        table=sql.Identifier(table_name),
        date_col=sql.Identifier(date_column),
    )
    if order_by:
        query += sql.SQL(" order by {}").format(sql.SQL(", ").join(order_by))
    query += sql.SQL(" limit %s")

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (selected_date, limit))
            rows = cur.fetchall()
            result_columns = [desc[0] for desc in cur.description]
    return pl.DataFrame(rows, schema=result_columns, orient="row")


def empty_table_frame(table_name: str) -> pl.DataFrame:
    columns = get_table_columns(table_name)
    return pl.DataFrame(schema=columns)


def batch_insert(table_name: str, columns: list[str], data: list[tuple]):
    if not data:
        return
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(table_name),
        sql.SQL(", ").join(sql.Identifier(col) for col in columns),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(query, data)
        conn.commit()


def table_exists(table_name: str) -> bool:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select exists (
                    select 1
                    from information_schema.tables
                    where table_schema = 'public'
                    and table_name = %s
                );
                """,
                (table_name,),
            )
            return cur.fetchone()[0]


def batch_insert_polars_df(df, columns, table_name):
    data = [tuple(row[col] for col in columns) for row in df.iter_rows(named=True)]
    batch_insert(table_name=table_name, columns=list(columns), data=data)

def create_relation(schema: str, table_name: str):
    columns = [
        sql.SQL("{} {}").format(
            sql.Identifier(col),
            sql.SQL(defn)
        )
        for col, defn in schema.items()
    ]

    query = sql.SQL("CREATE TABLE IF NOT EXISTS {} ({});").format(
        sql.Identifier(table_name),
        sql.SQL(", ").join(columns)
    )
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
        conn.commit()
