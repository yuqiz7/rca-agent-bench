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
import logging
import sys
import time


def log(event: str, **fields) -> None:
    print(json.dumps({"ts": time.time(), "event": event, **fields}, default=str),
          file=sys.stdout, flush=True)


class JsonFormatter(logging.Formatter):
    """Same one-line JSON shape as log(), for the loggers we do not own.

    uvicorn writes its startup and error lines through the stdlib logging module,
    not through log(), so without this the api container's stdout is two formats:
    JSON for everything the application says and `INFO:     Uvicorn running on ...`
    for everything the server says. That is the seam a `docker compose logs api |
    jq` falls into, and it is the whole point of collecting logs in one shape.

    Referenced by app/log_config.json as a `()` factory; uvicorn loads that file
    with logging.config.dictConfig.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": record.created, "event": record.name,
                   "level": record.levelname.lower(), "message": record.getMessage()}
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)
