#!/usr/bin/env python3
"""把 _cat/indices 的 JSON 排版成表。log_index_report.sh 调用。"""
import json, sys

rows = json.load(sys.stdin)
rows.sort(key=lambda r: r.get("index", ""))
w = max([len(r.get("index", "")) for r in rows] + [10])
print("{:<{w}}  {:>10}  {:>10}  {}".format("index", "docs", "size", "created", w=w))
for r in rows:
    print("{:<{w}}  {:>10}  {:>10}  {}".format(
        r.get("index", ""), r.get("docs.count", "?"),
        r.get("store.size", "?"), r.get("creation.date.string", "?"), w=w))
print("\n共 {} 个索引".format(len(rows)))
