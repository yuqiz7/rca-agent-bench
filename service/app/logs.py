#!/usr/bin/env python3
"""logs.py -- structured JSON on stdout, for whoever is running (design §5).

One line per event, picked up by docker's json-file driver (max-size 10m,
max-file 3). No file sink, no shipper, no library. Lives in its own module so the
worker can log without importing main.py -- pulling in the FastAPI app just to
print a line would give the worker process a web framework and a route table it
has no use for.

Every line about a run carries run_id, the same value as the trace attribute
rca.run_id, so logs and traces join on one key (§5).
"""
import json
import sys
import time


def log(event: str, **fields) -> None:
    print(json.dumps({"ts": time.time(), "event": event, **fields}, default=str),
          file=sys.stdout, flush=True)
