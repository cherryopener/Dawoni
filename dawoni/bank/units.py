"""단원표 저장·편집. 작업공간 `units/<단원표ID>.json` (팀 공유 폴더면 모든 강사가 같이 씀).

- 처음 열 때 단원표가 하나도 없으면 기본 초안(2015 개정 과학 I·II)을 넣는다.
- 저장할 때 읽었던 버전과 지금 파일 버전이 다르면 거부한다 (다른 강사가 먼저 고침).
- 문항이 쓰는 단원은 지울 수 없다 — 합치기(`merge`)로 옮긴다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from dawoni.bank import BankError
from dawoni.bank.curriculum import default_trees
from dawoni.fsio import read_json, write_model_atomic
from dawoni.models.base import utcnow
from dawoni.models.units import Unit, UnitTree
from dawoni.paths import WorkspacePaths

GROUP_ORDER = ("물리", "화학", "생명과학", "지구과학")


class UnitStore:
    def __init__(self, ws: WorkspacePaths) -> None:
        self.ws = ws
        self.dir = ws.units

    def _path(self, tree_id: str) -> Path:
        return self.dir / f"{tree_id}.json"

    def _ensure(self) -> None:
        """처음 쓸 때 기본 단원표를 깐다 (과목 팀 작업공간이면 그 과목 것만). 한 번만, 빠진 것만 —
        강사가 단원표를 먼저 만들어도 기본 단원표가 빠지지 않게 (실측: 교재 단원표를 먼저 만든
        작업공간에 물리학 I·II가 없었음). 깔았다는 표시는 `.seeded`."""
        mark = self.dir / ".seeded"
        if mark.exists():
            return
        from dawoni.workspace import workspace_subjects

        subjects = workspace_subjects(self.ws)
        self.dir.mkdir(parents=True, exist_ok=True)
        for t in default_trees():
            if (not subjects or t.group in subjects) and not self._path(t.id).exists():
                write_model_atomic(self._path(t.id), t)
        mark.write_text("1", encoding="utf-8")

    def all(self) -> list[UnitTree]:
        """과목군 순서(물리·화학·생명과학·지구과학, 그 밖은 뒤) → 단원표 ID 순."""
        self._ensure()
        trees = [UnitTree.model_validate(read_json(p)) for p in sorted(self.dir.glob("*.json"))]
        order = {g: i for i, g in enumerate(GROUP_ORDER)}
        return sorted(trees, key=lambda t: (order.get(t.group, len(order)), t.id))

    def get(self, tree_id: str) -> UnitTree:
        self._ensure()
        p = self._path(tree_id)
        if not p.exists():
            raise KeyError(f"단원표 {tree_id} 없음")
        return UnitTree.model_validate(read_json(p))

    def find(self, unit_id: str) -> UnitTree | None:
        tid = unit_id.split(".", 1)[0]
        try:
            return self.get(tid)
        except KeyError:
            return None

    def label(self, unit_id: str) -> str:
        t = self.find(unit_id)
        return (t.label(unit_id) if t else None) or f"(없는 단원 {unit_id})"

    def create(self, tree: UnitTree, by: str | None = None) -> UnitTree:
        if self._path(tree.id).exists():
            raise BankError(f"단원표 ID '{tree.id}'가 이미 있습니다")
        tree = tree.model_copy(update={"version": 1, "updated_at": utcnow(), "updated_by": by})
        write_model_atomic(self._path(tree.id), tree)
        return tree

    def save(
        self,
        tree: UnitTree,
        *,
        base_version: int,
        by: str | None = None,
        in_use: Iterable[str] = (),
    ) -> UnitTree:
        """`base_version`: 강사가 편집을 시작할 때 읽은 버전. `in_use`: 문항이 쓰는 단원 ID."""
        old = self.get(tree.id)
        if old.version != base_version:
            raise BankError(
                f"다른 강사가 먼저 단원표 '{old.subject}'를 고쳤습니다"
                f"{f' ({old.updated_by})' if old.updated_by else ''} — 다시 불러와 고쳐 주세요"
            )
        kept = {u.id for u, _ in tree.walk()} | set(tree.aliases)
        removed = {u.id for u, _ in old.walk()} - kept
        used = sorted(removed & {old.resolve(i) or i for i in in_use})
        if used:
            names = ", ".join(old.label(i) or i for i in used)
            raise BankError(
                f"문항이 들어 있는 단원은 지울 수 없습니다 — 다른 단원에 합치세요: {names}"
            )
        tree = tree.model_copy(
            update={"version": old.version + 1, "updated_at": utcnow(), "updated_by": by}
        )
        write_model_atomic(self._path(tree.id), UnitTree.model_validate(tree.model_dump()))
        return tree


def merge(tree: UnitTree, source_id: str, into_id: str) -> UnitTree:
    """`source_id` 단원을 `into_id`에 합친다: 아래 단원은 옮기고, 옛 ID는 새 ID를 가리킨다."""
    if source_id == into_id:
        raise BankError("같은 단원끼리는 합칠 수 없습니다")
    if into_id in tree.descendants(source_id):
        raise BankError("단원을 자기 아래 단원에 합칠 수 없습니다")
    moved: list[Unit] = []

    def drop(units: list[Unit]) -> list[Unit]:
        out: list[Unit] = []
        for u in units:
            if u.id == source_id:
                moved.extend(u.children)
                continue
            out.append(u.model_copy(update={"children": drop(u.children)}))
        return out

    def add(units: list[Unit]) -> list[Unit]:
        return [
            u.model_copy(
                update={"children": [*add(u.children), *(moved if u.id == into_id else [])]}
            )
            for u in units
        ]

    ids = {u.id for u, _ in tree.walk()}
    for x in (source_id, into_id):
        if x not in ids:
            raise BankError(f"단원 {x} 없음")
    units = drop(tree.units)
    aliases = {k: (into_id if v == source_id else v) for k, v in tree.aliases.items()}
    aliases[source_id] = into_id
    return tree.model_copy(update={"units": add(units), "aliases": aliases})


# ---------------------------------------------------------------- 목차 붙여 넣기

_ROMAN = r"(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+|(?=[IVX])X{0,3}(?:IX|IV|V?I{0,3}))"
_MARKERS: list[tuple[str, re.Pattern[str]]] = [
    ("roman", re.compile(rf"^{_ROMAN}\s*[.)]?\s+")),
    ("dotted", re.compile(r"^\d+(?:[.-]\d+)+\.?\s+")),
    ("chapter", re.compile(r"^(?:제\s*)?\d+\s*(?:단원|장|강)[.)]?\s*")),
    ("num", re.compile(r"^\d+\s*[.)]\s*")),
    ("paren", re.compile(r"^\(\d+\)\s*")),
    ("circled", re.compile(r"^[①-⑳]\s*")),
    ("hangul", re.compile(r"^[가-하]\s*[.)]\s+")),
]
_PAGE_TAIL = re.compile(r"[\s.·…]*\d{1,4}\s*$")


def _style(line: str) -> tuple[str, str]:
    stripped = line.strip()
    for name, pat in _MARKERS:
        m = pat.match(stripped)
        if m and stripped[m.end() :].strip():
            if name == "dotted":
                name = f"dotted{m.group().count('.') + m.group().count('-')}"
            return name, stripped[m.end() :]
    indent = len(line) - len(line.lstrip(" \t"))
    return f"indent{indent}", stripped


def parse_toc(text: str, prefix: str) -> list[Unit]:
    """교재 목차 글 → 단원 트리. 번호 모양(Ⅰ., 1., (1), ①, 1-1 …)이 처음 나온 순서대로 깊어진다.

    끝의 쪽수("…… 12")는 뗀다. ID는 `prefix.1.2`처럼 차례대로 붙인다.
    """
    order: list[str] = []
    rows: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        style, title = _style(raw.rstrip())
        title = _PAGE_TAIL.sub("", title).strip() or title.strip()
        if style not in order:
            order.append(style)
        rows.append((order.index(style), title))
    if not rows:
        raise BankError("목차에서 단원을 찾지 못했습니다")

    root: list[Unit] = []
    stack: list[tuple[int, Unit, list[Unit]]] = []  # (깊이, 단원, 아이 목록)

    for depth, title in rows:
        while stack and stack[-1][0] >= depth:
            stack.pop()
        siblings = stack[-1][2] if stack else root
        parent_id = stack[-1][1].id if stack else prefix
        u = Unit(id=f"{parent_id}.{len(siblings) + 1}", title=title)
        siblings.append(u)
        stack.append((depth, u, u.children))
    return root
