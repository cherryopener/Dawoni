// 직접 입력 편집기의 글 다루기 — 규칙은 dawoni/bank/manual.py와 같다.
// 한 줄 = 문단, `[그림 F1]` 줄 = 그림, `[블록 k]` 줄 = 원래 문항의 보기·표 (고치지 않음).
import type { ManualDraft } from "./types";

export const FIG_LINE = /^\[그림 (F[1-9]\d*)\]$/;
export const BLOCK_LINE = /^\[블록 (\d+)\]$/;
export const DISPLAY_LINE = /^\\\[(.+)\\\]$/;
const PART_LINE = /^\(?\d+\)$/;

/** 본문·선지에서 쓴 그림 ID (나온 순서, 중복 없이). */
export function usedFigures(d: Pick<ManualDraft, "text" | "choices">): string[] {
  const out: string[] = [];
  for (const line of [...d.text.split("\n"), ...d.choices]) {
    const id = FIG_LINE.exec(line.trim())?.[1];
    if (id && !out.includes(id)) out.push(id);
  }
  return out;
}

/** 커서 자리에 넣는다. 줄 단위 조각(그림)은 앞뒤에 줄바꿈을 붙여 한 줄로. */
export function insertAt(text: string, pos: number, snippet: string, ownLine = false): { text: string; cursor: number } {
  let s = snippet;
  if (ownLine) {
    if (pos > 0 && text[pos - 1] !== "\n") s = "\n" + s;
    if (pos < text.length && text[pos] !== "\n") s = s + "\n";
  }
  return { text: text.slice(0, pos) + s + text.slice(pos), cursor: pos + s.length };
}

export function removeFigure(text: string, id: string): string {
  return text
    .split("\n")
    .filter((l) => l.trim() !== `[그림 ${id}]`)
    .join("\n");
}

/** 그림이 곧 문항 (회로 7개 + "합성저항을 구하라"): 본문 글 + 그림마다 소문항 (1), (2) …
 *  배점이 소문항 수로 나눠떨어지지 않으면 소문항마다 1점 (강사가 고친다). */
export function partsPerFigure(d: ManualDraft): ManualDraft {
  const figs = usedFigures({ text: d.text, choices: [] });
  if (figs.length < 2) return d;
  const intro = d.text
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l && !FIG_LINE.test(l) && !PART_LINE.test(l));
  const n = figs.length;
  const even = Number.isInteger(d.points / n);
  const each = even ? d.points / n : 1;
  const labels = figs.map((_, k) => `(${k + 1})`);
  const old = new Map(d.parts.map((p) => [p.label, p]));
  return {
    ...d,
    item_type: d.item_type === "multiple_choice" ? "short_answer" : d.item_type,
    choices: [],
    points: even ? d.points : n,
    text: [...intro, ...figs.flatMap((f, k) => [labels[k], `[그림 ${f}]`])].join("\n"),
    parts: labels.map((label) => ({
      label, points: each, kind: old.get(label)?.kind ?? "short_answer", answer: old.get(label)?.answer ?? "",
    })),
  };
}

export type Line =
  | { kind: "text"; v: string }
  | { kind: "figure"; id: string }
  | { kind: "block"; k: number }
  | { kind: "display"; latex: string };

export function lines(text: string): Line[] {
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l): Line => {
      const fig = FIG_LINE.exec(l)?.[1];
      if (fig) return { kind: "figure", id: fig };
      const block = BLOCK_LINE.exec(l)?.[1];
      if (block) return { kind: "block", k: Number(block) };
      const latex = DISPLAY_LINE.exec(l)?.[1];
      if (latex) return { kind: "display", latex: latex.trim() };
      return { kind: "text", v: l };
    });
}

/** 저장 전에 화면에서 보이는 문제 (서버도 같은 검사를 한다). */
export function draftProblems(d: ManualDraft, figureIds: string[]): string[] {
  const out: string[] = [];
  if (!d.text.trim()) out.push("본문이 비어 있습니다");
  const unknown = usedFigures(d).filter((f) => !figureIds.includes(f));
  if (unknown.length) out.push(`없는 그림: ${unknown.join(", ")}`);
  if (d.item_type === "multiple_choice") {
    if (d.choices.length < 2) out.push("선택형은 선지가 2개 이상 필요합니다");
    d.choices.forEach((c, k) => { if (!c.trim()) out.push(`${k + 1}번 선지가 비어 있습니다`); });
  }
  if (d.parts.length) {
    const sum = d.parts.reduce((a, p) => a + p.points, 0);
    if (Math.abs(sum - d.points) > 1e-9) out.push(`소문항 배점 합(${sum})이 문항 배점(${d.points})과 다릅니다`);
    const labels = d.parts.map((p) => p.label);
    if (new Set(labels).size !== labels.length) out.push("소문항 표시가 겹칩니다");
  }
  return out;
}

/** 수식 안내: 넣을 글, 뜻. 누르면 커서 자리에 들어간다. */
export const MATH_GUIDE: { snippet: string; label: string }[] = [
  { snippet: "\\(\\frac{a}{b}\\)", label: "분수" },
  { snippet: "\\(x^{2}\\)", label: "제곱·위첨자" },
  { snippet: "\\(v_{0}\\)", label: "아래첨자" },
  { snippet: "\\(\\sqrt{2}\\)", label: "제곱근" },
  { snippet: "\\(4\\,\\Omega\\)", label: "옴" },
  { snippet: "\\(\\theta\\)", label: "θ" },
  { snippet: "\\(\\lambda\\)", label: "λ" },
  { snippet: "\\(\\rho\\)", label: "ρ" },
  { snippet: "\\(\\Delta t\\)", label: "Δ" },
  { snippet: "\\(\\vec{F}\\)", label: "벡터" },
  { snippet: "\\(30^{\\circ}\\)", label: "도" },
  { snippet: "\\(\\times\\)", label: "×" },
  { snippet: "\\(\\le\\)", label: "≤" },
  { snippet: "\\[ F = ma \\]", label: "한 줄 전체 식" },
];
