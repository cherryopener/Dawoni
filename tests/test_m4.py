"""M4 채점 · 나눠 맡기: 동시 실행·한도 신호, 한도에서 멈춤, 문항 맡기, 진행판."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from test_grading import GRADE_A, RUBRIC_OK, approved_rubric
from tests_support import fake_prompts

from dawoai.agents.base import RATE_LIMIT_WARNING, AgentError, AgentErrorCode
from dawoai.agents.claude import ClaudeAdapter
from dawoai.config import AppConfig, save_config
from dawoai.fsio import read_json
from dawoai.grading import assign, workflow
from dawoai.grading.store import ExamStore, ScopeStore
from dawoai.models.grading import Exam, ExamQuestion, Transcript, TranscriptLine
from dawoai.models.scope import ScopeProfile
from dawoai.parallel import Throttle, each_in_parallel
from dawoai.paths import AppPaths
from dawoai.services import Services, open_services
from dawoai.workspace import create_workspace

FIX = Path(__file__).parent / "fixtures" / "grading"


def test_throttle_slows_down_on_rate_signal() -> None:
    """한도 신호마다 동시 호출을 하나씩 줄이고, 1 밑으로는 줄이지 않는다."""
    from dawoai.parallel import RATE_SIGNAL

    th = Throttle(3)
    peak = 0
    now = 0
    lock = threading.Lock()

    def fn(n: int) -> int:
        nonlocal peak, now
        with lock:
            now += 1
            peak = max(peak, now)
        if n == 0:
            signal = RATE_SIGNAL.get()
            assert signal is not None
            signal()
            signal()
            signal()
        time.sleep(0.05)
        with lock:
            now -= 1
        return n

    assert each_in_parallel(list(range(8)), fn, name=str, what="채점", workers=3, throttle=th) == (
        list(range(8))
    )
    assert th.limit == 1 and th.signals == 3 and peak <= 3


def test_quota_stops_remaining_jobs() -> None:
    """한도 초과는 한 명씩 실패로 쌓지 않고 남은 것을 시작하지 않은 채 그 오류로 멈춘다."""
    started: list[int] = []

    def fn(n: int) -> int:
        started.append(n)
        if n == 1:
            raise AgentError(AgentErrorCode.QUOTA, "429")
        return n

    with pytest.raises(AgentError) as e:
        each_in_parallel(list(range(10)), fn, name=str, what="채점", workers=1)
    assert e.value.code == AgentErrorCode.QUOTA and started == [0, 1]


def test_claude_rate_limit_event_becomes_warning() -> None:
    a = ClaudeAdapter()
    p = a.parse_line(json.dumps({"type": "rate_limit_event",
                                 "rate_limit_info": {"status": "allowed_warning"}}))  # fmt: skip
    assert p.rate_limited and p.progress == RATE_LIMIT_WARNING
    assert not a.parse_line(
        json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}})
    ).rate_limited


# ---------------------------------------------------------------- 문항 맡기


def _services(
    app_paths: AppPaths, tmp_path: Path, exe: Path, *, workers: int = 1
) -> tuple[Services, ExamStore]:
    cfg = AppConfig(parallel_grading=workers)
    cfg.agents["agy"].executable = str(exe)
    cfg.agents["claude"].enabled = False
    cfg.agents["codex"].enabled = False
    save_config(app_paths, cfg)
    ws = create_workspace(tmp_path / "ws")
    scope = ScopeProfile.model_validate(read_json(FIX / "scope.json"))
    ScopeStore(ws).save(scope)
    q = ExamQuestion.model_validate(read_json(FIX / "questions.json")[0])
    store = ExamStore.create(ws, Exam(title="M4", scope_id=scope.id, questions=[q]))
    store.save_rubric(approved_rubric(RUBRIC_OK))
    for s in ("가", "나", "다", "라"):
        store.save_transcript(
            Transcript(
                student_id=s,
                question_number="1",
                confirmed=True,
                lines=[
                    TranscriptLine(text="식: y=x^2-4x+7=(x-2)^2+3"),
                    TranscriptLine(text="따라서 x=2일 때 최솟값은 3이다."),
                ],
            )
        )
    return open_services(ws.root, paths=app_paths), store


def _fake(make_fake: Any, n: int) -> Path:
    return make_fake("script", script=[{"response": json.dumps(GRADE_A, ensure_ascii=False)}] * n)


def test_assign_take_release_and_board(app_paths: AppPaths, tmp_path: Path, make_fake: Any) -> None:
    svc, store = _services(app_paths, tmp_path, _fake(make_fake, 0))
    a = assign.assign(store, "1", "김선생")
    assert a.teacher == "김선생" and [e.action for e in a.history] == ["assign"]
    with pytest.raises(assign.AssignError, match="가져오기"):
        assign.assign(store, "1", "이선생")
    a = assign.assign(store, "1", "이선생", take=True)
    assert a.teacher == "이선생" and a.history[-1].previous == "김선생", "넘겨받은 기록"
    assert assign.locked_for(store, "1", "김선생") == "이선생"
    assert assign.locked_for(store, "1", None) is None, "이름 없이(혼자 쓰는 PC)는 잠그지 않음"
    with pytest.raises(assign.AssignError):
        assign.release(store, "1", "김선생")
    assert assign.release(store, "1", "이선생").teacher is None
    (b,) = assign.board(store)
    assert (b.students, b.ungraded, b.graded, b.teacher) == (4, 4, 0, None)
    del svc


def test_grade_exam_parallel_respects_assignment(
    app_paths: AppPaths, tmp_path: Path, make_fake: Any
) -> None:
    exe = _fake(make_fake, 4)
    svc, store = _services(app_paths, tmp_path, exe, workers=3)
    assign.assign(store, "1", "김선생")
    run = workflow.grade_exam(svc, store, by="이선생")
    assert not run.graded and len(run.skipped) == 4 and not fake_prompts(exe)
    assert "김선생 선생님이 맡은 문항" in run.skipped[0].reason
    run = workflow.grade_exam(svc, store, by="김선생")
    assert sorted(g.student_id for g in run.graded) == ["가", "나", "다", "라"]
    assert run.calls == 4 and run.repairs == 0 and run.workers == 3
    assert all(g.total == 4 for g in run.graded)
    (b,) = assign.board(store)
    assert (b.graded, b.ungraded, b.teacher) == (4, 0, "김선생")


def test_learning_from_hwpx_confirm_collects_changed_lines() -> None:
    """한글에서 고쳐 확인 완료한 판독본들은 고친 줄만 모아 배우기 한 번으로 (M3에서 남긴 것)."""
    from dawoai.server.learning import confirmed_files_correction

    def t(s: str, text: str) -> Transcript:
        lines = [
            TranscriptLine(part="(1)", text="전류는 같다"),
            TranscriptLine(part="(1)", text=text),
        ]
        return Transcript(student_id=s, question_number="2", lines=lines)

    c = confirmed_files_correction(
        [(t("가", "저항이 적은 ㉢이 밝아"), t("가", "저항이 적은 ㉠이 밝아")),
         (t("나", "그대로"), t("나", "그대로"))],
        "10월 시험",
    )  # fmt: skip
    assert c.target == "transcribe" and "[가 문항 2]" in c.after and "나 문항" not in c.after
    assert "㉠" in c.after and "㉢" in c.before and "전류는 같다" not in c.after
