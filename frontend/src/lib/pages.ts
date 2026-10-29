/** '3-40, 45' → [3..40, 45] */
export function parsePages(s: string): number[] {
  const out = new Set<number>();
  for (const part of s.split(",").map((x) => x.trim()).filter(Boolean)) {
    const m = /^(\d+)\s*[-~]\s*(\d+)$/.exec(part);
    if (m) for (let p = Number(m[1]); p <= Number(m[2]); p++) out.add(p);
    else if (/^\d+$/.test(part)) out.add(Number(part));
  }
  return [...out].sort((a, b) => a - b);
}

export function pagesText(pages: number[]): string {
  const out: string[] = [];
  let start = pages[0];
  for (let i = 1; i <= pages.length; i++) {
    const prev = pages[i - 1] ?? 0;
    if (pages[i] !== prev + 1) {
      out.push(start === prev ? `${start}` : `${start}-${prev}`);
      start = pages[i];
    }
  }
  return out.join(", ");
}
