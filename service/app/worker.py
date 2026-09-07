#!/usr/bin/env python3
"""worker.py -- the queue consumer (design §4). A separate process, on purpose.

WHY NOT FastAPI BackgroundTasks
-------------------------------
A background task lives inside the web process. Restart the api -- a deploy, an
OOM, a `compose up --build` -- and any run in flight vanishes, leaving a row that
says `running` forever and no way to tell that from a run that is genuinely still
going. These runs take 10-100 s and each one spends real money, so a lost task is
not "retry it": it is a charge with nothing to show. The queue lives in Postgres
and this process holds nothing that a restart would lose.

CONCURRENCY IS ONE, AND THAT IS THE FEATURE
-------------------------------------------
No thread pool, no asyncio fan-out, no second replica. The concurrency limit is
the number of worker containers, which makes "what is the most this can spend in
an hour" a number you can compute rather than estimate. §8 is explicit that
"高并发" is not a claim this service gets to make.

CRASH RECOVERY
--------------
Rows stuck in `running` past RCA_RUN_TIMEOUT_S are pushed back to `queued` with a
note appended to `error`. Done at boot AND before every claim: a worker that
stays up for a week would otherwise never sweep the row left behind by the
container it replaced. The note is appended rather than overwriting because a run
can be requeued after having already recorded something worth keeping.
"""
import os
import signal
import time

from . import db, repo, runner
from .main import log

POLL_INTERVAL_S = 2.0                       # §4: "worker 每 2 s 扫一次队列"

_stop = False


def _handle_signal(signum, _frame):
    """Finish the run in hand, then exit. SIGTERM arrives on every `compose down`."""
    global _stop
    _stop = True
    log("worker.signal", signum=signum)


def run_timeout_s() -> int:
    return int(os.environ.get("RCA_RUN_TIMEOUT_S") or 600)


def sweep(conn, always_log: bool = False) -> None:
    """Push `running` rows older than the timeout back to `queued`.

    Silent when it finds nothing, except at boot. The steady-state sweep runs
    every couple of seconds, so a line per no-op would be ~40k lines a day saying
    that nothing happened -- and it would be the noise the requeue line has to be
    found inside. The BOOT sweep is the one worth a line either way: it is the
    answer to "did the container I just replaced leave a run stranded", and a
    silent no-op makes that unanswerable from `docker compose logs` alone.
    """
    requeued = repo.requeue_stale(conn, run_timeout_s())
    if requeued or always_log:
        log("worker.requeued", run_ids=[str(r) for r in requeued],
            note=repo.REQUEUE_NOTE if requeued else None, timeout_s=run_timeout_s())


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    log("worker.start", poll_interval_s=POLL_INTERVAL_S, run_timeout_s=run_timeout_s())
    first_sweep = True

    while not _stop:
        try:
            with db.connect(autocommit=True) as conn:
                sweep(conn, always_log=first_sweep)
                first_sweep = False
                while not _stop:
                    run = repo.claim_next(conn)
                    if run is None:
                        break
                    log("run.started", run_id=str(run["run_id"]),
                        card_id=run["card_id"], arm=run["arm"], model=run["model"])
                    outcome = runner.execute(conn, run)
                    log("run.finished", **outcome)
                    sweep(conn)
        except Exception as exc:                # noqa: BLE001
            # A database that went away must not end the worker: compose will not
            # restart it any faster than this loop will reconnect, and an exited
            # worker is indistinguishable from a healthy idle one in `ps`.
            log("worker.error", error=f"{type(exc).__name__}: {exc}")
        for _ in range(int(POLL_INTERVAL_S * 10)):
            if _stop:
                break
            time.sleep(0.1)
    log("worker.stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
