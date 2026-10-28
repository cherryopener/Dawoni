"""단원 체크 → 범위 프로필 하나.

교재 단원을 DB화한 범위 프로필에는 그 단원 ID가 붙어 있다 (`ScopeProfile.unit_ids`).
강사가 여러 교재에 걸쳐 단원을 체크하면 해당 프로필들의 개념·기법·금지 기법·관례를 합친다.
같은 이름은 하나로 두고 ID는 다시 매긴다. 교재 프로필이 없는 단원은 단원 이름을 개념으로 넣는다.
결과는 보통의 범위 프로필이라 시험·채점·제작이 지금처럼 쓴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from dawoni.bank import BankError
from dawoni.models.scope import Concept, ForbiddenTechnique, ScopeProfile, Skill
from dawoni.models.units import UnitTree


def _key(name: str) -> str:
    return re.sub(r"[\s·・()\[\]]", "", name).lower()


@dataclass
class ComposedScope:
    scope: ScopeProfile
    used: list[str] = field(default_factory=lambda: list[str]())
    """합친 범위 프로필 제목."""
    warnings: list[str] = field(default_factory=lambda: list[str]())


def _tree(trees: list[UnitTree], unit_id: str) -> UnitTree | None:
    tid = unit_id.split(".", 1)[0]
    return next((t for t in trees if t.id == tid), None)


def compose_scope(
    scopes: list[ScopeProfile],
    trees: list[UnitTree],
    unit_ids: list[str],
    *,
    title: str,
    level: str | None = None,
) -> ComposedScope:
    if not unit_ids:
        raise BankError("단원을 하나 이상 체크하세요")
    checked: list[str] = []
    for u in unit_ids:
        t = _tree(trees, u)
        rid = t.resolve(u) if t else None
        if t is None or rid is None:
            raise BankError(f"없는 단원: {u}")
        checked.append(rid)
    want: set[str] = set()
    for u in checked:
        want |= _tree(trees, u).descendants(u)  # pyright: ignore[reportOptionalMemberAccess]

    def covers(s: ScopeProfile) -> bool:
        for raw in s.unit_ids:
            t = _tree(trees, raw)
            su = (t.resolve(raw) if t else None) or raw
            # 체크한 단원 안의 교재 단원이거나, 체크한 단원을 품은 교재 단원
            if su in want or (t and t.descendants(su) & set(checked)):
                return True
        return False

    picked = [s for s in scopes if covers(s)]
    warnings: list[str] = []
    concepts: list[Concept] = []
    skills: list[Skill] = []
    forbidden: list[ForbiddenTechnique] = []
    conventions: list[str] = []
    by_name: dict[str, str] = {}
    for s in picked:
        remap: dict[str, str] = {}
        for c in s.concepts:
            k = _key(c.name)
            if k not in by_name:
                by_name[k] = f"C{len(concepts) + 1}"
                concepts.append(c.model_copy(update={"id": by_name[k],
                                                     "source": c.source or s.title}))  # fmt: skip
            remap[c.id] = by_name[k]
        skill_names = {_key(x.name) for x in skills}
        for sk in s.skills:
            if _key(sk.name) in skill_names:
                continue
            skill_names.add(_key(sk.name))
            skills.append(sk.model_copy(update={
                "id": f"S{len(skills) + 1}",
                "concept_ids": sorted({remap[c] for c in sk.concept_ids if c in remap}),
            }))  # fmt: skip
        for f in s.forbidden:
            if _key(f.name) not in {_key(x.name) for x in forbidden}:
                forbidden.append(f)
        conventions += [c for c in s.conventions if c not in conventions]

    allowed = {_key(x.name) for x in skills} | {_key(x.name) for x in concepts}
    clash = [f.name for f in forbidden if _key(f.name) in allowed]
    if clash:
        warnings.append(
            f"한 교재는 금지, 다른 교재는 허용한 기법: {', '.join(clash)} — 금지 목록을 확인하세요"
        )

    covered = {
        (t.resolve(raw) if (t := _tree(trees, raw)) else None) or raw
        for s in picked for raw in s.unit_ids
    }  # fmt: skip
    for u in checked:
        t = _tree(trees, u)
        assert t is not None
        sub = t.descendants(u)
        inside = any(c in sub or u in t.descendants(c) for c in covered)
        if inside:
            continue
        name = (t.label(u) or u).split(" > ")[-1]
        k = _key(name)
        if k not in by_name:
            by_name[k] = f"C{len(concepts) + 1}"
            concepts.append(Concept(id=by_name[k], name=name, summary=t.label(u) or name,
                                    source="단원표 (교재 개념 없음)"))  # fmt: skip
        warnings.append(f"교재 개념이 없는 단원 '{name}': 단원 이름만 개념으로 넣었습니다")

    subjects = sorted({t.subject for u in checked if (t := _tree(trees, u))})
    lvl = level or (picked[0].level if picked else f"고등학교 {' · '.join(subjects)}")
    scope = ScopeProfile(
        title=title,
        subject=" · ".join(subjects) or None,
        level=lvl,
        concepts=concepts,
        skills=skills,
        forbidden=forbidden,
        conventions=conventions,
        unit_ids=checked,
    )
    return ComposedScope(scope, [s.title for s in picked], warnings)
