#!/usr/bin/env python3
"""Rebuild tests/fixtures/clean/ from the full clean-window packs.

Run this only when the clean observation windows are re-cut. It needs the
gitignored big files in evidence/_clean/observe-*/, so it works on the VM and not
in CI -- which is the whole reason the fixtures exist.

The reduction is exact for the detector, not a sample: rules 1-5 and 7 read
metrics.json only, and rule 6 skips every span without a demo.<entity>.id tag, so
tagless spans cannot affect any rule. The script verifies that claim rather than
asserting it -- it runs detect() over the original and the reduction and refuses
to write if the two disagree.
"""
import json
import os
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts", "evidence"))

from detect import BUSINESS_ID_RE, detect  # noqa: E402

SRC_ROOT = os.path.join(REPO, "evidence", "_clean")
DST_ROOT = os.path.join(REPO, "tests", "fixtures", "clean")
COPY_VERBATIM = ("metrics.json", "manifest.json")

NOTE = ("reduced: only spans carrying demo.<entity>.id tags, and only the four "
        "fields detect.detect_entity_concentration reads. Rule 6 ignores every "
        "span without such a tag, so this is equivalent to the full file for the "
        "detector. Rebuild with tools/refresh_clean_fixtures.py.")


def reduce_traces(path):
    with open(path) as f:
        tj = json.load(f)
    kept = []
    for sp in tj.get("spans") or []:
        tags = {k: v for k, v in (sp.get("tags") or {}).items()
                if BUSINESS_ID_RE.match(k)}
        if not tags:
            continue
        kept.append({"start": sp.get("start"), "service": sp.get("service"),
                     "status": sp.get("status"), "tags": tags})
    return {"window": tj.get("window"), "span_count": len(kept),
            "spans": kept, "_fixture_note": NOTE}, len(tj.get("spans") or [])


def strip_ts(d):
    d = dict(d)
    d.pop("detected_at", None)
    return d


def main():
    names = sorted(n for n in os.listdir(SRC_ROOT) if n.startswith("observe-"))
    if not names:
        raise SystemExit(f"no clean windows under {SRC_ROOT}")
    for name in names:
        src, dst = os.path.join(SRC_ROOT, name), os.path.join(DST_ROOT, name)
        os.makedirs(dst, exist_ok=True)
        for f in COPY_VERBATIM:
            shutil.copy(os.path.join(src, f), os.path.join(dst, f))
        reduced, total = reduce_traces(os.path.join(src, "traces.json"))
        tmp = os.path.join(dst, "traces.json")
        with open(tmp, "w") as f:
            json.dump(reduced, f, indent=1)
        full = detect(os.path.join(src, "metrics.json"), os.path.join(src, "traces.json"))
        fix = detect(os.path.join(dst, "metrics.json"), tmp)
        if strip_ts(full) != strip_ts(fix):
            raise SystemExit(
                f"{name}: the reduced fixture does not reproduce detect() on the "
                f"full pack -- refusing to write a fixture that measures something "
                f"else ({len(full['alerts'])} vs {len(fix['alerts'])} alerts)")
        print(f"{name}: spans {total} -> {reduced['span_count']}, "
              f"detect() identical, alerts={len(fix['alerts'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
