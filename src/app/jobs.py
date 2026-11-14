"""Background jobs in the server process (Build PRD 7): one at a time, state in memory.

The page polls `GET /api/jobs/{id}` once a second. A job's results are in the database, so losing the
in-memory state on a restart loses only the progress display.
"""
from __future__ import annotations

import itertools
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable


class Busy(RuntimeError):
    pass


@dataclass
class Step:
    name: str
    state: str = "waiting"          # waiting | running | done | failed | paused
    started: float | None = None
    seconds: float | None = None
    note: str | None = None


@dataclass
class Job:
    id: int
    kind: str
    product_id: str | None = None
    status: str = "running"         # running | done | failed | waiting_for_category | stopped
    steps: list[Step] = field(default_factory=list)
    error: str | None = None
    error_step: str | None = None
    cost_usd: float = 0.0
    models: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    items: list[dict] = field(default_factory=list)      # listing jobs: one per product, in page order
    stop: bool = False
    started: float = field(default_factory=time.time)
    finished: float | None = None

    def step(self, name: str) -> Step:
        s = next((x for x in self.steps if x.name == name), None)
        if s is None:
            s = Step(name)
            self.steps.append(s)
        return s

    def begin(self, name: str):
        s = self.step(name)
        s.state, s.started = "running", time.time()

    def end(self, name: str, note: str | None = None):
        s = self.step(name)
        s.state, s.note = "done", note
        s.seconds = round(time.time() - (s.started or time.time()), 1)

    def to_json(self) -> dict:
        return {"id": self.id, "kind": self.kind, "productId": self.product_id, "status": self.status,
                "steps": [{"name": s.name, "state": s.state, "seconds": s.seconds, "note": s.note}
                          for s in self.steps],
                "error": self.error, "errorStep": self.error_step, "costUsd": round(self.cost_usd, 4),
                "models": self.models, "result": self.result, "items": self.items, "stopping": self.stop,
                "seconds": round((self.finished or time.time()) - self.started, 1)}


_ids = itertools.count(1)
_jobs: dict[int, Job] = {}
_lock = threading.Lock()
_running: int | None = None


def get(job_id: int) -> Job | None:
    return _jobs.get(job_id)


def start(kind: str, work: Callable[[Job], None], product_id: str | None = None,
          steps: list[str] = ()) -> Job:
    """Run `work(job)` in a background thread. Raises Busy while another job runs."""
    global _running
    with _lock:
        if _running is not None and _jobs[_running].status == "running":
            raise Busy("Another job is still running. Wait for it to finish, then try again.")
        job = Job(next(_ids), kind, product_id, steps=[Step(n) for n in steps])
        _jobs[job.id] = job
        _running = job.id

    def run():
        try:
            work(job)
            if job.status == "running":
                job.status = "done"
        except Exception as e:  # noqa: BLE001 - reported to the page as plain text, logged in full
            job.status = "failed"
            running = next((s for s in job.steps if s.state == "running"), None)
            if running:
                running.state = "failed"
                job.error_step = running.name
            job.error = str(e) or type(e).__name__
            traceback.print_exc()
        finally:
            job.finished = time.time()

    threading.Thread(target=run, name=f"job-{job.id}", daemon=True).start()
    return job


def busy() -> bool:
    """Whether a job is running now."""
    with _lock:
        return _running is not None and _jobs[_running].status == "running"
