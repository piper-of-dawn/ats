import os
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg
from flask import Flask, Response, request
from psycopg import sql
from psycopg.errors import UndefinedTable

app = Flask(__name__)

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
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(safe_qs, doseq=True), parts.fragment))


def _connect():
    for key in ("SUPABASE_DB_URL", "DATABASE_URL", "POSTGRES_URL", "POSTGRES_URL_NON_POOLING"):
        raw = os.getenv(key)
        dsn = _sanitize_postgres_dsn(raw) if raw else None
        if not dsn:
            continue
        try:
            return psycopg.connect(dsn, connect_timeout=10, prepare_threshold=None)
        except psycopg.ProgrammingError:
            continue

    return psycopg.connect(
        host="aws-1-eu-north-1.pooler.supabase.com",
        port=6543,
        dbname="postgres",
        user="postgres.fcbvypqyxoinuqbnkikx",
        password=os.getenv("SUPABASE_PASSWORD"),
        connect_timeout=10,
        prepare_threshold=None,
    )


def _fetch_rows(table_name: str):
    with _connect() as conn:
        with conn.cursor() as cur:
            query = sql.SQL("SELECT * FROM {} LIMIT 500").format(sql.Identifier(table_name))
            cur.execute(query)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
    return cols, rows


@app.get("/")
def dashboard() -> Response:
    table = request.args.get("table", "us_midcap400")
    try:
        columns, rows = _fetch_rows(table)
    except UndefinedTable:
        return Response(
            f"<h1>404 - Table not found</h1><p>{table}</p>",
            status=404,
            mimetype="text/html",
        )

    head = "".join(f"<th>{c.replace('_', ' ').upper()}</th>" for c in columns)
    body = "".join(
        "<tr>"
        + "".join(
            f"<td class=\"{'status' if 'status' in col.lower() else ''}\">{'' if val is None else val}</td>"
            for col, val in zip(columns, row)
        )
        + "</tr>"
        for row in rows
    )

    html = f"""
<!doctype html>
<html>
<head>
  <meta charset='utf-8' />
  <meta name='viewport' content='width=device-width, initial-scale=1' />
  <title>Dashboard</title>
  <style>
    :root {{ --line:#e6e8ec; --text:#1f2937; --muted:#6b7280; --accent:#1f5fae; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; padding:24px; font-family:Arial,sans-serif; background:linear-gradient(180deg,#f7f8fa,#eef1f4); color:var(--text); }}
    .card {{ max-width:1280px; margin:0 auto; background:#fff; border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
    .meta {{ display:flex; justify-content:space-between; padding:14px 16px; border-bottom:1px solid var(--line); font-size:12px; color:var(--muted); }}
    .table-wrap {{ overflow-x:auto; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th {{ text-align:left; font-size:11px; letter-spacing:.06em; color:var(--muted); background:#fafbfc; padding:12px; border-bottom:1px solid var(--line); white-space:nowrap; }}
    td {{ padding:12px; border-bottom:1px solid var(--line); white-space:nowrap; }}
    tr:hover td {{ background:#f9fbff; }}
    td.status {{ color:var(--accent); font-weight:600; }}
  </style>
</head>
<body>
  <div class='card'>
    <div class='meta'>
      <span>TABLE: {table}</span>
      <span>ROWS: {len(rows)} | UPDATED: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</span>
    </div>
    <div class='table-wrap'>
      <table>
        <thead><tr>{head}</tr></thead>
        <tbody>{body}</tbody>
      </table>
    </div>
  </div>
</body>
</html>
"""
    return Response(html, mimetype="text/html")
