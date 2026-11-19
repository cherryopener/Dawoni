// 강사가 글에 식을 넣는 칸 (판독문 고치기·0단계 해설 다듬기·사례집). 2026-11-19 사용자: `\( \)`는 직관적이지
// 않다 → 칸에서는 `$수식$`으로 보이고 그렇게 쓴다. 저장할 때 `\( \)`로 바꿔 서버·AI·HWPX는 그대로 둔다.
import type { TranscriptLine } from "./types";

/** 한국어 윈도우 글꼴은 \를 ₩로 보여 준다(같은 글자). 입력기가 진짜 ₩(U+20A9)를 넣는 때가 있어 \로 바꾼다. */
export const fixWon = (s: string) => s.replace(/[₩￦]/g, "\\");
/** 저장된 글 → 칸에 보일 글: `\(x\)` → `$x$`. 여러 줄에 걸친 식은 그대로 둔다 (`fromDollars`가 한 줄 식만 되돌린다). */
export const toDollars = (s: string) => s.replace(/\\\(([^\n$]+?)\\\)/g, (_, m: string) => `$${m}$`);
/** 칸의 글 → 저장할 글: `$x$`(`$$x$$`도) → `\(x\)`, ₩ → \. */
export const fromDollars = (s: string) => fixWon(s).replace(/\$\$?([^$\n]+?)\$\$?/g, "\\($1\\)");
/** 식 줄: 줄 전체가 식이므로 감싼 `$`·`\( \)`는 뗀다. */
export const tidyMath = (s: string) => fixWon(s).trim().replace(/^\$\$?|\$\$?$/g, "").replace(/^\\\(|\\\)$/g, "");
/** 판독문 줄을 저장할 모양으로. */
export const tidy = (l: TranscriptLine): TranscriptLine => (l.math !== null ? { ...l, math: tidyMath(l.math) } : { ...l, text: fromDollars(l.text) });
/** 판독문 줄을 칸에 보일 모양으로. */
export const shown = (l: TranscriptLine): TranscriptLine => (l.math !== null ? l : { ...l, text: toDollars(l.text) });

/** 식 단추: `|`는 넣은 뒤 커서 자리. 글 줄에서 식 밖이면 `$ $`로 감싸 넣는다. */
export const SNIPPETS: { label: string; tex: string; title: string }[] = [
  { label: "$ $", tex: "|", title: "식 칸 넣기 — $와 $ 사이에 수식을 씁니다" },
  { label: "a/b", tex: "\\frac{|}{}", title: "분수 \\frac{위}{아래}" },
  { label: "x²", tex: "^{|}", title: "위 첨자(제곱) ^{ }" },
  { label: "x₁", tex: "_{|}", title: "아래 첨자 _{ }" },
  { label: "√", tex: "\\sqrt{|}", title: "제곱근 \\sqrt{ }" },
  { label: "×", tex: "\\times |", title: "곱하기 \\times" },
  { label: "→", tex: "\\rightarrow |", title: "화살표 \\rightarrow" },
  { label: "Δ", tex: "\\Delta |", title: "델타 \\Delta" },
];
/** 커서가 `$…$` 안인가 (앞에 있는 `$`가 홀수 개). */
export const insideMath = (s: string, at: number) => (s.slice(0, at).match(/\$/g)?.length ?? 0) % 2 === 1;
