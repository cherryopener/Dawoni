import { ArrowRight, Check, Minus, Pencil, Plus, Strikethrough, Trash2, ZoomIn } from "lucide-react";
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
import { fixWon, fromDollars, insideMath, shown, SNIPPETS, tidy, tidyMath } from "@/lib/transcript";
import { useExam } from "../Exam";

/** 고칠 답안 하나: 학생 × 문항. */
export type EditTarget = { student: string; question: string };

const UNREADABLE = "[판독불가]";
/** 강사가 [판독불가]를 바꿔 다는 표시 (서버 `ERASED`·`ILLEGIBLE`): 학생이 지운 자리 / 강사도 못 읽음. 둘 다 확인할 수 있다. */
const ERASED = "[지움]";
const ILLEGIBLE = "[못 읽음]";
const INLINE = /\\\((.+?)\\\)/g;
const ZOOMS = [1, 1.5, 2, 3];


type Row = { id: number; line: TranscriptLine };

/** 칸에 새 값과 커서를 바로 넣는다. React가 나중에 같은 값을 넣으면 커서를 건드리지 않는다 — 커서를
 * 나중에(다음 프레임에) 옮기면 그 사이에 친 글자가 '$ $' 밖에 써졌다 (2026-11-19 화면 확인). */
function placeCaret(el: HTMLTextAreaElement, value: string, from: number, to = from) {
  const v = fixWon(value); // 줄에 저장되는 값과 같게 (`onText`가 ₩를 바꾼다)
  if (el.value !== v) el.value = v;
  el.focus();
  el.setSelectionRange(from, to);
}

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
    // 칸에는 식이 `$수식$`으로 보인다 (저장할 때 `\( \)`로 — `tidy`)
    if (v && rows === null) setRows(v.transcript.lines.map((line) => ({ id: seq.current++, line: shown(line) })));
  }, [v, rows]);

  const original = v?.transcript.lines ?? [];
  const lines = (rows ?? []).map((r) => r.line);
  const dirty = rows !== null && !same(lines.map(tidy), original);
  const unreadable = countUnreadable(rows ?? []);
  const q = v?.question;

  /** 소문항 칸: 시험의 소문항 순서, 그 밖의 표시(판독문에만 있음)·소문항 모름은 뒤에. */
  const sections = useMemo(() => {
    const labels: (string | null)[] = q?.parts.length ? q.parts.map((p) => p.label) : [];
    for (const r of rows ?? []) if (!labels.includes(r.line.part)) labels.push(r.line.part);
    return labels.length ? labels : [null];
  }, [q, rows]);
  const partLabel = (p: string | null) => (p === null ? (q?.parts.length ? "소문항 모름" : `${target.question}번`) : `${target.question}-${p}`);

  /** 식 단추가 넣을 칸: 마지막으로 누른 줄 (단추를 눌러도 바뀌지 않게 — 칸이 사라지거나 밀리지 않는다). */
  const [active, setActive] = useState<{ rid: number; el: HTMLTextAreaElement } | null>(null);
  const insert = (tex: string) => {
    const row = active && rows?.find((r) => r.id === active.rid);
    if (!active || !row) return;
    const isMath = row.line.math !== null;
    const value = isMath ? (row.line.math ?? "") : row.line.text;
    const el = active.el;
    const a = el.selectionStart ?? value.length;
    const b = el.selectionEnd ?? value.length;
    const wrap = !isMath && !insideMath(value, a);
    const [pre, post] = tex.split("|") as [string, string];
    const head = (wrap ? "$" : "") + pre;
    const picked = value.slice(a, b);
    const next = value.slice(0, a) + head + picked + post + (wrap ? "$" : "") + value.slice(b);
    update(row.id, isMath ? { math: next } : { text: next });
    placeCaret(el, next, a + head.length, a + head.length + picked.length);
  };
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
    if (l.math !== null) update(r.id, { math: null, text: l.math ? `$${l.math}$` : "" });
    else update(r.id, { math: tidyMath(fromDollars(l.text).replace(INLINE, "$1")), text: "" });
  };

  const save = (confirm: boolean) =>
    act.run(async () => {
      // 확인하면 강사가 본 판독문 — 애매한 곳 표시를 지운다 (내용이 같으면 다시 채점하지 않는다)
      const kept = lines
        .map(tidy)
        .filter((l) => l.text.trim() || (l.math ?? "").trim())
        .map((l) => (confirm ? { ...l, confidence: 1, uncertain: [] } : l));
      await api.put<Transcript>(`${base}/students/${enc(target.student)}/transcripts/${enc(target.question)}`, { lines: kept, confirm, by, learn: learn && dirty });
      if (!confirm) {
        setRows(kept.map((line) => ({ id: seq.current++, line: shown(line) })));
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
      desc="회색 칸을 눌러 원본과 다른 곳만 고칩니다. 노란 줄은 AI가 애매하다고 본 줄 — 맞으면 [맞음]. 식은 $수식$처럼 $로 감쌉니다 (예: $\frac{1}{2}mv^2$ — 한국어 윈도우에서 \는 ₩로 보여도 같은 글자). 위 '식 넣기' 단추로도 넣을 수 있습니다. 확인하면 확정 판독문이 되고, 고친 답안만 다시 채점합니다."
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
              {unreadable ? `${UNREADABLE} ${unreadable}곳 — 줄 아래에서 채우기·학생이 지운 자리·원본도 못 읽음을 골라야 확인할 수 있습니다.` : "⌘/Ctrl + Enter: 확인"}
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
            <div className="sticky top-0 z-10 mb-3 flex flex-wrap items-center gap-1 rounded-lg border border-line bg-card px-2 py-1.5">
              <span className="mr-1 text-[12px] text-text-2">식 넣기</span>
              {SNIPPETS.map((x) => (
                <button
                  key={x.label}
                  type="button"
                  title={active ? x.title : "먼저 고칠 칸을 누르세요"}
                  disabled={!active}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => insert(x.tex)}
                  className="h-7 min-w-8 cursor-pointer rounded border border-line bg-card px-1.5 text-[13px] text-text-2 hover:border-accent hover:text-accent-text disabled:cursor-default disabled:opacity-40"
                >
                  {x.label}
                </button>
              ))}
              <span className="ml-1 text-[11px] leading-tight text-text-3">칸을 누른 뒤 단추 · 식은 $수식$처럼 $로 감싸기</span>
            </div>
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
                        onText={(text) => update(r.id, r.line.math !== null ? { math: fixWon(text) } : { text: fixWon(text) })}
                        onMath={() => toggleMath(r)}
                        onStruck={() => update(r.id, { struck: !r.line.struck }, false)}
                        onOk={() => update(r.id, {})}
                        active={active?.rid === r.id}
                        onFocusBox={(el) => setActive({ rid: r.id, el })}
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
  onOk,
  onPart,
  onRemove,
  active,
  onFocusBox,
}: {
  row: Row;
  parts: (string | null)[];
  partLabel: (p: string | null) => string;
  onText: (v: string) => void;
  onMath: () => void;
  onStruck: () => void;
  onOk: () => void;
  onPart: (p: string | null) => void;
  onRemove: () => void;
  active: boolean;
  onFocusBox: (el: HTMLTextAreaElement) => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const l = row.line;
  const isMath = l.math !== null;
  const value = isMath ? (l.math ?? "") : l.text;
  const holes = value.split(UNREADABLE).length - 1;
  const shaky = l.confidence < 0.7 || l.uncertain.length > 0;
  const showPreview = isMath || /\\\(|\$|~~|\[(판독불가|지움|못 읽음)\]/.test(l.text);

  /** 바꾼 뒤 커서를 `from`~`to`에 (React가 값을 넣은 다음에). */
  const setAndPlace = (next: string, from: number, to = from) => {
    onText(next);
    if (ref.current) placeCaret(ref.current, next, from, to);
  };
  /** 지운 글씨: 고른 부분이 있으면 그 부분만 `~~…~~`, 없으면 줄 전체. */
  const strike = () => {
    const el = ref.current;
    if (!isMath && el && document.activeElement === el && el.selectionStart !== el.selectionEnd) {
      const [a, b] = [el.selectionStart, el.selectionEnd];
      setAndPlace(`${value.slice(0, a)}~~${value.slice(a, b)}~~${value.slice(b)}`, b + 4);
    } else onStruck();
  };
  /** 첫 [판독불가]: 고르기(원본 보고 채우기) 또는 다른 표시로. */
  const fillHole = () => {
    const at = value.indexOf(UNREADABLE);
    if (at >= 0) setAndPlace(value, at, at + UNREADABLE.length);
  };
  const markHole = (mark: string) => {
    const at = value.indexOf(UNREADABLE);
    if (at >= 0) onText(value.slice(0, at) + mark + value.slice(at + UNREADABLE.length));
  };
  const keep = (e: React.MouseEvent) => e.preventDefault(); // 단추를 눌러도 칸의 커서·고른 글을 지킨다

  return (
    <div className={cn("group rounded-lg border bg-card px-3 py-2", holes ? "border-bad" : shaky ? "border-warn" : "border-line")}>
      <div className="flex items-start gap-2">
        <button
          type="button"
          onClick={onMath}
          title={isMath ? "글 줄로 바꾸기" : "식 줄로 바꾸기 (줄 전체가 식)"}
          className={cn("mt-1.5 h-6 shrink-0 cursor-pointer rounded border px-1.5 text-[12px]", isMath ? "border-accent bg-accent-soft text-accent-text" : "border-line bg-card text-text-2 hover:border-text-3")}
        >
          {isMath ? "식" : "글"}
        </button>
        <div className="min-w-0 flex-1">
          <AutoText ref={ref} value={value} onChange={onText} onFocus={() => ref.current && onFocusBox(ref.current)} struck={l.struck} mono={isMath} active={active} />
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          {parts.length > 1 && (
            <select value={l.part ?? ""} onChange={(e) => onPart(e.target.value === "" ? null : e.target.value)} className="h-7 cursor-pointer rounded border border-line bg-card px-1 text-[12px] text-text-2" aria-label="소문항">
              {parts.map((p) => (
                <option key={p ?? ""} value={p ?? ""}>
                  {partLabel(p)}
                </option>
              ))}
            </select>
          )}
          {shaky && !holes && (
            <Button size="sm" variant="outline" className="h-7 px-2 text-[12px]" title="AI가 읽은 그대로 맞습니다 — 노란 표시를 지웁니다" onClick={onOk}>
              <Check /> 맞음
            </Button>
          )}
          <Button
            size="icon"
            variant="ghost"
            className={cn("size-7", l.struck && "bg-muted")}
            title={l.struck ? "줄 전체 지운 표시 풀기" : "학생이 지운 글씨 — 글을 끌어 고르고 누르면 그 부분만, 고르지 않으면 줄 전체"}
            aria-label="지운 글씨"
            onMouseDown={keep}
            onClick={strike}
          >
            {l.struck ? <Minus /> : <Strikethrough />}
          </Button>
          <Button size="icon" variant="ghost" className="size-7" title="줄 빼기" aria-label="줄 빼기" onClick={onRemove}>
            <Trash2 />
          </Button>
        </div>
      </div>
      {holes > 0 && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 pl-9 text-[12px]">
          <span className="unreadable">{UNREADABLE}</span>
          <span className="text-text-2">{holes > 1 ? `${holes}곳 — 앞에서부터:` : "원본을 보고:"}</span>
          <Button size="sm" variant="outline" className="h-7 px-2 text-[12px]" title="칸에서 [판독불가]를 골라 둡니다 — 바로 고쳐 쓰세요" onClick={fillHole}>
            <Pencil /> 채우기
          </Button>
          <Button size="sm" variant="outline" className="h-7 px-2 text-[12px]" title="학생이 지워서 읽을 것이 없는 자리 — 채점에서 뺍니다" onClick={() => markHole(ERASED)}>
            학생이 지운 자리
          </Button>
          <Button size="sm" variant="outline" className="h-7 px-2 text-[12px]" title="원본을 봐도 읽을 수 없음 — 그대로 확인하고, 채점은 못 읽은 자리로 합니다" onClick={() => markHole(ILLEGIBLE)}>
            원본도 못 읽음
          </Button>
        </div>
      )}
      {showPreview && value.trim() && (
        <div className={cn("mt-1.5 ml-9 border-l-2 border-line pl-2.5 text-[14px] leading-[1.9]", l.struck && "text-text-3 line-through")} title="보이는 모양 (미리보기)">
          {isMath ? <TeX latex={tidyMath(value)} /> : <Inline text={fromDollars(value)} />}
        </div>
      )}
      {l.uncertain.length > 0 && <div className="mt-1 pl-9 text-[12px] text-warn">애매한 곳: {l.uncertain.join(" · ")}</div>}
    </div>
  );
}

/** 글 줄: `\( \)` 식은 그리고, [판독불가]는 분홍, 지운 자리·못 읽음은 회색, `~~…~~`는 줄 그음. */
function Inline({ text }: { text: string }) {
  const parts = text.split(INLINE);
  return (
    <>
      {parts.map((p, i) =>
        i % 2 === 1 ? (
          <TeX key={i} latex={p} />
        ) : (
          p.split(/(\[판독불가\]|\[지움\]|\[못 읽음\]|~~.+?~~)/).map((s, j) => {
            const k = `${i}-${j}`;
            if (s === UNREADABLE) return <span key={k} className="unreadable">{s}</span>;
            if (s === ERASED || s === ILLEGIBLE)
              return (
                <span key={k} className="rounded bg-muted px-1 text-[12px] text-text-3">
                  {s === ERASED ? "학생이 지운 자리" : "못 읽음"}
                </span>
              );
            if (s.startsWith("~~") && s.endsWith("~~") && s.length > 4)
              return (
                <span key={k} className="text-text-3 line-through" title="학생이 지운 글씨">
                  {s.slice(2, -2)}
                </span>
              );
            return <span key={k}>{s}</span>;
          })
        ),
      )}
    </>
  );
}

/** 내용만큼 늘어나는 입력 칸 — 고칠 수 있는 칸임이 보이게 테두리·바탕을 둔다. */
function AutoText({
  value,
  onChange,
  onFocus,
  struck,
  mono,
  active,
  ref,
}: {
  value: string;
  onChange: (v: string) => void;
  onFocus: () => void;
  struck: boolean;
  mono: boolean;
  active: boolean;
  ref: React.RefObject<HTMLTextAreaElement | null>;
}) {
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const fit = () => {
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
    };
    fit();
    const ro = new ResizeObserver(fit); // 폭이 바뀌면(맞음 단추가 사라지면) 줄 수도 바뀐다
    ro.observe(el);
    return () => ro.disconnect();
  }, [value, ref]);
  return (
    <textarea
      ref={ref}
      rows={1}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onFocus={onFocus}
      placeholder="눌러서 쓰기"
      title="눌러서 고치기"
      className={cn(
        "block w-full cursor-text resize-none rounded-md border border-line bg-muted px-2 py-1 text-[14px] leading-[1.6] text-text outline-none placeholder:text-text-3 hover:border-text-3 focus:border-accent focus:bg-card",
        active && "border-accent",
        struck && "text-text-3 line-through",
        mono && "font-mono text-[13px]",
      )}
      spellCheck={false}
    />
  );
}
