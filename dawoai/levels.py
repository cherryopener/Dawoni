"""수준 프리셋: 학년·과목 → "이 수준에서 쓰지 않는 것" (PLAN.md §4-2).

다워니는 교재를 DB화한 범위 프로필로 미적분·대학 수준 풀이를 막았다. 다워이는 교재가 없으므로
**해설지 개념 목록**이 범위가 되고, 프리셋은 그 밖에서 수준을 넘는 기법(미분·적분 …)만 막는다.
시험을 만들 때 학년·과목을 고르면 프리셋 범위 프로필이 작업공간에 생긴다. 이미 있는 프로필(강사가
고쳤을 수 있음)은 덮어쓰지 않는다. 영재반처럼 학년보다 앞선 내용을 다루면 강사가 금지 목록을
덜어 쓴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dawoai.grading.store import ScopeStore
from dawoai.models.scope import ForbiddenTechnique, ScopeProfile

GRADES = {"m1": "중1", "m2": "중2", "m3": "중3", "h1": "고1", "h2": "고2", "h3": "고3"}
SUBJECTS = {
    "math": "수학", "phys": "물리", "chem": "화학", "bio": "생명과학", "earth": "지구과학",
}  # fmt: skip

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


@dataclass(frozen=True)
class Level:
    grade: str
    subject: str

    @property
    def label(self) -> str:
        return f"{GRADES[self.grade]} {SUBJECTS[self.subject]}"

    @property
    def scope_id(self) -> str:
        return f"scope_preset_{self.grade}_{self.subject}"

    @property
    def level_text(self) -> str:
        school = "중학교" if self.grade.startswith("m") else "고등학교"
        return f"{school} {self.grade[1]}학년 {SUBJECTS[self.subject]}"

    def forbidden(self) -> tuple[str, ...]:
        if self.subject == "math":
            return _FORBIDDEN[("math", self.grade)]
        return _FORBIDDEN[("science", self.grade[0])]


def all_levels() -> list[Level]:
    return [Level(g, s) for s in SUBJECTS for g in GRADES]


_SPACES = re.compile(r"[\s_/·-]+")


def parse_level(spec: str) -> Level:
    """ "고1 물리", "h1_phys", "중3수학" → Level."""
    s = _SPACES.sub("", spec.strip())
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
    choices = ", ".join(lv.label for lv in all_levels()[:3])
    raise ValueError(f"수준을 알 수 없음: {spec!r} (예: {choices} …)")


def preset_scope(level: Level) -> ScopeProfile:
    reason = f"{GRADES[level.grade]} 과정에서 배우지 않음"
    return ScopeProfile(
        id=level.scope_id,
        title=f"{level.label} (기본)",
        subject=SUBJECTS[level.subject],
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
