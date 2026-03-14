build:
    uv build --wheel

host-web host="0.0.0.0" port="8000":
    uv run flask --app dashboard.index:app run --host {{host}} --port {{port}}
