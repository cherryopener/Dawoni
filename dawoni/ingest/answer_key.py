"""해설지(모범 답안) PDF 쪽 → 문항·소문항별 답·해설·부분 점수 표기.

해설지는 시험지 뒤에 붙어 있거나 따로 온다. 수식이 글꼴 전용 문자로 들어 있는 경우가 많아
(실측: 개인 영역 문자) 쪽 이미지를 함께 넘기고, PDF 글자는 한글 문장 근거로만 쓴다.

코드 검증:
- 문항 번호는 시험에 있는 것만, 소문항은 시험의 소문항 표시 그대로 빠짐없이
- 해설의 한글이 PDF 글자에 있어야 함 (지어낸 해설 차단). 수리해도 이것만 남으면 받아들이고
  강사 확인 메모를 붙인다 (그림 속 글·깨진 PDF 글자)
- 부분 점수 합이 배점과 다르면 **수리하지 않고** 경고 (강사가 적은 그대로 두고 알린다)
- 두 번(새 세션) 읽어 답·해설의 **숫자**를 비교 — 다르면 그 소문항을 '원본과 확인'으로 표시.
  (실측: 한글 문장은 그대로 옮기면서 "전체 전압이, … 1/2"을 "2/3, 1/3"로 바꿔 읽었다.
  수식이 글꼴 문자라 코드가 PDF 글자와 숫자를 맞춰 볼 수 없다.)
"""

from __future__ import annotations

import dataclasses
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from dawoni.context import ContextEngine, TaskDef, TaskInput
from dawoni.grading.render import blocks_text
from dawoni.ingest.grounding import (
    UNGROUNDED,
    accept_ungrounded,
    broken_glyphs,
    hangul_missing,
    only_ungrounded,
    review_note,
)
from dawoni.ingest.layout import render_page
from dawoni.models.answer_key import AnswerKey, KeyExtraction, KeyPart, KeyQuestion
from dawoni.models.grading import Exam, ExamQuestion

log = logging.getLogger(__name__)

EXTRACT_KEY: TaskDef[KeyExtraction] = TaskDef(
    role="key_reader",
    name="extract_answer_key",
    template="extract_answer_key.md.j2",
    output=KeyExtraction,
    rule_target="ingest",
    timeout_s=900,
)

PAGES_PER_CALL = 3
MIN_MISSING = 6
"""해설에 PDF에 없는 한글이 이만큼은 있어야 지어낸 것으로 본다 (비율과 함께)."""


@dataclass
class KeyIngest:
    keys: list[AnswerKey] = field(default_factory=lambda: list[AnswerKey]())
    warnings: list[str] = field(default_factory=lambda: list[str]())
    failed_pages: list[int] = field(default_factory=lambda: list[int]())
    """`partial`일 때 읽지 못한 해설 쪽."""
    notes: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    """문항 번호 → 강사가 원본과 비교할 것 (PDF 글자로 확인하지 못한 해설, 모양이 다른 답)."""


def _labels(q: ExamQuestion) -> list[str]:
    return [p.label for p in q.parts] or [""]


def _question_line(q: ExamQuestion) -> str:
    stem = blocks_text(q.item.stem).replace("\n", " ")
    head = f"- 문항 번호 {q.number} ({q.points:g}점): {stem[:80]}…"
    if not q.parts:
        return head + '\n  소문항 없음 → parts에 label "" 하나'
    kinds = {"essay": "서술형", "short_answer": "단답형", None: "유형 표시 없음"}
    return (
        head
        + "\n"
        + "\n".join(f"  소문항 {p.label}: {p.points:g}점, {kinds[p.kind]}" for p in q.parts)
    )


def page_text(pdf: Path, pages: list[int]) -> str:
    with pymupdf.open(pdf) as doc:
        return "\n".join(str(doc.load_page(p - 1).get_text()) for p in pages)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]


def text_beyond_pdf(pdf: Path, pages: list[int], text: str) -> bool:
    """해설 일부가 PDF 글자 밖에 있을 수 있음 — 쪽에 그림 이미지가 있거나 글자가 깨짐."""
    if broken_glyphs(text):
        return True
    with pymupdf.open(pdf) as doc:
        return any(doc.load_page(p - 1).get_images() for p in pages)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]


_LONE_NUMBER = re.compile(r"(?m)^\s*0?(\d{2,3})\s*\.?\s*$")


def numbered_on_page(text: str, numbers: set[str]) -> set[str]:
    """해설 쪽 글에서 문항 번호만 한 줄에 있는 줄 ("63") 중 이어지는 줄기에 든 번호.

    권말 해설은 번호 한 줄 뒤에 해설이 온다 — 이 번호는 그 쪽에 해설이 있다는 뜻이다.
    두 자리 이상만 본다 (분수·식 조각 "2", "3"이 한 줄로 떨어져 나오는 일이 흔함)."""
    from dawoni.ingest.layout import number_chain

    cands = [(i, int(m.group(1))) for i, m in enumerate(_LONE_NUMBER.finditer(text))]
    keep = number_chain(cands)
    found = {str(n) for i, n in cands if i in keep}
    return found & numbers if len(keep) >= 2 else set()


_CIRCLED = re.compile(r"[①②③④⑤]")


def type_mismatch(kq: KeyQuestion, q: ExamQuestion) -> bool:
    """답의 모양이 문항 유형과 맞지 않음 — 객관식인데 선지 번호가 없거나, 아닌데 선지 번호뿐.

    해설 쪽에 같은 번호의 다른 묶음 답이 섞인 경우를 거른다 (실측: 강마다 '기본 문제' 답 목록과
    '학습 과제' 해설이 같은 번호로 이어짐)."""
    answers = [p.answer.strip() for p in kq.parts if p.answer and p.answer.strip()]
    if not answers:
        return False
    if q.item.item_type == "multiple_choice":
        # ①~⑤, 합답형 조합("ㄱ, ㄷ" — 실측: 수능 해설), 숫자 번호("3") 모두 선지 답
        choice = re.compile(r"[①②③④⑤ㄱ-ㅎ]|^\s*\(?[1-5]\)?\s*(번)?\s*$")
        return not any(choice.search(a) for a in answers)
    only_choice = re.compile(r"^[\s,()①②③④⑤]+$")
    return all(only_choice.match(a) for a in answers)


def check_key(
    out: KeyExtraction, exam: Exam, source_text: str, *, expected: set[str] | None = None
) -> list[str]:
    """`expected`: 이 쪽에 해설이 있는 것이 분명한 문항 번호 (빠뜨리면 다시)."""
    problems: list[str] = []
    numbers = {q.number for q in exam.questions}
    skipped = sorted((expected or set()) - {q.number for q in out.questions}, key=int)
    if skipped:
        problems.append(
            f"문항 {', '.join(skipped)}의 해설이 이 쪽에 있는데 빠졌다 (번호 줄이 있음) — "
            "모두 옮길 것"
        )
    for kq in out.questions:
        if kq.number not in numbers:
            problems.append(f"시험에 없는 문항 번호 {kq.number!r} — [자료]의 문항 번호만 쓸 것")
            continue
        want = _labels(exam.question(kq.number))
        got = [p.label for p in kq.parts]
        if len(got) != len(set(got)):
            problems.append(f"문항 {kq.number}: 소문항 표시 중복 {got}")
        extra = [x for x in got if x not in want]
        missing = [x for x in want if x not in got]
        if extra:
            problems.append(
                f"문항 {kq.number}: 시험에 없는 소문항 표시 {extra} "
                f"— 주어진 표시 {want} 중에서 쓸 것"
            )
        if missing:
            problems.append(
                f"문항 {kq.number}: 소문항 {missing}의 해설이 빠졌다. 해설지에 따로 없으면 "
                "solution을 비우고 credit_policy를 unspecified로 적을 것. 여러 소문항에 함께 적힌 "
                "부분 점수 표기(예: (1) 전체 '부분 점수: 없음')는 소문항마다 적을 것"
            )
        if (outside := key_ungrounded_share(kq, source_text)) is not None:
            problems.append(
                f"문항 {kq.number}: 해설에 {UNGROUNDED} {outside:.0%} 들어갔다. "
                "해설지에 적힌 문장을 그대로 옮길 것 (요약·보충 금지)"
            )
    return problems


def key_ungrounded_share(kq: KeyQuestion, source_text: str) -> float | None:
    """해설의 한글 중 PDF 글자에 없는 비율 — 15%를 넘고 MIN_MISSING자 이상일 때만."""
    # 칸(답·해설·부분 점수…)마다 따로 원본과 비교한다 — 해설 끝 문장을 답 칸에도 옮겨 적는 것은
    # 지어낸 것이 아니다 (실측: 합쳐서 세면 같은 글자가 두 번 나와 23% '없는 한글'로 잡힘)
    fields = [x for p in kq.parts for x in (p.answer or "", p.solution, p.credit_text or "",
                                            *p.zero_if) if x]  # fmt: skip
    missing = total = 0
    for f in fields:
        m, t = hangul_missing(f, source_text)
        missing, total = missing + m, total + t
    outside = missing / total if total else 0.0
    # 짧은 답("15m/s 서쪽")에 몇 글자 붙은 것까지 비율로 막지 않게 글자 수도 본다
    return outside if outside > 0.15 and missing >= MIN_MISSING else None


def _warnings(k: AnswerKey, q: ExamQuestion) -> list[str]:
    out: list[str] = []
    for p in k.parts:
        part = q.part(p.label)
        points = part.points if part else q.points
        name = f"문항 {q.number}{p.label}"
        if p.credit_policy == "listed" and abs(p.credit_total - points) > 1e-9:
            out.append(
                f"{name}: 해설지의 부분 점수 합({p.credit_total:g})이 배점({points:g})과 다름 "
                "— 루브릭은 배점에 맞추고 부분 점수 표기는 참고만 함"
            )
        if p.credit_policy == "range" and p.credit_max is not None and p.credit_max > points:
            out.append(f"{name}: 부분 점수 범위 최댓값({p.credit_max:g})이 배점({points:g})보다 큼")
        if not p.answer and not p.solution:
            out.append(f"{name}: 해설지에 해설이 없음 — AI 풀이로만 루브릭을 만듦")
    return out


_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _numbers(p: KeyPart) -> Counter[str]:
    return Counter(_NUMBER.findall(" ".join([p.answer or "", p.solution])))


def compare_reads(a: KeyQuestion, b: KeyQuestion) -> list[str]:
    """두 번 읽은 해설지에서 숫자가 다른 소문항."""
    other = {p.label: p for p in b.parts}
    out: list[str] = []
    for p in a.parts:
        q = other.get(p.label)
        if q is not None and _numbers(p) != _numbers(q):
            out.append(p.label)
    return out


def _merge(a: KeyQuestion, b: KeyQuestion) -> KeyQuestion:
    """여러 번에 나눠 읽은 같은 문항 (쪽을 넘어간 해설): 소문항별로 내용이 있는 쪽을 쓴다."""
    parts: dict[str, KeyPart] = {p.label: p for p in a.parts}
    for p in b.parts:
        old = parts.get(p.label)
        if old is None or (not old.solution and not old.answer):
            parts[p.label] = p
        elif p.solution or p.answer:
            parts[p.label] = old.model_copy(
                update={"solution": "\n".join(x for x in (old.solution, p.solution) if x)}
            )
    return KeyQuestion(number=a.number, parts=list(parts.values()), notes=a.notes + b.notes)


type _Read = tuple[list[Path], list[KeyQuestion], dict[str, list[str]], dict[str, float]]
"""쪽 묶음 하나 읽기: (쪽 이미지, 해설, 두 번 읽어 숫자가 다른 소문항, PDF에 없는 한글 비율)."""


def extract_answer_key(
    engine: ContextEngine,
    pdf: Path,
    pages: list[int],
    exam: Exam,
    work_dir: Path,
    *,
    level: str | None = None,
    cross: bool = True,
    ignore_other_numbers: bool = False,
    workers: int = 1,
    partial: bool = False,
    pages_per_call: int = PAGES_PER_CALL,
) -> KeyIngest:
    """`cross`: 새 세션에서 한 번 더 읽어 숫자를 비교한다 (읽기 1번 더).

    `ignore_other_numbers`: 해설 쪽에 다른 문항(옆 챕터)의 해설이 함께 있을 때 — 그 번호는
    수리 요청 없이 버린다. `workers`: 쪽 묶음 여러 개를 동시에 읽는다.
    `partial`: 어떤 쪽 묶음을 끝내 못 읽어도 나머지는 돌려준다 (경고로 알림)."""
    from dawoni.agents import AgentError
    from dawoni.context import TaskFailed
    from dawoni.parallel import each_in_parallel

    work_dir.mkdir(parents=True, exist_ok=True)
    found: dict[str, KeyQuestion] = {}
    doubtful: dict[str, list[str]] = {}
    images_all: list[Path] = []
    numbers = {q.number for q in exam.questions}
    dropped: list[str] = []
    odd_found: set[str] = set()
    """모양이 안 맞는데 같은 번호의 다른 답이 없어 남긴 해설 (강사 확인)."""
    ungrounded: dict[str, float] = {}

    def odd_shape(kq: KeyQuestion) -> bool:
        return type_mismatch(kq, exam.question(kq.number))

    def _fitting(qs: list[KeyQuestion]) -> list[KeyQuestion]:
        """이 시험의 번호인 해설. 유형과 모양이 안 맞는 답은 같은 번호의 맞는 답이 함께 있을 때만
        버린다 (옆 챕터·다른 묶음의 같은 번호 답). 혼자면 남기고 강사 확인으로 넘긴다.

        실측(2026-10-30): 수능 해설은 객관식 10번을 선지 번호 없이 "…경우는 b와 c 이다"로 끝낸다
        — 버린 뒤 '10번 해설이 빠졌다'고 수리 요청을 되풀이해 쪽을 잃음."""
        mine = [kq for kq in qs if kq.number in numbers]
        fits = {kq.number for kq in mine if not odd_shape(kq)}
        out: list[KeyQuestion] = []
        for kq in mine:
            if kq.number in fits and odd_shape(kq):
                dropped.append(kq.number)
                continue
            out.append(kq)
        return out

    step = max(1, pages_per_call)
    groups = [pages[i : i + step] for i in range(0, len(pages), step)]
    cache_dir = work_dir / "reads"

    def read(group: list[int]) -> _Read:
        images = tuple(render_page(pdf, p, work_dir / f"key_p{p}.png", dpi=170) for p in group)
        text = page_text(pdf, group)

        on_page = numbered_on_page(text, numbers)

        def check(out: KeyExtraction, text: str = text) -> list[str]:
            if ignore_other_numbers:
                out = out.model_copy(update={"questions": _fitting(out.questions)})
            return check_key(out, exam, text, expected=on_page)

        task = dataclasses.replace(
            EXTRACT_KEY, name=f"extract_answer_key_p{group[0]}", validator=check
        )
        inp = TaskInput(
            variables={"pages": ", ".join(map(str, group))},
            material="시험 문항:\n"
            + "\n".join(_question_line(q) for q in exam.questions)
            + (f"\n\n학생 수준: {level}" if level else "")
            + "\n\n해설지 PDF 글자 "
            "(수식은 글꼴 문자라 깨져 있을 수 있음 — 수식은 이미지로 읽을 것):\n" + text,
            attachments=images,
        )
        cached = cache_dir / f"p{group[0]}-{group[-1]}.json"
        raw: KeyExtraction | None = None
        if not cross and cached.is_file():  # 다시 할 때: 읽은 쪽은 AI 없이
            try:
                raw = KeyExtraction.model_validate_json(cached.read_text(encoding="utf-8"))
            except ValueError:
                raw = None
            if raw is not None and (problems := check(raw)) and not only_ungrounded(problems):
                raw = None  # 검사가 강해졌으면 다시 읽는다 (예: 빠뜨린 문항)
        if raw is None:
            try:
                raw = engine.run(task, inp).value
            except TaskFailed as e:
                accepted = accept_ungrounded(e, KeyExtraction)
                if accepted is None or not text_beyond_pdf(pdf, group, text):
                    raise
                log.warning("해설 %s쪽: 근거 검사만 남아 받아들임 (강사 확인): %s", group,
                            e.errors[:3])  # fmt: skip
                raw = accepted
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(raw.model_dump_json(), encoding="utf-8")
        first = _fitting(raw.questions) if ignore_other_numbers else (
            [q for q in raw.questions if q.number in numbers]
        )  # fmt: skip
        doubts: dict[str, list[str]] = {}
        if cross:
            second = {kq.number: kq for kq in engine.run(task, inp).value.questions}
            for kq in first:
                if kq.number in second:
                    doubts.setdefault(kq.number, []).extend(compare_reads(kq, second[kq.number]))
        shares = {kq.number: s for kq in first if (s := key_ungrounded_share(kq, text)) is not None}
        return list(images), first, doubts, shares

    failed: list[list[int]] = []

    def read_safe(group: list[int]) -> _Read:
        try:
            return read(group)
        except TaskFailed, AgentError:
            if not partial:
                raise
            failed.append(group)
            return [], [], {}, {}

    reads = each_in_parallel(
        groups, read_safe, name=lambda g: f"{g[0]}쪽", what="해설지 읽기", workers=workers
    )
    for images, first, doubts, shares in reads:
        images_all += images
        for n, d in doubts.items():
            doubtful.setdefault(n, []).extend(d)
        for n, s in shares.items():
            ungrounded[n] = max(s, ungrounded.get(n, 0.0))
        for kq in first:
            n = kq.number
            odd = ignore_other_numbers and odd_shape(kq)
            if n in found and odd != (n in odd_found):
                # 쪽마다 따로 읽어 한쪽만 모양이 맞음 → 맞는 쪽을 쓴다
                dropped.append(n)
                if not odd:
                    found[n] = kq
                    odd_found.discard(n)
                continue
            found[n] = _merge(found[n], kq) if n in found else kq
            if odd:
                odd_found.add(n)

    source = f"{pdf.name} {', '.join(map(str, pages))}쪽"
    out = KeyIngest()
    out.warnings += [
        f"해설지 {g[0]}~{g[-1]}쪽을 읽지 못함 — 다시 하면 다시 읽습니다" for g in failed
    ]
    out.failed_pages = sorted(p for g in failed for p in g)
    if dropped:
        nums = ", ".join(sorted(set(dropped), key=lambda x: int(x) if x.isdigit() else 0))
        out.warnings.append(
            f"문항 {nums}: 문항 유형과 맞지 않는 답(다른 묶음의 해설로 보임)은 뺐습니다"
        )
    for q in exam.questions:
        kq = found.get(q.number)
        if kq is None:
            out.warnings.append(f"문항 {q.number}: 해설지에서 찾지 못함")
            continue
        order = {label: i for i, label in enumerate(_labels(q))}
        key = AnswerKey(
            question_number=q.number,
            parts=sorted(kq.parts, key=lambda p: order.get(p.label, 99)),
            source=source,
            pages=[img.relative_to(work_dir.parent).as_posix() for img in images_all],
            notes=kq.notes,
            doubtful=sorted(set(doubtful.get(q.number, [])), key=lambda x: order.get(x, 99)),
        )
        notes: list[str] = []
        if q.number in odd_found:
            notes.append(
                "해설: 문항 유형과 답의 모양이 다름(객관식인데 선지 번호가 없는 등) — 다른 묶음의 "
                "해설이 아닌지 원본과 확인하세요"
            )
        if q.number in ungrounded:
            notes.append(f"해설: {review_note(ungrounded[q.number])}")
        if notes:
            out.notes[q.number] = " / ".join(notes)
        key.warnings = (
            _warnings(key, q)
            + [f"문항 {q.number}: {n}" for n in notes]
            + [
                f"문항 {q.number}{label or ''}: 해설지를 두 번 읽은 결과의 숫자가 다름 "
                "— 원본과 비교하세요"
                for label in key.doubtful
            ]
        )
        out.warnings += key.warnings
        out.keys.append(key)
    return out
