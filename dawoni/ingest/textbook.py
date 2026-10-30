"""교재 PDF → 범위 프로필 초안 (강사가 검토·편집).

쪽 묶음마다 AI가 개념·기법 후보를 뽑음 → 근거 검사(교재 글자에 실제로 있는가)
→ 코드가 이름 기준으로 합치고 ID(C1, S1 …)를 매김
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import Field

from dawoni.context import ContextEngine, TaskDef, TaskInput
from dawoni.ingest.grounding import hangul_outside
from dawoni.models.base import DawoniModel
from dawoni.models.scope import Concept, ForbiddenTechnique, ScopeProfile, Skill

CHUNK_CHARS = 6000


class ConceptCandidate(DawoniModel):
    name: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    page: int = Field(ge=1)


class SkillCandidate(DawoniModel):
    name: str = Field(min_length=1)
    description: str
    concept_names: list[str] = Field(default_factory=lambda: list[str]())
    page: int = Field(ge=1)


class ForbiddenSuggestion(DawoniModel):
    name: str
    reason: str


class ChapterExtraction(DawoniModel):
    concepts: list[ConceptCandidate] = Field(default_factory=lambda: list[ConceptCandidate]())
    skills: list[SkillCandidate] = Field(default_factory=lambda: list[SkillCandidate]())
    forbidden_suggestions: list[ForbiddenSuggestion] = Field(
        default_factory=lambda: list[ForbiddenSuggestion]()
    )
    conventions: list[str] = Field(default_factory=lambda: list[str]())


EXTRACT_CONCEPTS: TaskDef[ChapterExtraction] = TaskDef(
    role="concept_extractor",
    name="extract_concepts",
    template="extract_concepts.md.j2",
    output=ChapterExtraction,
    rule_target="ingest",
    timeout_s=900,
)


def _key(name: str) -> str:
    return re.sub(r"[\s·・.,()\[\]]", "", name).lower()


def page_texts(path: Path, pages: list[int] | None = None) -> dict[int, str]:
    from dawoni.ingest.layout import analyze

    layout = analyze(path)
    out: dict[int, str] = {}
    for p in layout.pages:
        if pages and p.number not in pages:
            continue
        out[p.number] = "\n".join(
            line.text for line in sorted(p.lines, key=lambda x: (x.rect.y0, x.rect.x0))
        )
    return out


def chunks(texts: dict[int, str], limit: int = CHUNK_CHARS) -> list[dict[int, str]]:
    out: list[dict[int, str]] = []
    cur: dict[int, str] = {}
    size = 0
    for n in sorted(texts):
        t = texts[n]
        if cur and size + len(t) > limit:
            out.append(cur)
            cur, size = {}, 0
        cur[n] = t
        size += len(t)
    if cur:
        out.append(cur)
    return out


def check_extraction(d: ChapterExtraction, chunk: dict[int, str], known: set[str]) -> list[str]:
    problems: list[str] = []
    all_text = "\n".join(chunk.values())
    names = {_key(c.name) for c in d.concepts} | {_key(k) for k in known}
    for c in d.concepts:
        if c.page not in chunk:
            problems.append(
                f"개념 '{c.name}'의 page {c.page}는 [자료]의 쪽 {sorted(chunk)}이 아니다"
            )
            continue
        if hangul_outside(c.name, chunk[c.page]) > 0.4:
            problems.append(f"개념 이름 '{c.name}'이 {c.page}쪽 글에 없다. 교재의 표현을 쓸 것")
        if hangul_outside(c.summary, all_text) > 0.3:
            problems.append(f"개념 '{c.name}'의 요약에 교재에 없는 내용이 많다. 교재 내용만 쓸 것")
    for s in d.skills:
        if s.page not in chunk:
            problems.append(f"기법 '{s.name}'의 page {s.page}는 [자료]의 쪽이 아니다")
        bad = [n for n in s.concept_names if _key(n) not in names]
        if bad:
            problems.append(f"기법 '{s.name}'이 없는 개념 이름 {bad}를 참조한다")
    return problems


@dataclass
class ScopeDraft:
    scope: ScopeProfile
    warnings: list[str] = field(default_factory=lambda: list[str]())


def build_scope(
    parts: list[ChapterExtraction], *, title: str, level: str, subject: str | None
) -> ScopeDraft:
    """후보들을 이름 기준으로 합치고 ID를 매긴다 (결정적)."""
    warnings: list[str] = []
    concepts: list[Concept] = []
    by_key: dict[str, str] = {}
    for part in parts:
        for c in part.concepts:
            k = _key(c.name)
            if k in by_key:
                continue
            cid = f"C{len(concepts) + 1}"
            by_key[k] = cid
            concepts.append(Concept(id=cid, name=c.name, summary=c.summary, source=f"p.{c.page}"))
    skills: list[Skill] = []
    seen_skills: set[str] = set()
    for part in parts:
        for s in part.skills:
            k = _key(s.name)
            if k in seen_skills:
                continue
            seen_skills.add(k)
            ids = [by_key[_key(n)] for n in s.concept_names if _key(n) in by_key]
            dropped = [n for n in s.concept_names if _key(n) not in by_key]
            if dropped:
                warnings.append(f"기법 '{s.name}': 알 수 없는 개념 {dropped} 제외")
            skills.append(
                Skill(
                    id=f"S{len(skills) + 1}",
                    name=s.name,
                    description=s.description,
                    concept_ids=ids,
                )
            )
    forbidden: list[ForbiddenTechnique] = []
    for part in parts:
        for f in part.forbidden_suggestions:
            if all(_key(f.name) != _key(x.name) for x in forbidden):
                forbidden.append(ForbiddenTechnique(name=f.name, reason=f.reason))
    conventions = list(dict.fromkeys(c for part in parts for c in part.conventions))
    if not concepts:
        raise ValueError("교재에서 개념을 찾지 못했습니다.")
    scope = ScopeProfile(
        title=title,
        subject=subject,
        level=level,
        concepts=concepts,
        skills=skills,
        forbidden=forbidden,
        conventions=conventions,
    )
    return ScopeDraft(scope=scope, warnings=warnings)


def extract_scope(
    engine: ContextEngine,
    path: Path,
    *,
    title: str,
    level: str,
    subject: str | None = None,
    pages: list[int] | None = None,
) -> ScopeDraft:
    texts = page_texts(path, pages)
    if not any(t.strip() for t in texts.values()):
        raise ValueError("교재에 글자 정보가 없습니다 (스캔본은 아직 지원하지 않음).")
    parts: list[ChapterExtraction] = []
    known: list[str] = []
    for chunk in chunks(texts):

        def check(
            d: ChapterExtraction,
            chunk: dict[int, str] = chunk,
            seen: frozenset[str] = frozenset(known),
        ) -> list[str]:
            return check_extraction(d, chunk, set(seen))

        material = "\n\n".join(f"<{n}쪽>\n{t}" for n, t in chunk.items())
        res = engine.run(
            dataclasses.replace(EXTRACT_CONCEPTS, validator=check),
            TaskInput(variables={"level": level, "known": list(known)}, material=material),
        )
        parts.append(res.value)
        known += [c.name for c in res.value.concepts]
    return build_scope(parts, title=title, level=level, subject=subject)
