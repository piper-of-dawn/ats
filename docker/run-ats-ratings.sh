#!/bin/sh
set -eu

if [ -z "${ATS_COMMANDS:-}" ]; then
    echo "ATS_COMMANDS must be set, for example: ATS_COMMANDS=us_largecap,us_midcap" >&2
    exit 1
fi

old_ifs=$IFS
IFS=','
for raw_command in $ATS_COMMANDS; do
    command=$(printf '%s' "$raw_command" | xargs)
    if [ -n "$command" ]; then
        echo "Running ats-update-ratings ${command}"
        ats-update-ratings "$command"
    fi
done
IFS=$old_ifs
