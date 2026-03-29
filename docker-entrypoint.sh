#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${WHEEL_URL:-}" ]]; then
  echo "WHEEL_URL is required." >&2
  exit 64
fi

wheel_path="/tmp/package.whl"

curl -fsSL "${WHEEL_URL}" -o "${wheel_path}"

if [[ -n "${WHEEL_SHA256:-}" ]]; then
  echo "${WHEEL_SHA256}  ${wheel_path}" | sha256sum -c -
fi

python3.13 -m pip install --no-cache-dir --upgrade pip
python3.13 -m pip install --no-cache-dir "${wheel_path}"

exec "$@"
