"""총괄 AI (PLAN.md §2-1): 강사의 자연어 부탁 → 정해진 작업 하나, 또는 실패 진단.

총괄 흐름은 코드다 — AI는 부탁을 작업 이름·인자로 바꾸기만 하고, 학생 답안·해설지 원문은 보지
않는다(시험 이름·문항 번호·반 이름과 최근 실패 기록 요약만). 코드가 ID·문항·반이 실제로 있는지
검사하고(없으면 수리), 강사가 "실행"을 눌러야 작업이 돈다.
"""

from __future__ import annotations

import dataclasses
from typing import Literal

from pydantic import Field

from dawoni.config import AgentName
from dawoni.context.engine import TaskInput
from dawoni.context.tasks import TaskDef
from dawoni.grading.store import ExamStore
from dawoni.models.base import DawoniModel
from dawoni.services import Services

Action = Literal["grade", "rubric", "confirm_files", "read_marks", "export", "scores", "open"]
Screen = Literal["read", "rubric", "scan", "grade", "export", "students", "learning", "settings"]
EXAM_SCREENS = ("read", "rubric", "scan", "grade", "export")


class CoordinatorPlan(DawoniModel):
    kind: Literal["action", "diagnose", "unknown"]
    action: Action | None = Field(default=None, description="kind가 action일 때 작업 이름")
    exam_id: str | None = Field(default=None, description="[시험]의 ID")
    questions: list[str] = Field(default_factory=lambda: list[str](), description="문항 번호")
    classes: list[str] = Field(default_factory=lambda: list[str](), description="반 이름")
    students: list[str] = Field(default_factory=lambda: list[str](), description="학생 이름")
    regrade: bool = False
    by_class: bool | None = None
    screen: Screen | None = None
    summary: str = Field(description="강사에게 보여 줄 한 문장 (한국어)")
    cause: str | None = Field(default=None, description="diagnose: 무슨 일이 있었나")
    fix: str | None = Field(default=None, description="diagnose: 무엇을 하면 되나")


COORDINATE: TaskDef[CoordinatorPlan] = TaskDef(
    role="coordinator",
    name="coordinate",
    template="coordinate.md.j2",
    output=CoordinatorPlan,
    rule_target=None,
    timeout_s=180,
)


@dataclasses.dataclass
class ExamBrief:
    id: str
    title: str
    questions: list[str]
    classes: list[str]


def exam_briefs(svc: Services) -> list[ExamBrief]:
    out: list[ExamBrief] = []
    for eid in reversed(ExamStore.list_ids(svc.workspace)):
        store = ExamStore(svc.workspace, eid)
        exam = store.exam()
        classes = sorted({r.class_name for r in store.student_records() if r.class_name})
        out.append(ExamBrief(exam.id, exam.title, [q.number for q in exam.questions], classes))
    return out


def check_plan(plan: CoordinatorPlan, exams: list[ExamBrief]) -> list[str]:
    """AI가 고른 ID·문항·반이 정말 있는지 (없는 것을 지어내면 수리)."""
    problems: list[str] = []
    by_id = {e.id: e for e in exams}
    if plan.kind != "action":
        return problems
    if plan.action is None:
        return ["kind가 action이면 action을 적을 것"]
    needs_exam = plan.action != "open" or plan.screen in EXAM_SCREENS
    if plan.action == "open" and plan.screen is None:
        problems.append("open에는 screen을 적을 것")
    if needs_exam:
        e = by_id.get(plan.exam_id or "")
        if e is None:
            problems.append(f"exam_id {plan.exam_id!r}는 [시험]에 없다 — 목록의 ID만 쓸 것")
        else:
            bad_q = [q for q in plan.questions if q not in e.questions]
            if bad_q:
                problems.append(f"문항 {bad_q}는 그 시험에 없다 (있는 것: {e.questions})")
            bad_c = [c for c in plan.classes if c not in e.classes]
            if bad_c:
                problems.append(f"반 {bad_c}는 그 시험에 없다 (있는 것: {e.classes})")
    return problems


def resolve_students(store: ExamStore, plan: CoordinatorPlan) -> list[str] | None:
    """반·이름 → 학생 ID (둘 다 비면 None = 모두). 띄어쓰기는 무시한다."""
    if not plan.classes and not plan.students:
        return None
    names = {n.replace(" ", "") for n in plan.students}
    out: list[str] = []
    for r in store.student_records():
        if plan.classes and r.class_name not in plan.classes:
            continue
        if names and (r.name or "").replace(" ", "") not in names:
            continue
        out.append(r.student_id)
    return out


def plan_request(
    svc: Services,
    request: str,
    *,
    current: str | None,
    failures: list[str],
    agent: AgentName | None = None,
) -> CoordinatorPlan:
    exams = exam_briefs(svc)

    def check(p: CoordinatorPlan) -> list[str]:
        return check_plan(p, exams)

    task = dataclasses.replace(COORDINATE, validator=check)
    res = svc.engine.run(
        task,
        TaskInput(
            variables={
                "request": request,
                "current": current,
                "exams": [dataclasses.asdict(e) for e in exams[:20]],
                "failures": failures[:12],
            }
        ),
        agent=agent,
    )
    return res.value
