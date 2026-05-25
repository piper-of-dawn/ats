set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

build:
    uv build --wheel

host-web host="0.0.0.0" port="8000":
    uv run flask --app dashboard.index:app run --host {{host}} --port {{port}}

install:
    uv pip install -e .

venv:
    source .venv/Scripts/activate
