"""백그라운드 작업 실행기 — AI 호출처럼 오래 걸리는 일은 모두 여기서.

- 화면은 `GET /api/jobs/{id}`를 1초마다 물어 진행률(`JobProgress`)과 결과를 받는다
  (웹뷰에서도 확실히 동작하는 단순한 방식).
- AI 호출은 설정의 `max_parallel_jobs`(기본 1)개까지만 동시에 → 구독 한도 소모를 조절.
- 취소: `call_context`로 걸어 둔 신호를 CLI 실행기가 보고 프로세스 그룹째 끝낸다.
- 작업 기록은 메모리에만 둔다 (앱을 다시 켜면 사라짐). 결과물은 작업공간 파일에 이미 있다.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from pydantic import JsonValue

from dawoni.agents import AgentError, AgentErrorCode
from dawoni.context import TaskFailed, call_context
from dawoni.models.base import utcnow
from dawoni.models.job import Job, JobError, JobProgress

log = logging.getLogger(__name__)


class JobCancelled(Exception):
    pass


@dataclass
class JobContext:
    """작업 함수가 받는 것: 진행 알림과 취소 확인."""

    job: Job
    cancel: threading.Event
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def progress(self, done: int, total: int, message: str) -> None:
        with self._lock:
            total = max(total, done)
            self.job.progress = JobProgress(done=done, total=total, message=message)

    def note(self, message: str) -> None:
        """AI 실행기의 세부 알림 ('응답 수리 요청' 등) — 진행 수치는 그대로 두고 문구만."""
        with self._lock:
            p = self.job.progress
            self.job.progress = JobProgress(done=p.done, total=p.total, message=message)

    def check_cancelled(self) -> None:
        if self.cancel.is_set():
            raise JobCancelled


JobFn = Callable[[JobContext], dict[str, Any] | None]


def describe_error(e: BaseException) -> JobError:
    if isinstance(e, AgentError):
        return JobError(code=e.code.value, message=e.user_message, detail=e.detail[:2000] or None)
    if isinstance(e, TaskFailed):
        return JobError(
            code="invalid_output",
            message="AI 응답이 검증을 통과하지 못했습니다. 다시 시도하거나 다른 AI를 선택하세요.",
            detail=str(e)[:2000],
        )
    return JobError(code="failed", message=str(e).strip("'\"") or type(e).__name__)


class JobManager:
    def __init__(self, max_workers: int = 1) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="dawoni-job")
        self._jobs: dict[str, Job] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, fn: JobFn, params: dict[str, JsonValue] | None = None) -> Job:
        job = Job(kind=kind, params=params or {})
        cancel = threading.Event()
        with self._lock:
            self._jobs[job.id] = job
            self._cancel[job.id] = cancel
        self._pool.submit(self._run, job, cancel, fn)
        return job

    def _run(self, job: Job, cancel: threading.Event, fn: JobFn) -> None:
        if cancel.is_set():
            job.status = "cancelled"
            job.finished_at = utcnow()
            return
        ctx = JobContext(job, cancel)
        job.status = "running"
        job.started_at = utcnow()
        try:
            with call_context(cancel=cancel, on_progress=ctx.note):
                result = fn(ctx)
            job.result = result  # pyright: ignore[reportAttributeAccessIssue]
            job.status = "succeeded"
        except JobCancelled:
            job.status = "cancelled"
        except AgentError as e:
            if e.code == AgentErrorCode.CANCELLED:
                job.status = "cancelled"
            else:
                job.status = "failed"
                job.error = describe_error(e)
        except Exception as e:  # 작업 하나의 실패가 서버를 멈추지 않게
            log.exception("작업 실패: %s", job.kind)
            job.status = "failed"
            job.error = describe_error(e)
        finally:
            job.finished_at = utcnow()

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"작업 {job_id} 없음")
        return job

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        self._cancel[job_id].set()
        return job

    def shutdown(self) -> None:
        for ev in self._cancel.values():
            ev.set()
        self._pool.shutdown(wait=False, cancel_futures=True)
