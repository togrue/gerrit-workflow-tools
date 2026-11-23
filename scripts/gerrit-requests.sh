#!/usr/bin/env bash
# Run a command and print what a Gerrit server logged for it (logs/httpd_log): one line per
# request with status, milliseconds, bytes, method and path. See docu/Server-load.md.
#
#   scripts/gerrit-requests.sh [--ssh HOST] [--container NAME] [--log PATH] -- COMMAND...
#
# The Gerrit runs in a Docker container: on this machine, or on HOST reached over ssh.
# It shows every line added to the log while the command ran, so nobody else should be active.
set -euo pipefail

ssh_host=""
container="gerrit"
log="/var/gerrit/logs/httpd_log"
while [ $# -gt 0 ]; do
  case "$1" in
    --ssh) ssh_host=$2; shift 2 ;;
    --container) container=$2; shift 2 ;;
    --log) log=$2; shift 2 ;;
    --) shift; break ;;
    -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "error: unknown option $1 (the command goes after --)" >&2; exit 2 ;;
  esac
done
[ $# -gt 0 ] || { echo "error: no command given (put it after --)" >&2; exit 2; }

in_container() {
  if [ -n "$ssh_host" ]; then
    ssh -o BatchMode=yes "$ssh_host" "docker exec $container sh -c '$1'"
  else
    docker exec "$container" sh -c "$1"
  fi
}

before=$(in_container "wc -l < $log")
code=0
"$@" || code=$?
sleep 1  # the log is written when a request ends
lines=$(in_container "tail -n +$((before + 1)) $log")
count=$(printf '%s' "$lines" | grep -c . || true)
echo
echo "--- $count requests on the server (exit code of the command: $code)"
# ip [thread] - user [time] "METHOD path HTTP/x" status bytes ms ...
printf '%s\n' "$lines" | sed -nE 's/^[^"]*"([A-Z]+) ([^ ]+) HTTP[^"]*" ([0-9]+) ([0-9-]+) ([0-9-]+).*/\3 \5ms \4b \1 \2/p'
exit "$code"
