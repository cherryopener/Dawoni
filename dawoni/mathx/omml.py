"""Word 수식(OMML) → LaTeX (DOCX 시험지 읽기, PLAN.md §3).

변환기를 싣지 않고 자주 쓰는 구조만 직접 바꾼다: 분수, 첨자, 근호, 괄호, 큰 연산자(∑ ∫),
악센트(벡터·윗줄), 함수 이름, 극한, 행렬·연립. 모르는 요소는 안의 글자를 그대로 이어 붙인다
(식이 통째로 사라지지 않게). 학생 답안이 아니라 시험지·해설지 글이므로 AI가 읽을 수 있으면 된다.
"""

from __future__ import annotations

import re

from lxml import etree

M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

type _El = etree._Element  # pyright: ignore[reportPrivateUsage]

_SYMBOLS = {
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta", "ε": r"\epsilon",
    "ϵ": r"\epsilon", "ζ": r"\zeta", "η": r"\eta", "θ": r"\theta", "ϑ": r"\vartheta",
    "ι": r"\iota", "κ": r"\kappa", "λ": r"\lambda", "μ": r"\mu", "ν": r"\nu", "ξ": r"\xi",
    "π": r"\pi", "ρ": r"\rho", "σ": r"\sigma", "τ": r"\tau", "υ": r"\upsilon", "φ": r"\phi",
    "ϕ": r"\phi", "χ": r"\chi", "ψ": r"\psi", "ω": r"\omega", "Γ": r"\Gamma", "Δ": r"\Delta",
    "Θ": r"\Theta", "Λ": r"\Lambda", "Ξ": r"\Xi", "Π": r"\Pi", "Σ": r"\Sigma", "Φ": r"\Phi",
    "Ψ": r"\Psi", "Ω": r"\Omega", "×": r"\times", "÷": r"\div", "·": r"\cdot", "⋅": r"\cdot",
    "±": r"\pm", "∓": r"\mp", "≤": r"\leq", "≥": r"\geq", "≠": r"\neq", "≈": r"\approx",
    "≒": r"\fallingdotseq", "∝": r"\propto", "∞": r"\infty", "→": r"\rightarrow",
    "←": r"\leftarrow", "⇒": r"\Rightarrow", "⇔": r"\Leftrightarrow", "↔": r"\leftrightarrow",
    "∴": r"\therefore", "∵": r"\because", "∠": r"\angle", "°": r"^{\circ}", "∂": r"\partial",
    "∇": r"\nabla", "∈": r"\in", "∉": r"\notin", "⊂": r"\subset", "∪": r"\cup", "∩": r"\cap",
    "∅": r"\emptyset", "⊥": r"\perp", "∥": r"\parallel", "△": r"\triangle", "′": "'",
    "″": "''", "−": "-", "…": r"\ldots", "⋯": r"\cdots", "ℏ": r"\hbar", "∘": r"\circ",
    "⟨": r"\langle", "⟩": r"\rangle", "∑": r"\sum", "∏": r"\prod", "∫": r"\int",
    "∬": r"\iint", "∮": r"\oint", "√": r"\sqrt{}",
}  # fmt: skip
_ACCENTS = {
    "̂": r"\hat", "̃": r"\tilde", "̄": r"\bar", "̅": r"\overline",
    "̇": r"\dot", "̈": r"\ddot", "⃗": r"\vec", "⃖": r"\overleftarrow",
    "⃡": r"\overleftrightarrow", "̆": r"\breve", "̌": r"\check",
    "→": r"\overrightarrow", "^": r"\hat", "~": r"\tilde", "¯": r"\bar",
}  # fmt: skip
_FUNCS = {"sin", "cos", "tan", "cot", "sec", "csc", "log", "ln", "exp", "lim", "max", "min",
          "sinh", "cosh", "tanh", "arcsin", "arccos", "arctan", "det"}  # fmt: skip
_DELIMS = {"(": "(", ")": ")", "[": "[", "]": "]", "{": r"\{", "}": r"\}", "|": "|",
           "‖": r"\|", "⟨": r"\langle", "⟩": r"\rangle", "⌊": r"\lfloor", "⌋": r"\rfloor",
           "⌈": r"\lceil", "⌉": r"\rceil", "": "."}  # fmt: skip
_ESCAPE = {
    "{": r"\{",
    "}": r"\}",
    "%": r"\%",
    "#": r"\#",
    "&": r"\&",
    "$": r"\$",
    "\\": r"\backslash",
}
_HANGUL = re.compile(r"[가-힣ㄱ-ㆎ]+")
_CMD_END = re.compile(r"\\[A-Za-z]+$")


def _val(el: _El | None, name: str, default: str | None = None) -> str | None:
    """`<m:chr m:val="∑"/>` 같은 속성 값."""
    if el is None:
        return default
    child = el.find(M + name)
    if child is None:
        return default
    return child.get(M + "val", default)


def _join(pieces: list[str]) -> str:
    out = ""
    for p in pieces:
        if not p:
            continue
        if _CMD_END.search(out) and p[0].isalpha():
            out += " "
        out += p
    return out


def _text(s: str, plain: bool) -> str:
    out: list[str] = []
    pos = 0
    for m in _HANGUL.finditer(s):
        out.append(_symbols(s[pos : m.start()], plain))
        out.append(rf"\text{{{m.group(0)}}}")
        pos = m.end()
    out.append(_symbols(s[pos:], plain))
    return _join(out)


def _symbols(s: str, plain: bool) -> str:
    if not s:
        return ""
    if plain and s.strip() in _FUNCS:
        return "\\" + s.strip()
    if plain and re.fullmatch(r"[A-Za-z]{2,}", s.strip()):
        return rf"\mathrm{{{s.strip()}}}"  # 단위 (cm, kg) — 기울이지 않은 글자
    return _join([_SYMBOLS.get(ch) or _ESCAPE.get(ch) or ch for ch in s])


def _group(s: str) -> str:
    return s if len(s) == 1 else "{" + s + "}"


def _arg(el: _El, name: str) -> str:
    child = el.find(M + name)
    return _children(child) if child is not None else ""


def _children(el: _El) -> str:
    return _join([_node(c) for c in el])


def _node(el: _El) -> str:
    tag = el.tag
    if not isinstance(tag, str) or tag.endswith("Pr"):
        return ""  # 속성 요소(fPr, ctrlPr…)·주석
    if tag == M + "r":
        plain = (
            _val(el.find(M + "rPr"), "sty") == "p" or el.find(M + "rPr/" + M + "nor") is not None
        )
        return _text("".join(t.text or "" for t in el.iter(M + "t", W + "t")), plain)
    if tag == M + "f":
        num, den = _arg(el, "num"), _arg(el, "den")
        kind = _val(el.find(M + "fPr"), "type")
        if kind == "lin":
            return f"{_group(num)}/{_group(den)}"
        if kind == "noBar":
            return rf"\genfrac{{}}{{}}{{0pt}}{{}}{{{num}}}{{{den}}}"
        return rf"\frac{{{num}}}{{{den}}}"
    if tag == M + "sSub":
        return f"{_group(_arg(el, 'e'))}_{{{_arg(el, 'sub')}}}"
    if tag == M + "sSup":
        return f"{_group(_arg(el, 'e'))}^{{{_arg(el, 'sup')}}}"
    if tag == M + "sSubSup":
        return f"{_group(_arg(el, 'e'))}_{{{_arg(el, 'sub')}}}^{{{_arg(el, 'sup')}}}"
    if tag == M + "sPre":
        return f"{{}}_{{{_arg(el, 'sub')}}}^{{{_arg(el, 'sup')}}}{_group(_arg(el, 'e'))}"
    if tag == M + "rad":
        deg = _arg(el, "deg")
        hidden = _val(el.find(M + "radPr"), "degHide") in ("1", "on", "true")
        e = _arg(el, "e")
        return rf"\sqrt[{deg}]{{{e}}}" if deg and not hidden else rf"\sqrt{{{e}}}"
    if tag == M + "d":
        pr = el.find(M + "dPr")
        beg = _DELIMS.get(_val(pr, "begChr", "(") or "", "(")
        end = _DELIMS.get(_val(pr, "endChr", ")") or "", ")")
        sep = _val(pr, "sepChr", "|") or "|"
        inner = _DELIMS.get(sep, sep).join(_children(e) for e in el.findall(M + "e"))
        return rf"\left{beg}{inner}\right{end}"
    if tag == M + "nary":
        pr = el.find(M + "naryPr")
        op = _SYMBOLS.get(_val(pr, "chr", "∫") or "∫", r"\int")
        sub = "" if _val(pr, "subHide") in ("1", "on") else _arg(el, "sub")
        sup = "" if _val(pr, "supHide") in ("1", "on") else _arg(el, "sup")
        lims = (f"_{{{sub}}}" if sub else "") + (f"^{{{sup}}}" if sup else "")
        return _join([op + lims, " ", _arg(el, "e")])
    if tag == M + "acc":
        cmd = _ACCENTS.get(_val(el.find(M + "accPr"), "chr", "̂") or "̂", r"\hat")
        return f"{cmd}{{{_arg(el, 'e')}}}"
    if tag == M + "bar":
        top = _val(el.find(M + "barPr"), "pos", "bot") == "top"
        return rf"\{'overline' if top else 'underline'}{{{_arg(el, 'e')}}}"
    if tag == M + "groupChr":
        pr = el.find(M + "groupChrPr")
        top = _val(pr, "pos", "bot") == "top"
        brace = (_val(pr, "chr", "⏟") or "⏟") in "⏟⏞"
        name = ("over" if top else "under") + ("brace" if brace else "line")
        return rf"\{name}{{{_arg(el, 'e')}}}"
    if tag == M + "func":
        name = _arg(el, "fName")
        return _join([name, " ", _arg(el, "e")])
    if tag == M + "limLow":
        base = _arg(el, "e")
        lim = _arg(el, "lim")
        return f"{base}_{{{lim}}}" if base.startswith("\\") else rf"\underset{{{lim}}}{{{base}}}"
    if tag == M + "limUpp":
        return rf"\overset{{{_arg(el, 'lim')}}}{{{_arg(el, 'e')}}}"
    if tag == M + "borderBox":
        return rf"\boxed{{{_arg(el, 'e')}}}"
    if tag == M + "phant":
        return ""
    if tag == M + "eqArr":
        rows = [_children(e) for e in el.findall(M + "e")]
        return r"\begin{aligned}" + r" \\ ".join(rows) + r"\end{aligned}"
    if tag == M + "m":
        rows = [
            " & ".join(_children(e) for e in mr.findall(M + "e")) for mr in el.findall(M + "mr")
        ]
        return r"\begin{matrix}" + r" \\ ".join(rows) + r"\end{matrix}"
    return _children(el)  # oMath, e, box, 모르는 요소: 안의 것을 이어 붙임


def omml_to_latex(el: _El) -> str:
    """`m:oMath`(또는 `m:oMathPara`) 요소 하나 → LaTeX (구분 기호 `\\( \\)` 없이)."""
    if el.tag == M + "oMathPara":
        return r" \\ ".join(omml_to_latex(m) for m in el.iter(M + "oMath"))
    return re.sub(r"\s+", " ", _children(el)).strip()
