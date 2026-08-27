#!/usr/bin/env bash
# wakeup.sh -- daily start for the testbed (decision 019).
#
# Why this exists: at VM boot the restart policy brings all 25 containers up at
# once, and the Go services build their flagd provider with the non-blocking
# openfeature.SetProvider. If flagd's resolver on 8013 is not listening yet the
# first connection fails silently -- no log line, no error -- and that process
# serves the flag default for the rest of its life. Measured at boot on
# 2026-08-26: checkout started 5.9s before flagd's listener was ready, and
# paymentUnreachable had no effect at all until checkout was restarted.
# See open item O-P2-10 and decision 019.
#
# Usage: wakeup.sh [--watch-prometheus]
#   --watch-prometheus  start prom_mem_watch.sh before the compose command, so a
#                       cold boot's WAL-replay peak is captured (open item O-P2-3).

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$(dirname "$HERE")")"
DEMO="$(dirname "$REPO")/opentelemetry-demo"

# The wake-up command, verbatim from decision 002 in docs/decisions.md.
# Order matters: the override file must come last or the observability file's
# limits silently win.
COMPOSE_FILES=(-f compose.yaml -f compose.observability.yaml -f compose.override.yaml)

# Services that read feature flags, from the reader column of docs/flag_catalog.md.
# These are restarted unconditionally after flagd is confirmed ready.
FLAG_CONSUMERS=(ad cart checkout email frontend payment product-catalog recommendation shipping load-generator)

WATCH_PROM=0
[ "${1:-}" = "--watch-prometheus" ] && WATCH_PROM=1

t0=$(date -u +%s)
say() { echo "[$(date -u +'%H:%M:%SZ')] $*"; }
dc()  { (cd "$DEMO" && docker compose "${COMPOSE_FILES[@]}" "$@"); }

# ── 1. optional Prometheus watcher, started before anything else ──
WATCH_NOTE="not started"
if [ "$WATCH_PROM" -eq 1 ]; then
  CSV="$REPO/artifacts/resource_audit/prom_mem_$(date -u +%F)_boot.csv"
  mkdir -p "$(dirname "$CSV")"
  nohup "$HERE/prom_mem_watch.sh" --out "$CSV" --container prometheus \
        > "${CSV%.csv}.log" 2>&1 &
  WATCH_NOTE="pid $! -> $CSV"
  say "prometheus watcher: $WATCH_NOTE"
fi

# ── 2. the wake-up command ──
say "compose up -d"
dc up -d >/dev/null 2>&1 || { say "FAIL: compose up returned non-zero"; exit 1; }
t_up=$(date -u +%s)

# ── 3. wait for flagd to actually answer, not just for its container to exist ──
say "waiting for flagd to answer OFREP (cap 120s)"
FLAGD_OK=0
for _ in $(seq 1 24); do
  port="$(docker port flagd 8016 2>/dev/null | head -1 | sed 's/.*://')"
  if [ -n "$port" ]; then
    v="$(curl -s --max-time 5 -X POST \
         "http://localhost:$port/ofrep/v1/evaluate/flags/paymentUnreachable" \
         -H 'Content-Type: application/json' -d '{"context":{}}' 2>/dev/null \
       | python3 -c 'import sys,json;print(json.load(sys.stdin).get("variant",""))' 2>/dev/null)"
    [ -n "$v" ] && { FLAGD_OK=1; break; }
  fi
  sleep 5
done
if [ "$FLAGD_OK" -ne 1 ]; then
  say "FAIL: flagd did not answer OFREP within 120s"
  dc ps
  exit 1
fi
t_flagd=$(date -u +%s)
say "flagd ready after $(( t_flagd - t_up ))s"

# ── 4. restart the flag consumers so their providers connect to a live flagd ──
say "restarting flag consumers: ${FLAG_CONSUMERS[*]}"
dc restart "${FLAG_CONSUMERS[@]}" >/dev/null 2>&1 || { say "FAIL: restart returned non-zero"; exit 1; }
for _ in $(seq 1 24); do
  bad=0
  for s in "${FLAG_CONSUMERS[@]}"; do
    [ "$(docker inspect -f '{{.State.Status}}' "$s" 2>/dev/null)" = "running" ] || bad=1
  done
  [ "$bad" -eq 0 ] && break
  sleep 5
done
t_restart=$(date -u +%s)
say "flag consumers running after $(( t_restart - t_flagd ))s"

# ── 5. the gate ──
GATE_OK=1
RUNNING="$(dc ps --format '{{.State}}' 2>/dev/null | grep -c running)"
CART_RC="$(docker inspect -f '{{.RestartCount}}' cart 2>/dev/null)"
PROM="$(docker inspect -f '{{.RestartCount}} {{.State.OOMKilled}}' prometheus 2>/dev/null)"
[ "$RUNNING" = "25" ]   || GATE_OK=0
[ "$CART_RC" = "0" ]    || GATE_OK=0
[ "$PROM" = "0 false" ] || GATE_OK=0

t_end=$(date -u +%s)
echo
echo "== wakeup summary =="
echo "compose up            : $(( t_up - t0 ))s"
echo "flagd ready           : $(( t_flagd - t_up ))s"
echo "flag consumers restart: $(( t_restart - t_flagd ))s"
echo "total                 : $(( t_end - t0 ))s"
echo "restarted             : ${FLAG_CONSUMERS[*]}"
echo "prometheus watcher    : $WATCH_NOTE"
echo "gate running          : $RUNNING/25"
echo "gate cart RestartCount: $CART_RC (want 0)"
echo "gate prometheus       : $PROM (want '0 false')"
echo "gate                  : $([ "$GATE_OK" -eq 1 ] && echo PASS || echo FAIL)"

if [ "$GATE_OK" -ne 1 ]; then
  echo
  dc ps
  exit 1
fi
