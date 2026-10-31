// 직접 입력·확인 (AI 없음): 왼쪽 원본 쪽, 오른쪽 글·그림·정답. dawoni/bank/manual.py
import { useRef, useState } from "react";
import { api, errorText } from "../lib/api";
import { bankFileUrl } from "../lib/bank";
import {
  FIG_LINE, MATH_GUIDE, draftProblems, insertAt, lines, partsPerFigure, removeFigure, usedFigures,
} from "../lib/manual";
import type { DraftFigure, ItemManual, ItemType, ManualDraft, ManualQuestion } from "../lib/types";
import { Loose, Tex } from "./Math";
import {
  Badge, Button, ErrorBox, Field, Info, Modal, NumberInput, Row, Select, Stack, TextInput, teacherName,
} from "./ui";
import s from "./ManualEditor.module.css";

export type ManualTarget =
  | { kind: "new"; sourceId: string; index: number; q: ManualQuestion }
  | { kind: "item"; itemId: string; m: ItemManual; revision: number };

const TYPES: { value: ItemType; label: string }[] = [
  { value: "short_answer", label: "단답형" },
  { value: "essay", label: "서술형" },
  { value: "multiple_choice", label: "선택형" },
];
const KINDS = [
  { value: "short_answer", label: "단답형" },
  { value: "essay", label: "서술형" },
] as const;

type Box = [number, number, number, number];

export function ManualEditor({ target, onSaved, onClose }: {
  target: ManualTarget;
  onSaved: () => void;
  onClose: () => void;
}) {
  const init = target.kind === "new" ? target.q : target.m;
  const sourceId = target.kind === "new" ? target.sourceId : target.m.source_id;
  const pages = target.kind === "new" ? target.q.pages : target.m.pages;
  const [d, setD] = useState<ManualDraft>(init.draft);
  const [figs, setFigs] = useState<DraftFigure[]>(init.figures);
  const [page, setPage] = useState(pages[0] ?? 1);
  const [cropping, setCropping] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);

  const figUrl = (path: string) =>
    target.kind === "new"
      ? `/api/bank/sources/${target.sourceId}/chunks/${target.index}/work-file?path=${encodeURIComponent(path)}`
      : bankFileUrl(path);
  const used = usedFigures(d);
  const problems = draftProblems(d, figs.map((f) => f.id));
  const set = (patch: Partial<ManualDraft>) => setD({ ...d, ...patch });

  const insert = (snippet: string, ownLine = false) => {
    const el = area.current;
    const pos = el ? el.selectionStart : d.text.length;
    const r = insertAt(d.text, pos, snippet, ownLine);
    set({ text: r.text });
    requestAnimationFrame(() => {
      if (!el) return;
      el.focus();
      el.setSelectionRange(r.cursor, r.cursor);
    });
  };

  const crop = (box: Box) => {
    setErr(null);
    setCropping(false);
    const url = target.kind === "new"
      ? `/bank/sources/${target.sourceId}/chunks/${target.index}/crop`
      : `/bank/items/${encodeURIComponent(target.itemId)}/crop`;
    api
      .post<DraftFigure>(url, { page, box, number: d.number })
      .then((f) => {
        setFigs([...figs, f]);
        insert(`[그림 ${f.id}]`, true);
      })
      .catch((e: unknown) => setErr(errorText(e)));
  };

  const save = () => {
    setErr(null);
    setSaving(true);
    const by = teacherName() || null;
    const req = target.kind === "new"
      ? api.post(`/bank/sources/${target.sourceId}/chunks/${target.index}/manual`, { draft: d, by })
      : api.put(`/bank/items/${encodeURIComponent(target.itemId)}/manual`, {
          draft: d, by, base_revision: target.revision,
        });
    req.then(onSaved).catch((e: unknown) => setErr(errorText(e))).finally(() => setSaving(false));
  };

  return (
    <Modal wide onClose={onClose}
           title={<>{target.kind === "new" ? "직접 입력" : "원본과 비교해 고치기"} · {d.number}번</>}
           actions={<Button variant="primary" disabled={saving || problems.length > 0} onClick={save}>
             {saving ? "저장 중…" : "저장 (검토함)"}</Button>}>
      <div className={s.grid}>
        <div className={s.left}>
          <Row style={{ gap: 6 }}>
            <b style={{ fontSize: 13 }}>원본</b>
            {pages.map((p) => (
              <Button key={p} small variant={p === page ? "primary" : "ghost"} onClick={() => setPage(p)}>{p}쪽</Button>
            ))}
            {sourceId && pages.length > 0 && (
              <>
                <Button small variant="ghost" onClick={() => setPage(Math.max(1, page - 1))}>◀</Button>
                <Button small variant="ghost" onClick={() => setPage(page + 1)}>▶</Button>
              </>
            )}
            <span style={{ flex: 1 }} />
            {sourceId && (
              <Button small variant={cropping ? "primary" : "default"} onClick={() => setCropping(!cropping)}>
                {cropping ? "자를 곳을 끌어서 고르세요" : "그림 새로 자르기"}
              </Button>
            )}
          </Row>
          {sourceId ? (
            <CropPage src={`/api/bank/sources/${sourceId}/page/${page}`} active={cropping} onCrop={crop} />
          ) : (
            <Info>원본 자료가 없는 문항입니다.</Info>
          )}
        </div>

        <div className={s.right}>
          <Row>
            <Field label="유형">
              <Select value={d.item_type} options={TYPES} onChange={(v) => set({
                item_type: v, choices: v === "multiple_choice" ? (d.choices.length ? d.choices : ["", "", "", "", ""]) : [],
              })} />
            </Field>
            <Field label="배점"><NumberInput value={d.points} min={0.5} onChange={(v) => set({ points: v })} /></Field>
          </Row>

          <Field label="본문" hint="한 줄이 한 문단입니다. 수식은 \( \) 안에, 그림은 [그림 F1] 줄로 넣습니다.">
            <textarea ref={area} className={s.editor} value={d.text} rows={9}
                      onChange={(e) => set({ text: e.target.value })} />
          </Field>
          <div className={s.guide}>
            {MATH_GUIDE.map((g) => (
              <button key={g.snippet} type="button" className={s.chip} title={g.snippet}
                      onClick={() => insert(g.snippet, g.snippet.startsWith("\\["))}>
                {g.label} <code>{g.snippet}</code>
              </button>
            ))}
          </div>
          {target.kind === "item" && target.m.locked.length > 0 && (
            <Info>
              [블록 k] 줄은 원래 문항의 보기·표·상자입니다. 여기서는 고치지 않고 그 자리에 그대로 둡니다.
              {target.m.locked.map((l, i) => <div key={i} className={s.locked}>{l}</div>)}
            </Info>
          )}

          <Stack gap={6}>
            <Row>
              <b style={{ fontSize: 13 }}>그림 ({figs.length})</b>
              <span style={{ fontSize: 12.5, color: "var(--text-2)" }}>원본과 같은지 보고, 쓸 그림만 넣으세요.</span>
              <span style={{ flex: 1 }} />
              {used.length >= 2 && d.item_type !== "multiple_choice" && (
                <Button small onClick={() => setD(partsPerFigure(d))}
                        title="본문 글 한 줄 + 그림마다 소문항 (1), (2) … — 그림이 곧 문항일 때">
                  그림마다 소문항 만들기
                </Button>
              )}
            </Row>
            <div className={s.figs}>
              {figs.map((f) => {
                const on = used.includes(f.id);
                return (
                  <div key={f.id} className={`${s.fig} ${on ? s.figUsed : ""}`}>
                    <img src={figUrl(f.path)} alt={f.id} />
                    <Row style={{ gap: 4 }}>
                      <b>{f.id}</b>
                      {on ? (
                        <Button small variant="ghost" onClick={() => set({ text: removeFigure(d.text, f.id) })}>빼기</Button>
                      ) : (
                        <Button small onClick={() => insert(`[그림 ${f.id}]`, true)}>넣기</Button>
                      )}
                    </Row>
                  </div>
                );
              })}
            </div>
          </Stack>

          {d.item_type === "multiple_choice" && (
            <Stack gap={4}>
              <b style={{ fontSize: 13 }}>선지 (그림 선지는 [그림 F3])</b>
              {d.choices.map((c, k) => (
                <Row key={k} style={{ gap: 6 }}>
                  <span style={{ width: 18 }}>{"①②③④⑤⑥⑦⑧⑨"[k]}</span>
                  <div style={{ flex: 1 }}>
                    <TextInput value={c} onChange={(v) => set({ choices: d.choices.map((x, i) => (i === k ? v : x)) })} />
                  </div>
                  <Button small variant="ghost" onClick={() => set({ choices: d.choices.filter((_, i) => i !== k) })}>−</Button>
                </Row>
              ))}
              {d.choices.length < 9 && (
                <Button small variant="ghost" onClick={() => set({ choices: [...d.choices, ""] })}>+ 선지</Button>
              )}
            </Stack>
          )}

          <Stack gap={4}>
            <Row>
              <b style={{ fontSize: 13 }}>{d.parts.length ? "소문항 · 정답" : "정답"}</b>
              <span style={{ fontSize: 12.5, color: "var(--text-2)" }}>원본 해설지의 답 (모르면 비워 두세요)</span>
              <span style={{ flex: 1 }} />
              <Button small variant="ghost" onClick={() => set({
                parts: [...d.parts, { label: `(${d.parts.length + 1})`, points: 1, kind: "short_answer", answer: "" }],
              })}>+ 소문항</Button>
            </Row>
            {d.parts.length === 0 ? (
              <TextInput value={d.answer} placeholder="예: ③, \(4\,\Omega\), 2 m/s" onChange={(v) => set({ answer: v })} />
            ) : (
              d.parts.map((p, k) => {
                const patch = (q: Partial<typeof p>) => set({ parts: d.parts.map((x, i) => (i === k ? { ...x, ...q } : x)) });
                return (
                  <div key={k} className={s.partRow}>
                    <TextInput value={p.label} onChange={(v) => patch({ label: v })} />
                    <NumberInput value={p.points} min={0.5} onChange={(v) => patch({ points: v })} />
                    <Select value={p.kind ?? "short_answer"} options={[...KINDS]} onChange={(v) => patch({ kind: v })} />
                    <TextInput value={p.answer} placeholder="정답" onChange={(v) => patch({ answer: v })} />
                    <Button small variant="ghost" onClick={() => set({ parts: d.parts.filter((_, i) => i !== k) })}>−</Button>
                  </div>
                );
              })
            )}
          </Stack>

          <Stack gap={4}>
            <Row>
              <b style={{ fontSize: 13 }}>미리보기</b>
              <Badge>{TYPES.find((t) => t.value === d.item_type)?.label}</Badge>
              <Badge>{d.points}점</Badge>
            </Row>
            <Preview d={d} figs={figs} figUrl={figUrl} locked={target.kind === "item" ? target.m.locked : []} />
          </Stack>

          {problems.length > 0 && (
            <ErrorBox error={problems.join(" · ")} />
          )}
          <ErrorBox error={err} />
        </div>
      </div>
    </Modal>
  );
}

function Preview({ d, figs, figUrl, locked }: {
  d: ManualDraft; figs: DraftFigure[]; figUrl: (p: string) => string; locked: string[];
}) {
  const fig = (id: string) => {
    const f = figs.find((x) => x.id === id);
    return f ? <img src={figUrl(f.path)} alt={id} /> : <span style={{ color: "var(--bad)" }}>[그림 {id} 없음]</span>;
  };
  return (
    <div className={s.preview}>
      {lines(d.text).map((l, i) => {
        switch (l.kind) {
          case "figure": return <div key={i}>{fig(l.id)}</div>;
          case "display": return <Tex key={i} latex={l.latex} display />;
          case "block": return <div key={i} className={s.locked}>{locked.find((x) => x.startsWith(`[블록 ${l.k}]`)) ?? `[블록 ${l.k}]`}</div>;
          default: return <div key={i}><Loose text={l.v} /></div>;
        }
      })}
      {d.item_type === "multiple_choice" && (
        <Row style={{ gap: 14 }}>
          {d.choices.map((c, k) => {
            const id = FIG_LINE.exec(c.trim())?.[1];
            return <span key={k}>{"①②③④⑤⑥⑦⑧⑨"[k]} {id ? fig(id) : <Loose text={c} />}</span>;
          })}
        </Row>
      )}
    </div>
  );
}

/** 원본 쪽 이미지. `active`면 끌어서 네모를 고르면 쪽 비율 좌표(0~1)로 알려 준다. */
function CropPage({ src, active, onCrop }: { src: string; active: boolean; onCrop: (box: Box) => void }) {
  const img = useRef<HTMLImageElement>(null);
  const [start, setStart] = useState<[number, number] | null>(null);
  const [cur, setCur] = useState<[number, number] | null>(null);
  const at = (e: React.MouseEvent): [number, number] => {
    const r = img.current!.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
  };
  const box = start && cur
    ? ([Math.min(start[0], cur[0]), Math.min(start[1], cur[1]), Math.max(start[0], cur[0]), Math.max(start[1], cur[1])] as Box)
    : null;
  return (
    <div className={`${s.pageWrap} ${active ? s.cropping : ""}`}>
      {/* 네모는 이미지 크기 기준 (바깥 틀은 스크롤된다) */}
      <div style={{ position: "relative" }}
           onMouseDown={(e) => { if (!active) return; e.preventDefault(); const p = at(e); setStart(p); setCur(p); }}
           onMouseMove={(e) => { if (active && start) setCur(at(e)); }}
           onMouseUp={() => {
             if (active && box && box[2] - box[0] > 0.01 && box[3] - box[1] > 0.01) onCrop(box);
             setStart(null); setCur(null);
           }}>
        <img ref={img} src={src} alt="원본 쪽" draggable={false} />
        {box && (
          <div className={s.cropBox} style={{
            left: `${box[0] * 100}%`, top: `${box[1] * 100}%`,
            width: `${(box[2] - box[0]) * 100}%`, height: `${(box[3] - box[1]) * 100}%`,
          }} />
        )}
      </div>
    </div>
  );
}
