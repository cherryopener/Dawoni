"""앱 창이 닫히면 서버도 끝낸다 (Windows Edge `--app` 창·브라우저로 열었을 때).

Edge 창은 따로 뜬 프로세스라 닫혀도 서버가 모른다 — 그래서 창을 닫은 뒤에도 python(Dawoni.exe)이
남아 묶음 폴더를 지울 수 없었다 (2026-11-06). 화면이 살아 있다고 알리고, 닫힐 때 인사한다:

- `alive`: 화면이 켜질 때와 그 뒤 주기적으로 (숨긴 창은 브라우저가 타이머를 1분에 한 번으로 줄인다).
- `closed`: 창을 닫거나 다른 주소로 갈 때(`pagehide`). 새로 고침도 같은 신호를 보내므로 곧바로 끄지
  않고, `BYE_GRACE` 안에 `alive`가 다시 오지 않으면 끈다.
- 아무 소식이 `SILENT` 동안 없으면 끈다 (브라우저가 인사 없이 죽었을 때).
- 작업(판독·채점 등)이 돌고 있으면 끝날 때까지 기다린다 — 창을 실수로 닫아도 작업은 마친다.
- PC가 잠들었다 깨면 그동안은 세지 않는다 (깨어나자마자 꺼지지 않게).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

BYE_GRACE = 10.0
SILENT = 180.0
TICK = 2.0
SLEPT = 30.0
"""감시 간격이 이보다 벌어지면 PC가 잠들었던 것으로 본다."""


class Lifeline:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._last = clock()
        self._bye: float | None = None

    def alive(self) -> None:
        with self._lock:
            self._last = self._clock()
            self._bye = None

    def closed(self) -> None:
        with self._lock:
            self._bye = self._clock()

    def woke(self) -> None:
        """PC가 잠들었다 깸 — 잠든 시간은 조용했던 것으로 치지 않는다."""
        with self._lock:
            self._last = self._clock()
            self._bye = None

    def should_exit(self, busy: bool) -> bool:
        if busy:
            return False
        now = self._clock()
        with self._lock:
            if self._bye is not None and now - self._bye >= BYE_GRACE:
                return True
            return now - self._last >= SILENT


def watch(
    lifeline: Lifeline,
    busy: Callable[[], bool],
    stop: Callable[[], None],
    *,
    running: Callable[[], bool],
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """서버가 도는 동안 `TICK`마다 보고, 끌 때가 되면 `stop()`."""
    prev = clock()
    while running():
        sleep(TICK)
        now = clock()
        if now - prev > SLEPT:
            lifeline.woke()
        prev = now
        if lifeline.should_exit(busy()):
            stop()
            return
