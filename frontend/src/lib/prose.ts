// AI가 쓴 채점 이유·루브릭 기준 → (조각, 식인가). dawoai/export/hwpx.py `prose_pieces`와 같은 규칙
// (실측 채점 이유 317개의 식 319개 모두 바뀜): 지시서가 `$`로 감싸지 말라고 해서 식이
// `2\Omega이나`, `T_1 = 2\sqrt{\frac{r}{g}}를`처럼 글에 섞여 온다 — 한글 사이의 덩어리 중 LaTeX 흔적
// (`\명령`, `^`, `_`, `{}`)이 있는 것만 식으로 본다. `\( \)`·`$ $`로 감싼 것은 그대로 식.

const INLINE = /\\\((.+?)\\\)/;
const DOLLAR = /\$\$?(.+?)\$\$?/g;
const HANGUL = /[가-힣ᄀ-ᇿ㄰-㆏]+/g;
const LATEX_SIGN = /\\[A-Za-z]+|[\^_{}]/;
const EDGE = " \t,.;:!?'\"“”‘’·";

function count(s: string, c: string): number {
  return s.split(c).length - 1;
}

function wrapped(s: string): boolean {
  let depth = 0;
  for (let i = 0; i < s.length; i++) {
    depth += Number(s[i] === "(") - Number(s[i] === ")");
    if (depth === 0) return i === s.length - 1;
  }
  return false;
}

function lstrip(s: string, chars: string): string {
  let i = 0;
  while (i < s.length && chars.includes(s[i]!)) i++;
  return s.slice(i);
}

function rstrip(s: string, chars: string): string {
  let i = s.length;
  while (i > 0 && chars.includes(s[i - 1]!)) i--;
  return s.slice(0, i);
}

function trimMath(run: string): [string, string, string] {
  let lead = run.slice(0, run.length - lstrip(run, EDGE).length);
  let core = run.slice(lead.length);
  let tail = core.slice(rstrip(core, EDGE).length);
  core = core.slice(0, core.length - tail.length);
  while (core.startsWith("(") && count(core, "(") > count(core, ")")) {
    lead += "(";
    core = core.slice(1).trimStart();
  }
  while (core.endsWith(")") && count(core, ")") > count(core, "(")) {
    core = core.slice(0, -1).trimEnd();
    tail = ")" + tail;
  }
  if (core.startsWith("(") && core.endsWith(")") && wrapped(core)) {
    lead += "(";
    tail = ")" + tail;
    core = core.slice(1, -1).trim();
  }
  return [lead, core, tail];
}

export function prosePieces(text: string): [string, boolean][] {
  const src = text.replace(DOLLAR, (_, m: string) => `\\(${m}\\)`);
  const out: [string, boolean][] = [];
  const split = src.split(new RegExp(INLINE.source));
  split.forEach((piece, i) => {
    if (i % 2) {
      out.push([piece, true]);
      return;
    }
    let pos = 0;
    const matches = [...piece.matchAll(HANGUL)];
    for (const m of [...matches, null]) {
      const end = m ? m.index : piece.length;
      const run = piece.slice(pos, end);
      if (LATEX_SIGN.test(run)) {
        const [lead, core, tail] = trimMath(run);
        out.push([lead, false], [core, true], [tail, false]);
      } else {
        out.push([run, false]);
      }
      if (m) {
        out.push([m[0], false]);
        pos = m.index + m[0].length;
      }
    }
  });
  const merged: [string, boolean][] = [];
  for (const [s, isMath] of out) {
    if (!s) continue;
    const last = merged[merged.length - 1];
    if (last && !isMath && !last[1]) last[0] += s;
    else merged.push([s, isMath]);
  }
  return merged;
}
