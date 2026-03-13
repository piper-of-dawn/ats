#!/bin/sh
set -eu

if [ -z "${ATS_COMMANDS:-}" ]; then
    echo "ATS_COMMANDS must be set, for example: ATS_COMMANDS=us_midcap,us_smallcap" >&2
    exit 1
fi

cat >/usr/local/bin/run-ats-commands.sh <<'EOF'
#!/bin/sh
set -eu

old_ifs=$IFS
IFS=','
for raw_command in $ATS_COMMANDS; do
    command=$(printf '%s' "$raw_command" | xargs)
    if [ -n "$command" ]; then
        echo "Running ats ${command}"
        ats "$command"
    fi
done
IFS=$old_ifs
EOF

chmod +x /usr/local/bin/run-ats-commands.sh

cat >/etc/cron.d/ats <<EOF
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
PYTHONUNBUFFERED=1
TZ=UTC
ATS_COMMANDS=${ATS_COMMANDS}
0 8 * * * root /usr/local/bin/run-ats-commands.sh >> /proc/1/fd/1 2>> /proc/1/fd/2
EOF

chmod 0644 /etc/cron.d/ats
crontab /etc/cron.d/ats

echo "Scheduled ats to run daily at 08:00 UTC with commands: ${ATS_COMMANDS}"
exec cron -f
