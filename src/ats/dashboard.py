import os
from datetime import datetime
from flask import Flask, Response, render_template, request
import polars as pl
from psycopg.errors import UndefinedTable

from ats.dataIO.supabase_integration import fetch_table

app = Flask(__name__)


def _normalize_date_str(value) -> str:
    text = str(value)
    if " " in text:
        text = text.split(" ", 1)[0]
    if "T" in text:
        text = text.split("T", 1)[0]
    return text


@app.get("/")
def dashboard() -> Response:
    table_name = "factor_metrics"
    try:
        df = fetch_table(table_name)
    except UndefinedTable:
        return Response(
            render_template("table_not_found.html", table_name=table_name),
            status=404,
            mimetype="text/html",
        )

    available_dates = []
    selected_date = None
    filtered_df = df

    if "date" in df.columns:
        date_values = []
        for value in df.get_column("date").to_list():
            if value is None:
                continue
            date_values.append(_normalize_date_str(value))
        available_dates = sorted(set(date_values), reverse=True)[:7]
        selected_date = request.args.get("date")
        if selected_date not in available_dates:
            selected_date = available_dates[0] if available_dates else None
        if selected_date:
            filtered_df = df.filter(
                pl.col("date").cast(pl.Utf8).str.slice(0, 10) == selected_date
            )

    if "ltm" in filtered_df.columns and "stm" in filtered_df.columns:
        filtered_df = filtered_df.sort(
            by=["ltm", "stm"],
            descending=[True, True],
            nulls_last=True,
        )
    rows_df = filtered_df.head(20)
    columns = list(rows_df.columns)
    rows = rows_df.to_dicts()
    updated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    return Response(
        render_template(
            "dashboard.html",
            table_name=table_name,
            columns=columns,
            rows=rows,
            available_dates=available_dates,
            selected_date=selected_date,
            updated_at=updated_at,
        ),
        mimetype="text/html",
    )


def run() -> None:
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "8000")), debug=True)
