"""여러 문항·쪽·학생을 동시에 AI로 처리 (각자 새 세션). 해설·루브릭·문항 구조화·판독·채점이 쓴다.

동시 개수는 설정으로 정하고(PLAN.md §2-5 4번), 한도 신호(claude `rate_limit_event`)를 보면
`Throttle`이 하나씩 줄인다. 한도 초과·로그인 필요처럼 다른 호출도 똑같이 실패할 오류가 나면
남은 항목을 시작하지 않고 그 오류로 멈춘다 (한 명씩 실패로 쌓으며 한도를 더 쓰지 않게).
"""

from __future__ import annotations

import contextvars
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from dawoai.agents.base import SWITCHABLE_ERRORS, AgentError, AgentErrorCode

log = logging.getLogger(__name__)

Progress = Callable[[int, int, str], None]

RATE_SIGNAL: contextvars.ContextVar[Callable[[], None] | None] = contextvars.ContextVar(
    "dawoai_rate_signal", default=None
)
"""AI 호출이 한도에 가깝다는 신호를 받을 곳 (엔진이 부른다). 동시 실행 중에만 걸려 있다."""


def _noop(done: int, total: int, message: str) -> None:
    pass


class ParallelError(ValueError):
    """여러 항목 중 일부가 실패 (성공한 항목은 이미 처리됨)."""


def stops_all(e: BaseException) -> bool:
    """다른 항목도 똑같이 실패할 오류 — 취소, 한도 초과·로그인 필요·미설치."""
    return isinstance(e, AgentError) and (
        e.code == AgentErrorCode.CANCELLED or e.code in SWITCHABLE_ERRORS
    )


class Throttle:
    """동시에 도는 호출 수의 상한. 한도 신호마다 하나씩 줄인다 (1 밑으로는 줄이지 않음)."""

    def __init__(self, limit: int) -> None:
        self.limit = max(1, limit)
        self.start_limit = self.limit
        self.signals = 0
        self._running = 0
        self._cond = threading.Condition()

    def acquire(self) -> None:
        with self._cond:
            while self._running >= self.limit:
                self._cond.wait()
            self._running += 1

    def release(self) -> None:
        with self._cond:
            self._running -= 1
            self._cond.notify_all()

    def slow_down(self) -> None:
        with self._cond:
            self.signals += 1
            if self.limit > 1:
                self.limit -= 1
                log.warning("AI 사용 한도에 가까움 — 동시 호출을 %d개로 줄입니다", self.limit)


def each_in_parallel[T, R](
    items: list[T],
    fn: Callable[[T], R],
    *,
    name: Callable[[T], str],
    what: str,
    workers: int,
    progress: Progress = _noop,
    throttle: Throttle | None = None,
) -> list[R]:
    """항목마다 `fn`을 최대 `workers`개씩 동시에 (AI 호출은 각자 새 세션).

    결과는 입력 순서대로. 하나가 실패해도 나머지는 끝까지 하고(저장은 `fn` 안에서), 실패는
    모아서 마지막에 알린다 — `stops_all` 오류만은 남은 항목을 시작하지 않고 그 오류를 올린다.
    작업의 취소·진행 알림(ContextVar)은 스레드마다 복사해 넘긴다.
    """
    total = len(items)
    if total == 0:
        return []
    throttle = throttle or Throttle(workers)
    lock = threading.Lock()
    stop = threading.Event()
    running: list[str] = []
    done = 0

    def report() -> None:
        now = "·".join(running)
        progress(done, total, f"{what} {now} 진행 중 ({done}/{total} 완료)" if now else f"{what}")

    def one(x: T) -> R:
        nonlocal done
        throttle.acquire()
        try:
            if stop.is_set():
                raise _Skipped
            with lock:
                running.append(name(x))
                report()
            RATE_SIGNAL.set(throttle.slow_down)
            try:
                return fn(x)
            except BaseException as e:
                if stops_all(e):
                    stop.set()
                raise
            finally:
                with lock:
                    running.remove(name(x))
                    done += 1
                    report()
        finally:
            throttle.release()

    results: dict[int, R] = {}
    failed: list[tuple[str, BaseException]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, total))) as pool:
        futures = {
            pool.submit(contextvars.copy_context().run, one, x): i for i, x in enumerate(items)
        }
        for f in as_completed(futures):
            i = futures[f]
            try:
                results[i] = f.result()
            except _Skipped:
                pass
            except Exception as e:
                failed.append((name(items[i]), e))
    if failed:
        fatal = next((e for _, e in failed if stops_all(e)), None)
        if fatal is not None:  # 취소·한도는 실패 목록이 아니라 그 오류로 끝나야 한다
            raise fatal
        if len(failed) == 1 and len(items) == 1:
            raise failed[0][1]
        detail = "; ".join(f"{n}: {str(e).strip() or type(e).__name__}"[:200] for n, e in failed)
        raise ParallelError(f"{len(failed)}개 실패 (나머지는 저장됨) — {detail}")
    return [results[i] for i in range(total)]


class _Skipped(Exception):
    """멈춘 뒤 시작하지 않은 항목."""
