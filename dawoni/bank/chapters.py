"""교재 챕터 표 제안: "1챕터 개념 | 1챕터 문제, 2챕터 개념 | 2챕터 문제 …" 구성 (AI 없음).

강사가 표에서 고친다. 순서:
1. 쪽 분류 — 해설 표시("정답과 해설" 등)가 있으면 해설 쪽(처음~마지막 해설 쪽 사이는 모두 —
   실측: 머리말이 짝수 쪽에만 있음), 번호 붙은 문항이 2개 이상 시작하면 문제 쪽, 글이 있으면
   개념 쪽, 거의 비었으면 기타(표지·간지)
2. 챕터 경계 — PDF 목차(책갈피), 앞쪽의 글 목차("1. 속도와 가속도 ……… 2"), 강 제목 줄
   ("제1강 등속운동"), 마지막으로 "개념 쪽들 → 문제 쪽들" 묶음 순서로.
3. 챕터 안: 문제 묶음 제목("기본 문제", "학습 과제" …)이 있으면 첫 묶음 앞까지가 개념 쪽, 묶음마다
   구간 하나 (번호가 묶음마다 1번부터 다시 시작해도 된다). 없으면 첫 문제 쪽 앞까지가 개념 쪽,
   번호가 1번으로 돌아가는 쪽에서 구간을 나눈다. 그림만 있는 이어지는 쪽도 문제에 넣는다
4. 권말 해설은 문항 번호로 챕터에 나눈다 (번호가 책 전체에서 이어질 때). 아니면 챕터 제목으로.
   묶음마다 번호가 1번부터면 해설 번호 개수가 맞는 묶음에만 붙인다 (실측: '학습 과제' 해설만
   있는 교재 — 아무 데나 붙이면 '기본 문제'에 다른 문항의 답이 붙음). 못 나누면 비워 두고 알린다
5. 챕터 제목을 단원표 단원 이름과 맞춰 단원을 제안한다 (확실할 때만)
6. 점검 — 구간마다 DB화와 같은 방법으로 문항 번호를 찾아 빠진 번호, 해설이 없거나 맞지 않는
   구간을 알린다 (AI 비용을 쓰기 전에 강사가 표를 고치게)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pymupdf

from dawoni.ingest.layout import SET_MARK, analyze, find_key_pages, number_chain, page_count
from dawoni.models.source import SourceChunk
from dawoni.models.units import UnitTree

PageKind = Literal["concept", "problem", "key", "other"]

MIN_PROBLEMS = 2
"""한 쪽에서 문항이 이만큼 시작하면 문제 쪽."""
MIN_TEXT = 40
"""글자가 이보다 적은 쪽은 표지·간지 (기타)."""

_NUMBERING = re.compile(
    r"^\s*(?:chapter\s*\d+|part\s*\d+|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*[.)]?|제\s*\d{0,3}\s*(?:강|장|단원|과)"
    r"|\d+\s*(?:단원|장|강)|\d+\s*[.)]|\d{1,2}\s+)",
    re.I,
)
_CHAPTER_HEAD = re.compile(
    r"^\s*(?:제\s*\d{0,3}\s*(?:강|장|단원|과)|chapter\s*\d+|unit\s*\d+|\d{1,2}\s*(?:강|장|단원))"
    r"\s*[.:]?\s*(\S.{0,40})$",
    re.I,
)
"""강·장 제목 줄 ("제1강 등속운동" — PDF에서 숫자 글꼴이 빠져 "제강 등속운동"으로도 나옴)."""
_QNUM = re.compile(r"^\s*0?(\d{1,3})\s*[.．](?!\d)")


@dataclass
class PageSort:
    number: int
    kind: PageKind
    problems: int
    heading: str
    numbers: list[int] = field(default_factory=lambda: list[int]())
    """문제 쪽: 이 쪽에서 시작하는 문항 번호. 해설 쪽: 이 쪽에 해설이 있는 문항 번호."""
    top: str = ""
    """쪽 위쪽 몇 줄 (강 제목·해설의 챕터 제목 찾기)."""
    marks: list[tuple[float, str]] = field(default_factory=lambda: list[tuple[float, str]]())
    """문제 묶음 제목 (읽는 순서 위치, 이름)."""
    qnums: list[tuple[float, int]] = field(default_factory=lambda: list[tuple[float, int]]())
    """번호로 시작하는 줄 (읽는 순서 위치, 번호) — 묶음 나누기·번호 되돌아감 찾기."""
    key_numbers: list[int] = field(default_factory=lambda: list[int]())
    """해설 쪽: "1) ⑤", "1." 같은 해설 번호 (묶음에 맞추기)."""
    heads: list[tuple[float, str]] = field(default_factory=lambda: list[tuple[float, str]]())
    """강·장 제목 줄 (읽는 순서 위치, 제목) — 쪽 중간에서 새 강이 시작하기도 한다."""


@dataclass
class ChapterSuggestion:
    chapters: list[SourceChunk]
    pages: list[PageSort]
    notes: list[str] = field(default_factory=lambda: list[str]())
    checks: list[str] = field(default_factory=lambda: list[str]())
    """DB화 전에 강사가 볼 점 (빠진 번호, 해설 없음 …)."""


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
_KEY_NUMBER = re.compile(r"^\s*0?(\d{1,3})\s*[).．.](?!\d)")
"""해설 번호 ("1) ⑤", "3." …)."""


def classify_pages(pdf: Path) -> list[PageSort]:
    key = set(find_key_pages(pdf))
    if key:
        # 권말 해설(책 뒤쪽 절반에서 시작)이면 끝까지 해설 — 머리말이 일부 쪽에만 있는 책
        last = page_count(pdf) if min(key) > page_count(pdf) / 2 else max(key)
        key |= set(range(min(key), last + 1))
    toc_pages = _toc_pages(pdf)
    raw_heads = _raw_heads(pdf)
    raw_marks = _raw_lines(pdf, SET_MARK)
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
        top = "\n".join(ln.text.strip() for ln in body[:5])

        def order(ln: Any, p: Any = p) -> float:  # 단 순서 → 위에서 아래
            return next(
                (k for k, (a, b) in enumerate(p.columns) if a <= ln.rect.cx < b), 0
            ) * 1e4 + (ln.rect.y0)

        marks = sorted((order(ln), SET_MARK.match(ln.text).group(1)) for ln in body  # type: ignore[union-attr]
                       if SET_MARK.match(ln.text))  # fmt: skip
        if not marks and raw_marks.get(p.number):
            # 쪽 위쪽 띠 안의 묶음 제목 (레이아웃이 머리말로 뺌) → 쪽 맨 앞
            marks = [(-1.0, raw_marks[p.number][0][1])]
        qnums = sorted((order(ln), int(m.group(1))) for ln in body if (m := _QNUM.match(ln.text)))
        heads = raw_heads.get(p.number, [])
        nums = starts.get(p.number, [])
        n = max(len(nums), _numbered([ln.text for ln in body]))
        key_numbers: list[int] = []
        if p.number in key:
            kind: PageKind = "key"
            key_cands += [(p.number, int(m.group(1))) for ln in body
                          if (m := _LONE_NUMBER.match(ln.text))]  # fmt: skip
            key_numbers = [int(m.group(1)) for ln in body if (m := _KEY_NUMBER.match(ln.text))]
        elif p.number in toc_pages:
            kind = "other"
        elif n >= MIN_PROBLEMS or marks:
            kind = "problem"
        elif len(text.strip()) < MIN_TEXT:
            kind = "other"
        else:
            kind = "concept"
        out.append(
            PageSort(p.number, kind, n, heading, nums if kind != "key" else [], top,
                     marks if kind != "key" else [], qnums if kind != "key" else [], key_numbers,
                     heads)
        )  # fmt: skip
    # 해설의 번호 줄 중 이어지는 줄기만 (식 조각 "2", 소문항 "1)" 등은 빠진다)
    keep = number_chain([(i, n) for i, (_, n) in enumerate(key_cands)])
    by_page = {p.number: p for p in out}
    for i, (page, n) in enumerate(key_cands):
        if i in keep:
            by_page[page].numbers.append(n)
    return out


def _raw_heads(pdf: Path) -> dict[int, list[tuple[float, str]]]:
    """쪽의 원래 글자에서 강·장 제목 줄 — 레이아웃 분석은 쪽 위쪽 띠의 제목을 머리말로 빼므로
    (실측: "제강 가속도 운동"이 머리말 띠 안에 있음)."""
    return _raw_lines(pdf, _CHAPTER_HEAD)


def _raw_lines(pdf: Path, pat: re.Pattern[str]) -> dict[int, list[tuple[float, str]]]:
    out: dict[int, list[tuple[float, str]]] = {}
    with pymupdf.open(pdf) as doc:
        count = int(doc.page_count)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        for i in range(count):
            text = str(doc.load_page(i).get_text())  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            for k, ln in enumerate(text.splitlines()):
                line = ln.strip()
                if len(line) <= 30 and (m := pat.match(line)):
                    out.setdefault(i + 1, []).append((float(k), m.group(1).strip()))
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
    # 챕터 끝 문항의 해설이 다음 쪽 첫머리에 이어지는 일이 있어 한 쪽을 더 붙인다
    # (실측: 47·48번 해설이 다음 챕터로 나뉜 164쪽 첫머리에 있었음)
    order = [kp.number for kp in key_pages]
    for pages in out:
        if pages and (k := order.index(pages[-1]) + 1) < len(order):
            pages.append(order[k])
    return out


def _heading_chapters(pages: list[PageSort]) -> list[tuple[str, int]]:
    """강·장 제목 줄이 쪽 위쪽에 있는 쪽 → (제목, 쪽). 두 개 이상일 때만."""
    out: list[tuple[str, int]] = []
    for p in pages:
        if p.kind not in ("key", "other") and p.heads:
            out.append((p.heads[0][1], p.number))
    return out if len(out) >= 2 else []


@dataclass
class _Set:
    name: str | None
    pages: list[int]
    numbers: list[int]


def _split_span(span: list[PageSort]) -> tuple[list[int], list[_Set]]:
    """(개념 쪽, 문제 묶음들).

    문제 묶음 제목이 있으면 첫 제목 앞까지가 개념, 제목마다 새 묶음. 없으면 첫 문제 쪽 앞까지가
    개념 (실측: 109번 하나만 있는 쪽도 문제), 번호가 1번으로 돌아가면 새 묶음. 묶음이 한 쪽 안에서
    바뀌면 그 쪽은 두 묶음에 모두 들어간다 (문항 영역은 묶음 제목에서 끊긴다)."""
    marked = any(p.marks for p in span)
    if marked:
        first = next(k for k, p in enumerate(span) if p.marks)
    else:
        first = next((k for k, p in enumerate(span) if p.kind == "problem" or p.numbers), len(span))
    concept = [p.number for p in span[:first] if p.kind == "concept"]
    sets: list[_Set] = []
    for p in span[first:]:
        events = sorted([(y, "mark", name) for y, name in p.marks]
                        + [(y, "num", n) for y, n in p.qnums], key=lambda e: e[0])  # fmt: skip
        if not sets:
            sets.append(_Set(None, [], []))
        for _, kind, val in events:
            cur = sets[-1]
            restart = kind == "num" and val == 1 and cur.numbers and max(cur.numbers) > 1
            if (kind == "mark" and (cur.numbers or cur.name)) or (restart and not marked):
                sets.append(_Set(str(val) if kind == "mark" else None, [], []))
                cur = sets[-1]
            elif kind == "mark":
                cur.name = str(val)
            if kind == "num":
                cur.numbers.append(int(val))
            if p.number not in cur.pages:
                cur.pages.append(p.number)
        # 번호·제목이 없는 쪽 (그림만 이어짐) → 지금 묶음
        if p.number not in sets[-1].pages:
            sets[-1].pages.append(p.number)
    return concept, [st for st in sets if st.pages]


def _number_range(problem_pages: list[PageSort]) -> tuple[int, int] | None:
    nums = [n for p in problem_pages for n in p.numbers]
    return (min(nums), max(nums)) if nums else None


def _split_keys_tolerant(
    key_pages: list[int], pages: dict[int, PageSort], titles: list[str]
) -> list[list[int]]:
    """챕터 제목이 나오는 해설 쪽으로 나눈다. 챕터 i의 범위는 바로 다음 챕터(i+1)의 제목도 찾았을
    때만 정한다 — 사이에 제목을 못 찾은 챕터가 끼면 그 범위에 다른 챕터의 해설이 섞이므로 비워
    둔다 (실측: 8강 중 3강의 해설 제목이 글자가 아님. 묶음마다 번호가 1번부터인 책에서는 섞인
    해설의 같은 번호 답이 엉뚱한 문항에 붙는다)."""
    starts: list[int | None] = []
    for t in titles:
        nt = norm(t)

        def has(k: int, nt: str = nt) -> bool:
            return nt in norm(pages[k].top) or any(nt in norm(h) for _, h in pages[k].heads)

        hit = next((k for k in key_pages if has(k)), None) if nt else None
        starts.append(hit)
    out: list[list[int]] = [[] for _ in titles]
    for i, s in enumerate(starts):
        if s is None:
            continue
        if i + 1 < len(starts):
            nxt = starts[i + 1]
            if nxt is None or nxt <= s:
                continue
            out[i] = [k for k in key_pages if s <= k < nxt]
        else:
            out[i] = [k for k in key_pages if k >= s]
    return out


def suggest_chapters(pdf: Path, trees: list[UnitTree], *, check: bool = True) -> ChapterSuggestion:
    pages = classify_pages(pdf)
    by_num = {p.number: p for p in pages}
    notes: list[str] = []
    groups: list[tuple[str, list[PageSort]]] = []
    toc, how = _toc(pdf), "PDF 목차(책갈피)"
    if not toc:
        toc, how = _text_toc(pdf, pages), "앞쪽의 글 목차"
    if not toc:
        toc, how = _heading_chapters(pages), "강·장 제목 줄"
    if toc:
        last = max(by_num)
        for i, (title, start) in enumerate(toc):
            end = toc[i + 1][1] - 1 if i + 1 < len(toc) else last
            span = [by_num[n] for n in range(start, end + 1) if n in by_num]
            span = [p for p in span if p.kind != "key"]
            if any(p.kind in ("concept", "problem") for p in span):
                groups.append((title, span))
        notes.append(f"{how} {len(toc)}개로 챕터를 나눴습니다.")
    else:
        groups = _runs(pages)
        notes.append(
            "목차·강 제목을 찾지 못해 '개념 쪽 → 문제 쪽' 묶음으로 챕터를 나눴습니다. "
            "제목·쪽을 확인하세요."
        )

    key_pages = [p.number for p in pages if p.kind == "key"]
    spans = [_split_span(span) for _, span in groups]
    ranges = [_number_range([by_num[n] for st in sets for n in st.pages]) for _, sets in spans]
    keys = _key_by_numbers([by_num[k] for k in key_pages], ranges)
    restart_sets = any(len(sets) > 1 and sum(1 for st in sets if 1 in st.numbers) > 1
                       for _, sets in spans)  # fmt: skip
    if any(keys) and not restart_sets:
        notes.append("권말 해설을 문항 번호로 챕터에 나눴습니다.")
    else:
        empty: list[list[int]] = [[] for _ in groups]
        keys = (
            _split_keys_tolerant(key_pages, by_num, [t for t, _ in groups]) if key_pages else empty
        )
        if any(keys):
            notes.append("권말 해설을 챕터 제목으로 나눴습니다.")
    checks: list[str] = []
    if key_pages and not any(keys):
        checks.append(
            f"정답·해설 {key_pages[0]}~{key_pages[-1]}쪽을 챕터별로 나누지 못했습니다 — "
            "챕터마다 해설 쪽을 적어 주세요."
        )
    chapters: list[SourceChunk] = []
    for (title, _), (concept, sets), kp in zip(groups, spans, keys, strict=True):
        name = title or f"{len(chapters) + 1}챕터"
        if key_pages and not kp:
            checks.append(f"'{name}': 해설 쪽을 찾지 못했습니다 — 해설이 있으면 쪽을 적어 주세요.")
        # 묶음마다 1번부터인데 해설을 챕터 단위로만 나눴으면 어느 묶음의 답인지 알 수 없다 — 붙이지
        # 않고 알린다 (실측: 강마다 '기본 문제' 답 목록과 '학습 과제' 해설이 따로, 같은 번호)
        set_keys: list[list[int]] = [kp for _ in sets]
        if kp and len(sets) > 1 and sum(1 for st in sets if 1 in st.numbers) > 1:
            set_keys = [[] for _ in sets]
            checks.append(
                f"'{name}': 문제 묶음마다 1번부터라 해설 {kp[0]}~{kp[-1]}쪽이 어느 묶음의 답인지 "
                "알 수 없어 비워 두었습니다 — 표에서 묶음마다 해설 쪽을 정하세요."
            )
        for k, (st, skp) in enumerate(zip(sets, set_keys, strict=True)):
            label = name if len(sets) == 1 else f"{name} · {st.name or f'묶음 {k + 1}'}"
            chapters.append(
                SourceChunk(
                    index=len(chapters) + 1,
                    label=label,
                    concept_pages=concept if k == 0 else [],
                    pages=st.pages,
                    key_pages=skp,
                    unit_hint=match_unit(title, trees),
                )
            )
        if not sets and concept:
            chapters.append(SourceChunk(index=len(chapters) + 1, label=name, concept_pages=concept,
                                        unit_hint=match_unit(title, trees)))  # fmt: skip
    if not chapters:
        checks.append("챕터를 찾지 못했습니다 — 표에 직접 적어 주세요.")
    if check:
        checks += check_chunks(pdf, chapters, by_num)
    return ChapterSuggestion(chapters, pages, notes, checks)


def check_chunks(pdf: Path, chunks: list[SourceChunk], pages: dict[int, PageSort]) -> list[str]:
    """구간마다 DB화와 같은 방법(레이아웃 분석)으로 문항 번호를 찾아 빠진 번호를 알린다.
    해설 쪽이 있으면 해설 번호가 덮지 못하는 문항도 알린다 (AI 비용을 쓰기 전에)."""
    total = page_count(pdf)
    out: list[str] = []
    for c in chunks:
        if not c.pages:
            continue
        name = c.label or f"구간 {c.index}"
        lay = analyze(pdf, exclude=[p for p in range(1, total + 1) if p not in set(c.pages)])
        nums = [int(r.label) for r in lay.regions if r.label.isdigit()]
        if not nums:
            out.append(f"'{name}': 문항 번호를 찾지 못했습니다 — 문제 쪽을 확인하세요.")
            continue
        gaps = sorted(set(range(min(nums), max(nums) + 1)) - set(nums))
        if gaps:
            # 앞 번호가 두 번 찍힌 자리인지 (실측: 교재에 "10."이 두 번, 11번 없음)
            seen = [n for k in c.pages for _, n in pages[k].qnums if min(nums) <= n <= max(nums)]
            twice = [g for g in gaps if seen.count(g - 1) >= 2]
            if twice:
                out.append(
                    f"'{name}': " + ", ".join(f"{g - 1}번이 두 번 인쇄돼 {g}번이 없는 것 같습니다"
                                              for g in twice)
                    + " — 원본을 확인하세요."
                )  # fmt: skip
                gaps = [g for g in gaps if g not in twice]
        if gaps:
            shown = ", ".join(map(str, gaps[:8])) + (" …" if len(gaps) > 8 else "")
            out.append(
                f"'{name}': {min(nums)}~{max(nums)}번 중 {shown}번이 보이지 않습니다 — "
                "쪽 범위를 확인하세요."
            )
        if c.key_pages:
            have = {n for k in c.key_pages for n in pages[k].key_numbers} | {
                n for k in c.key_pages for n in pages[k].numbers
            }
            missing = [n for n in nums if n not in have]
            if have and len(missing) > len(nums) / 2:
                out.append(
                    f"'{name}': 해설 쪽에서 문항 번호의 절반 이상을 찾지 못했습니다 — "
                    "해설 쪽을 확인하세요."
                )
    return out
