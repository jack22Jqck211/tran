"""Async job queue with per-user limits, rate limiting and cancellation."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Dict, List, Optional

from .job import Job, JobStatus

log = logging.getLogger("app.queue")


class QueueFullError(Exception):
    pass


class UserBusyError(Exception):
    pass


class RateLimitedError(Exception):
    def __init__(self, wait_seconds: float) -> None:
        super().__init__("rate limited")
        self.wait_seconds = wait_seconds


Runner = Callable[[Job], Awaitable[None]]


class JobQueue:
    def __init__(self, queue_size: int, user_cooldown: float) -> None:
        self._queue: "asyncio.Queue[Job]" = asyncio.Queue(maxsize=queue_size)
        self._user_cooldown = user_cooldown
        self._jobs: Dict[str, Job] = {}
        self._user_active: Dict[int, str] = {}
        self._last_submit: Dict[int, float] = {}
        self._workers: List[asyncio.Task] = []

    # -- submission ---------------------------------------------------------
    def submit(self, job: Job) -> int:
        """Enqueue a job; returns queue position (1-based). Raises on limits."""
        now = time.monotonic()
        if job.user_id in self._user_active:
            raise UserBusyError()
        last = self._last_submit.get(job.user_id, 0.0)
        wait = self._user_cooldown - (now - last)
        if wait > 0:
            raise RateLimitedError(wait)
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            raise QueueFullError()
        self._jobs[job.id] = job
        self._user_active[job.user_id] = job.id
        self._last_submit[job.user_id] = now
        log.info("job queued", extra={"job_id": job.id, "user_id": job.user_id,
                                      "file": job.file_name,
                                      "size": job.file_size})
        return self._queue.qsize()

    # -- workers --------------------------------------------------------------
    async def start(self, concurrency: int, runner: Runner) -> None:
        for i in range(concurrency):
            self._workers.append(asyncio.create_task(self._worker(i, runner)))

    async def _worker(self, index: int, runner: Runner) -> None:
        while True:
            job = await self._queue.get()
            try:
                if job.cancel_event.is_set():
                    job.status = JobStatus.CANCELLED
                    continue
                job.status = JobStatus.RUNNING
                job.started_at = time.monotonic()
                await runner(job)
            except asyncio.CancelledError:  # shutdown
                raise
            except Exception:
                log.exception("worker crashed while running job",
                              extra={"job_id": job.id, "worker": index})
            finally:
                self._release(job)
                self._queue.task_done()

    def _release(self, job: Job) -> None:
        self._jobs.pop(job.id, None)
        if self._user_active.get(job.user_id) == job.id:
            self._user_active.pop(job.user_id, None)

    # -- cancellation & introspection ----------------------------------------
    def cancel_job(self, job_id: str, requester_id: int,
                   is_admin: bool) -> Optional[bool]:
        """None = not found; False = permission denied; True = cancelled."""
        job = self._jobs.get(job_id)
        if job is None:
            return None
        if job.user_id != requester_id and not is_admin:
            return False
        job.cancel_event.set()
        return True

    def cancel_user(self, user_id: int) -> bool:
        job_id = self._user_active.get(user_id)
        if not job_id:
            return False
        job = self._jobs.get(job_id)
        if not job:
            return False
        job.cancel_event.set()
        return True

    def job_for_user(self, user_id: int) -> Optional[Job]:
        job_id = self._user_active.get(user_id)
        return self._jobs.get(job_id) if job_id else None

    @property
    def queued_count(self) -> int:
        return self._queue.qsize()

    @property
    def active_count(self) -> int:
        return sum(1 for j in self._jobs.values()
                   if j.status == JobStatus.RUNNING)
