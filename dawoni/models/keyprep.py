"""0단계 해설 다듬기 (2026-11-18 사용자 요청): 시험지·해설 HWP/HWPX → AI가 해설을 보며 풀고
부분 점수 기준을 촘촘하게 → 강사가 고르고 고침 → 다듬은 HWPX.

부분 점수 기준은 줄(`PrepLine`)의 목록이다. 줄마다 어디서 왔는지(`source`)가 붙는다:
- key: 강사 해설지에 있던 기준 (그대로 옮김)
- ai: AI 추천 — 해설지와 어긋나지 않고 그대로 받아들일 만한 것. 바로 들어간다
- ask: 넣어도 될지 분명하지 않은 것 — 강사가 '넣기'를 고르기 전에는 들어가지 않는다
- fill: AI가 정할 수 없어 강사가 적어야 하는 기준 — 빈칸(□)을 채우기 전에는 HWPX를 받을 수 없다
  (그림 답의 값을 해설 그림에서도 풀이로도 정할 수 없을 때, 2026-11-18 사용자 요청)
- teacher: 강사가 직접 쓴 줄
모든 줄은 강사가 고치거나 뺄 수 있다.

줄 종류(`kind`): item(채점 요소, 점수를 더함) · level(바로 앞 item의 낮은 단계 — 그중 하나만 받음) ·
accept(같은 점수로 인정하는 다른 답·표현·풀이) · deduct(감점·0점 조건) · note(그 밖의 안내).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from dawoni.models.base import DawoniModel, utcnow

LineKind = Literal["item", "level", "accept", "deduct", "note"]
LineSource = Literal["key", "ai", "ask", "fill"]
LineOrigin = Literal["key", "ai", "ask", "fill", "teacher"]
Agree = Literal["same", "different", "unsure"]
Decision = Literal["in", "out"]

POINTED: tuple[LineKind, ...] = ("item", "level")
"""점수가 붙는 줄."""
BLANK = "□"
"""강사가 적을 빈칸 (fill 줄)."""

KIND_LABELS: dict[LineKind, str] = {
    "item": "채점 요소",
    "level": "낮은 단계",
    "accept": "인정",
    "deduct": "감점",
    "note": "안내",
}


# ---------------------------------------------------------------- AI 출력


class RefineLine(DawoniModel):
    kind: LineKind = Field(
        description="item=채점 요소(점수를 더함), "
        "level=바로 앞 item의 낮은 단계(그중 하나만 받음), "
        "accept=같은 점수로 인정하는 다른 답·표현·풀이, deduct=감점·0점 조건, note=그 밖의 안내"
    )
    text: str = Field(
        min_length=1,
        description="해설지에 들어갈 문장 (한국어, 식은 \\( \\)로 감쌈). "
        "점수는 쓰지 않는다 — points에",
    )
    points: float | None = Field(
        default=None, description="item·level만: 이 줄이 주는 점수. 나머지는 null"
    )
    source: LineSource = Field(
        description="key=해설지에 있던 기준, ai=추천(그대로 넣음), ask=넣어도 될지 강사에게 물음, "
        "fill=정할 수 없는 값 — text의 빈칸 □를 강사가 채운다"
    )
    why: str | None = Field(
        default=None,
        description="ai·ask·fill: 왜 넣는지·왜 정할 수 없는지 한 문장 (강사에게만 보이고 파일에는 "
        "안 씀)",
    )
    question: str | None = Field(
        default=None,
        description="ask: 강사에게 물을 한 문장 (넣을지 말지로 답할 수 있게). "
        "fill: 강사가 □에 무엇을 적을지 (예: 'y절편 값을 적어 주세요')",
    )


class RefinePart(DawoniModel):
    label: str = Field(description="소문항 표시 그대로 (예: 1), (2)). 소문항이 없으면 빈 문자열")
    points: float = Field(gt=0, description="소문항 배점 (소문항이 없으면 문항 배점)")
    answer: str | None = Field(
        default=None, description="최종 답 (해설지 것 — 없으면 직접 푼 답, 식은 \\( \\))"
    )
    no_partial: bool = Field(
        description="해설지가 이 소문항에 '부분점수 없음'이라고 했으면 true "
        "(맞으면 만점, 아니면 0점)"
    )
    lines: list[RefineLine] = Field(default_factory=lambda: list[RefineLine]())


class SolveCheck(DawoniModel):
    """해설지를 따라 풀어 본 결과 — 해설지 답을 의심할 곳을 강사에게 알린다."""

    label: str = Field(description="소문항 표시 (없으면 빈 문자열)")
    answer: str = Field(description="해설지를 따라 풀어 확인한 답 (식은 \\( \\))")
    agrees: Agree = Field(description="해설지 답과 같은가 (해설지가 없으면 unsure)")
    note: str | None = Field(default=None, description="다르거나 확실하지 않으면 이유 한 문장")


class KeyRefineOutput(DawoniModel):
    """AI 출력: 문항 하나의 다듬은 부분 점수 기준."""

    checks: list[SolveCheck] = Field(default_factory=lambda: list[SolveCheck]())
    explanation: str | None = Field(
        default=None,
        description="해설지에 풀이가 없거나 답만 있을 때만: 직접 푼 해설 (줄마다 한 단계, 식은 "
        "\\( \\)). 해설지에 풀이가 있으면 null",
    )
    replace: list[int] = Field(
        default_factory=lambda: list[int](),
        description="새 기준으로 바꿀 해설지 문단 번호(¶) — 원래 부분 점수·채점 기준 문단만",
    )
    parts: list[RefinePart] = Field(min_length=1)


# ---------------------------------------------------------------- 저장 (강사가 고침)


class PrepLine(DawoniModel):
    id: str
    kind: LineKind
    text: str
    points: float | None = None
    source: LineOrigin
    why: str | None = None
    question: str | None = None
    decision: Decision | None = None
    """강사가 정한 것. None이면 ask는 아직 안 정함(넣지 않음), 나머지는 넣음."""

    @property
    def included(self) -> bool:
        if self.decision is not None:
            return self.decision == "in"
        return self.source != "ask"

    @property
    def pending(self) -> bool:
        """답하지 않은 질문."""
        return self.source == "ask" and self.decision is None

    @property
    def blank(self) -> bool:
        """강사가 아직 채우지 않은 빈칸(□)이 있는 줄 — 빼지 않았으면 HWPX를 받을 수 없다."""
        return self.included and BLANK in self.text


class PrepPart(DawoniModel):
    label: str = ""
    points: float = Field(gt=0)
    answer: str | None = None
    no_partial: bool = False
    lines: list[PrepLine] = Field(default_factory=lambda: list[PrepLine]())


QuestionStatus = Literal["waiting", "done", "failed"]


class PrepQuestion(DawoniModel):
    number: str
    points: float | None = None
    """문항 배점: 시험지에 적힌 것, 없으면 ⌊100 ÷ 문항 수⌋ (`points_guessed`)."""
    points_guessed: bool = False
    """시험지에 배점 표시가 없어 1단계와 같은 규칙(`ingest.exam.suggested_points`)으로 정함."""
    text: str = ""
    """문항 글 (보기용, 식은 \\( \\))."""
    figures: list[str] = Field(default_factory=lambda: list[str]())
    """문항 그림 (초안 폴더 기준 경로)."""
    key_paragraphs: list[str] = Field(default_factory=lambda: list[str]())
    """원래 해설 문단 (¶1부터 차례로)."""
    key_figures: list[str] = Field(default_factory=lambda: list[str]())
    status: QuestionStatus = "waiting"
    error: str | None = None
    checks: list[SolveCheck] = Field(default_factory=lambda: list[SolveCheck]())
    explanation: str | None = None
    """AI가 쓴 해설 (해설지에 풀이가 없을 때)."""
    explanation_in: bool = True
    replace: list[int] = Field(default_factory=lambda: list[int]())
    """바꿀 원래 해설 문단 (¶ 번호, 1부터) — 원래 부분 점수 기준."""
    parts: list[PrepPart] = Field(default_factory=lambda: list[PrepPart]())
    agent: str | None = None
    model: str | None = None
    edited_by: str | None = None
    cases: list[str] = Field(default_factory=lambda: list[str]())
    """AI에게 참고로 준 지난 채점 사례 (제목, `dawoni.casebook`)."""

    @property
    def has_key(self) -> bool:
        return any(p.strip() for p in self.key_paragraphs)

    @property
    def pending(self) -> int:
        return sum(ln.pending for p in self.parts for ln in p.lines)

    @property
    def blanks(self) -> int:
        return sum(ln.blank for p in self.parts for ln in p.lines)


PrepState = Literal["running", "done", "failed"]


class KeyPrep(DawoniModel):
    id: str
    title: str
    source_name: str
    """강사가 넣은 파일 이름."""
    level: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    state: PrepState = "running"
    error: str | None = None
    questions: list[PrepQuestion] = Field(default_factory=lambda: list[PrepQuestion]())
    warnings: list[str] = Field(default_factory=lambda: list[str]())
    exported: str | None = None
    """마지막으로 쓴 HWPX (작업공간 기준 경로)."""
    exported_at: datetime | None = None

    def question(self, number: str) -> PrepQuestion:
        q = next((q for q in self.questions if q.number == number), None)
        if q is None:
            raise KeyError(f"{number}번 문항이 없습니다")
        return q
