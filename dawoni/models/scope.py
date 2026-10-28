"""풀이 범위 프로필 — 해설·루브릭·채점이 공유하는 "이 시험에서 쓸 수 있는 것".

AI가 단계마다 쓰는 개념·기법을 짧은 ID로 표시하게 하고, 코드가 이 목록과 대조한다.
ID 형식: 개념 `C1`, 기법 `S1`, 확장 개념 `X1` (신규 문제 B 전용, §9-4).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from dawoni.models.base import DawoniModel, new_id, utcnow


class Concept(DawoniModel):
    id: str = Field(pattern=r"^C[1-9][0-9]*$")
    name: str = Field(min_length=1)
    summary: str = Field(description="핵심 내용 (공식·정의). 프롬프트에 그대로 들어간다")
    source: str | None = Field(default=None, description="교재 쪽수 등 출처")


class Skill(DawoniModel):
    id: str = Field(pattern=r"^S[1-9][0-9]*$")
    name: str = Field(min_length=1)
    description: str
    concept_ids: list[str] = Field(default_factory=lambda: list[str]())


class ForbiddenTechnique(DawoniModel):
    name: str = Field(min_length=1, description="예: 미분, 로피탈 정리")
    reason: str | None = None


class ExtensionConcept(DawoniModel):
    """제시문으로 알려 주는 교과 밖 개념 (신규 문제 B)."""

    id: str = Field(pattern=r"^X[1-9][0-9]*$")
    name: str
    definition: str = Field(description="제시문에서 주는 정의·식 (예: 공기 저항력 f = bv)")
    assumptions: list[str] = Field(default_factory=lambda: list[str]())
    expected_inference: str | None = Field(default=None, description="학생이 유추할 내용")


class ScopeProfile(DawoniModel):
    id: str = Field(default_factory=lambda: new_id("scope"))
    title: str
    subject: str | None = None
    level: str = Field(description="학년·과정 (예: 중학교 3학년, 고등학교 물리학 I)")
    concepts: list[Concept] = Field(min_length=1)
    skills: list[Skill] = Field(default_factory=lambda: list[Skill]())
    forbidden: list[ForbiddenTechnique] = Field(default_factory=lambda: list[ForbiddenTechnique]())
    conventions: list[str] = Field(default_factory=lambda: list[str]())
    extensions: list[ExtensionConcept] = Field(default_factory=lambda: list[ExtensionConcept]())
    unit_ids: list[str] = Field(
        default_factory=lambda: list[str](),
        description="이 범위가 다루는 공통 단원표 단원 (교재 단원 DB화 때 강사가 고름)",
    )
    version: int = Field(default=1, ge=1)
    updated_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _ids(self) -> ScopeProfile:
        ids = [c.id for c in self.concepts] + [s.id for s in self.skills]
        ids += [x.id for x in self.extensions]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"ID 중복: {dup}")
        concept_ids = {c.id for c in self.concepts}
        for s in self.skills:
            bad = sorted(set(s.concept_ids) - concept_ids)
            if bad:
                raise ValueError(f"기법 {s.id}가 없는 개념을 참조: {bad}")
        return self

    def allowed_ids(self, *, with_extensions: bool = False) -> set[str]:
        ids = {c.id for c in self.concepts} | {s.id for s in self.skills}
        if with_extensions:
            ids |= {x.id for x in self.extensions}
        return ids

    def name_of(self, any_id: str) -> str:
        for group in (self.concepts, self.skills, self.extensions):
            for x in group:
                if x.id == any_id:
                    return x.name
        return any_id
