"""앱 창을 닫으면 서버도 끝난다 (dawoni/server/lifeline.py)."""

from __future__ import annotations

from dawoni.server.lifeline import BYE_GRACE, SILENT, SLEPT, TICK, Lifeline, watch


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_closed_window_stops_after_grace_but_reload_does_not() -> None:
    c = Clock()
    life = Lifeline(c)
    life.closed()
    c.t += BYE_GRACE - 1
    assert not life.should_exit(False)
    life.alive()  # 새로 고침: 곧 다시 살아 있다고 알림
    c.t += BYE_GRACE + 1
    assert not life.should_exit(False)
    life.closed()
    c.t += BYE_GRACE
    assert life.should_exit(False)
    assert not life.should_exit(True), "작업이 돌고 있으면 끝날 때까지 기다린다"


def test_silence_stops_but_sleep_does_not() -> None:
    c = Clock()
    life = Lifeline(c)
    c.t += SILENT - 1
    assert not life.should_exit(False)
    c.t += 1
    assert life.should_exit(False)

    # 감시 고리: PC가 잠들었다 깨면(간격이 SLEPT보다 큼) 조용했던 것으로 치지 않는다
    c2 = Clock()
    life2 = Lifeline(c2)
    stopped: list[bool] = []
    ticks = iter([SILENT + SLEPT, TICK, TICK])

    def sleep(_: float) -> None:
        try:
            c2.t += next(ticks)
        except StopIteration:
            c2.t += TICK  # 깬 뒤로 정말 조용함 → SILENT가 지나면 끝냄

    woke_at = 1000 + SILENT + SLEPT
    watch(life2, lambda: False, lambda: stopped.append(True), running=lambda: not stopped,
          clock=c2, sleep=sleep)  # fmt: skip
    assert stopped and woke_at + SILENT <= c2.t < woke_at + SILENT + TICK
