#!/usr/bin/env bash
# run_batch.sh —— run_batch.py 的 nohup 包装，供无人值守批跑（workflow.md §5）
#
#   ./scripts/runner/run_batch.sh <cycles-file> [batch-id] [extra args...]
#
# 后台启动 run_batch.py，日志写 <out-root>/<batch-id>.log，PID 写 <batch-id>.pid。
# 看进度：  tail -f scripts/out/<batch-id>.log
# 停批次：  kill "$(cat scripts/out/<batch-id>.pid)"   （随后需人工核对残留）
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS="$(dirname "$HERE")"
[ $# -ge 1 ] || { echo "usage: $0 <cycles-file> [batch-id] [extra args...]" >&2; exit 2; }
CYCLES="$1"; shift
BATCH_ID="${1:-$(date -u +%Y%m%dT%H%M%SZ)_batch}"; [ $# -gt 0 ] && shift || true
OUT_ROOT="$SCRIPTS/out"
mkdir -p "$OUT_ROOT"
LOG="$OUT_ROOT/$BATCH_ID.log"; PIDF="$OUT_ROOT/$BATCH_ID.pid"
nohup python3 "$HERE/run_batch.py" --cycles "$CYCLES" --batch-id "$BATCH_ID" \
      --out-root "$OUT_ROOT" "$@" > "$LOG" 2>&1 &
echo $! > "$PIDF"
echo "batch_id=$BATCH_ID"
echo "pid=$(cat "$PIDF")"
echo "log=$LOG"
echo "tail -f $LOG"
