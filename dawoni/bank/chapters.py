"""교재 챕터 표 제안: "1챕터 개념 | 1챕터 문제, 2챕터 개념 | 2챕터 문제 …" 구성 (AI 없음).

강사가 표에서 고친다. 순서:
1. 쪽 분류 — 해설 표시("정답과 해설" 등)가 있으면 해설 쪽, 번호 붙은 문항이 2개 이상 시작하면
   문제 쪽, 글이 있으면 개념 쪽, 거의 비었으면 기타(표지·간지)
2. 챕터 경계 — PDF 목차(책갈피)가 있으면 그 최상위 항목, 없으면 "개념 쪽들 → 문제 쪽들" 묶음
3. 권말 해설은 해설 쪽 안의 챕터 제목으로 나눈다 (못 찾으면 비워 두고 알린다)
4. 챕터 제목을 단원표 단원 이름과 맞춰 단원을 제안한다 (확실할 때만)
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pymupdf

from dawoni.ingest.layout import analyze, find_key_pages
from dawoni.models.source import SourceChunk
from dawoni.models.units import UnitTree

PageKind = Literal["concept", "problem", "key", "other"]

MIN_PROBLEMS = 2
"""한 쪽에서 문항이 이만큼 시작하면 문제 쪽."""
MIN_TEXT = 40
"""글자가 이보다 적은 쪽은 표지·간지 (기타)."""

_NUMBERING = re.compile(
    r"^\s*(?:chapter\s*\d+|part\s*\d+|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*[.)]?|\d+\s*(?:단원|장|강)|\d+\s*[.)]|\d{1,2}\s+)",
    re.I,
)


@dataclass
class PageSort:
    number: int
    kind: PageKind
    problems: int
    heading: str


@dataclass
class ChapterSuggestion:
    chapters: list[SourceChunk]
    pages: list[PageSort]
    notes: list[str] = field(default_factory=lambda: list[str]())


def norm(s: str) -> str:
    """제목 비교용: 앞 번호(Ⅰ., 1단원, Chapter 1 …)·공백·기호를 뺀다."""
    s = _NUMBERING.sub("", s.strip())
    return re.sub(r"[\s·・.,:()\[\]<>「」『』\-–—~]", "", s).lower()


_QSTART = re.compile(r"^\s*0?(\d{1,3})\s*(?:[.)]|\s{2,}|번)\s*\S")
"""문항 번호로 시작하는 줄: "1. …", "01 …", "3) …", "2번 …"."""


def _numbered(lines: list[str]) -> int:
    """쪽 안에서 번호로 시작하는 줄 수 (서로 다른 번호만). 교재는 챕터마다 1번부터 다시 시작해
    문서 전체의 번호 순서로 문항을 찾는 레이아웃 분석이 놓친다 — 쪽마다 따로 센다."""
    nums = [int(m.group(1)) for ln in lines if (m := _QSTART.match(ln))]
    return len(set(nums))


def classify_pages(pdf: Path) -> list[PageSort]:
    key = set(find_key_pages(pdf))
    layout = analyze(pdf, exclude=sorted(key))
    starts = Counter(r.boxes[0][0] for r in layout.regions if r.boxes)
    out: list[PageSort] = []
    for p in layout.pages:
        body = [ln for ln in sorted(p.lines, key=lambda x: (x.rect.y0, x.rect.x0))
                if p.header_y <= ln.rect.cy <= p.footer_y and ln.text.strip()]  # fmt: skip
        text = "".join(ln.text for ln in body)
        heading = body[0].text.strip()[:60] if body else ""
        n = max(starts.get(p.number, 0), _numbered([ln.text for ln in body]))
        if p.number in key:
            kind: PageKind = "key"
        elif n >= MIN_PROBLEMS:
            kind = "problem"
        elif len(text.strip()) < MIN_TEXT:
            kind = "other"
        else:
            kind = "concept"
        out.append(PageSort(p.number, kind, n, heading))
    return out


def _toc(pdf: Path) -> list[tuple[str, int]]:
    """PDF 목차(책갈피)의 최상위 항목 (제목, 시작 쪽)."""
    with pymupdf.open(pdf) as doc:
        raw: list[list[Any]] = doc.get_toc()  # type: ignore[assignment]
    rows: list[tuple[int, str, int]] = []
    for e in raw:
        try:
            rows.append((int(e[0]), str(e[1]).strip(), int(e[2])))
        except TypeError, ValueError, IndexError:
            continue
    if not rows:
        return []
    top = min(r[0] for r in rows)
    return [(t, p) for lvl, t, p in rows if lvl == top and t and p >= 1]


def _runs(pages: list[PageSort]) -> list[tuple[str, list[PageSort]]]:
    """목차가 없을 때: 문제 쪽 다음에 개념 쪽이 나오면 새 챕터."""
    out: list[tuple[str, list[PageSort]]] = []
    cur: list[PageSort] = []
    for p in pages:
        if p.kind in ("key", "other"):
            continue
        if cur and p.kind == "concept" and cur[-1].kind == "problem":
            out.append((cur[0].heading, cur))
            cur = []
        cur.append(p)
    if cur:
        out.append((cur[0].heading, cur))
    return out


def match_unit(title: str, trees: list[UnitTree]) -> str | None:
    """챕터 제목과 이름이 맞는 단원 (하나로 정해질 때만)."""
    t = norm(title)
    if len(t) < 2:
        return None
    hits: list[tuple[int, str]] = []
    for tree in trees:
        stack = [(u, 0) for u in tree.units]
        while stack:
            u, depth = stack.pop()
            n = norm(u.title)
            if len(n) >= 2 and (n == t or n in t or t in n):
                hits.append((len(n) if n == t else len(n) - 100, u.id))
            stack += [(c, depth + 1) for c in u.children]
    if not hits:
        return None
    hits.sort(reverse=True)
    best = [h for h in hits if h[0] == hits[0][0]]
    return best[0][1] if len(best) == 1 else None


def _split_keys(
    key_pages: list[int], pages: dict[int, PageSort], titles: list[str]
) -> list[list[int]]:
    """권말 해설 쪽을 챕터 제목이 나오는 쪽으로 나눈다. 못 나누면 모두 빈 목록."""
    if not key_pages or not titles:
        return [[] for _ in titles]
    starts: list[int | None] = []
    for t in titles:
        nt = norm(t)
        hit = (
            next((k for k in key_pages if nt and nt in norm(pages[k].heading)), None)
            if nt
            else None
        )
        starts.append(hit)
    found = [s for s in starts if s is not None]
    if len(found) != len(titles) or found != sorted(found):
        return [[] for _ in titles]
    out: list[list[int]] = []
    for i, s in enumerate(found):
        end = found[i + 1] if i + 1 < len(found) else key_pages[-1] + 1
        out.append([k for k in key_pages if s <= k < end])
    return out


def suggest_chapters(pdf: Path, trees: list[UnitTree]) -> ChapterSuggestion:
    pages = classify_pages(pdf)
    by_num = {p.number: p for p in pages}
    notes: list[str] = []
    groups: list[tuple[str, list[PageSort]]] = []
    toc = _toc(pdf)
    if toc:
        last = max(by_num)
        for i, (title, start) in enumerate(toc):
            end = toc[i + 1][1] - 1 if i + 1 < len(toc) else last
            span = [by_num[n] for n in range(start, end + 1) if n in by_num]
            span = [p for p in span if p.kind in ("concept", "problem")]
            if span:
                groups.append((title, span))
        notes.append(f"PDF 목차(책갈피) {len(toc)}개 항목으로 챕터를 나눴습니다.")
    else:
        groups = _runs(pages)
        notes.append(
            "PDF 목차가 없어 '개념 쪽 → 문제 쪽' 묶음으로 챕터를 나눴습니다. 제목·쪽을 확인하세요."
        )

    key_pages = [p.number for p in pages if p.kind == "key"]
    keys = _split_keys(key_pages, by_num, [t for t, _ in groups])
    if key_pages and not any(keys):
        notes.append(
            f"정답·해설 {key_pages[0]}~{key_pages[-1]}쪽을 챕터별로 나누지 못했습니다 — "
            "챕터마다 해설 쪽을 적어 주세요."
        )
    chapters: list[SourceChunk] = []
    for i, ((title, span), kp) in enumerate(zip(groups, keys, strict=True), start=1):
        concept = [p.number for p in span if p.kind == "concept"]
        problem = [p.number for p in span if p.kind == "problem"]
        chapters.append(
            SourceChunk(
                index=i,
                label=title or f"{i}챕터",
                concept_pages=concept,
                pages=problem,
                key_pages=kp,
                unit_hint=match_unit(title, trees),
            )
        )
    if not chapters:
        notes.append("챕터를 찾지 못했습니다 — 표에 직접 적어 주세요.")
    return ChapterSuggestion(chapters, pages, notes)
