import os
from pathlib import Path

from flask import Flask, Response, render_template
from dashboard.data import (
    UndefinedTable,
    get_dashboard_context,
    get_nav_context,
    get_positions_treemap_context,
)


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

    def render_nav() -> tuple[str, int]:
        try:
            nav_context = get_nav_context()
        except (KeyError, UndefinedTable):
            nav_context = None
        if nav_context is None:
            return render_template("nav_unavailable.html"), 200
        return render_template("nav_card.html", nav_context=nav_context), 200

    def render_treemap(group_by: str) -> tuple[str, int]:
        context = get_positions_treemap_context(group_by)
        return render_template("treemap.html", **context), 200

    @app.get("/")
    def dashboard() -> Response:
        return Response(
            render_template("dashboard.html"),
            status=200,
            mimetype="text/html",
        )

    @app.get("/partials/nav")
    def nav_partial() -> Response:
        nav_html, nav_status = render_nav()
        return Response(nav_html, status=nav_status, mimetype="text/html")

    @app.get("/partials/table/<table_name>")
    def table_partial(table_name: str) -> Response:
        table_html, table_status = render_table(table_name)
        return Response(table_html, status=table_status, mimetype="text/html")

    @app.get("/partials/treemap/<group_by>")
    def treemap_partial(group_by: str) -> Response:
        try:
            treemap_html, treemap_status = render_treemap(group_by)
        except (KeyError, UndefinedTable):
            treemap_html = render_template("table_not_found.html", table_name=f"positions:{group_by}")
            treemap_status = 404
        return Response(treemap_html, status=treemap_status, mimetype="text/html")

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
