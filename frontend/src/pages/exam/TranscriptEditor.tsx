import { ArrowRight, Check, Minus, Plus, Strikethrough, Trash2, ZoomIn } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { TeX } from "@/components/Math";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { ErrorNote } from "@/components/ui/misc";
import { api, enc, examFileUrl, useAction, useApi } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useTeacher } from "@/lib/session";
import type { Transcript, TranscriptLine, TranscriptView } from "@/lib/types";
import { useExam } from "../Exam";

/** 고칠 답안 하나: 학생 × 문항. */
export type EditTarget = { student: string; question: string };

const UNREADABLE = "[판독불가]";
const INLINE = /\\\((.+?)\\\)/g;
const ZOOMS = [1, 1.5, 2, 3];

type Row = { id: number; line: TranscriptLine };

const countUnreadable = (rows: Row[]) => rows.reduce((n, r) => n + `${r.line.text}${r.line.math ?? ""}`.split(UNREADABLE).length - 1, 0);
const same = (a: TranscriptLine[], b: TranscriptLine[]) => JSON.stringify(a) === JSON.stringify(b);

/** 판독문 고치기 (PLAN.md §5-1 5·6단계, 2026-11-18 HWPX 대신): 왼쪽 원본 쪽, 오른쪽 판독문 줄. 고쳐 "확인
 * 완료"하면 확정 판독문이 되고, 고친 답안은 다시 채점한다 (서버 `PUT …/transcripts/{문항}`). `queue`를 주면
 * "확인하고 다음"으로 차례로 넘어간다. */
export function TranscriptEditor({ target, queue = [], onClose, onChanged }: { target: EditTarget | null; queue?: EditTarget[]; onClose: () => void; onChanged: () => void }) {
  const [cur, setCur] = useState<EditTarget | null>(target);
  const [done, setDone] = useState<string[]>([]);
  /** 고친 줄에서 배우기 (AI 호출 1번씩 — 기본은 끔. 예전 한글 반영은 고친 것을 모아 한 번 배웠다). */
  const [learn, setLearn] = useState(false);
  useEffect(() => {
    setCur(target);
    setDone([]);
  }, [target]);
  const keyOf = (t: EditTarget) => `${t.student}|${t.question}`;
  const next = cur ? queue.find((t) => keyOf(t) !== keyOf(cur) && !done.includes(keyOf(t))) : undefined;
  const left = queue.filter((t) => !done.includes(keyOf(t))).length;
  if (!cur) return null;
  return (
    <Body
      key={keyOf(cur)}
      target={cur}
      next={next}
      left={left}
      learn={learn}
      setLearn={setLearn}
      onClose={onClose}
      onSaved={(confirmed) => {
        onChanged();
        if (!confirmed) return;
        setDone((d) => [...d, keyOf(cur)]);
        if (next) setCur(next);
        else onClose();
      }}
    />
  );
}

function Body({
  target,
  next,
  left,
  learn,
  setLearn,
  onClose,
  onSaved,
}: {
  target: EditTarget;
  next?: EditTarget;
  left: number;
  learn: boolean;
  setLearn: (v: boolean) => void;
  onClose: () => void;
  onSaved: (confirmed: boolean) => void;
}) {
  const { id, base } = useExam();
  const by = useTeacher();
  const view = useApi<TranscriptView>(`${base}/students/${enc(target.student)}/transcripts/${enc(target.question)}`, { refreshOnJobs: false });
  const v = view.data;
  const [rows, setRows] = useState<Row[] | null>(null);
  const seq = useRef(0);
  const [zoom, setZoom] = useState(1);
  const [leaving, setLeaving] = useState(false);
  const act = useAction();

  useEffect(() => {
    if (v && rows === null) setRows(v.transcript.lines.map((line) => ({ id: seq.current++, line })));
  }, [v, rows]);

  const original = v?.transcript.lines ?? [];
  const lines = (rows ?? []).map((r) => r.line);
  const dirty = rows !== null && !same(lines, original);
  const unreadable = countUnreadable(rows ?? []);
  const q = v?.question;

  /** 소문항 칸: 시험의 소문항 순서, 그 밖의 표시(판독문에만 있음)·소문항 모름은 뒤에. */
  const sections = useMemo(() => {
    const labels: (string | null)[] = q?.parts.length ? q.parts.map((p) => p.label) : [];
    for (const r of rows ?? []) if (!labels.includes(r.line.part)) labels.push(r.line.part);
    return labels.length ? labels : [null];
  }, [q, rows]);
  const partLabel = (p: string | null) => (p === null ? (q?.parts.length ? "소문항 모름" : `${target.question}번`) : `${target.question}-${p}`);

  const update = (rid: number, patch: Partial<TranscriptLine>, edited = true) =>
    setRows((rs) => (rs ?? []).map((r) => (r.id === rid ? { ...r, line: { ...r.line, ...patch, ...(edited ? { confidence: 1, uncertain: [] } : {}) } } : r)));
  const remove = (rid: number) => setRows((rs) => (rs ?? []).filter((r) => r.id !== rid));
  const add = (part: string | null) =>
    setRows((rs) => {
      const list = rs ?? [];
      const at = list.map((r) => r.line.part).lastIndexOf(part);
      const row: Row = { id: seq.current++, line: { text: "", part, math: null, confidence: 1, uncertain: [], struck: false } };
      return at < 0 ? [...list, row] : [...list.slice(0, at + 1), row, ...list.slice(at + 1)];
    });
  const toggleMath = (r: Row) => {
    const l = r.line;
    if (l.math !== null) update(r.id, { math: null, text: l.math ? `\\(${l.math}\\)` : "" });
    else update(r.id, { math: l.text.replace(INLINE, "$1"), text: "" });
  };

  const save = (confirm: boolean) =>
    act.run(async () => {
      const kept = lines.filter((l) => l.text.trim() || (l.math ?? "").trim());
      await api.put<Transcript>(`${base}/students/${enc(target.student)}/transcripts/${enc(target.question)}`, { lines: kept, confirm, by, learn: learn && dirty });
      if (!confirm) {
        setRows(kept.map((line) => ({ id: seq.current++, line })));
        view.reload();
      }
      onSaved(confirm);
    });
  const close = () => (dirty ? setLeaving(true) : onClose());

  useEffect(() => {
    const f = (e: KeyboardEvent) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !act.busy && unreadable === 0 && rows !== null) {
        e.preventDefault();
        void save(true);
      }
    };
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  });

  const t = v?.transcript;
  return (
    <Dialog
      open
      full
      onOpenChange={(o) => !o && close()}
      title={
        <span className="flex items-center gap-2">
          판독문 확인 · {v ? `${v.class_name ? `${v.class_name} ` : ""}${v.name}` : target.student} · {target.question}번
          {t?.confirmed && <Badge tone="ok">확인 완료{t.confirmed_by ? ` (${t.confirmed_by})` : ""}</Badge>}
          {t && !t.confirmed && <Badge tone="warn">확인 전</Badge>}
          {left > 1 && <span className="text-[13px] font-normal text-text-3">남은 답안 {left}</span>}
        </span>
      }
      desc="원본을 보고 학생이 쓴 그대로 고칩니다. 식은 \( \) 안에 LaTeX로 씁니다. 확인하면 확정 판독문이 되고, 고친 답안은 다시 채점합니다."
      footer={
        leaving ? (
          <>
            <span className="mr-auto text-[13px] text-warn">고친 내용을 저장하지 않았습니다.</span>
            <Button variant="ghost" onClick={() => setLeaving(false)}>
              계속 고치기
            </Button>
            <Button variant="danger" onClick={onClose}>
              버리고 닫기
            </Button>
          </>
        ) : (
          <>
            <span className={cn("text-[13px]", unreadable ? "text-bad" : "text-text-3")}>
              {unreadable ? `${UNREADABLE} ${unreadable}곳을 원본을 보고 채우거나 지워야 확인할 수 있습니다.` : "⌘/Ctrl + Enter: 확인"}
            </span>
            <label className="mr-auto ml-4 flex cursor-pointer items-center gap-1.5 text-[13px] text-text-2" title="고친 줄을 AI가 보고 다음 판독에 쓸 규칙을 제안합니다 (고칠 때마다 AI 호출 1번)">
              <input type="checkbox" checked={learn} onChange={(e) => setLearn(e.target.checked)} />
              이 수정에서 배우기
            </label>
            <Button variant="ghost" onClick={close}>
              닫기
            </Button>
            <Button variant="outline" disabled={act.busy || !dirty} onClick={() => void save(false)}>
              저장만
            </Button>
            <Button variant="primary" disabled={act.busy || rows === null || unreadable > 0} onClick={() => void save(true)}>
              <Check /> {next ? "확인하고 다음" : "확인 완료"}
              {next && <ArrowRight />}
            </Button>
          </>
        )
      }
    >
      {view.error && <ErrorNote message={view.error} />}
      {!v && !view.error && <div className="text-text-3">불러오는 중…</div>}
      {v && rows && (
        <div className="grid h-full grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] gap-5">
          <div className="flex min-h-0 flex-col rounded-lg bg-muted">
            <div className="flex items-center gap-1 border-b border-line px-3 py-1.5 text-[12px] text-text-3">
              <ZoomIn className="size-3.5" />
              {ZOOMS.map((z) => (
                <button key={z} type="button" onClick={() => setZoom(z)} className={cn("cursor-pointer rounded px-2 py-0.5 hover:bg-card", zoom === z && "bg-card font-medium text-text")}>
                  {z * 100}%
                </button>
              ))}
              <span className="ml-auto">원본 {v.page_images.length}쪽</span>
            </div>
            <div className="min-h-0 flex-1 overflow-auto p-3">
              {v.page_images.length === 0 && <div className="p-6 text-center text-text-3">원본 그림 없음 (입력한 답안)</div>}
              <div className="flex flex-col gap-3" style={{ width: `${zoom * 100}%` }}>
                {v.page_images.map((p) => (
                  <img key={p} src={examFileUrl(id, p)} alt="원본 쪽" className="w-full rounded bg-white" />
                ))}
              </div>
            </div>
          </div>
          <div className="min-h-0 overflow-auto pr-1">
            {t && t.notes.length > 0 && <div className="mb-3 text-[12px] text-text-3">{t.notes.join(" · ")}</div>}
            {sections.map((part) => {
              const mine = rows.filter((r) => r.line.part === part);
              const pts = part === null ? (q?.parts.length ? null : q?.points) : q?.parts.find((p) => p.label === part)?.points;
              return (
                <section key={part ?? ""} className="mb-5">
                  <div className="mb-2 flex items-center gap-2">
                    <span className="text-[15px] font-medium">{partLabel(part)}</span>
                    {pts != null && <span className="text-[12px] text-text-3">{pts}점</span>}
                    {mine.length === 0 && <span className="text-[12px] text-text-3">(빈 답안)</span>}
                  </div>
                  <div className="flex flex-col gap-2">
                    {mine.map((r) => (
                      <LineRow
                        key={r.id}
                        row={r}
                        parts={sections}
                        partLabel={partLabel}
                        onText={(text) => update(r.id, r.line.math !== null ? { math: text } : { text })}
                        onMath={() => toggleMath(r)}
                        onStruck={() => update(r.id, { struck: !r.line.struck }, false)}
                        onPart={(p) => update(r.id, { part: p }, false)}
                        onRemove={() => remove(r.id)}
                      />
                    ))}
                  </div>
                  <Button size="sm" variant="ghost" className="mt-1" onClick={() => add(part)}>
                    <Plus /> 줄 더하기
                  </Button>
                </section>
              );
            })}
            {act.error && <ErrorNote message={act.error} />}
          </div>
        </div>
      )}
    </Dialog>
  );
}

function LineRow({
  row,
  parts,
  partLabel,
  onText,
  onMath,
  onStruck,
  onPart,
  onRemove,
}: {
  row: Row;
  parts: (string | null)[];
  partLabel: (p: string | null) => string;
  onText: (v: string) => void;
  onMath: () => void;
  onStruck: () => void;
  onPart: (p: string | null) => void;
  onRemove: () => void;
}) {
  const l = row.line;
  const isMath = l.math !== null;
  const value = isMath ? (l.math ?? "") : l.text;
  const unreadable = value.includes(UNREADABLE);
  const shaky = l.confidence < 0.7 || l.uncertain.length > 0;
  const showPreview = isMath || /\\\(/.test(l.text) || unreadable;
  return (
    <div className={cn("rounded-lg border bg-card px-3 py-2", unreadable ? "border-bad" : shaky ? "border-warn" : "border-line")}>
      <div className="flex items-start gap-2">
        <button
          type="button"
          onClick={onMath}
          title={isMath ? "글 줄로 바꾸기" : "식 줄로 바꾸기 (줄 전체가 식)"}
          className={cn("mt-1 h-6 shrink-0 cursor-pointer rounded px-1.5 text-[12px]", isMath ? "bg-accent-soft text-accent-text" : "bg-muted text-text-3")}
        >
          {isMath ? "식" : "글"}
        </button>
        <AutoText value={value} onChange={onText} struck={l.struck} mono={isMath} />
        <div className="flex shrink-0 items-center">
          {parts.length > 1 && (
            <select value={l.part ?? ""} onChange={(e) => onPart(e.target.value === "" ? null : e.target.value)} className="h-7 cursor-pointer rounded border border-line bg-card px-1 text-[12px] text-text-2" aria-label="소문항">
              {parts.map((p) => (
                <option key={p ?? ""} value={p ?? ""}>
                  {partLabel(p)}
                </option>
              ))}
            </select>
          )}
          <Button size="icon" variant="ghost" className="size-7" title={l.struck ? "지운 줄 아님" : "학생이 지운 줄"} aria-label="지운 줄" onClick={onStruck}>
            {l.struck ? <Minus /> : <Strikethrough />}
          </Button>
          <Button size="icon" variant="ghost" className="size-7" title="줄 빼기" aria-label="줄 빼기" onClick={onRemove}>
            <Trash2 />
          </Button>
        </div>
      </div>
      {showPreview && value.trim() && (
        <div className={cn("mt-1.5 pl-9 text-[14px] leading-[1.9]", l.struck && "text-text-3 line-through")}>
          {isMath ? <TeX latex={value} /> : <Inline text={value} />}
        </div>
      )}
      {l.uncertain.length > 0 && <div className="mt-1 pl-9 text-[12px] text-warn">애매한 곳: {l.uncertain.join(" · ")}</div>}
    </div>
  );
}

/** 글 줄: `\( \)` 식은 그리고, [판독불가]는 분홍. */
function Inline({ text }: { text: string }) {
  const parts = text.split(INLINE);
  return (
    <>
      {parts.map((p, i) =>
        i % 2 === 1 ? (
          <TeX key={i} latex={p} />
        ) : (
          p.split(/(\[판독불가\])/).map((s, j) =>
            s === UNREADABLE ? (
              <span key={`${i}-${j}`} className="unreadable">
                {s}
              </span>
            ) : (
              <span key={`${i}-${j}`}>{s}</span>
            ),
          )
        ),
      )}
    </>
  );
}

/** 내용만큼 늘어나는 입력 칸. */
function AutoText({ value, onChange, struck, mono }: { value: string; onChange: (v: string) => void; struck: boolean; mono: boolean }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);
  return (
    <textarea
      ref={ref}
      rows={1}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={cn(
        "min-w-0 flex-1 resize-none bg-transparent py-1 text-[14px] leading-[1.6] text-text outline-none",
        struck && "text-text-3 line-through",
        mono && "font-mono text-[13px]",
      )}
      spellCheck={false}
    />
  );
}
