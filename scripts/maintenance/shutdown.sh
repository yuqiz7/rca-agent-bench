#!/usr/bin/env bash
# shutdown.sh -- end-of-day stop for the testbed (decision 019).
#
# Stopping (rather than leaving the stack to the restart policy) is what keeps
# the next boot from racing: wakeup.sh then brings everything up in dependency
# order and restarts the flag consumers once flagd is answering. See O-P2-10.
#
# Usage: shutdown.sh [--poweroff]

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$(dirname "$HERE")")"
DEMO="$(dirname "$REPO")/opentelemetry-demo"
COMPOSE_FILES=(-f compose.yaml -f compose.observability.yaml -f compose.override.yaml)

POWEROFF=0
[ "${1:-}" = "--poweroff" ] && POWEROFF=1

t0=$(date -u +%s)
say() { echo "[$(date -u +'%H:%M:%SZ')] $*"; }
dc()  { (cd "$DEMO" && docker compose "${COMPOSE_FILES[@]}" "$@"); }

say "compose stop"
dc stop >/dev/null 2>&1 || { say "FAIL: compose stop returned non-zero"; exit 1; }

for _ in $(seq 1 24); do
  left="$(dc ps -a --format '{{.State}}' 2>/dev/null | grep -vc exited)"
  [ "$left" = "0" ] && break
  sleep 5
done
t_end=$(date -u +%s)

TOTAL="$(dc ps -a --format '{{.Service}}' 2>/dev/null | wc -l)"
EXITED="$(dc ps -a --format '{{.State}}' 2>/dev/null | grep -c exited)"

echo
echo "== shutdown summary =="
echo "elapsed : $(( t_end - t0 ))s"
echo "exited  : $EXITED/$TOTAL"
[ "$EXITED" = "$TOTAL" ] || { echo "status  : FAIL (some containers are not exited)"; dc ps -a; exit 1; }
echo "status  : OK"

if [ "$POWEROFF" -eq 1 ]; then
  say "powering off"
  sudo shutdown -h now
fi
