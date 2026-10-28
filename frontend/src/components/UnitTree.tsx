import { useState } from "react";
import type { Unit, UnitTree } from "../lib/types";
import s from "./UnitTree.module.css";

/**
 * 과목군 → 과목 → 단원 트리. 문제은행·문제 만들기·범위 만들기가 함께 쓴다.
 * `checked`/`onToggle`을 주면 체크 상자로 여러 단원을 고른다.
 */
export function UnitTreeNav({ trees, counts, selected, onSelect, checked, onToggle, showEmpty = true }: {
  trees: UnitTree[];
  counts?: Record<string, number>;
  selected?: string | null;
  onSelect?: (unitId: string | null) => void;
  checked?: Set<string>;
  onToggle?: (unitId: string, on: boolean) => void;
  showEmpty?: boolean;
}) {
  const [open, setOpen] = useState<Set<string>>(() => new Set<string>(selected ? [selected.split(".")[0] ?? selected] : []));
  const toggleOpen = (id: string) =>
    setOpen((o) => {
      const n = new Set(o);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  const groups = [...new Set(trees.map((t) => t.group))];

  const row = (id: string, title: string, depth: number, hasKids: boolean) => {
    const n = counts?.[id] ?? 0;
    return (
      <div
        key={`row-${id}`}
        className={`${s.row} ${selected === id ? s.active : ""}`}
        style={{ paddingLeft: 6 + depth * 14 }}
      >
        <button className={s.caret} onClick={() => hasKids && toggleOpen(id)} aria-label="펼치기">
          {hasKids ? (open.has(id) ? "▾" : "▸") : ""}
        </button>
        {checked && onToggle && (
          <input type="checkbox" checked={checked.has(id)} onChange={(e) => onToggle(id, e.target.checked)} />
        )}
        <button className={s.title} onClick={() => onSelect?.(selected === id ? null : id)} title={title}>
          {title}
        </button>
        {counts && <span className={s.count}>{n || ""}</span>}
      </div>
    );
  };

  const units = (list: Unit[], depth: number): React.ReactNode[] =>
    list
      .filter((u) => showEmpty || (counts?.[u.id] ?? 0) > 0)
      .flatMap((u) => [
        row(u.id, u.title, depth, u.children.length > 0),
        ...(open.has(u.id) ? units(u.children, depth + 1) : []),
      ]);

  return (
    <div className={s.nav}>
      {groups.map((g) => (
        <div key={g}>
          <div className={s.group}>{g}</div>
          {trees
            .filter((t) => t.group === g)
            .filter((t) => showEmpty || (counts?.[t.id] ?? 0) > 0 || t.units.some((u) => (counts?.[u.id] ?? 0) > 0))
            .map((t) => (
              <div key={t.id}>
                <div className={`${s.row} ${s.subject}`}>
                  <button className={s.caret} onClick={() => toggleOpen(t.id)}>
                    {open.has(t.id) ? "▾" : "▸"}
                  </button>
                  <button className={s.title} onClick={() => toggleOpen(t.id)}>
                    {t.subject}
                    {!t.reviewed && <span className={s.draft} title="기본 초안 — 단원표 탭에서 검토하세요"> 초안</span>}
                  </button>
                  {counts && (
                    <span className={s.count}>
                      {t.units.reduce((a, u) => a + (counts[u.id] ?? 0), 0) || ""}
                    </span>
                  )}
                </div>
                {open.has(t.id) && units(t.units, 1)}
              </div>
            ))}
        </div>
      ))}
    </div>
  );
}
