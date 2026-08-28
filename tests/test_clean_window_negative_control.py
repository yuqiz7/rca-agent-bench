#!/usr/bin/env python3
"""Gate 3: the detector must stay silent on a window with no fault in it.

A detector that fires on clean traffic makes every alert count meaningless, and
the failure is silent -- nothing in a card's own run tells you the rules have
started reporting noise. So the two clean observation windows are pinned here and
re-checked on every push.

The fixtures under tests/fixtures/clean/ are REDUCED copies of
evidence/_clean/observe-*/, because the originals carry an 8 MB traces.json each
and are gitignored (see .gitignore: the big three are generated, not source).
The reduction is not a sample -- it is exact for this purpose:

  metrics.json   copied verbatim; rules 1-5 and 7 read it and nothing else
  traces.json    only spans carrying a demo.<entity>.id tag, and only the four
                 fields detect_entity_concentration reads. Rule 6 skips every
                 span without such a tag, so dropping them cannot change its
                 output. 29 910 -> 0 spans and 29 718 -> 1 347 spans.
  manifest.json  copied verbatim; supplies the window boundaries

Equivalence was checked against the full files when the fixtures were built:
detect() returns identical output on both, modulo the detected_at timestamp.
tools/refresh_clean_fixtures.py rebuilds them if the originals are ever re-cut.

The first window predates the span-tag whitelist (O-P2-16) and therefore has no
tagged spans at all, which is why it exercises rules 3 and 5 only. The second was
cut after it and covers all seven.
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "scripts", "evidence"))

from detect import detect  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures", "clean")
WINDOWS = ("observe-20260827T185059Z", "observe-20260827T214612Z")


@pytest.mark.parametrize("name", WINDOWS)
def test_clean_window_raises_no_alert(name):
    d = os.path.join(FIXTURES, name)
    res = detect(os.path.join(d, "metrics.json"), os.path.join(d, "traces.json"))
    assert res["alerts"] == [], (
        f"{name}: {len(res['alerts'])} alert(s) on a clean window -- "
        f"rules are firing on normal traffic: "
        f"{[(a['rule'], a['service']) for a in res['alerts']]}")
    assert res["no_alert"] is True


@pytest.mark.parametrize("name", WINDOWS)
def test_fixture_is_shaped_like_a_pack(name):
    """Guards the fixture itself: a truncated file would pass the test above."""
    d = os.path.join(FIXTURES, name)
    with open(os.path.join(d, "metrics.json")) as f:
        m = json.load(f)
    assert m.get("queries"), f"{name}: metrics.json has no queries"
    # All ten collector queries must be present, or the negative control is only
    # exercising the rules whose input survived.
    assert len(m["queries"]) == 10, f"{name}: expected 10 metric queries, got {len(m['queries'])}"
    with open(os.path.join(d, "manifest.json")) as f:
        man = json.load(f)
    for k in ("baseline", "inject", "recover"):
        assert k in man["windows"], f"{name}: manifest missing {k} window"
