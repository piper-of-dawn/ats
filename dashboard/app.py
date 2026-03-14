import os
from pathlib import Path

from flask import Flask, Response, render_template
from dashboard.data import UndefinedTable, get_dashboard_context, get_nav_context


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent / "templates"),
        static_folder=str(Path(__file__).resolve().parent / "static"),
    )

    def render_table(table_name: str) -> tuple[str, int]:
        try:
            context = get_dashboard_context(table_name)
        except UndefinedTable:
            return render_template("table_not_found.html", table_name=table_name), 404
        return render_template("table.html", **context), 200

    @app.get("/")
    def dashboard() -> Response:
        factor_table_html, factor_status = render_table("us_midcap_metrics")
        largecap_table_html, largecap_status = render_table("us_largecap_metrics")
        try:
            nav_context = get_nav_context()
        except (KeyError, UndefinedTable):
            nav_context = None
        return Response(
            render_template(
                "dashboard.html",
                nav_context=nav_context,
                table_html=factor_table_html,
                secondary_table_html=largecap_table_html,
            ),
            status=max(factor_status, largecap_status),
            mimetype="text/html",
        )

    return app


app = create_app()


def run() -> None:
    debug = os.getenv("FLASK_DEBUG") == "1"
    app.run(
        host="127.0.0.1",
        port=int(os.getenv("PORT", "8000")),
        debug=debug,
        use_reloader=debug,
    )
