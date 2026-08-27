#!/usr/bin/env bash
# prom_wal_restart_probe.sh -- self-waiting probe for O-P2-3 evidence (b):
# restart Prometheus once its TSDB head spans >= 2h40m and record the WAL-replay
# memory peak. Evidence (a), "the boot replay does not OOM", is already satisfied.
#
# Only the prometheus container is ever restarted. flagd and the flag consumers
# are never touched -- restarting those is wakeup.sh's job (decision 019).
#
# Usage:
#   prom_wal_restart_probe.sh [--min-span-sec 9600] [--poll-sec 30]
#                             [--ready-cap-sec 300] [--settle-sec 60]

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS="$(dirname "$HERE")"
REPO="$(dirname "$SCRIPTS")"
DEMO="$(dirname "$REPO")/opentelemetry-demo"

# Same file set and order as decision 002 / wakeup.sh.
COMPOSE_FILES=(-f compose.yaml -f compose.observability.yaml -f compose.override.yaml)
CONTAINER="prometheus"

# Prometheus base URL: the port is pinned in compose.override.yaml, so read it
# from the merged config rather than assuming 9090.
PROM_PORT="$( (cd "$DEMO" && docker compose "${COMPOSE_FILES[@]}" config 2>/dev/null) \
  | python3 -c '
import sys, yaml
c = yaml.safe_load(sys.stdin)
p = c["services"]["prometheus"].get("ports") or []
print(p[0].get("published", "9090") if p else "9090")
' 2>/dev/null)"
PROM_PORT="${PROM_PORT:-9090}"
PROM="http://localhost:$PROM_PORT"

# run_batch.py's serial lock. NOTE: that lock is an existence check
# (os.path.exists then open(...,"w"); see run_batch.py), NOT an flock. We honour
# it by testing for the file and by creating it while we hold the container, and
# we additionally take an flock on the same file so a future flock-aware user is
# excluded too. Because the two schemes differ there is a small TOCTOU window;
# acceptable here since the probe polls every 30s on a single-user host.
LOCK="$SCRIPTS/state/runner.lock"

OUT_DIR="$REPO/artifacts/resource_audit"
DATE_TAG="$(date -u +%F)"
PROBE_LOG="$OUT_DIR/prom_wal_probe_${DATE_TAG}.log"
WATCH_CSV="$OUT_DIR/prom_mem_${DATE_TAG}_walrestart.csv"
SUMMARY="$OUT_DIR/prom_mem_${DATE_TAG}_walrestart.summary.txt"

MIN_SPAN_SEC=9600      # 2h40m
POLL_SEC=30
READY_CAP_SEC=300
SETTLE_SEC=60

while [ $# -gt 0 ]; do
  case "$1" in
    --min-span-sec)  MIN_SPAN_SEC="${2:-}"; shift 2 ;;
    --poll-sec)      POLL_SEC="${2:-}"; shift 2 ;;
    --ready-cap-sec) READY_CAP_SEC="${2:-}"; shift 2 ;;
    --settle-sec)    SETTLE_SEC="${2:-}"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$OUT_DIR" "$SCRIPTS/state"
log() { echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') $*" >> "$PROBE_LOG"; }

# head_min_time / head_max_time live on Prometheus's own /metrics endpoint, not
# in its TSDB: this deployment has no scrape_configs at all (decision 013 -- the
# collector pushes over OTLP), so Prometheus never scrapes itself and the query
# API returns an empty vector for these series.
head_span_sec() {
  curl -s --max-time 10 "$PROM/metrics" 2>/dev/null | python3 -c '
import sys
mn = mx = None
for line in sys.stdin:
    if line.startswith("prometheus_tsdb_head_min_time "):
        mn = float(line.split()[1])
    elif line.startswith("prometheus_tsdb_head_max_time "):
        mx = float(line.split()[1])
print(int((mx - mn) / 1000) if (mn is not None and mx is not None and mx >= mn) else -1)
' 2>/dev/null || echo -1
}

mib_limit() {
  local b; b="$(docker inspect -f '{{.HostConfig.Memory}}' "$CONTAINER" 2>/dev/null)"
  [ -n "$b" ] && [ "$b" != "0" ] && echo "scale=1; $b/1048576" | bc || echo ""
}

log "probe start: min_span=${MIN_SPAN_SEC}s poll=${POLL_SEC}s prom=$PROM lock=$LOCK"

while : ; do
  SPAN="$(head_span_sec)"
  if [ "$SPAN" -lt 0 ] 2>/dev/null; then
    log "span=unavailable status=metrics_unreachable"
    sleep "$POLL_SEC"; continue
  fi
  if [ "$SPAN" -lt "$MIN_SPAN_SEC" ]; then
    log "span=${SPAN}s status=waiting need=${MIN_SPAN_SEC}s"
    sleep "$POLL_SEC"; continue
  fi

  # Span is long enough. Respect run_batch.py's lock, then take our own.
  if [ -e "$LOCK" ]; then
    log "span=${SPAN}s status=lock busy"
    sleep "$POLL_SEC"; continue
  fi
  exec 9>"$LOCK"
  if ! flock -n 9; then
    log "span=${SPAN}s status=lock busy (flock)"
    exec 9>&-; sleep "$POLL_SEC"; continue
  fi
  printf 'prom_wal_restart_probe\npid=%s\n' "$$" >&9
  log "span=${SPAN}s status=lock acquired"

  release() { flock -u 9 2>/dev/null; exec 9>&- 2>/dev/null; rm -f "$LOCK"; }

  LIMIT_MIB="$(mib_limit)"
  RC_BEFORE="$(docker inspect -f '{{.RestartCount}}' "$CONTAINER" 2>/dev/null)"
  OOM_BEFORE="$(docker inspect -f '{{.State.OOMKilled}}' "$CONTAINER" 2>/dev/null)"

  nohup "$HERE/prom_mem_watch.sh" --out "$WATCH_CSV" --container "$CONTAINER" \
        > "${WATCH_CSV%.csv}.log" 2>&1 &
  WATCH_PID=$!
  sleep 3

  T_RESTART_EPOCH="$(date -u +%s)"
  T_RESTART_UTC="$(date -u -d "@$T_RESTART_EPOCH" +'%Y-%m-%dT%H:%M:%SZ')"
  T_RESTART_ET="$(TZ=America/New_York date -d "@$T_RESTART_EPOCH" +'%Y-%m-%d %H:%M:%S %Z')"
  log "restarting $CONTAINER at $T_RESTART_UTC"
  (cd "$DEMO" && docker compose "${COMPOSE_FILES[@]}" restart "$CONTAINER") >/dev/null 2>&1

  READY_SEC=-1
  for _ in $(seq 1 "$READY_CAP_SEC"); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$PROM/-/ready" 2>/dev/null)"
    if [ "$code" = "200" ]; then READY_SEC=$(( $(date -u +%s) - T_RESTART_EPOCH )); break; fi
    sleep 1
  done
  log "ready_sec=$READY_SEC (cap=$READY_CAP_SEC)"

  sleep "$SETTLE_SEC"
  kill "$WATCH_PID" 2>/dev/null; wait "$WATCH_PID" 2>/dev/null

  PEAK_MIB="$(python3 -c '
import csv, sys
vals = []
try:
    for r in csv.DictReader(open(sys.argv[1])):
        if r["status"] == "running" and r["mem_used_mib"]:
            vals.append(float(r["mem_used_mib"]))
except Exception:
    pass
print(max(vals) if vals else "")
' "$WATCH_CSV")"
  PEAK_PCT=""
  [ -n "$PEAK_MIB" ] && [ -n "$LIMIT_MIB" ] && \
    PEAK_PCT="$(echo "scale=1; $PEAK_MIB*100/$LIMIT_MIB" | bc)"

  RC_AFTER="$(docker inspect -f '{{.RestartCount}}' "$CONTAINER" 2>/dev/null)"
  OOM_AFTER="$(docker inspect -f '{{.State.OOMKilled}}' "$CONTAINER" 2>/dev/null)"

  VERDICT=FAIL
  if [ -n "$PEAK_PCT" ] && [ "$OOM_AFTER" = "false" ] && [ "$READY_SEC" -ge 0 ] \
     && [ "$(echo "$PEAK_PCT <= 60" | bc)" = "1" ]; then
    VERDICT=PASS
  fi

  {
    echo "trigger_head_span_sec=$SPAN"
    echo "restart_utc=$T_RESTART_UTC"
    echo "restart_et=$T_RESTART_ET"
    echo "ready_sec=$READY_SEC"
    echo "ready_cap_sec=$READY_CAP_SEC"
    echo "peak_mib=$PEAK_MIB"
    echo "limit_mib=$LIMIT_MIB"
    echo "peak_pct_of_limit=$PEAK_PCT"
    echo "oom_killed_before=$OOM_BEFORE"
    echo "oom_killed_after=$OOM_AFTER"
    echo "restart_count_before=$RC_BEFORE"
    echo "restart_count_after=$RC_AFTER"
    echo "csv=$WATCH_CSV"
    echo "verdict=$VERDICT"
  } > "$SUMMARY"

  log "verdict=$VERDICT peak=${PEAK_MIB}MiB pct=${PEAK_PCT}% summary=$SUMMARY"
  release
  exit $([ "$VERDICT" = "PASS" ] && echo 0 || echo 1)
done
