#!/usr/bin/env python3
"""挑出创建日期早于 cutoff 且不是当前写入索引的 otel-logs-* 索引名。

用法: _pick_old.py <today_index> <cutoff_yyyy-mm-dd>
索引名模式来自实测：collector 的 opensearch exporter 配置
logs_index: otel-logs + logs_index_time_format: yyyy-MM-dd。
"""
import json, sys

today_idx, cutoff = sys.argv[1], sys.argv[2]
for r in json.load(sys.stdin):
    idx = r.get("index", "")
    day = idx.replace("otel-logs-", "")
    if idx != today_idx and day < cutoff:
        print(idx)
