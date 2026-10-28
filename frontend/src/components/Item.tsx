import { examFileUrl } from "../lib/api";
import type { Block, Choice, Figure, Item, LabeledItem, Run } from "../lib/types";
import { Tex } from "./Math";
import s from "./Item.module.css";

const CIRCLED = "①②③④⑤⑥⑦⑧⑨";
export const circled = (n: number) => CIRCLED[n - 1] ?? `(${n})`;

type Ctx = { examId: string; figures: Figure[]; renderBase?: string; fileUrl?: (path: string) => string };

/** 파라미터 그림이면 다시 그린 그림, 아니면 원본 크롭. */
export function figureSrc(f: Figure, ctx: Ctx): string | null {
  if (f.mode === "parametric" && f.spec && ctx.renderBase) {
    // 스펙·변수가 바뀌면 주소도 바뀌게 (서버 캐시와 별개로 브라우저 캐시 무효화)
    const key = JSON.stringify(f.spec).length;
    return `${ctx.renderBase}/${encodeURIComponent(f.id)}/render?k=${key}`;
  }
  if (!f.orig) return null;
  return ctx.fileUrl ? ctx.fileUrl(f.orig) : examFileUrl(ctx.examId, f.orig);
}

export function Runs({ runs }: { runs: Run[] }) {
  return (
    <>
      {runs.map((r, i) => {
        if (r.t === "math") return <Tex key={i} latex={r.v} />;
        let el = <>{r.v}</>;
        if (r.underline) el = <u>{el}</u>;
        if (r.bold) el = <b>{el}</b>;
        return <span key={i}>{el}</span>;
      })}
    </>
  );
}

function FigureImg({ id, ctx }: { id: string; ctx: Ctx }) {
  const f = ctx.figures.find((x) => x.id === id);
  const src = f ? figureSrc(f, ctx) : null;
  if (!f || !src) return <span className={s.missing}>[그림 {id}]</span>;
  return <img src={src} alt={f.caption ?? `그림 ${id}`} title={f.caption ?? undefined} />;
}

function Labeled({ items, sep }: { items: LabeledItem[]; sep: string }) {
  return (
    <>
      {items.map((it, i) => (
        <div key={i} className={s.labeled}>
          <span className={s.label}>
            {it.label}
            {sep}
          </span>
          <span>
            <Runs runs={it.runs} />
          </span>
        </div>
      ))}
    </>
  );
}

export function Blocks({ blocks, ctx, prefix, suffix }: {
  blocks: Block[];
  ctx: Ctx;
  prefix?: string;
  suffix?: string;
}) {
  // 문서와 같은 규칙: 번호는 첫 블록 앞, 배점은 마지막 '문단' 끝
  const lastPar = blocks.map((b) => b.type).lastIndexOf("paragraph");
  return (
    <>
      {blocks.map((b, i) => {
        const pre = i === 0 && prefix ? <span className={s.num}>{prefix}</span> : null;
        const post = i === lastPar && suffix ? <span className={s.points}>{suffix}</span> : null;
        switch (b.type) {
          case "paragraph":
            return (
              <p key={i} className={s.para} style={{ textAlign: b.align ?? "left" }}>
                {pre}
                <Runs runs={b.runs} />
                {post}
              </p>
            );
          case "display_math":
            return (
              <div key={i}>
                {pre}
                <Tex latex={b.latex} display />
              </div>
            );
          case "figure":
            return (
              <div key={i} className={s.figure}>
                {pre}
                <FigureImg id={b.figure_id} ctx={ctx} />
              </div>
            );
          case "bogi":
            return (
              <div key={i}>
                {pre}
                <div className={s.box}>
                  <div className={s.boxTitle}>&lt;{b.title ?? "보기"}&gt;</div>
                  <Labeled items={b.items} sep=". " />
                </div>
              </div>
            );
          case "conditions":
            return (
              <div key={i}>
                {pre}
                <div className={b.boxed === false ? undefined : s.box}>
                  <Labeled items={b.items} sep=" " />
                </div>
              </div>
            );
          case "box":
            return (
              <div key={i}>
                {pre}
                <div className={s.box}>
                  {b.title && <div className={s.boxTitle}>{b.title}</div>}
                  {b.paragraphs.map((p, k) => (
                    <p key={k} className={s.para}>
                      <Runs runs={p} />
                    </p>
                  ))}
                </div>
              </div>
            );
          case "table":
            return (
              <div key={i}>
                {pre}
                <table className={s.table}>
                  <tbody>
                    {b.rows.map((row, r) => (
                      <tr key={r}>
                        {row.map((cell, c) =>
                          r < (b.header_rows ?? 0) ? (
                            <th key={c}>
                              <Runs runs={cell} />
                            </th>
                          ) : (
                            <td key={c}>
                              <Runs runs={cell} />
                            </td>
                          ),
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
        }
      })}
      {lastPar < 0 && suffix && <div className={s.points}>{suffix}</div>}
    </>
  );
}

function perRow(item: Item): number {
  const n = item.choices.length;
  switch (item.choice_layout) {
    case "row":
      return n;
    case "grid_3_2":
      return 3;
    case "grid_2":
      return 2;
    case "column":
      return 1;
    default: {
      if (item.choices.some((c) => c.figure_id)) return Math.min(3, n);
      const len = (c: Choice) =>
        c.runs.reduce((a, r) => a + (r.t === "math" ? r.v.replace(/\\[A-Za-z]+|[{}^_\s]/g, "").length * 0.6 : r.v.length), 0);
      const widest = Math.max(...item.choices.map(len));
      return widest <= 4 ? n : widest <= 9 ? 3 : widest <= 15 ? 2 : 1;
    }
  }
}

export function Choices({ item, ctx }: { item: Item; ctx: Ctx }) {
  if (!item.choices.length) return null;
  if (item.choices_figure_id) {
    return (
      <div className={s.figure}>
        <FigureImg id={item.choices_figure_id} ctx={ctx} />
      </div>
    );
  }
  const cols = perRow(item);
  return (
    <div className={s.choices} style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
      {item.choices.map((c) =>
        c.figure_id ? (
          <div key={c.label} className={s.choice}>
            <span>{circled(c.label)}</span>
            <FigureImg id={c.figure_id} ctx={ctx} />
            {c.runs.length > 0 && <Runs runs={c.runs} />}
          </div>
        ) : (
          <div key={c.label} className={s.choiceInline}>
            <span>{circled(c.label)}</span>
            <span>
              <Runs runs={c.runs} />
            </span>
          </div>
        ),
      )}
    </div>
  );
}

export function ItemView({ number, points, item, examId, renderBase, fileUrl }: {
  number: string;
  points?: number;
  item: Item;
  examId: string;
  renderBase?: string;
  fileUrl?: (path: string) => string;
}) {
  const ctx = { examId, figures: item.figures, renderBase, fileUrl };
  const label = /^\d+$/.test(number) ? `${number}.` : `[${number}]`;
  return (
    <div className={s.item}>
      <Blocks blocks={item.stem} ctx={ctx} prefix={label} suffix={points ? `[${points}점]` : undefined} />
      <Choices item={item} ctx={ctx} />
    </div>
  );
}
