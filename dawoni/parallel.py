"""여러 문항·쪽을 동시에 AI로 처리 (각자 새 세션). 해설·루브릭·문제은행 풀이·문항 구조화가 쓴다."""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

Progress = Callable[[int, int, str], None]


def _noop(done: int, total: int, message: str) -> None:
    pass


class ParallelError(ValueError):
    """여러 항목 중 일부가 실패 (성공한 항목은 이미 처리됨)."""


def each_in_parallel[T, R](
    items: list[T],
    fn: Callable[[T], R],
    *,
    name: Callable[[T], str],
    what: str,
    workers: int,
    progress: Progress = _noop,
) -> list[R]:
    """항목마다 `fn`을 최대 `workers`개씩 동시에 (AI 호출은 각자 새 세션).

    결과는 입력 순서대로. 하나가 실패해도 나머지는 끝까지 하고(저장은 `fn` 안에서), 실패는
    모아서 마지막에 알린다. 작업의 취소·진행 알림(ContextVar)은 스레드마다 복사해 넘긴다.
    """
    total = len(items)
    if total == 0:
        return []
    lock = threading.Lock()
    running: list[str] = []
    done = 0

    def report() -> None:
        now = "·".join(running)
        progress(done, total, f"{what} {now} 진행 중 ({done}/{total} 완료)" if now else f"{what}")

    def one(x: T) -> R:
        nonlocal done
        with lock:
            running.append(name(x))
            report()
        try:
            return fn(x)
        finally:
            with lock:
                running.remove(name(x))
                done += 1
                report()

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
            except Exception as e:
                failed.append((name(items[i]), e))
    if failed:
        if len(failed) == 1 and len(items) == 1:
            raise failed[0][1]
        detail = "; ".join(f"{n}: {str(e).strip() or type(e).__name__}"[:200] for n, e in failed)
        raise ParallelError(f"{len(failed)}개 실패 (나머지는 저장됨) — {detail}")
    return [results[i] for i in range(total)]
