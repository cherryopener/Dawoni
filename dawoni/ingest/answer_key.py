"""해설지(모범 답안) PDF 쪽 → 문항·소문항별 답·해설·부분 점수 표기.

해설지는 시험지 뒤에 붙어 있거나 따로 온다. 수식이 글꼴 전용 문자로 들어 있는 경우가 많아
(실측: 개인 영역 문자) 쪽 이미지를 함께 넘기고, PDF 글자는 한글 문장 근거로만 쓴다.

코드 검증:
- 문항 번호는 시험에 있는 것만, 소문항은 시험의 소문항 표시 그대로 빠짐없이
- 해설의 한글이 PDF 글자에 있어야 함 (지어낸 해설 차단)
- 부분 점수 합이 배점과 다르면 **수리하지 않고** 경고 (강사가 적은 그대로 두고 알린다)
- 두 번(새 세션) 읽어 답·해설의 **숫자**를 비교 — 다르면 그 소문항을 '원본과 확인'으로 표시.
  (실측: 한글 문장은 그대로 옮기면서 "전체 전압이, … 1/2"을 "2/3, 1/3"로 바꿔 읽었다.
  수식이 글꼴 문자라 코드가 PDF 글자와 숫자를 맞춰 볼 수 없다.)
"""

from __future__ import annotations

import dataclasses
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from dawoni.context import ContextEngine, TaskDef, TaskInput
from dawoni.grading.render import blocks_text
from dawoni.ingest.grounding import hangul_missing
from dawoni.ingest.layout import render_page
from dawoni.models.answer_key import AnswerKey, KeyExtraction, KeyPart, KeyQuestion
from dawoni.models.grading import Exam, ExamQuestion

EXTRACT_KEY: TaskDef[KeyExtraction] = TaskDef(
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


def check_key(out: KeyExtraction, exam: Exam, source_text: str) -> list[str]:
    problems: list[str] = []
    numbers = {q.number for q in exam.questions}
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
        written = " ".join(
            " ".join(x for x in (p.answer or "", p.solution, p.credit_text or "", *p.zero_if) if x)
            for p in kq.parts
        )
        missing, total = hangul_missing(written, source_text)
        outside = missing / total if total else 0.0
        # 짧은 답("15m/s 서쪽")에 몇 글자 붙은 것까지 비율로 막지 않게 글자 수도 본다
        if outside > 0.15 and missing >= MIN_MISSING:
            problems.append(
                f"문항 {kq.number}: 해설에 PDF 글자에 없는 한글이 {outside:.0%} 들어갔다. "
                "해설지에 적힌 문장을 그대로 옮길 것 (요약·보충 금지)"
            )
    return problems


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
    step = max(1, pages_per_call)
    groups = [pages[i : i + step] for i in range(0, len(pages), step)]
    cache_dir = work_dir / "reads"

    def read(group: list[int]) -> tuple[list[Path], list[KeyQuestion], dict[str, list[str]]]:
        images = tuple(render_page(pdf, p, work_dir / f"key_p{p}.png", dpi=170) for p in group)
        text = page_text(pdf, group)

        def check(out: KeyExtraction, text: str = text) -> list[str]:
            if ignore_other_numbers:
                out = out.model_copy(
                    update={"questions": [q for q in out.questions if q.number in numbers]}
                )
            return check_key(out, exam, text)

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
        if raw is None:
            raw = engine.run(task, inp).value
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(raw.model_dump_json(), encoding="utf-8")
        first = [q for q in raw.questions if q.number in numbers]
        doubts: dict[str, list[str]] = {}
        if cross:
            second = {kq.number: kq for kq in engine.run(task, inp).value.questions}
            for kq in first:
                if kq.number in second:
                    doubts.setdefault(kq.number, []).extend(compare_reads(kq, second[kq.number]))
        return list(images), first, doubts

    failed: list[list[int]] = []

    def read_safe(group: list[int]) -> tuple[list[Path], list[KeyQuestion], dict[str, list[str]]]:
        try:
            return read(group)
        except TaskFailed, AgentError:
            if not partial:
                raise
            failed.append(group)
            return [], [], {}

    reads = each_in_parallel(
        groups, read_safe, name=lambda g: f"{g[0]}쪽", what="해설지 읽기", workers=workers
    )
    for images, first, doubts in reads:
        images_all += images
        for n, d in doubts.items():
            doubtful.setdefault(n, []).extend(d)
        for kq in first:
            found[kq.number] = _merge(found[kq.number], kq) if kq.number in found else kq

    source = f"{pdf.name} {', '.join(map(str, pages))}쪽"
    out = KeyIngest()
    out.warnings += [
        f"해설지 {g[0]}~{g[-1]}쪽을 읽지 못함 — 다시 하면 다시 읽습니다" for g in failed
    ]
    out.failed_pages = sorted(p for g in failed for p in g)
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
        key.warnings = _warnings(key, q) + [
            f"문항 {q.number}{label or ''}: 해설지를 두 번 읽은 결과의 숫자가 다름 "
            "— 원본과 비교하세요"
            for label in key.doubtful
        ]
        out.warnings += key.warnings
        out.keys.append(key)
    return out
