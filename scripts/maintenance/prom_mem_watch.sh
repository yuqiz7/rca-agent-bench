#!/usr/bin/env bash
# prom_mem_watch.sh -- sample a container's memory while it restarts, to capture
# the peak during Prometheus TSDB WAL replay (open item O-P2-3).
#
# Decision 008 recorded a self-locking loop: WAL replay exceeded the 200M cgroup
# cap, the container was OOM-killed ~7s into replay, so the WAL was never
# checkpointed and every restart replayed it again. Decision 013 later raised the
# metric export interval to 15s (4x the sample rate), so the replay cost had to be
# re-measured. This script only observes; it never changes a limit.
#
# Usage:
#   prom_mem_watch.sh --out <csv> [--container prometheus] [--interval-s 5]
#                     [--hard-cap-s 480] [--settle-s 150]
#                     [--stable-window 12] [--stable-band-mib 30]
#
# Stops when the container has been running for at least --settle-s AND the last
# --stable-window samples span no more than --stable-band-mib, or when
# --hard-cap-s elapses (stop_reason=not_stable_within_cap).

set -u

OUT=""
CONTAINER="prometheus"
INTERVAL_S=5
HARD_CAP_S=480
SETTLE_S=150
STABLE_WINDOW=12
STABLE_BAND_MIB=30

while [ $# -gt 0 ]; do
  case "$1" in
    --out)             OUT="${2:-}"; shift 2 ;;
    --container)       CONTAINER="${2:-}"; shift 2 ;;
    --interval-s)      INTERVAL_S="${2:-}"; shift 2 ;;
    --hard-cap-s)      HARD_CAP_S="${2:-}"; shift 2 ;;
    --settle-s)        SETTLE_S="${2:-}"; shift 2 ;;
    --stable-window)   STABLE_WINDOW="${2:-}"; shift 2 ;;
    --stable-band-mib) STABLE_BAND_MIB="${2:-}"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -n "$OUT" ] || { echo "error: --out is required" >&2; exit 2; }

mkdir -p "$(dirname "$OUT")"
echo "epoch,iso_ts,status,started_at,oom_killed,restart_count,mem_used_mib,mem_limit_mib,mem_pct" > "$OUT"

WATCH_START="$(date -u +%s)"

# "1.234GiB" / "512MiB" / "900KiB" / "12B" -> MiB with one decimal.
to_mib() {
  python3 - "$1" <<'PY'
import re, sys
s = (sys.argv[1] or "").strip()
m = re.match(r'^([0-9.]+)\s*([KMGT]?i?B)$', s, re.I)
if not m:
    print("")
    sys.exit()
v, unit = float(m.group(1)), m.group(2).lower()
mul = {"b": 1/1048576, "kib": 1/1024, "mib": 1, "gib": 1024, "tib": 1048576,
       "kb": 1000/1048576, "mb": 1000000/1048576, "gb": 1000000000/1048576}
print("%.1f" % (v * mul.get(unit, 0)))
PY
}

while : ; do
  NOW="$(date -u +%s)"
  ISO="$(date -u -d "@$NOW" +'%Y-%m-%dT%H:%M:%SZ')"
  ELAPSED=$(( NOW - WATCH_START ))

  INSPECT="$(docker inspect -f '{{.State.Status}},{{.State.StartedAt}},{{.State.OOMKilled}},{{.RestartCount}}' "$CONTAINER" 2>/dev/null)"
  if [ -z "$INSPECT" ]; then
    echo "$NOW,$ISO,absent,,,,,," >> "$OUT"
    [ "$ELAPSED" -ge "$HARD_CAP_S" ] && { STOP_REASON="not_stable_within_cap"; break; }
    sleep "$INTERVAL_S"; continue
  fi

  STATUS="$(echo "$INSPECT" | cut -d, -f1)"
  STARTED="$(echo "$INSPECT" | cut -d, -f2)"
  OOM="$(echo "$INSPECT" | cut -d, -f3)"
  RC="$(echo "$INSPECT" | cut -d, -f4)"

  STATS="$(timeout 20 docker stats --no-stream --format '{{.Name}},{{.MemUsage}},{{.MemPerc}}' "$CONTAINER" 2>/dev/null)"
  USED_MIB=""; LIMIT_MIB=""; PCT=""
  if [ -n "$STATS" ]; then
    USAGE="$(echo "$STATS" | cut -d, -f2)"          # e.g. "331.6MiB / 2GiB"
    PCT="$(echo "$STATS" | cut -d, -f3)"
    USED_MIB="$(to_mib "$(echo "$USAGE" | awk -F'/' '{print $1}')")"
    LIMIT_MIB="$(to_mib "$(echo "$USAGE" | awk -F'/' '{print $2}')")"
  fi

  echo "$NOW,$ISO,$STATUS,$STARTED,$OOM,$RC,$USED_MIB,$LIMIT_MIB,$PCT" >> "$OUT"

  # (a) running long enough and the recent window is flat
  if [ "$STATUS" = "running" ] && [ -n "$STARTED" ]; then
    SS="$(date -u -d "$STARTED" +%s 2>/dev/null || echo 0)"
    SINCE_START=$(( NOW - SS ))
    if [ "$SINCE_START" -ge "$SETTLE_S" ]; then
      FLAT="$(python3 - "$OUT" "$STABLE_WINDOW" "$STABLE_BAND_MIB" <<'PY'
import csv, sys
path, win, band = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
vals = []
with open(path) as f:
    for r in csv.DictReader(f):
        if r["status"] == "running" and r["mem_used_mib"]:
            vals.append(float(r["mem_used_mib"]))
print("yes" if len(vals) >= win and (max(vals[-win:]) - min(vals[-win:])) <= band else "no")
PY
)"
      [ "$FLAT" = "yes" ] && { STOP_REASON="stable"; break; }
    fi
  fi

  # (b) hard cap
  [ "$ELAPSED" -ge "$HARD_CAP_S" ] && { STOP_REASON="not_stable_within_cap"; break; }
  sleep "$INTERVAL_S"
done

SUMMARY="$(python3 - "$OUT" "$STABLE_WINDOW" "${STOP_REASON:-unknown}" <<'PY'
import csv, sys
from datetime import datetime, timezone

path, win, stop_reason = sys.argv[1], int(sys.argv[2]), sys.argv[3]
rows = [r for r in csv.DictReader(open(path))]
run = [r for r in rows if r["status"] == "running" and r["mem_used_mib"]]

def f(x):
    try: return float(x)
    except Exception: return None

if not run:
    print("no running samples captured")
    sys.exit()

mems = [f(r["mem_used_mib"]) for r in run]
peak = max(mems)
peak_row = run[mems.index(peak)]
started = peak_row["started_at"]
try:
    ss = datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp()
    peak_at_s = round(float(peak_row["epoch"]) - ss, 1)
except Exception:
    peak_at_s = "unavailable"

tail = mems[-win:] if len(mems) >= win else mems
steady = round(sum(tail) / len(tail), 1)
limit = f(run[-1]["mem_limit_mib"])
pct = round(peak / limit * 100, 1) if limit else None

starts = [r["started_at"] for r in run]
restarted = "true" if len(set(starts)) > 1 else "false"
oom = "true" if any(r["oom_killed"] == "true" for r in rows) else "false"

print(f"peak_mib={peak}")
print(f"peak_at_s={peak_at_s}")
print(f"steady_mib={steady}")
print(f"limit_mib={limit}")
print(f"peak_pct_of_limit={pct}")
print(f"oom_killed={oom}")
print(f"restart_count_first={run[0]['restart_count']}")
print(f"restart_count_last={run[-1]['restart_count']}")
print(f"restarted_during_watch={restarted}")
print(f"stop_reason={stop_reason}")
print(f"samples={len(rows)}")
PY
)"
echo "$SUMMARY" | tee "$OUT.summary.txt"
