#!/usr/bin/env bash
# log_index_report.sh —— OpenSearch 索引报告 / 清理（O-P2-1、O-P2-7）
#
#   ./log_index_report.sh                          只报告，不删（默认）
#   ./log_index_report.sh --delete-older-than-days N   删除创建日期早于 N 天的日志索引
#
# 只允许作为 run_batch.py 的 --pre-batch-hook 在**批次之间**执行。
# 周期内删索引会抹掉 logs.log_lines —— 那是确认注入生效的信号之一（O-P2-1）。
#
# 删除时排除「当前写入索引」：collector 的 opensearch exporter 配的是
# logs_index: otel-logs + logs_index_time_format: yyyy-MM-dd（见
# otelcol-config-observability.yml:32-34），即今天日期那个索引正在被写。

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$(dirname "$HERE")/backends.env"

DELETE_DAYS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --delete-older-than-days) DELETE_DAYS="${2:-}"; shift 2 ;;
    *) echo "usage: $0 [--delete-older-than-days N]" >&2; exit 2 ;;
  esac
done

echo "== OpenSearch 索引报告 $(date -u +'%Y-%m-%dT%H:%M:%SZ') =="
echo "-- 全部索引（名称 / 创建日期 / 文档数 / 存储大小）--"
curl -s --max-time 30 "$OPENSEARCH_BASE/_cat/indices?h=index,docs.count,store.size,creation.date.string&format=json" \
  | python3 "$HERE/_fmt_indices.py"

if [ -z "$DELETE_DAYS" ]; then
  echo "-- 模式：只报告，未删除任何索引（要删加 --delete-older-than-days N）--"
  exit 0
fi

TODAY_IDX="otel-logs-$(date -u +%Y-%m-%d)"
CUTOFF="$(date -u -d "-${DELETE_DAYS} days" +%Y-%m-%d)"
echo "-- 模式：删除创建日期早于 $CUTOFF 的 otel-logs-* 索引（排除当前写入 $TODAY_IDX）--"
victims="$(curl -s --max-time 30 "$OPENSEARCH_BASE/_cat/indices/otel-logs-*?h=index&format=json" \
  | python3 "$HERE/_pick_old.py" "$TODAY_IDX" "$CUTOFF")"
if [ -z "$victims" ]; then echo "   没有符合条件的索引"; exit 0; fi
for i in $victims; do
  echo "   删除 $i"
  curl -s --max-time 30 -XDELETE "$OPENSEARCH_BASE/$i" >/dev/null
done
echo "-- 完成 --"
