"""객관식·단답형 코드 채점 (PLAN.md §4-3, §2-4의 "같은 답 묶기").

- 루브릭: 해설지(없으면 시험지에서 읽은 정답)로 코드가 만든다 — AI 호출 0회. 강사가 정답을
  고치고(복수 정답·전원 정답) 승인해야 채점에 쓴다.
- 채점: 객관식은 선지 번호 비교, 단답형은 정규화한 글·sympy 값 비교. 코드로 정할 수 없는 단답만
  모아 **문항당 한 번** 가벼운 도우미에게 묻는다 (같은 답은 한 번만). 도우미가 실패해도 채점은
  끝난다 — 그 답은 '검토 필요'로 남는다.
- 서술형 소문항이 섞인 문항은 AI 채점이지만, 빈 소문항과 답이 분명한 단답형 소문항은 코드가
  먼저 정하고 나머지 항목만 AI에게 보낸다 (`pre_grade`, M4).
"""

from __future__ import annotations

import dataclasses
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from dawoai.agents import AgentError, AgentErrorCode
from dawoai.context import ContextEngine, TaskDef, TaskFailed, TaskInput
from dawoai.grading import checks
from dawoai.grading.render import exam_question_text, runs_text, scope_layer
from dawoai.mathx.sym import equivalent
from dawoai.models.answer_key import AnswerKey
from dawoai.models.base import DawoaiModel, utcnow
from dawoai.models.grading import (
    AnswerRule,
    ExamQuestion,
    GradeResult,
    ItemJudgement,
    Rubric,
    RubricItem,
    Transcript,
    TranscriptLine,
)
from dawoai.models.item import ChoiceAnswer, ItemContent, TextAnswer, ValueAnswer
from dawoai.models.scope import ScopeProfile

CODE_AGENT = "code"
"""코드로 채점한 결과의 `agent` 값 (AI 호출 없음)."""


def code_gradable(q: ExamQuestion) -> bool:
    """객관식, 단답형, 소문항이 모두 단답형인 문항."""
    return not q.needs_rubric()


def part_lines(t: Transcript, part: str) -> list[TranscriptLine]:
    """판독문에서 그 소문항 줄 (지운 줄 제외). 소문항 표시가 하나도 없으면 전부."""
    lines = [x for x in t.lines if not x.struck]
    if not part or not any(x.part for x in lines):
        return lines
    return [x for x in lines if (x.part or "") == part]


# ---------------------------------------------------------------- 객관식

_CIRCLED = "①②③④⑤⑥⑦⑧⑨"
_LONE_DIGIT = re.compile(r"(?<![\d./])([1-9])(?![\d.]|\s*[/^])")


def _plain(s: str) -> str:
    s = unicodedata.normalize("NFC", s).replace("\\(", "").replace("\\)", "").replace("$", "")
    return re.sub(r"[\s,.·]+", "", s)


def choice_labels(text: str, item: ItemContent | None = None) -> frozenset[int] | None:
    """적힌 선지 번호들 (①, 3, 3번 …). 합답형 "ㄱ, ㄷ"은 그 내용의 선지로. 없으면 None."""
    circled = [_CIRCLED.index(c) + 1 for c in text if c in _CIRCLED]
    if circled:
        return frozenset(circled)
    nums = _LONE_DIGIT.findall(text)
    if nums:
        return frozenset(int(n) for n in nums)
    want = _plain(text)
    if item is not None and want:
        hits = [c.label for c in item.choices if c.runs and _plain(runs_text(c.runs)) == want]
        if len(hits) == 1:
            return frozenset(hits)
    return None


def _choice_text(labels: frozenset[int]) -> str:
    return ", ".join(_CIRCLED[n - 1] if n <= len(_CIRCLED) else str(n) for n in sorted(labels))


def _choice_answer(a: str) -> str:
    """정답 규칙의 객관식 답 → 화면 글 ("2,4" → "②, ④")."""
    labels = choice_labels(a)
    return _choice_text(labels) if labels else a


# ---------------------------------------------------------------- 단답형

ValueVerdict = Literal["same", "different"]

_PREFIX = re.compile(r"^\s*(?:정답|답|∴|\\therefore)\s*[:：.)]?\s*")
_UNITS = (
    r"m/s\^\{?2\}?|km/h|m/s|kg|mg|km|cm|mm|nm|ms|min|kJ|kW|mV|mA|kHz|Hz|Pa|mol|mL|eV|kcal|cal"
    r"|°C|°|\^\{?\\circ\}?|\\Omega|Ω|[mgshNJWVAKLCT]"
)
_UNIT_TAIL = re.compile(
    r"(?:\\[,;: ]|\s)*(?:\\mathrm\{([^{}]+)\}|\\text\{\s*([^{}]+?)\s*\}|(" + _UNITS + r"))\s*$"
)
"""끝의 단위: \\mathrm{…}·\\text{…}, 또는 아는 단위 글자 (2R의 R처럼 모르는 글자는 변수)."""
_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d+)?|\\[dt]?frac\{\d+\}\{\d+\}|\d+/\d+)")


_BARE_FRAC = re.compile(r"\\([dt]?frac)\s*(\d)\s*(\d)")


def _clean(s: str) -> str:
    s = s.replace("\\(", " ").replace("\\)", " ").replace("$", " ").strip()
    s = _BARE_FRAC.sub(r"\\\1{\2}{\3}", s)  # \frac13 → \frac{1}{3}
    return _PREFIX.sub("", s).strip().rstrip(".")


def split_unit(s: str) -> tuple[str, str | None]:
    """ "3\\,\\mathrm{m/s}" → ("3", "m/s"). 앞이 숫자일 때만 단위로 본다 (2R의 R은 변수)."""
    m = _UNIT_TAIL.search(s)
    if not m:
        return s, None
    value = s[: m.start()].strip()
    if value.rsplit("=", 1)[-1].strip() and _NUMBER.fullmatch(
        value.rsplit("=", 1)[-1].replace(" ", "")
    ):
        unit = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        return value, unit.replace("\\Omega", "Ω") or None
    return s, None


def _lhs(latex: str) -> str:
    plain = checks.normalize_for_compare(latex)
    return plain.rsplit("=", 1)[0] if "=" in plain else ""


_SYMBOL = re.compile(r"^[㉠-㉯ㄱ-ㅎA-Za-z①-⑨()]{1,4}$")


def judge_value(
    student: str, answer: str, expr: str | None = None
) -> tuple[ValueVerdict | None, str | None]:
    """학생 단답과 정답 하나 → (same/different, 메모). 코드로 정할 수 없으면 (None, 메모).

    1. 정규화한 글이 같으면 같음
    2. 값이 같음 (1/2 = 0.5 = \\frac12) — 둘 다 좌변이 있으면 좌변도 같아야. 정답에 단위가 있고
       학생이 단위를 안 썼으면 같음 + 메모 (감점할지는 강사가)
    3. 값을 둘 다 읽었고 sympy로 분명히 다르면 다름. 짧은 기호 답(㉠, ㄷ, B)이 다르면 다름
    4. 나머지(말로 쓴 답, 단위가 다름 …)는 None — 도우미에게"""
    s, a = _clean(student), _clean(answer)
    if not s:
        return "different", None
    if checks.normalize_for_compare(s) == checks.normalize_for_compare(a):
        return "same", None
    sv, su = split_unit(s)
    av, au = split_unit(a)
    note = None
    if su and au and _plain(su) != _plain(au):
        return None, f"단위가 다름 ({su} / {au})"
    if au and not su:
        note = f"단위({au})를 쓰지 않음 — 감점할지 확인"
    ls, la = _lhs(sv), _lhs(av)
    if ls and la and ls != la:
        return None, note
    vs = checks.latex_values(sv)
    va = [expr] if expr and len(vs) == 1 else checks.latex_values(av)
    if vs and va:
        if checks.same_values(vs, va):
            return "same", note
        if all(equivalent(x, y) is not None for x in vs for y in va):
            return "different", None
        return None, note
    ns, na = _plain(s), _plain(a)
    if _SYMBOL.match(ns) and _SYMBOL.match(na):
        return "different", None
    return None, note


# ---------------------------------------------------------------- 루브릭 (AI 없음)


def _item_answer(item: ItemContent) -> tuple[list[str], str | None]:
    match item.answer:
        case ChoiceAnswer(labels=labels):
            return [str(n) for n in labels], None
        case ValueAnswer(latex=latex, expr=expr, unit=unit):
            return [f"{latex}\\,\\mathrm{{{unit}}}" if unit else latex], expr
        case TextAnswer(runs=runs):
            return [runs_text(runs)], None
        case None:
            return [], None


def _rule_text(r: AnswerRule) -> str:
    if r.all_correct:
        return "전원 정답"
    if not r.answers:
        return "(정답 없음 — 강사가 정답을 적어야 함)"
    shown = [_choice_answer(a) if r.kind == "choice" else a for a in r.answers]
    return " 또는 ".join(shown)


def rule_item(item_id: str, points: float, rule: AnswerRule) -> RubricItem:
    what = "선지 번호" if rule.kind == "choice" else "최종 답"
    return RubricItem(
        id=item_id,
        points=points,
        part=rule.part or None,
        criterion=f"{what}가 정답과 같음: {_rule_text(rule)}",
        evidence_hint=_rule_text(rule),
    )


def code_rubric(q: ExamQuestion, key: AnswerKey | None, previous: Rubric | None = None) -> Rubric:
    """객관식·단답형 문항의 루브릭을 코드로 만든다 (AI 호출 없음). 승인 전 초안."""
    kind: Literal["choice", "value"] = (
        "choice" if q.item.item_type == "multiple_choice" else "value"
    )
    slots = [(p.label, p.points) for p in q.parts] or [("", q.points)]
    items: list[RubricItem] = []
    rules: list[AnswerRule] = []
    notes: list[str] = []
    for n, (label, points) in enumerate(slots, start=1):
        kp = key.part(label) if key else None
        answers: list[str] = []
        expr: str | None = None
        if kp is not None and kp.answer:
            answers, expr = [kp.answer.strip()], kp.answer_expr
        elif not label:
            answers, expr = _item_answer(q.item)
        if kind == "choice" and answers:
            parsed = [choice_labels(a, q.item) for a in answers]
            labels = None if None in parsed else frozenset(x for p in parsed for x in p or ())
            if labels is None:
                notes.append(f"정답 '{answers[0]}'에서 선지 번호를 못 읽음 — 정답 번호를 적으세요")
                answers = []
            else:  # 해설지에 번호가 여럿이면 복수 정답(어느 것이든 정답)으로 둔다
                answers = [str(x) for x in sorted(labels)]
                if len(labels) > 1:
                    notes.append(
                        f"정답 번호가 여럿({_choice_text(labels)}) — 어느 것이든 정답으로 둡니다. "
                        "모두 써야 맞는 문항이면 정답을 고치세요"
                    )
        name = f"소문항 {label}" if label else "문항"
        kept = next(
            (r for r in previous.answer_rules if r.part == label and r.set_by), None
        ) if previous else None  # fmt: skip
        if kept is not None:  # 강사가 고친 정답(복수 정답·전원 정답)은 다시 만들어도 그대로
            rule = kept.model_copy(update={"item_id": f"R{n}", "kind": kind})
            notes.append(f"{name}: {kept.set_by}이(가) 고친 정답을 그대로 둠 ({_rule_text(rule)})")
            rules.append(rule)
            items.append(rule_item(f"R{n}", points, rule))
            continue
        if not answers:
            notes.append(f"{name}: 해설지·시험지에 정답이 없음 — 정답을 적어야 승인할 수 있어요")
        rule = AnswerRule(item_id=f"R{n}", part=label, kind=kind, answers=answers, expr=expr)
        rules.append(rule)
        items.append(rule_item(f"R{n}", points, rule))
    kind_name = "객관식" if kind == "choice" else "단답형"
    notes.append(f"{kind_name} — 코드로 채점 (AI 호출 없음). 복수 정답·전원 정답은 정답 고치기로")
    if key is not None and not key.confirmed:
        notes.append("해설지 판독을 강사가 아직 확인하지 않았습니다 — 정답을 원본과 비교하세요")
    return Rubric(
        items=items,
        question_number=q.number,
        answer_rules=rules,
        based_on_key=key is not None,
        review_notes=notes,
        version=(previous.version + 1) if previous else 1,
        agent=CODE_AGENT,
        created_at=utcnow(),
    )


def set_answers(
    rubric: Rubric,
    part: str,
    answers: list[str],
    *,
    all_correct: bool = False,
    by: str | None = None,
) -> Rubric:
    """강사가 정답을 고친다 (복수 정답, 전원 정답). 승인된 루브릭이면 새 판 — 다시 승인."""
    from dawoai.grading.pipeline import PipelineError

    rule = next((r for r in rubric.answer_rules if r.part == part), None)
    if rule is None:
        labels = [r.part or "(전체)" for r in rubric.answer_rules]
        raise PipelineError(f"코드 채점 소문항이 아닙니다: {part or '(전체)'} (있는 것: {labels})")
    answers = [a.strip() for a in answers if a.strip()]
    if rule.kind == "choice":
        bad = [a for a in answers if choice_labels(a) is None]
        if bad:
            raise PipelineError(f"선지 번호가 아닙니다: {bad} (예: 3, ③, 2,4)")
        answers = [",".join(str(x) for x in sorted(choice_labels(a) or ())) for a in answers]
    if not answers and not all_correct:
        raise PipelineError("정답을 하나 이상 적거나 전원 정답으로 하세요.")
    expr = rule.expr if answers[:1] == rule.answers[:1] else None
    update = {"answers": answers, "all_correct": all_correct, "set_by": by or "강사", "expr": expr}
    new = rule.model_copy(update=update)
    rules = [new if r is rule else r for r in rubric.answer_rules]
    points = {i.id: i.points for i in rubric.items}
    items = [rule_item(i.id, points[i.id], new) if i.id == new.item_id else i for i in rubric.items]
    bump = 1 if rubric.status == "approved" else 0
    return rubric.model_copy(
        update={"answer_rules": rules, "items": items, "status": "draft",
                "version": rubric.version + bump}
    )  # fmt: skip


def missing_answers(rubric: Rubric) -> list[str]:
    """정답도 전원 정답도 아닌 코드 채점 소문항 — 승인할 수 없다."""
    return [r.part or "(전체)" for r in rubric.answer_rules if not r.answers and not r.all_correct]


# ---------------------------------------------------------------- 애매한 단답 (가벼운 도우미)


class ShortAnswerVerdict(DawoaiModel):
    id: str = Field(description="[대상]의 답 표시 그대로 (답1, 답2 …)")
    verdict: Literal["same", "different", "unsure"] = Field(
        description="same=해설지 답과 같은 답(표기·말만 다름), different=다른 답, "
        "unsure=문제 해석에 따라 갈리거나 알아볼 수 없음"
    )
    reason: str = Field(description="한 줄 이유")


class ShortAnswerVerdicts(DawoaiModel):
    """AI 출력: 학생 단답마다 해설지 답과 같은지."""

    answers: list[ShortAnswerVerdict]


CHECK_SHORT: TaskDef[ShortAnswerVerdicts] = TaskDef(
    role="light_helper",
    name="check_short_answers",
    template="check_short_answers.md.j2",
    output=ShortAnswerVerdicts,
    rule_target="grade",
    timeout_s=300,
)


def _check_verdicts(out: ShortAnswerVerdicts, ids: list[str]) -> list[str]:
    seen = [v.id for v in out.answers]
    problems: list[str] = []
    missing = [i for i in ids if i not in seen]
    unknown = sorted({i for i in seen if i not in ids})
    dups = sorted({i for i in seen if seen.count(i) > 1})
    if missing:
        problems.append(f"{', '.join(missing)}이 빠졌다. 답마다 한 번씩 적을 것")
    if unknown:
        problems.append(f"[대상]에 없는 답 {unknown}")
    if dups:
        problems.append(f"{dups}를 두 번 이상 적었다")
    return problems


# ---------------------------------------------------------------- 채점


@dataclass
class _Mark:
    """학생 한 명 × 정답 규칙 하나의 판정 (도우미를 기다리면 `ask`에 묶음 열쇠)."""

    awarded: float
    quote: str | None
    reason: str
    deduction: str | None = None
    flag: str | None = None
    ask: tuple[str, str] | None = None


def _mark(rule: AnswerRule, points: float, q: ExamQuestion, t: Transcript) -> _Mark:
    lines = [x for x in part_lines(t, rule.part) if x.joined.strip()]
    text = " ".join(x.joined for x in lines).strip()
    if rule.all_correct:
        return _Mark(points, text or None, "전원 정답 처리")
    if not text:
        return _Mark(0, None, "답 없음", deduction="답을 쓰지 않음")
    right = _rule_text(rule)
    if rule.kind == "choice":
        got = choice_labels(text, q.item)
        if got is None:
            return _Mark(0, text, "선지 번호를 읽지 못함", deduction=f"정답은 {right}",
                         flag=f"선지 번호를 알아볼 수 없음: '{text}' — 확인")  # fmt: skip
        accepted = [choice_labels(a) for a in rule.answers]
        if got in accepted:
            return _Mark(points, text, f"정답 {_choice_text(got)}")
        return _Mark(0, text, f"{_choice_text(got)}을(를) 고름", deduction=f"정답은 {right}")
    final = lines[-1].joined.strip()
    notes: list[str] = []
    for n, a in enumerate(rule.answers):
        verdict, note = judge_value(final, a, rule.expr if n == 0 else None)
        if verdict == "same":
            return _Mark(points, final, f"정답 {a}와 같음", flag=note)
        if verdict is None:
            notes.append(note or "")
    if not notes:
        return _Mark(0, final, f"정답({right})과 다름", deduction=f"정답은 {right}")
    return _Mark(0, final, "", ask=(rule.part, checks.normalize_for_compare(final)),
                 flag=next((x for x in notes if x), None))  # fmt: skip


def grade_by_code(
    engine: ContextEngine,
    q: ExamQuestion,
    rubric: Rubric,
    transcripts: list[Transcript],
    scope: ScopeProfile,
) -> list[GradeResult]:
    """코드 채점. 정할 수 없는 단답은 (소문항, 답)으로 묶어 도우미에게 한 번에 묻는다."""
    points = {i.id: i.points for i in rubric.items}
    marks = {
        t.student_id: [(r, _mark(r, points[r.item_id], q, t)) for r in rubric.answer_rules]
        for t in transcripts
    }
    asks: dict[tuple[str, str], str] = {}  # 묶음 열쇠 → 학생이 쓴 답 (처음 나온 것)
    for row in marks.values():
        for _, m in row:
            if m.ask and m.ask not in asks:
                asks[m.ask] = m.quote or ""
    verdicts: dict[tuple[str, str], ShortAnswerVerdict] = {}
    helper: tuple[str | None, str | None] = (None, None)
    failure: str | None = None
    if asks:
        try:
            verdicts, helper = _ask_helper(engine, q, rubric, asks, scope)
        except (AgentError, TaskFailed) as e:
            if isinstance(e, AgentError) and e.code == AgentErrorCode.CANCELLED:
                raise
            failure = f"가벼운 도우미 판정 실패 ({e}) — 강사가 확인"
    out: list[GradeResult] = []
    for t in transcripts:
        judgements: list[ItemJudgement] = []
        flags: list[str] = []
        for rule, m in marks[t.student_id]:
            right = _rule_text(rule)
            conf = 1.0
            if m.ask:
                v = verdicts.get(m.ask)
                if v is not None and v.verdict == "same":
                    m.awarded = points[rule.item_id]
                    m.reason = f"정답 {right}와 같은 답: {v.reason}"
                    conf = 0.9
                elif v is not None and v.verdict == "different":
                    m.reason, m.deduction = f"정답({right})과 다름: {v.reason}", f"정답은 {right}"
                    conf = 0.9
                else:
                    m.reason = f"정답({right})과 같은지 코드로 정하지 못함"
                    m.deduction = "강사 확인 전 (0점으로 둠)"
                    m.flag = (v.reason if v else failure) or m.flag
                    conf = 0.0
                    flags.append(
                        f"{rule.part or '답'} '{m.quote}' — 정답 {right}와 같은지 확인하세요"
                    )
            if m.flag and not m.ask:
                flags.append(f"{rule.part + ' ' if rule.part else ''}{m.flag}")
            judgements.append(
                ItemJudgement(
                    rubric_item_id=rule.item_id, awarded=m.awarded, evidence_quote=m.quote,
                    reason=m.reason, deduction_reason=m.deduction, confidence=conf,
                )
            )  # fmt: skip
        used_helper = any(m.ask for _, m in marks[t.student_id])
        out.append(
            GradeResult(
                student_id=t.student_id,
                question_number=q.number,
                rubric_version=rubric.version,
                judgements=judgements,
                comment="",
                flags=flags,
                total=sum(j.awarded for j in judgements),
                max_points=rubric.total,
                status="needs_review" if flags else "auto",
                agent=(helper[0] or CODE_AGENT) if used_helper else CODE_AGENT,
                model=helper[1] if used_helper else None,
                item_points=points,
            )
        )
    return out


def _ask_helper(
    engine: ContextEngine,
    q: ExamQuestion,
    rubric: Rubric,
    asks: dict[tuple[str, str], str],
    scope: ScopeProfile,
) -> tuple[dict[tuple[str, str], ShortAnswerVerdict], tuple[str | None, str | None]]:
    labels = {f"답{n}": k for n, k in enumerate(asks, start=1)}
    rules = {r.part: r for r in rubric.answer_rules}
    material = "\n".join(
        [exam_question_text(q), "", "[해설지 답]"]
        + [f"- {('소문항 ' + r.part) if r.part else '문항'}: {_rule_text(r)}"
           for r in rubric.answer_rules]
    )  # fmt: skip
    target = "\n".join(
        f"[{label}] {('소문항 ' + part) if part else ''}: {asks[(part, norm)]}".replace(" :", ":")
        for label, (part, norm) in labels.items()
        if part in rules
    )
    ids = list(labels)

    def check(o: ShortAnswerVerdicts) -> list[str]:
        return _check_verdicts(o, ids)

    res = engine.run(
        dataclasses.replace(CHECK_SHORT, validator=check),
        TaskInput(
            variables={"level": scope.level},
            scope=scope_layer(scope).variants[-1],
            material=material,
            target=target,
        ),
    )
    by_id = {v.id: v for v in res.value.answers}
    return {labels[i]: by_id[i] for i in ids if i in by_id}, (res.agent, res.model)


# ---------------------------------------------------------------- 서술형 문항의 코드 선채점 (M4)


@dataclass
class PreGraded:
    """서술형 문항에서 AI 없이 정한 항목 (나머지 항목만 AI가 채점한다)."""

    judgements: dict[str, ItemJudgement] = dataclasses.field(
        default_factory=lambda: dict[str, ItemJudgement]()
    )
    notes: list[str] = dataclasses.field(default_factory=lambda: list[str]())
    """AI에게 알릴 말 ("소문항 (2)는 답이 없어 코드가 0점")."""


def pre_grade(q: ExamQuestion, rubric: Rubric, t: Transcript) -> PreGraded:
    """AI에게 보내기 전에 코드가 분명히 정할 수 있는 소문항을 채점한다 (PLAN.md §2-4 2번).

    - **빈 소문항**: 판독문에 소문항 표시가 있는데 그 소문항 줄이 하나도 없으면 그 항목 모두 0점.
      (표시가 아예 없는 판독문은 어느 줄이 어느 소문항인지 모르므로 하지 않는다.)
    - **단답형 소문항**: 그 소문항의 항목이 하나뿐이고 해설지 답(`checks`)이 있으면 결론 줄을
      `judge_value`로 비교 — 분명히 같으면 만점, 분명히 다르면 0점. 메모가 붙거나(단위 없음 등)
      정하지 못한 답, 강사 미확인 정답 후보가 있는 소문항은 AI에게 둔다."""
    out = PreGraded()
    if rubric.code_graded or not t.lines:
        return out
    labelled = any(x.part for x in t.lines if not x.struck)
    keys = {c.part: c.key_answer for c in rubric.checks if c.verdict != "key_doubt"}
    proposed = {c.part for c in rubric.candidates if c.status == "proposed"}
    accepted: dict[str, list[str]] = {}
    for c in rubric.candidates:
        if c.status == "accepted":
            accepted.setdefault(c.part, []).append(c.answer)
    by_part: dict[str, list[RubricItem]] = {}
    for item in rubric.items:
        if item.part:
            by_part.setdefault(item.part, []).append(item)
    for part, items in by_part.items():
        lines = [x for x in part_lines(t, part) if x.joined.strip()]
        if labelled and not lines:
            for item in items:
                out.judgements[item.id] = ItemJudgement(
                    rubric_item_id=item.id, awarded=0, evidence_quote=None,
                    reason="답 없음 (판독문에 이 소문항 줄이 없음)",
                    deduction_reason="답을 쓰지 않음", confidence=1.0,
                )  # fmt: skip
            out.notes.append(f"소문항 {part}: 답이 없어 코드가 0점 처리함")
            continue
        p = q.part(part)
        key = keys.get(part)
        if (
            p is None or p.kind != "short_answer" or len(items) != 1 or not key
            or part in proposed or not labelled or not lines
        ):  # fmt: skip
            continue
        item = items[0]
        final = lines[-1].joined.strip()
        verdicts = [judge_value(final, a) for a in [key, *accepted.get(part, [])]]
        if any(v == "same" and note is None for v, note in verdicts):
            j = ItemJudgement(
                rubric_item_id=item.id, awarded=item.points, evidence_quote=final,
                reason=f"정답 {key}와 같음 (코드 비교)", confidence=1.0,
            )  # fmt: skip
        elif all(v == "different" for v, _ in verdicts):
            j = ItemJudgement(
                rubric_item_id=item.id, awarded=0, evidence_quote=final,
                reason=f"정답({key})과 다름 (코드 비교)", deduction_reason=f"정답은 {key}",
                confidence=1.0,
            )  # fmt: skip
        else:
            continue
        out.judgements[item.id] = j
        out.notes.append(f"소문항 {part}: 단답형이라 코드가 채점함 ({j.awarded:g}점)")
    return out
