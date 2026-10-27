// 문장 속 수식 나누기 — dawoni/models/content.py `runs_from_text`,
// dawoni/export/docx/builder.py `loose_segments`와 같은 규칙 (화면·Word가 똑같이 보이게).

export type Segment = { kind: "text" | "math"; v: string };

const HANGUL = /[가-힣ㄱ-ㅎㅏ-ㅣ]/;
const INLINE = /\\\((.+?)\\\)|(?<![\\$])\$(?!\$)(.+?)(?<!\\)\$/gs;
const NON_HANGUL = /[^가-힣ㄱ-ㅎㅏ-ㅣ]+/g;
const BARE = /\\[A-Za-z]+(?:\{[^{}]*\})*|[A-Za-z0-9)]\^[A-Za-z0-9{(]|_\{/;
const EDGE = " ,.;:·";

/** `\( … \)`, `$ … $`(한글 없는 것만)를 수식으로. */
export function splitDelimited(s: string): Segment[] {
  const out: Segment[] = [];
  let pos = 0;
  for (const m of s.matchAll(INLINE)) {
    const latex = (m[1] ?? m[2] ?? "").trim();
    if (!latex || (m[2] !== undefined && HANGUL.test(latex))) continue;
    const start = m.index ?? 0;
    if (start > pos) out.push({ kind: "text", v: s.slice(pos, start) });
    out.push({ kind: "math", v: latex });
    pos = start + m[0].length;
  }
  if (pos < s.length) out.push({ kind: "text", v: s.slice(pos) });
  return out;
}

function strip(s: string): string {
  let a = 0;
  let b = s.length;
  while (a < b && EDGE.includes(s[a]!)) a++;
  while (b > a && EDGE.includes(s[b - 1]!)) b--;
  return s.slice(a, b);
}

/** AI·학생이 쓴 문장: 구분자 안 수식 + 한글 사이의 구분자 없는 LaTeX(`-\frac{b}{2a}=2`, `x^2`). */
export function looseSegments(s: string): Segment[] {
  const out: Segment[] = [];
  const addText = (t: string) => {
    if (!t) return;
    const last = out[out.length - 1];
    if (last && last.kind === "text") last.v += t;
    else out.push({ kind: "text", v: t });
  };
  for (const seg of splitDelimited(s)) {
    if (seg.kind === "math") {
      out.push(seg);
      continue;
    }
    const t = seg.v;
    let pos = 0;
    for (const m of t.matchAll(NON_HANGUL)) {
      const span = m[0];
      if (!BARE.test(span)) continue;
      let core = strip(span);
      while (core.endsWith(")") && count(core, ")") > count(core, "(")) {
        core = strip(core.slice(0, -1));
      }
      const leadLen = span.length - span.replace(/^[ ,.;:·]+/, "").length;
      const start = m.index ?? 0;
      addText(t.slice(pos, start) + span.slice(0, leadLen));
      out.push({ kind: "math", v: core });
      addText(span.slice(leadLen + core.length));
      pos = start + span.length;
    }
    addText(t.slice(pos));
  }
  return out;
}

function count(s: string, ch: string): number {
  return s.split(ch).length - 1;
}
