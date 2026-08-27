#!/usr/bin/env python3
"""pack.py -- build evidence/<card_id>/: the five artefacts an agent gets (decision 020).

Post-hoc snapshot mode: the agent never queries the live system, it reads this
directory. So everything the agent could possibly need has to be in here, and
nothing that identifies the injected fault may be (fault_schema §4, decision 005)
-- the packer is deliberately blind to the card's target/class/params; it only
takes a card_id (a label) and a time window.

Five artefacts:
  logs.jsonl      all services' logs in the window, one JSON object per line
  metrics.json    the fixed PromQL set from queries.py, range step 15s
  traces.json     Jaeger spans in the window, merged and de-duplicated
  config_diff.txt flagd config + compose env, unified diff against the baseline
  topology.json   a reference to evidence/_shared/topology.json plus its sha256
  manifest.json   window, the five paths, byte sizes, sha256 (6th file: the index)

Only the standard library, like every other script in this repo.

Usage:
  pack.py --card-id crash-cart-01 --t-start ISO --t-inject ISO --t-revert ISO --t-end ISO
"""
import argparse
import copy
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
REPO = os.path.dirname(SCRIPTS)
DEMO = os.path.join(os.path.dirname(REPO), "opentelemetry-demo")
EVIDENCE_ROOT = os.path.join(REPO, "evidence")

sys.path.insert(0, HERE)
import queries  # noqa: E402

# Window padding, from decision 016: 60s of lead-in gives rate()[1m] its lookback
# and covers the baseline window; 150s of tail is the settle the harvest snapshot
# already waits out, so spans that only finish after t_end are inside the pack.
PRE_PAD_S = 60
POST_PAD_S = 150

HTTP_TIMEOUT = 60
LOG_PAGE = 2000                 # OpenSearch page size for search_after
JAEGER_TRACE_LIMIT = 5000       # per service; hitting it is reported, never silent
COMPOSE_FILES = ["-f", "compose.yaml", "-f", "compose.observability.yaml",
                 "-f", "compose.override.yaml"]

OPENSEARCH_INDEX = "otel-logs-*"   # index pattern, from three_signals.collect_logs

# ── span tag whitelist (O-P2-16) ──────────────────────────────────────────
# traces.json used to drop every tag, which made the pack unable to answer
# "which product_id failed?" -- and Jaeger's in-memory store (MEMORY_MAX_TRACES
# =25000, ~30 min of lookback here) means a question you cannot answer from the
# pack you cannot answer at all once that window ages out.
#
# Whitelist, not everything: an unfiltered tag map roughly doubles traces.json,
# and most of what it would carry (otel.scope.*, server.address, http.url,
# upstream_cluster, ...) is invariant boilerplate. Keys below were taken from a
# live 25-minute sample across all services, not guessed.
#
# Business ids: any `demo.<entity>.id`. Observed in that sample --
#   demo.product.id (7332 spans), demo.order.id (1818), demo.shipping.tracking.id (606).
# The regex, not the list, is the rule: a new demo.*.id starts being captured on
# its own, which is what a targeting-style card needs.
BUSINESS_ID_RE = re.compile(r"^demo\..+\.id$")

# Error / exception message tags. Deliberately excludes the status-code family
# (http.status_code, rpc.grpc.status_code, ...): those sit on tens of thousands of
# healthy spans and carry no message. Each span already has its own `status`.
ERROR_TAG_KEYS = (
    "error",
    "error.type",
    "otel.status_code",
    "otel.status_description",
    "grpc.error_message",
    "grpc.error_name",
)


def whitelisted_tags(tags):
    out = {k: v for k, v in tags.items()
           if k in ERROR_TAG_KEYS or BUSINESS_ID_RE.match(k)}
    return dict(sorted(out.items()))


def load_backends():
    env = {}
    with open(os.path.join(SCRIPTS, "backends.env")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def iso_to_dt(s):
    s = s.strip().replace("Z", "+00:00")
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def http(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    if data is not None:
        req.get_method = lambda: "POST"
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode())


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sh(cmd, cwd=None):
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return p.returncode, p.stdout, p.stderr


# ── 1. logs ───────────────────────────────────────────────────────────────
def pack_logs(base, t0, t1, out_path):
    """All services' logs in the window. search_after, not scroll: no server-side
    state to leak if the packer dies mid-run, and the sort key is stable."""
    body_tmpl = {
        "size": LOG_PAGE,
        "query": {"range": {"observedTimestamp": {
            "gte": t0.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "lte": t1.strftime("%Y-%m-%dT%H:%M:%S.999Z"),
        }}},
        "sort": [{"observedTimestamp": "asc"}, {"_id": "asc"}],
        "_source": ["observedTimestamp", "resource.service.name", "body",
                    "severity", "traceId", "spanId"],
    }
    n, after, truncated = 0, None, False
    with open(out_path, "w") as f:
        while True:
            body = copy.deepcopy(body_tmpl)
            if after is not None:
                body["search_after"] = after
            data = http(f"{base}/{OPENSEARCH_INDEX}/_search",
                        data=json.dumps(body).encode(),
                        headers={"Content-Type": "application/json"})
            hits = data["hits"]["hits"]
            if not hits:
                break
            for h in hits:
                s = h["_source"]
                sev = s.get("severity") or {}
                rec = {
                    "ts": s.get("observedTimestamp"),
                    "service": ((s.get("resource") or {}).get("service") or {}).get("name")
                    if isinstance((s.get("resource") or {}).get("service"), dict)
                    else (s.get("resource") or {}).get("service.name"),
                    "body": s.get("body") if isinstance(s.get("body"), str)
                    else json.dumps(s.get("body"), ensure_ascii=False),
                    "severity": sev.get("text") if isinstance(sev, dict) else sev,
                    "trace_id": s.get("traceId"),
                    "span_id": s.get("spanId"),
                }
                f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
                n += 1
            after = hits[-1]["sort"]
    return {"lines": n, "index_pattern": OPENSEARCH_INDEX, "truncated": truncated,
            "page_size": LOG_PAGE}


# ── 2. metrics ────────────────────────────────────────────────────────────
def pack_metrics(base, t0, t1, windows, out_path):
    out = {"window": {"start": iso(t0), "end": iso(t1)},
           "windows": windows,
           "step_s": queries.STEP_S,
           "queries": []}
    for q in queries.QUERIES:
        url = f"{base}/api/v1/query_range?" + urllib.parse.urlencode({
            "query": q["promql"], "start": t0.timestamp(), "end": t1.timestamp(),
            "step": queries.STEP_S})
        entry = {k: q[k] for k in ("name", "promql", "unit", "group_by", "note")}
        try:
            data = http(url)
        except Exception as e:                          # noqa: BLE001
            entry.update(error=f"{type(e).__name__}: {e}", series={})
            out["queries"].append(entry)
            continue
        series = {}
        if data.get("status") == "success":
            gb = q["group_by"]
            labels = gb if isinstance(gb, list) else [gb]
            for r in data["data"]["result"]:
                key = queries.SERIES_SEP.join(r["metric"].get(l) or "?" for l in labels)
                series[key] = [[float(ts), None if v in ("NaN", "+Inf", "-Inf") else float(v)]
                               for ts, v in r["values"]]
        entry["series"] = {k: series[k] for k in sorted(series)}
        entry["series_count"] = len(series)
        out["queries"].append(entry)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=1, sort_keys=False)
    return {"queries": len(out["queries"]),
            "series": sum(q.get("series_count", 0) for q in out["queries"])}


# ── 3. traces ─────────────────────────────────────────────────────────────
def pack_traces(base, t0, t1, out_path):
    us0, us1 = int(t0.timestamp() * 1e6), int(t1.timestamp() * 1e6)
    svcs = (http(f"{base}/api/services").get("data") or [])
    spans, per_service, hit_limit = {}, {}, []
    for svc in sorted(svcs):
        q = urllib.parse.urlencode({"service": svc, "start": us0, "end": us1,
                                    "limit": JAEGER_TRACE_LIMIT})
        try:
            data = http(f"{base}/api/traces?{q}")
        except Exception as e:                          # noqa: BLE001
            per_service[svc] = {"traces": 0, "error": f"{type(e).__name__}: {e}"}
            continue
        traces = data.get("data") or []
        per_service[svc] = {"traces": len(traces)}
        if len(traces) >= JAEGER_TRACE_LIMIT:
            hit_limit.append(svc)
        for tr in traces:
            procs = {k: v.get("serviceName") for k, v in (tr.get("processes") or {}).items()}
            for sp in tr.get("spans") or []:
                sid = sp.get("spanID")
                if sid in spans:
                    continue
                st = sp.get("startTime")
                if st is None or not (us0 <= st <= us1):
                    continue
                tags = {t["key"]: t.get("value") for t in sp.get("tags") or []}
                parent = next((r.get("spanID") for r in (sp.get("references") or [])
                               if r.get("refType") == "CHILD_OF"), None)
                status = "ERROR" if (tags.get("error") is True or
                                     str(tags.get("otel.status_code", "")).upper() == "ERROR") \
                    else str(tags.get("otel.status_code", "") or "UNSET").upper()
                spans[sid] = {
                    "traceID": sp.get("traceID"), "spanID": sid,
                    "parentSpanID": parent,
                    "service": procs.get(sp.get("processID")),
                    "operation": sp.get("operationName"),
                    "start": st, "duration": sp.get("duration"),
                    "status": status,
                    "tags": whitelisted_tags(tags),
                }
    ordered = sorted(spans.values(), key=lambda s: (s["start"], s["spanID"]))
    payload = {
        "window": {"start": iso(t0), "end": iso(t1)},
        "tag_whitelist": {"business_id_pattern": BUSINESS_ID_RE.pattern,
                          "error_keys": list(ERROR_TAG_KEYS),
                          "note": "all other span tags are dropped (fault_schema §9)"},
        "jaeger_limit_per_service": JAEGER_TRACE_LIMIT,
        "services_hitting_limit": hit_limit,
        "limit_hit": bool(hit_limit),
        "traces_per_service": per_service,
        "span_count": len(ordered),
        "spans": ordered,
    }
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=1)
    return {"spans": len(ordered), "limit_hit": bool(hit_limit),
            "services_hitting_limit": hit_limit}


# ── 4. config diff ────────────────────────────────────────────────────────
FLAGD_JSON = os.path.join(DEMO, "src", "flagd", "demo.flagd.json")


def baseline_dir(out_root=EVIDENCE_ROOT):
    return os.path.join(out_root, "_baseline")


def compose_env_text():
    """Resolved compose environment, one `service=KEY=VALUE` line, sorted.

    `docker compose config` is the merged view (decision 002's -f order matters,
    so the merged view is the only honest one). Only the environment blocks are
    kept: the rest of the rendered config is image digests and volume paths that
    change for reasons unrelated to an injection.
    """
    rc, so, se = sh(["docker", "compose"] + COMPOSE_FILES + ["config", "--format", "json"],
                    cwd=DEMO)
    if rc != 0:
        return f"# docker compose config failed rc={rc}: {se.strip()[:300]}\n"
    cfg = json.loads(so)
    lines = []
    for svc, spec in sorted((cfg.get("services") or {}).items()):
        env = spec.get("environment") or {}
        if isinstance(env, list):
            env = dict(e.split("=", 1) if "=" in e else (e, "") for e in env)
        for k in sorted(env):
            lines.append(f"{svc}={k}={env[k]}")
    return "\n".join(lines) + "\n"


def ensure_baseline(out_root=EVIDENCE_ROOT):
    """Snapshot the clean state if it is not there yet. Returns (dir, meta)."""
    bdir = baseline_dir(out_root)
    stamp_path = os.path.join(bdir, "snapshot.json")
    if os.path.exists(stamp_path):
        with open(stamp_path) as f:
            return bdir, json.load(f)
    os.makedirs(bdir, exist_ok=True)
    with open(FLAGD_JSON) as f:
        flagd = f.read()
    with open(os.path.join(bdir, "demo.flagd.json"), "w") as f:
        f.write(flagd)
    env = compose_env_text()
    with open(os.path.join(bdir, "compose_env.txt"), "w") as f:
        f.write(env)
    rc, so, _ = sh(["git", "-C", DEMO, "status", "--short", "src/flagd/"])
    meta = {
        "created_at": iso(datetime.now(timezone.utc)),
        "flagd_path": os.path.relpath(FLAGD_JSON, os.path.dirname(REPO)),
        "flagd_sha256": hashlib.sha256(flagd.encode()).hexdigest(),
        "compose_env_sha256": hashlib.sha256(env.encode()).hexdigest(),
        "compose_files": " ".join(COMPOSE_FILES),
        "flagd_worktree_dirty": bool(so.strip()),
        "note": ("baseline snapshot taken from the live clean state; "
                 "config_diff.txt in every card is a unified diff against these two files"),
    }
    with open(stamp_path, "w") as f:
        json.dump(meta, f, indent=1)
    return bdir, meta


def pack_config_diff(out_path, out_root=EVIDENCE_ROOT):
    bdir, meta = ensure_baseline(out_root)
    with open(os.path.join(bdir, "demo.flagd.json")) as f:
        base_flagd = f.read().splitlines(keepends=True)
    with open(os.path.join(bdir, "compose_env.txt")) as f:
        base_env = f.read().splitlines(keepends=True)
    with open(FLAGD_JSON) as f:
        cur_flagd = f.read().splitlines(keepends=True)
    cur_env = compose_env_text().splitlines(keepends=True)

    chunks = []
    for label, b, c in (("demo.flagd.json", base_flagd, cur_flagd),
                        ("compose_env.txt", base_env, cur_env)):
        d = list(difflib.unified_diff(b, c, fromfile=f"baseline/{label}",
                                      tofile=f"current/{label}", n=3))
        chunks.append("".join(d) if d else f"# no diff: {label}\n")
    text = ("# unified diff against evidence/_baseline/ "
            f"(snapshot taken {meta['created_at']})\n" + "".join(chunks))
    with open(out_path, "w") as f:
        f.write(text)
    changed = any(not c.startswith("# no diff") for c in chunks)
    return {"changed": changed, "baseline_created_at": meta["created_at"]}


# ── 5. topology ───────────────────────────────────────────────────────────
def shared_topology(out_root=EVIDENCE_ROOT):
    return os.path.join(out_root, "_shared", "topology.json")


_VERB = re.compile(r"^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+")


def _norm_span_name(n):
    return _VERB.sub("", n or "").lstrip("/")


_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def container_ip_map():
    """container IP -> container name, so a peer tag that came out as a raw address
    does not become its own node. Some client spans carry `net.peer.name` already
    resolved and some carry the address the connection actually used; both are the
    same edge and the graph must not show them as two."""
    rc, so, _ = sh(["docker", "ps", "-q"])
    ids = so.split()
    if rc != 0 or not ids:
        return {}
    rc, so, _ = sh(["docker", "inspect", "--format",
                    "{{.Name}}|{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}"] + ids)
    out = {}
    for line in so.splitlines():
        if "|" not in line:
            continue
        name, ips = line.split("|", 1)
        for ip in ips.split():
            out[ip] = name.lstrip("/")
    return out


def build_topology(prom_base, jaeger_base, t0, t1, out_root=EVIDENCE_ROOT):
    """Call graph from baseline-window spanmetrics, supplemented from Jaeger.

    spanmetrics carries no caller/callee pair -- only (service_name, span_kind,
    span_name) -- so an edge is a join: A has a CLIENT span named X and B has a
    SERVER span whose name normalises to X. That resolves gRPC and named HTTP
    routes. It cannot resolve two cases, and both are filled from Jaeger client
    spans' peer tags (the method decision 018 already uses for these very targets):
      - SDK-less peers (valkey-cart, astronomy-db) have no server spanmetrics at all;
      - generic HTTP client spans named just "POST" normalise to the empty string.
    Every edge records which of the two produced it; span names spanmetrics could
    not resolve are listed rather than dropped.
    """
    q = 'sum by (service_name, span_kind, span_name) (traces_span_metrics_calls_total)'
    url = f"{prom_base}/api/v1/query?" + urllib.parse.urlencode(
        {"query": q, "time": t1.timestamp()})
    data = http(url)
    client, server = {}, {}
    for r in (data.get("data") or {}).get("result") or []:
        m = r["metric"]
        name = _norm_span_name(m.get("span_name"))
        bucket = client if m.get("span_kind") == "SPAN_KIND_CLIENT" else (
            server if m.get("span_kind") == "SPAN_KIND_SERVER" else None)
        if bucket is None or not name:
            continue
        bucket.setdefault(name, set()).add(m.get("service_name"))

    edges, unresolved = {}, {}
    for name, callers in client.items():
        if name in server:
            for c in callers:
                for s in server[name]:
                    if c != s:
                        edges.setdefault((c, s), set()).add("spanmetrics")
        else:
            for c in callers:
                unresolved.setdefault(c, set()).add(name)

    # Jaeger supplement: client spans' peer tags in the baseline window.
    peer_keys = ("net.peer.name", "server.address", "peer.service")
    ipmap = container_ip_map()
    unresolved_ips = set()
    us0, us1 = int(t0.timestamp() * 1e6), int(t1.timestamp() * 1e6)
    svcs = sorted(http(f"{jaeger_base}/api/services").get("data") or [])
    for svc in svcs:
        qs = urllib.parse.urlencode({"service": svc, "start": us0, "end": us1,
                                     "limit": 500})
        try:
            d = http(f"{jaeger_base}/api/traces?{qs}")
        except Exception:                              # noqa: BLE001
            continue
        for tr in d.get("data") or []:
            procs = {k: v.get("serviceName") for k, v in (tr.get("processes") or {}).items()}
            for sp in tr.get("spans") or []:
                tags = {t["key"]: t.get("value") for t in sp.get("tags") or []}
                if tags.get("span.kind") != "client":
                    continue
                owner = procs.get(sp.get("processID"))
                peer = next((tags.get(k) for k in peer_keys if tags.get(k)), None)
                if not owner or not peer or peer == owner:
                    continue
                peer = str(peer).split(":")[0]
                if _IPV4.match(peer):
                    if peer not in ipmap:
                        unresolved_ips.add(peer)
                        continue
                    peer = ipmap[peer]
                if peer == owner:
                    continue
                edges.setdefault((owner, peer), set()).add("jaeger_peer")

    payload = {
        "generated_at": iso(datetime.now(timezone.utc)),
        "baseline_window": {"start": iso(t0), "end": iso(t1)},
        "method": ("spanmetrics CLIENT/SERVER span_name join, supplemented by "
                   "Jaeger client-span peer tags for SDK-less and generic-HTTP peers"),
        "edges": [{"from": a, "to": b, "source": sorted(src)}
                  for (a, b), src in sorted(edges.items())],
        "edge_count": len(edges),
        "spanmetrics_unresolved_client_calls":
            {k: sorted(v) for k, v in sorted(unresolved.items())},
        "unresolved_peer_ips": sorted(unresolved_ips),
    }
    path = shared_topology(out_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=1)
    return payload


def pack_topology(prom_base, jaeger_base, t0, t1, out_path, rebuild=False,
                  out_root=EVIDENCE_ROOT):
    path = shared_topology(out_root)
    if rebuild or not os.path.exists(path):
        build_topology(prom_base, jaeger_base, t0, t1, out_root=out_root)
    digest = sha256_of(path)
    with open(path) as f:
        shared = json.load(f)
    ref = {
        "ref": os.path.relpath(path, out_root),
        "sha256": digest,
        "generated_at": shared["generated_at"],
        "edge_count": shared["edge_count"],
        "note": ("service topology is generated once from a baseline window and shared "
                 "by every card; this file is a reference plus its hash, the graph "
                 "itself lives at the path in `ref` (relative to evidence/)"),
    }
    with open(out_path, "w") as f:
        json.dump(ref, f, indent=1)
    return {"edge_count": shared["edge_count"], "sha256": digest}


# ── driver ────────────────────────────────────────────────────────────────
FIVE = ["logs.jsonl", "metrics.json", "traces.json", "config_diff.txt", "topology.json"]


def pack(card_id, t_start, t_inject, t_revert, t_end, out_root=EVIDENCE_ROOT,
         rebuild_topology=False):
    env = load_backends()
    w0 = t_start - timedelta(seconds=PRE_PAD_S)
    w1 = t_end + timedelta(seconds=POST_PAD_S)
    cdir = os.path.join(out_root, card_id)
    os.makedirs(cdir, exist_ok=True)

    windows = {
        "pack": {"start": iso(w0), "end": iso(w1)},
        "baseline": {"start": iso(t_start), "end": iso(t_inject)},
        "inject": {"start": iso(t_inject), "end": iso(t_revert)},
        "recover": {"start": iso(t_revert), "end": iso(t_end)},
    }

    stats = {}
    stats["logs.jsonl"] = pack_logs(env["OPENSEARCH_BASE"], w0, w1,
                                    os.path.join(cdir, "logs.jsonl"))
    stats["metrics.json"] = pack_metrics(env["PROM_BASE"], w0, w1, windows,
                                         os.path.join(cdir, "metrics.json"))
    stats["traces.json"] = pack_traces(env["JAEGER_BASE"], w0, w1,
                                       os.path.join(cdir, "traces.json"))
    stats["config_diff.txt"] = pack_config_diff(os.path.join(cdir, "config_diff.txt"),
                                                out_root=out_root)
    stats["topology.json"] = pack_topology(env["PROM_BASE"], env["JAEGER_BASE"],
                                           t_start, t_inject,
                                           os.path.join(cdir, "topology.json"),
                                           rebuild=rebuild_topology, out_root=out_root)

    _, baseline_meta = ensure_baseline(out_root)
    manifest = {
        "card_id": card_id,
        "packed_at": iso(datetime.now(timezone.utc)),
        "window": {"start": iso(w0), "end": iso(w1),
                   "pre_pad_s": PRE_PAD_S, "post_pad_s": POST_PAD_S},
        "windows": windows,
        "baseline_snapshot_created_at": baseline_meta["created_at"],
        "files": [],
        "stats": stats,
    }
    for name in FIVE:
        p = os.path.join(cdir, name)
        manifest["files"].append({
            "name": name,
            "path": os.path.relpath(p, REPO),
            "bytes": os.path.getsize(p),
            "sha256": sha256_of(p),
        })
    with open(os.path.join(cdir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    return manifest


def refresh_metrics(card_id, out_root=EVIDENCE_ROOT):
    """Re-query metrics.json for an already-packed card, in place.

    Prometheus keeps its TSDB for days, so a metrics query set added after a card
    was packed can still be answered for that card's window. Jaeger cannot: it is
    in-memory with ~30 min of lookback (fault_schema §9), so traces.json is never
    refreshable and this deliberately does not touch it.
    """
    env = load_backends()
    cdir = os.path.join(out_root, card_id)
    mpath = os.path.join(cdir, "manifest.json")
    with open(mpath) as f:
        manifest = json.load(f)
    w = manifest["window"]
    stat = pack_metrics(env["PROM_BASE"], iso_to_dt(w["start"]), iso_to_dt(w["end"]),
                        manifest["windows"], os.path.join(cdir, "metrics.json"))
    p = os.path.join(cdir, "metrics.json")
    for entry in manifest["files"]:
        if entry["name"] == "metrics.json":
            entry["bytes"] = os.path.getsize(p)
            entry["sha256"] = sha256_of(p)
    manifest["stats"]["metrics.json"] = stat
    manifest["metrics_refreshed_at"] = iso(datetime.now(timezone.utc))
    with open(mpath, "w") as f:
        json.dump(manifest, f, indent=1)
    return stat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card-id", required=True)
    ap.add_argument("--refresh-metrics", action="store_true",
                    help="re-query metrics.json for an already-packed card using "
                         "the window in its manifest, and update the manifest entry")
    ap.add_argument("--t-start", help="cycle t0 (ISO8601)")
    ap.add_argument("--t-inject", help="t_apply (ISO8601)")
    ap.add_argument("--t-revert", help="t_revert (ISO8601)")
    ap.add_argument("--t-end", help="end of the recover window (ISO8601)")
    ap.add_argument("--out-root", default=EVIDENCE_ROOT)
    ap.add_argument("--rebuild-topology", action="store_true")
    a = ap.parse_args()
    if a.refresh_metrics:
        print(json.dumps(refresh_metrics(a.card_id, a.out_root), indent=1))
        return 0
    if not all((a.t_start, a.t_inject, a.t_revert, a.t_end)):
        ap.error("--t-start/--t-inject/--t-revert/--t-end are required unless "
                 "--refresh-metrics is given")
    m = pack(a.card_id, iso_to_dt(a.t_start), iso_to_dt(a.t_inject),
             iso_to_dt(a.t_revert), iso_to_dt(a.t_end),
             out_root=a.out_root, rebuild_topology=a.rebuild_topology)
    print(json.dumps(m, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
