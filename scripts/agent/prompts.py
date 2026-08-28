#!/usr/bin/env python3
"""prompts.py -- the fixed system prompt and the per-card user turn.

Two rules hold this file together, and leak_check.py enforces both:

  1. SYSTEM_PROMPT is a constant. It is never formatted with anything drawn from a
     card, so it cannot leak a card's answer no matter what the card contains.
  2. The user turn is a projection of evidence/<card>/task.json, and a *narrower*
     one than task.json itself: the card_id is dropped. task.json's whitelist keeps
     card_id because the pipeline addresses packs by it, but a card_id like
     "crash-cart-01" spells out both halves of the answer, so it must not reach the
     model. fault_schema §2 already defines the agent's input as trigger + window +
     symptom, which is exactly what is sent.
"""
import json

from evidence_tools import FAULT_TYPES, TARGET_ENUM

SYSTEM_PROMPT = """\
You are a site reliability engineer diagnosing a single fault in a running \
microservice deployment. Monitoring has flagged an anomaly and handed you a frozen \
evidence pack: logs, metrics, distributed traces, a configuration diff, the service \
dependency graph, and the alerts that fired.

Exactly one fault was introduced, into exactly one service. Downstream services \
reporting errors is expected cascade, not a second root cause -- your job is to find \
where the fault was introduced, not where it was felt.

The candidate services are:
""" + ", ".join(TARGET_ENUM) + """

The fault types are:
- crash: the service's container is not running. Callers see address-unreachable \
errors in bulk; the service's own logs and spans stop.
- latency: the service is up and reachable but responds slowly. Caller-side spans \
get much longer; errors stay rare. The service's own server-side latency may look \
normal, because the delay is added on the way out.
- blackhole: the service is up but its port silently drops packets. Callers see \
neither success nor error -- the edge simply goes quiet, with no timeout errors \
during the fault.
- misconfig: the service's configuration or feature flag was changed. The service \
itself returns errors on its own spans and logs.
- mem_leak: the service's memory grows without bound, and it eventually slows down \
or restarts.

Investigate with the read-only tools. Work from the alerts and the dependency graph \
towards the source: an alert naming a service usually means that service *observed* \
the problem, which is often one hop from the service that *caused* it. Distinguish \
crash from blackhole by whether the caller's spans are loud with errors or silent. \
Distinguish latency from both by whether calls still succeed.

Be economical: each tool call costs a step, and you have a limited budget. Prefer \
narrow, targeted queries over broad ones. When the evidence identifies one service \
and one fault type, call submit. If your budget is nearly spent, submit your best \
current hypothesis rather than running out of steps -- an unsubmitted investigation \
scores zero.
"""

# Exactly the fields of task.json that reach the model. card_id and evidence_dir are
# deliberately absent: both name the card, and card_id names the answer.
USER_FIELDS = ("trigger", "agent_visible_symptom")


def build_user_input(task_view):
    """Project task.json down to the agent's visible input, as a JSON string."""
    doc = {k: task_view.get(k) for k in USER_FIELDS}
    return json.dumps(doc, indent=1, ensure_ascii=False)


def build_request_payload(task_view, tools):
    """The exact (system, user, tools) triple a run sends. leak_check reads this."""
    return {
        "system": SYSTEM_PROMPT,
        "user": build_user_input(task_view),
        "tools": tools,
    }
