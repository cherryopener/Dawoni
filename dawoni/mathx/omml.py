# pyright: reportPrivateUsage=false
"""LaTeX → OMML (Word 수식, 편집 가능). pandoc(texmath)을 쓴다.

- 식을 하나씩 변환하지 않고 **한 번의 pandoc 호출로 여러 식**을 변환한다 (pandoc JSON AST →
  docx → `m:oMath` 추출). 결과는 변환기 안에 캐시한다.
- 변환에 실패한 식은 pandoc이 `$…$` 글자로 남긴다 → 문단에 `m:oMath`가 없으면 실패로 본다.
  출력 시점에 조용히 글자로 새어 나가지 않게, 저장 전에 `problems()`로 검사해 AI에 수리를
  요청한다 (PLAN §7-2).
- texmath가 모르는 흔한 명령(`\\degree`, 화학식 `\\ce{}`)은 변환 전에 표준 LaTeX로 바꾼다.
"""

from __future__ import annotations

import copy
import io
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from lxml import etree

M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"m": M_NS, "w": W_NS}

_PANDOC_API = [1, 23, 1]


class PandocMissing(RuntimeError):
    pass


def pandoc_path() -> Path:
    """동봉 pandoc(pypandoc-binary) → 없으면 PATH의 pandoc."""
    exe = "pandoc.exe" if sys.platform == "win32" else "pandoc"
    try:
        bundled = Path(str(resources.files("pypandoc") / "files" / exe))
    except ModuleNotFoundError:
        bundled = None
    if bundled is not None and bundled.is_file():
        return bundled
    found = shutil.which("pandoc")
    if found:
        return Path(found)
    raise PandocMissing("pandoc을 찾을 수 없습니다 (pypandoc-binary 설치 필요)")


# ---------------------------------------------------------------- 변환 전 정규화

_CE = re.compile(r"\\ce\{((?:[^{}]|\{[^{}]*\})*)\}")
_CE_ARROWS = (("<=>", r"\rightleftharpoons "), ("->", r"\rightarrow "), ("<-", r"\leftarrow "))


def _ce_species(s: str) -> str:
    """'H2O' → 'H_{2}O', 'SO4^2-' → 'SO_{4}^{2-}', '2H2' → '2H_{2}'."""
    s = re.sub(r"\^\{?([0-9]*[+-])\}?", r"^{\1}", s)
    s = re.sub(r"(?<=[A-Za-z)\]])([0-9]+)", r"_{\1}", s)
    return s


def _ce(m: re.Match[str]) -> str:
    body = m.group(1)
    for a, b in _CE_ARROWS:
        body = body.replace(a, f"\0{b}\0")
    parts = body.split("\0")
    out: list[str] = []
    for p in parts:
        if p.startswith("\\"):
            out.append(p)
            continue
        tokens = re.split(r"(\s+\+\s+|\s+)", p)
        out.append("".join(t if t.strip() in ("", "+") else _ce_species(t) for t in tokens))
    return r"\mathrm{" + "".join(out).strip() + "}"


def normalize_latex(latex: str) -> str:
    s = latex.strip()
    s = _CE.sub(_ce, s)
    s = re.sub(r"\\celsius\b", r"{}^{\\circ}\\mathrm{C}", s)
    s = re.sub(r"\\degree\b", r"{}^{\\circ}", s)
    return s


# ---------------------------------------------------------------- 변환기


@dataclass(frozen=True)
class Converted:
    latex: str
    omml: etree._Element | None
    """`m:oMath` 요소. 실패하면 None."""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.omml is not None

    def element(self) -> etree._Element:
        """문서에 넣을 사본 (캐시 원본은 건드리지 않음)."""
        if self.omml is None:
            raise ValueError(f"변환되지 않은 수식: {self.latex}")
        return copy.deepcopy(self.omml)


def _warning_for(stderr: str, latex: str) -> str | None:
    """pandoc 경고에서 그 식의 오류 줄('unexpected control sequence \\foo')을 찾는다."""
    head = f"Could not convert TeX math {latex}, rendering as TeX:"
    i = stderr.find(head)
    if i < 0:
        return None
    lines = stderr[i + len(head) :].splitlines()
    for k, line in enumerate(lines):
        if line.strip() == "^" and k + 1 < len(lines):
            return lines[k + 1].strip()
    return None


class OmmlConverter:
    def __init__(self, pandoc: Path | None = None, *, timeout_s: float = 60) -> None:
        self._pandoc = pandoc
        self._timeout = timeout_s
        self._cache: dict[str, Converted] = {}

    def _run(self, latexes: list[str]) -> list[Converted]:
        prepared = [normalize_latex(x) for x in latexes]
        doc: dict[str, object] = {
            "pandoc-api-version": _PANDOC_API,
            "meta": {},
            "blocks": [
                {"t": "Para", "c": [{"t": "Math", "c": [{"t": "InlineMath"}, x]}]} for x in prepared
            ],
        }
        exe = self._pandoc or pandoc_path()
        proc = subprocess.run(
            [str(exe), "-f", "json", "-t", "docx", "-o", "-"],
            input=json.dumps(doc).encode(),
            capture_output=True,
            timeout=self._timeout,
            env={**os.environ, "LANG": "C.UTF-8"},
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"pandoc 실패: {proc.stderr.decode(errors='replace')[:500]}")
        stderr = proc.stderr.decode(errors="replace")
        with zipfile.ZipFile(io.BytesIO(proc.stdout)) as z:
            root = etree.fromstring(z.read("word/document.xml"))
        paras = root.findall("w:body/w:p", NS)
        if len(paras) != len(latexes):
            raise RuntimeError(f"pandoc 결과 문단 수 불일치: {len(paras)} ≠ {len(latexes)}")
        out: list[Converted] = []
        for latex, prep, p in zip(latexes, prepared, paras, strict=True):
            om = p.find("m:oMath", NS)
            if om is None:
                why = _warning_for(stderr, prep) or "pandoc이 읽지 못함"
                out.append(Converted(latex, None, why))
            else:
                out.append(Converted(latex, om))
        return out

    def convert_many(self, latexes: Iterable[str]) -> dict[str, Converted]:
        wanted = list(dict.fromkeys(x for x in latexes))
        todo = [x for x in wanted if x not in self._cache]
        if todo:
            for c in self._run(todo):
                self._cache[c.latex] = c
        return {x: self._cache[x] for x in wanted}

    def convert(self, latex: str) -> Converted:
        return self.convert_many([latex])[latex]

    def problems(self, latexes: Iterable[str]) -> list[str]:
        """변환되지 않는 식마다 한 줄 (AI 수리 요청·출력 전 검사용)."""
        res = self.convert_many(latexes)
        return [
            f"수식 `{c.latex}`을 Word 수식으로 바꿀 수 없음 ({c.error}). 표준 LaTeX로 고칠 것"
            for c in res.values()
            if not c.ok
        ]


_default: OmmlConverter | None = None


def default_converter() -> OmmlConverter:
    global _default
    if _default is None:
        _default = OmmlConverter()
    return _default
