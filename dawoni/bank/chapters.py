"""교재 챕터 표 제안: "1챕터 개념 | 1챕터 문제, 2챕터 개념 | 2챕터 문제 …" 구성 (AI 없음).

강사가 표에서 고친다. 순서:
1. 쪽 분류 — 해설 표시("정답과 해설" 등)가 있으면 해설 쪽(처음~마지막 해설 쪽 사이는 모두 —
   실측: 머리말이 짝수 쪽에만 있음), 번호 붙은 문항이 2개 이상 시작하면 문제 쪽, 글이 있으면
   개념 쪽, 거의 비었으면 기타(표지·간지)
2. 챕터 경계 — PDF 목차(책갈피), 없으면 앞쪽의 글 목차("1. 속도와 가속도 ……… 2"), 없으면
   "개념 쪽들 → 문제 쪽들" 묶음. 챕터 안에서는 첫 문제 쪽 앞까지가 개념 쪽, 그 뒤는 모두 문제 쪽
   (그림만 있는 이어지는 쪽도 문제에 넣는다)
3. 권말 해설은 문항 번호로 챕터에 나눈다 (번호가 책 전체에서 이어질 때). 아니면 챕터 제목으로.
   못 나누면 비워 두고 알린다
4. 챕터 제목을 단원표 단원 이름과 맞춰 단원을 제안한다 (확실할 때만)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pymupdf

from dawoni.ingest.layout import analyze, find_key_pages, number_chain, page_count
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
    numbers: list[int] = field(default_factory=lambda: list[int]())
    """문제 쪽: 이 쪽에서 시작하는 문항 번호. 해설 쪽: 이 쪽에 해설이 있는 문항 번호."""


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


_LONE_NUMBER = re.compile(r"^\s*0?(\d{1,3})\s*\.?\s*$")
"""권말 해설의 문항 번호 줄 ("172" 한 줄, 아래에 해설)."""


def classify_pages(pdf: Path) -> list[PageSort]:
    key = set(find_key_pages(pdf))
    if key:
        # 권말 해설(책 뒤쪽 절반에서 시작)이면 끝까지 해설 — 머리말이 일부 쪽에만 있는 책
        last = page_count(pdf) if min(key) > page_count(pdf) / 2 else max(key)
        key |= set(range(min(key), last + 1))
    toc_pages = _toc_pages(pdf)
    layout = analyze(pdf, exclude=sorted(key | toc_pages))
    starts: dict[int, list[int]] = {}
    for r in layout.regions:
        if r.boxes and r.label.isdigit():
            starts.setdefault(r.boxes[0][0], []).append(int(r.label))
    out: list[PageSort] = []
    key_cands: list[tuple[int, int]] = []  # (쪽, 번호) 읽는 순서
    for p in layout.pages:
        body = [ln for ln in sorted(p.lines, key=lambda x: (x.rect.y0, x.rect.x0))
                if p.header_y <= ln.rect.cy <= p.footer_y and ln.text.strip()]  # fmt: skip
        text = "".join(ln.text for ln in body)
        heading = body[0].text.strip()[:60] if body else ""
        nums = starts.get(p.number, [])
        n = max(len(nums), _numbered([ln.text for ln in body]))
        if p.number in key:
            kind: PageKind = "key"
            key_cands += [(p.number, int(m.group(1))) for ln in body
                          if (m := _LONE_NUMBER.match(ln.text))]  # fmt: skip
        elif p.number in toc_pages:
            kind = "other"
        elif n >= MIN_PROBLEMS:
            kind = "problem"
        elif len(text.strip()) < MIN_TEXT:
            kind = "other"
        else:
            kind = "concept"
        out.append(PageSort(p.number, kind, n, heading, nums if kind != "key" else []))
    # 해설의 번호 줄 중 이어지는 줄기만 (식 조각 "2", 소문항 "1)" 등은 빠진다)
    keep = number_chain([(i, n) for i, (_, n) in enumerate(key_cands)])
    by_page = {p.number: p for p in out}
    for i, (page, n) in enumerate(key_cands):
        if i in keep:
            by_page[page].numbers.append(n)
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


_TOC_LINE = re.compile(r"^\s*(.+?)\s*[.·…‥]{4,}\s*(\d{1,4})\s*$")


def _toc_pages(pdf: Path) -> set[int]:
    """글 목차 쪽 (목차 줄이 2개 이상) — 목차의 "1. … 7."이 문항 번호로 잡히지 않게 뺀다."""
    out: set[int] = set()
    with pymupdf.open(pdf) as doc:
        count = int(doc.page_count)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        for i in range(min(8, count)):
            text = str(doc.load_page(i).get_text())  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            if sum(bool(_TOC_LINE.match(ln)) for ln in text.splitlines()) >= 2:
                out.add(i + 1)
    return out


def _text_toc(pdf: Path, pages: list[PageSort]) -> list[tuple[str, int]]:
    """앞쪽 글 목차 ("1. 속도와 가속도 ……… 2") → (제목, PDF 쪽). 인쇄 쪽과 PDF 쪽의 차이는
    챕터 제목이 실제로 나오는 쪽으로 맞춘다."""
    entries: list[tuple[str, int]] = []
    with pymupdf.open(pdf) as doc:
        count = int(doc.page_count)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        for i in range(min(8, count)):
            text = str(doc.load_page(i).get_text())  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            for ln in text.splitlines():
                if m := _TOC_LINE.match(ln):
                    entries.append((m.group(1).strip(), int(m.group(2))))
            if len(entries) >= 2:
                break
        if len(entries) < 2:
            return []
        texts = [norm(str(doc.load_page(i).get_text())) for i in range(count)]  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]

    def hits(offset: int) -> int:
        out = 0
        for title, printed in entries:
            k = printed + offset - 1
            t = norm(title)[:6]
            out += bool(t) and 0 <= k < len(texts) and t in texts[k]
        return out

    offset = max(range(-10, 31), key=lambda o: (hits(o), -abs(o)))
    if hits(offset) < len(entries) / 2:
        return []
    last = max(p.number for p in pages)
    return [(t, n + offset) for t, n in entries if 1 <= n + offset <= last]


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


def _key_by_numbers(
    key_pages: list[PageSort], ranges: list[tuple[int, int] | None]
) -> list[list[int]]:
    """해설 쪽을 문항 번호로 챕터에 나눈다. 번호가 없는 쪽은 앞 쪽의 챕터로 (해설이 이어짐)."""
    out: list[list[int]] = [[] for _ in ranges]
    current: list[int] = []
    for kp in key_pages:
        if kp.numbers:
            current = [i for i, r in enumerate(ranges)
                       if r and any(r[0] <= n <= r[1] for n in kp.numbers)]  # fmt: skip
        for i in current:
            out[i].append(kp.number)
    return out


def _split_span(span: list[PageSort]) -> tuple[list[int], list[int]]:
    """(개념 쪽, 문제 쪽): 첫 문제 쪽 앞까지가 개념, 그 뒤는 모두 문제 (그림만 있는 쪽 포함)."""
    # 챕터 안에서는 문항이 하나라도 시작하는 첫 쪽부터 문제 (실측: 109번 하나만 있는 쪽)
    first = next((k for k, p in enumerate(span) if p.kind == "problem" or p.numbers), len(span))
    concept = [p.number for p in span[:first] if p.kind == "concept"]
    return concept, [p.number for p in span[first:]]


def _number_range(problem_pages: list[PageSort]) -> tuple[int, int] | None:
    nums = [n for p in problem_pages for n in p.numbers]
    return (min(nums), max(nums)) if nums else None


def suggest_chapters(pdf: Path, trees: list[UnitTree]) -> ChapterSuggestion:
    pages = classify_pages(pdf)
    by_num = {p.number: p for p in pages}
    notes: list[str] = []
    groups: list[tuple[str, list[PageSort]]] = []
    toc, how = _toc(pdf), "PDF 목차(책갈피)"
    if not toc:
        toc, how = _text_toc(pdf, pages), "앞쪽의 글 목차"
    if toc:
        last = max(by_num)
        for i, (title, start) in enumerate(toc):
            end = toc[i + 1][1] - 1 if i + 1 < len(toc) else last
            span = [by_num[n] for n in range(start, end + 1) if n in by_num]
            span = [p for p in span if p.kind != "key"]
            if any(p.kind in ("concept", "problem") for p in span):
                groups.append((title, span))
        notes.append(f"{how} {len(toc)}개 항목으로 챕터를 나눴습니다.")
    else:
        groups = _runs(pages)
        notes.append(
            "PDF 목차가 없어 '개념 쪽 → 문제 쪽' 묶음으로 챕터를 나눴습니다. 제목·쪽을 확인하세요."
        )

    key_pages = [p.number for p in pages if p.kind == "key"]
    spans = [_split_span(span) for _, span in groups]
    ranges = [_number_range([by_num[n] for n in problem]) for _, problem in spans]
    keys = _key_by_numbers([by_num[k] for k in key_pages], ranges)
    if any(keys):
        notes.append("권말 해설을 문항 번호로 챕터에 나눴습니다.")
    else:
        keys = _split_keys(key_pages, by_num, [t for t, _ in groups])
    if key_pages and not any(keys):
        notes.append(
            f"정답·해설 {key_pages[0]}~{key_pages[-1]}쪽을 챕터별로 나누지 못했습니다 — "
            "챕터마다 해설 쪽을 적어 주세요."
        )
    chapters: list[SourceChunk] = []
    for i, ((title, _), (concept, problem), kp) in enumerate(
        zip(groups, spans, keys, strict=True), start=1
    ):
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
