#!/bin/sh
set -eu

if [ -z "${SOURCE_TABLES:-}" ]; then
    echo "SOURCE_TABLES must be set, for example: SOURCE_TABLES=us_largecap,us_midcap" >&2
    exit 1
fi

if [ -z "${MARKET_INDEX:-}" ]; then
    echo "MARKET_INDEX must be set, for example: MARKET_INDEX=^GSPC" >&2
    exit 1
fi

old_ifs=$IFS
IFS=','
for raw_source_table in $SOURCE_TABLES; do
    source_table=$(printf '%s' "$raw_source_table" | xargs)
    if [ -n "$source_table" ]; then
        echo "Running ats ${source_table} ${source_table}_metrics ${MARKET_INDEX}"
        ats "$source_table" "${source_table}_metrics" "$MARKET_INDEX"
    fi
done
IFS=$old_ifs
