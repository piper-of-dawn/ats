import os
from functools import lru_cache

import polars as pl
import psycopg
from dotenv import load_dotenv
from psycopg import sql


@lru_cache(maxsize=1)
def _get_conn_params():
    load_dotenv()
    return dict(
        host="aws-1-eu-north-1.pooler.supabase.com",
        port=6543,
        dbname="postgres",
        user="postgres.fcbvypqyxoinuqbnkikx",
        password=os.getenv("SUPABASE_PASSWORD"),
        connect_timeout=10,
        prepare_threshold=None,
    )


def fetch_table(table_name: str) -> pl.DataFrame:
    conn_params = _get_conn_params()
    with psycopg.connect(**conn_params) as conn:
        with conn.cursor() as cur:
            query = sql.SQL("SELECT * FROM {}").format(sql.Identifier(table_name))
            cur.execute(query)
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
    return pl.DataFrame(rows, schema=columns, orient="row")


def batch_insert(table_name: str, columns: list[str], data: list[tuple]):
    if not data:
        return
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(table_name),
        sql.SQL(", ").join(sql.Identifier(col) for col in columns),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )
    conn_params = _get_conn_params()
    with psycopg.connect(**conn_params) as conn:
        with conn.cursor() as cur:
            cur.executemany(query, data)
        conn.commit()


def table_exists(table_name: str) -> bool:
    with psycopg.connect(**_get_conn_params()) as conn:
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
