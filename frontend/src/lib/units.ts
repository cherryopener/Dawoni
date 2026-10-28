import type { Unit, UnitTree } from "./types";

/** 단원표 도우미 — 서버 `models/units.py`와 같은 규칙 (합친 단원은 aliases로 새 ID). */

export function walk(tree: UnitTree): { unit: Unit; path: string[] }[] {
  const out: { unit: Unit; path: string[] }[] = [];
  const go = (units: Unit[], path: string[]) => {
    for (const u of units) {
      out.push({ unit: u, path });
      go(u.children, [...path, u.title]);
    }
  };
  go(tree.units, []);
  return out;
}

export function treeOf(trees: UnitTree[], unitId: string): UnitTree | undefined {
  return trees.find((t) => t.id === unitId.split(".")[0]);
}

export function resolve(trees: UnitTree[], unitId: string): string {
  const t = treeOf(trees, unitId);
  let id = unitId;
  const seen = new Set<string>();
  let next = t?.aliases[id];
  while (next && !seen.has(id)) {
    seen.add(id);
    id = next;
    next = t?.aliases[id];
  }
  return id;
}

export function unitLabel(trees: UnitTree[], unitId: string, short = false): string {
  const id = resolve(trees, unitId);
  const t = treeOf(trees, id);
  const hit = t && walk(t).find((x) => x.unit.id === id);
  if (!t || !hit) return `(없는 단원 ${unitId})`;
  return short ? hit.unit.title : [t.subject, ...hit.path, hit.unit.title].join(" > ");
}

/** 그 단원과 아래 단원 ID 모두. */
export function descendants(trees: UnitTree[], unitId: string): Set<string> {
  const id = resolve(trees, unitId);
  const t = treeOf(trees, id);
  const out = new Set<string>();
  const hit = t && walk(t).find((x) => x.unit.id === id);
  if (!hit) return out;
  const stack = [hit.unit];
  while (stack.length) {
    const u = stack.pop()!;
    out.add(u.id);
    stack.push(...u.children);
  }
  return out;
}

/** 단원(위 단원 포함)마다 문항 수. 한 문항은 한 단원에 한 번만 센다. */
export function unitCounts(trees: UnitTree[], itemUnits: string[][]): Record<string, number> {
  const parent: Record<string, string | undefined> = {};
  for (const t of trees) {
    const go = (units: Unit[], p?: string) => {
      for (const u of units) {
        parent[u.id] = p;
        go(u.children, u.id);
      }
    };
    go(t.units);
  }
  const counts: Record<string, number> = {};
  for (const ids of itemUnits) {
    const seen = new Set<string>();
    for (const raw of ids) {
      let id: string | undefined = resolve(trees, raw);
      while (id && !seen.has(id)) {
        seen.add(id);
        id = parent[id];
      }
    }
    for (const id of seen) counts[id] = (counts[id] ?? 0) + 1;
  }
  return counts;
}
