"""실험 (M0): AI가 쓴 LaTeX가 한글 수식 스크립트로 얼마나 바뀌는가 (PLAN.md §5-3, §14).

python-hwpx의 `latex_to_eqedit`(LaTeX → 한글 수식)과 `eqedit_to_latex`(반대)를 실제 자료의 식에
돌려 본다. 모르는 명령은 조용히 바꾸지 않고 예외를 내므로, 실패한 식은 그대로 목록에 남는다.

    uv run python experiments/hwpx_latex_coverage.py <JSON·TXT 파일 또는 폴더> ...

결과: 바뀐 비율, 실패 원인별 개수, 되돌렸다 다시 바꾸면 같은 스크립트가 되는 비율(안정),
되돌린 식이 원래 식과 표기 차이(`^{2}`↔`^2`, `\\ge`↔`\\geq`)만 빼고 같은 비율.
`\\quad`·`\\ `처럼 간격만 뜻하는 명령은 바꾸기 전에 공백으로 고친다 (다워이 변환기가 할 일).
`experiments/out/latex_coverage.json`에 식마다 결과를 남긴다.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from hwpx.equation.authoring import latex_to_eqedit
from hwpx.equation.eqedit import eqedit_to_latex

from dawoai.grading.checks import normalize_latex
from dawoai.models.content import MathRun, runs_from_text

OUT = Path(__file__).resolve().parent / "out"
BS = "\\"


def _strings(obj: Any) -> Iterator[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():  # pyright: ignore[reportUnknownVariableType]
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:  # pyright: ignore[reportUnknownVariableType]
            yield from _strings(v)


def _files(args: list[str]) -> Iterator[Path]:
    for a in args:
        p = Path(a)
        if p.is_dir():
            yield from sorted(x for x in p.rglob("*") if x.suffix in (".json", ".txt"))
        elif p.is_file():
            yield p


def collect(paths: list[str]) -> list[str]:
    """파일 안 글의 인라인 식. 줄 전체가 식인 판독 줄(`식: …`)도 넣는다."""
    found: list[str] = []
    for f in _files(paths):
        text = f.read_text(encoding="utf-8", errors="replace")
        strings = list(_strings(json.loads(text))) if f.suffix == ".json" else text.splitlines()
        for s in strings:
            runs = runs_from_text(s)
            found += [r.v for r in runs if isinstance(r, MathRun)]
            if f.suffix == ".txt" and s.startswith("식:"):
                found.append(s.removeprefix("식:").strip())
    return list(dict.fromkeys(x for x in found if x.strip()))


_SPACING = re.compile(re.escape(BS) + r"(?:quad|qquad|[ ,;:!])")
_SAME = tuple((BS + a, BS + b) for a, b in (("geq", "ge"), ("leq", "le"), ("neq", "ne")))
_SINGLE_BRACED = re.compile(r"\{([^{}" + re.escape(BS) + r"])\}")


def preclean(latex: str) -> str:
    """간격 명령 → 공백. 뜻은 그대로다."""
    return _SPACING.sub(" ", latex)


def canonical(latex: str) -> str:
    s = normalize_latex(latex)
    for a, b in _SAME:
        s = s.replace(a, b)
    return _SINGLE_BRACED.sub(lambda m: m.group(1), s)  # x^{2} → x^2


def _reason(e: Exception) -> str:
    msg = str(e)
    m = re.search(re.escape(BS) + r"[A-Za-z]+|environment \w+|character .", msg)
    return f"{type(e).__name__}: {m.group(0) if m else msg[:60]}"


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    latexes = collect(argv)
    rows: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    for latex in latexes:
        row: dict[str, Any] = {"latex": latex}
        try:
            script = latex_to_eqedit(preclean(latex))
            row["eqedit"] = script
            back = eqedit_to_latex(script)
            row["back"] = back
            row["stable"] = latex_to_eqedit(back) == script
            row["roundtrip"] = canonical(back) == canonical(preclean(latex))
        except Exception as e:  # 라이브러리 예외 종류가 여럿 — 원인만 센다
            row["error"] = _reason(e)
            reasons[row["error"]] += 1
        rows.append(row)

    ok = [r for r in rows if "eqedit" in r]
    same = [r for r in ok if r["roundtrip"]]
    stable = [r for r in ok if r["stable"]]
    print(f"식 {len(rows)}개: 한글 수식으로 바뀜 {len(ok)} ({len(ok) / max(len(rows), 1):.0%}), "
          f"안정 {len(stable)}, 되돌려도 같음 {len(same)}")  # fmt: skip
    for reason, n in reasons.most_common(15):
        print(f"  실패 {n:3d}  {reason}")
    for r in [r for r in ok if not r["roundtrip"]][:8]:
        print(f"  다르게 돌아옴: {r['latex']!r} → {r['eqedit']!r} → {r['back']!r}")
    OUT.mkdir(exist_ok=True)
    out = OUT / "latex_coverage.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"식마다 결과: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
