"""수준 프리셋: 학년·과목 → "이 수준에서 쓰지 않는 것" (PLAN.md §4-2).

옛 다워니는 교재를 DB화한 범위 프로필로 미적분·대학 수준 풀이를 막았다. 다워니는 교재가 없으므로
**해설지 개념 목록**이 범위가 되고, 프리셋은 그 밖에서 수준을 넘는 기법(미분·적분 …)만 막는다.
시험을 만들 때 과목을 고르면 프리셋 범위 프로필이 작업공간에 생긴다. 이미 있는 프로필(강사가
고쳤을 수 있음)은 덮어쓰지 않는다.

화면에서 고르는 것은 영재 과목 8개(`GIFTED`, 학년 없이 — 2026-11-05 사용자 결정: 물리·화학·
생물학·지구과학·대수·정수·기하·조합). 학년 × 과목 프리셋(`고1 물리`)은 CLI와 예전 시험을 위해
남긴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dawoni.grading.store import ScopeStore
from dawoni.models.scope import ForbiddenTechnique, ScopeProfile

GRADES = {"m1": "중1", "m2": "중2", "m3": "중3", "h1": "고1", "h2": "고2", "h3": "고3"}
SUBJECTS = {
    "math": "수학", "phys": "물리", "chem": "화학", "bio": "생명과학", "earth": "지구과학",
}  # fmt: skip

GIFTED_GRADE = "g"
"""학년 없는 영재 과목 프리셋의 `Level.grade`."""
GIFTED = {
    "phys": "물리", "chem": "화학", "bio": "생물학", "earth": "지구과학",
    "algebra": "대수", "number": "정수", "geometry": "기하", "combi": "조합",
}  # fmt: skip
_GIFTED_MATH = frozenset({"algebra", "number", "geometry", "combi"})

_CALCULUS = ("미분", "적분", "극한")
_COLLEGE = ("편미분", "로피탈 정리", "테일러 급수")
_FORBIDDEN: dict[tuple[str, str], tuple[str, ...]] = {
    ("math", "m1"): (*_CALCULUS, "삼각함수", "삼각비", "로그", "벡터", "행렬", "판별식"),
    ("math", "m2"): (*_CALCULUS, "삼각함수", "삼각비", "로그", "벡터", "행렬", "판별식"),
    ("math", "m3"): (*_CALCULUS, "삼각함수", "로그", "벡터", "행렬", "판별식"),
    ("math", "h1"): (*_CALCULUS, "삼각함수", "로그", "벡터", *_COLLEGE),
    ("math", "h2"): (*_COLLEGE, "벡터 외적"),
    ("math", "h3"): (*_COLLEGE, "벡터 외적"),
    ("science", "m"): (*_CALCULUS, "삼각함수", "벡터 외적", "라그랑지안"),
    ("science", "h"): (*_CALCULUS, "벡터 외적", "라그랑지안", *_COLLEGE),
}
"""2022 개정 교육과정 기준으로 넉넉히: 확실히 배우지 않는 것만. 과학은 학교급만 나눈다."""
_GIFTED_FORBIDDEN = {
    "math": (*_CALCULUS, *_COLLEGE),
    "science": (*_CALCULUS, "벡터 외적", "라그랑지안", *_COLLEGE),
}
"""영재 과목: 학년보다 앞선 내용(삼각함수·벡터 …)은 쓰므로 미적분·대학 수준만 막는다."""


@dataclass(frozen=True)
class Level:
    grade: str
    subject: str

    @property
    def gifted(self) -> bool:
        return self.grade == GIFTED_GRADE

    @property
    def subject_name(self) -> str:
        return GIFTED[self.subject] if self.gifted else SUBJECTS[self.subject]

    @property
    def id(self) -> str:
        return f"{self.grade}_{self.subject}"

    @property
    def label(self) -> str:
        return self.subject_name if self.gifted else f"{GRADES[self.grade]} {self.subject_name}"

    @property
    def scope_id(self) -> str:
        return f"scope_preset_{self.grade}_{self.subject}"

    @property
    def level_text(self) -> str:
        if self.gifted:
            return f"영재 {self.subject_name} (학년보다 앞선 내용을 다룸)"
        school = "중학교" if self.grade.startswith("m") else "고등학교"
        return f"{school} {self.grade[1]}학년 {self.subject_name}"

    def forbidden(self) -> tuple[str, ...]:
        if self.gifted:
            return _GIFTED_FORBIDDEN["math" if self.subject in _GIFTED_MATH else "science"]
        if self.subject == "math":
            return _FORBIDDEN[("math", self.grade)]
        return _FORBIDDEN[("science", self.grade[0])]


def all_levels() -> list[Level]:
    """화면에서 고르는 과목 (영재 8과목)."""
    return [Level(GIFTED_GRADE, s) for s in GIFTED]


def grade_levels() -> list[Level]:
    """학년 × 과목 프리셋 (CLI `--level "고1 물리"`, 예전 시험)."""
    return [Level(g, s) for s in SUBJECTS for g in GRADES]


_SPACES = re.compile(r"[\s_/·-]+")


def parse_level(spec: str) -> Level:
    """ "물리", "g_phys"(영재 과목) · "고1 물리", "h1_phys", "중3수학"(학년 × 과목) → Level."""
    s = _SPACES.sub("", spec.strip())
    for key, name in GIFTED.items():
        if s in (key, name, GIFTED_GRADE + key):  # `g_phys`는 밑줄이 지워져 `gphys`
            return Level(GIFTED_GRADE, key)
    grade = next((g for g, name in GRADES.items() if s.startswith((g, name))), None)
    if grade is not None:
        rest = s.removeprefix(grade).removeprefix(GRADES[grade])
        subject = next((k for k, name in SUBJECTS.items() if rest in (k, name)), None)
        if subject is None and rest == "생명":
            subject = "bio"
        if subject is None and rest == "지구":
            subject = "earth"
        if subject is not None:
            return Level(grade, subject)
    choices = ", ".join(lv.label for lv in all_levels()[:3] + grade_levels()[:1])
    raise ValueError(f"수준을 알 수 없음: {spec!r} (예: {choices} …)")


def preset_scope(level: Level) -> ScopeProfile:
    reason = (
        "영재 과정에서도 쓰지 않는 수준"
        if level.gifted
        else f"{GRADES[level.grade]} 과정에서 배우지 않음"
    )
    return ScopeProfile(
        id=level.scope_id,
        title=f"{level.label} (기본)",
        subject=level.subject_name,
        level=level.level_text,
        concepts=[],
        forbidden=[ForbiddenTechnique(name=n, reason=reason) for n in level.forbidden()],
    )


def ensure_preset(store: ScopeStore, level: Level) -> ScopeProfile:
    """작업공간에 프리셋 범위가 없으면 만든다. 있으면 (강사가 고쳤어도) 그대로 쓴다."""
    try:
        return store.get(level.scope_id)
    except KeyError:
        return store.save(preset_scope(level))
