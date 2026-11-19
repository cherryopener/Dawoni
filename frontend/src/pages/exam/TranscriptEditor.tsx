import { ArrowRight, Check, CircleAlert, ListTree, Minus, Pencil, Plus, Sigma, Strikethrough, Trash2, ZoomIn } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import * as Sel from "@radix-ui/react-select";
import * as S from "@radix-ui/react-switch";
import { TeX } from "@/components/Math";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Help } from "@/components/ui/help";
import { ErrorNote } from "@/components/ui/misc";
import { Workspace } from "@/components/ui/workspace";
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
/** Radix 고르기는 빈 값을 못 쓴다 — 소문항 모름(null) 자리. */
const NO_PART = "__none";
/** 이 화면이 연 방문 기록 칸 — 마우스 뒤로 단추·Alt+←가 이 화면만 닫는다. */
const HISTORY_MARK = "dawoniTranscript";
const HOW_TO =
  "칸을 눌러 원본과 다른 곳만 고칩니다. 노란 줄은 AI가 애매하다고 본 줄 — 맞으면 [맞음]. 식은 $수식$처럼 $로 감쌉니다 " +
  "(예: $\\frac{1}{2}mv^2$ — 한국어 윈도우에서 \\는 ₩로 보여도 같은 글자). 위 '식 넣기' 단추로도 넣을 수 있습니다. " +
  "확인하면 확정 판독문이 되고, 고친 답안만 다시 채점합니다. 줄의 Σ는 줄 전체를 식으로, 취소선은 학생이 지운 글씨.";

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
const marked = () => (window.history.state as Record<string, unknown> | null)?.[HISTORY_MARK] === true;

/** 판독문 고치기 (PLAN.md §5-1 5·6단계, 2026-11-18 HWPX 대신): 왼쪽 원본 쪽, 오른쪽 판독문 줄. 고쳐 "확인
 * 완료"하면 확정 판독문이 되고, 고친 답안은 다시 채점한다 (서버 `PUT …/transcripts/{문항}`). `queue`를 주면
 * "확인하고 다음"으로 차례로 넘어간다.
 *
 * 창(모달)이 아니라 화면 하나를 통째로 쓴다 (2026-11-19 사용자 요청 — "윈도우98 느낌"): 위 줄에 돌아가기·
 * 학생·확인 단추, 아래는 원본과 판독문. 뒤로 가기(마우스 단추·Alt+←)도 돌아가기와 같다. */
export function TranscriptEditor({ target, queue = [], onClose, onChanged }: { target: EditTarget | null; queue?: EditTarget[]; onClose: () => void; onChanged: () => void }) {
  const [cur, setCur] = useState<EditTarget | null>(target);
  const [done, setDone] = useState<string[]>([]);
  /** 고친 줄에서 배우기 (AI 호출 1번씩 — 기본은 끔. 예전 한글 반영은 고친 것을 모아 한 번 배웠다). */
  const [learn, setLearn] = useState(false);
  useEffect(() => {
    setCur(target);
    setDone([]);
  }, [target]);

  /** 떠나도 되는가 — 고친 것이 남았으면 Body가 묻는 줄을 띄우고 false. */
  const mayLeave = useRef<() => boolean>(() => true);
  const force = useRef(false);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const open = cur !== null;
  useEffect(() => {
    if (!open) return;
    window.history.pushState({ [HISTORY_MARK]: true }, "");
    const onPop = () => {
      if (force.current || mayLeave.current()) {
        force.current = false;
        closeRef.current();
      } else window.history.pushState({ [HISTORY_MARK]: true }, ""); // 그대로 두고 묻는다
    };
    window.addEventListener("popstate", onPop);
    return () => {
      window.removeEventListener("popstate", onPop);
      if (marked()) window.history.replaceState(null, ""); // 다른 길로 닫혔으면 남은 칸을 비워 둔다
    };
  }, [open]);
  /** 닫기: 이 화면이 연 방문 기록을 되돌린다 → `onPop`이 닫는다. `discard`면 묻지 않는다. */
  const leave = (discard = false) => {
    if (!discard && !mayLeave.current()) return;
    force.current = true;
    if (marked()) window.history.back();
    else closeRef.current();
  };

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
      guard={mayLeave}
      onLeave={leave}
      onSaved={(confirmed) => {
        onChanged();
        if (!confirmed) return;
        setDone((d) => [...d, keyOf(cur)]);
        if (next) setCur(next);
        else leave(true);
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
  guard,
  onLeave,
  onSaved,
}: {
  target: EditTarget;
  next?: EditTarget;
  left: number;
  learn: boolean;
  setLearn: (v: boolean) => void;
  guard: React.RefObject<() => boolean>;
  onLeave: (discard?: boolean) => void;
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
  guard.current = () => {
    if (!dirty) return true;
    setLeaving(true);
    return false;
  };

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
  const canConfirm = !act.busy && rows !== null && unreadable === 0;

  useEffect(() => {
    const f = (e: KeyboardEvent) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && canConfirm) {
        e.preventDefault();
        void save(true);
      } else if (e.key === "Escape" && !e.defaultPrevented && !document.querySelector("[data-radix-popper-content-wrapper]")) {
        // Esc: 확대한 원본이면 100%로, 아니면 돌아가기 (고친 것이 있으면 묻는다)
        if (zoom !== 1) setZoom(1);
        else onLeave();
      }
    };
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  });

  const t = v?.transcript;
  const who = v ? `${v.class_name ? `${v.class_name} ` : ""}${v.name}` : target.student;
  return (
    <Workspace
      onBack={() => onLeave()}
      escape={false}
      title={
        <>
          <span className="truncate text-[15px] font-medium">{who}</span>
          <span className="text-[15px] text-text-2">{target.question}번</span>
          {t?.confirmed &&
            (t.confirmed_by === "AI" ? (
              <span title="[판독불가]가 없어 AI가 읽은 그대로 확정했습니다 — 고쳐 확인하면 강사 확인이 됩니다">
                <Badge tone="accent">AI 확인</Badge>
              </span>
            ) : (
              <Badge tone="ok">확인 완료{t.confirmed_by ? ` (${t.confirmed_by})` : ""}</Badge>
            ))}
          {t && !t.confirmed && <Badge tone="warn">확인 전</Badge>}
          <Help>{HOW_TO}</Help>
          {left > 1 && <span className="ml-1 shrink-0 rounded-full bg-muted px-2.5 py-0.5 text-[12px] text-text-2">남은 답안 {left}</span>}
        </>
      }
      actions={
        leaving ? (
          <>
            <span className="text-[13px] text-warn">고친 내용을 저장하지 않았습니다.</span>
            <Button variant="ghost" size="sm" className="h-9" onClick={() => setLeaving(false)}>
              계속 고치기
            </Button>
            <Button variant="danger" size="sm" className="h-9" onClick={() => onLeave(true)}>
              버리고 나가기
            </Button>
          </>
        ) : (
          <>
            {unreadable > 0 ? (
              <span className="flex items-center gap-1.5 text-[13px] text-bad" title="줄 아래에서 채우기·학생이 지운 자리·원본도 못 읽음을 골라야 확인할 수 있습니다">
                <CircleAlert className="size-4" />
                {UNREADABLE} {unreadable}곳 남음
              </span>
            ) : (
              <span className="text-[12px] text-text-3">⌘/Ctrl + Enter로 확인</span>
            )}
            <label className="flex cursor-pointer items-center gap-2 text-[13px] text-text-2" title="고친 줄을 AI가 보고 다음 판독에 쓸 규칙을 제안합니다 (고칠 때마다 AI 호출 1번)">
              <S.Root checked={learn} onCheckedChange={setLearn} className="relative h-5 w-8 shrink-0 cursor-pointer rounded-full bg-line transition-colors data-[state=checked]:bg-accent">
                <S.Thumb className="block size-4 translate-x-0.5 rounded-full bg-white transition-transform data-[state=checked]:translate-x-[14px]" />
              </S.Root>
              수정에서 배우기
            </label>
            <Button variant="ghost" size="sm" className="h-9" disabled={act.busy || !dirty} onClick={() => void save(false)}>
              저장만
            </Button>
            <Button variant="primary" size="sm" className="h-9 px-4" disabled={!canConfirm} onClick={() => void save(true)}>
              <Check /> {next ? "확인하고 다음" : "확인 완료"}
              {next && <ArrowRight />}
            </Button>
          </>
        )
      }
    >

      {(view.error || act.error) && (
        <div className="shrink-0 px-4 pt-3">
          <ErrorNote message={(view.error ?? act.error)!} />
        </div>
      )}
      {!v && !view.error && <div className="p-8 text-text-3">불러오는 중…</div>}
      {v && rows && (
        <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
          <PagePane examId={id} images={v.page_images} zoom={zoom} setZoom={setZoom} />
          <div className="min-h-0 overflow-auto border-l border-line bg-card">
            <div className="sticky top-0 z-10 border-b border-line bg-card/95 backdrop-blur">
              <div className="mx-auto flex max-w-[760px] flex-wrap items-center gap-0.5 px-6 py-2">
                <span className="mr-2 text-[12px] text-text-3">식 넣기</span>
                {SNIPPETS.map((x) => (
                  <button
                    key={x.label}
                    type="button"
                    title={active ? x.title : "먼저 고칠 칸을 누르세요"}
                    disabled={!active}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => insert(x.tex)}
                    className="h-7 min-w-8 cursor-pointer rounded-md px-1.5 text-[13px] text-text-2 hover:bg-muted hover:text-text disabled:cursor-default disabled:opacity-35 disabled:hover:bg-transparent"
                  >
                    {x.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="mx-auto max-w-[760px] px-6 pt-5 pb-16">
              {t && t.notes.length > 0 && <div className="mb-4 rounded-lg bg-muted px-3 py-2 text-[12px] text-text-2">{t.notes.join(" · ")}</div>}
              {sections.map((part) => {
                const mine = rows.filter((r) => r.line.part === part);
                const pts = part === null ? (q?.parts.length ? null : q?.points) : q?.parts.find((p) => p.label === part)?.points;
                return (
                  <section key={part ?? ""} className="mb-7">
                    <div className="mb-2.5 flex items-baseline gap-2">
                      <span className="text-[14px] font-medium">{partLabel(part)}</span>
                      {pts != null && <span className="text-[12px] text-text-3">{pts}점</span>}
                      {mine.length === 0 && <span className="text-[12px] text-text-3">빈 답안</span>}
                    </div>
                    <div className="flex flex-col gap-3">
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
                    <button
                      type="button"
                      onClick={() => add(part)}
                      className="mt-2 flex h-8 cursor-pointer items-center gap-1.5 rounded-md px-2 text-[13px] text-text-3 hover:bg-muted hover:text-text-2 [&_svg]:size-4"
                    >
                      <Plus /> 줄 더하기
                    </button>
                  </section>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </Workspace>
  );
}

const PAD = 16; // 원본 칸 안쪽 여백 (p-4) — 확대해도 그대로라 누른 자리를 맞출 때 뺀다

/** 원본 쪽: 돋보기처럼 누른 자리를 150 → 200 → 300%로 키우고, 300%에서 누르면 100%로. Esc도 100%로.
 * 2026-11-19 사용자 요청. 아래 % 단추로 바로 고를 수도 있다. */
export function PagePane({ examId, images, zoom, setZoom }: { examId: string; images: string[]; zoom: number; setZoom: (z: number) => void }) {
  const box = useRef<HTMLDivElement>(null);
  /** 누른 자리 (내용 좌표, 칸 안 좌표, 배율 변화) — 다시 그린 뒤 그 자리가 포인터 밑에 오게 굴린다. */
  const anchor = useRef<{ x: number; y: number; cx: number; cy: number; f: number } | null>(null);
  useLayoutEffect(() => {
    const a = anchor.current;
    const el = box.current;
    anchor.current = null;
    if (!a || !el) return;
    el.scrollLeft = (a.x - PAD) * a.f + PAD - a.cx;
    el.scrollTop = (a.y - PAD) * a.f + PAD - a.cy;
  }, [zoom]);
  const top = ZOOMS[ZOOMS.length - 1]!;
  const click = (e: React.MouseEvent<HTMLDivElement>) => {
    const el = box.current;
    if (!el || images.length === 0) return;
    const i = ZOOMS.indexOf(zoom);
    const next = zoom >= top || i < 0 ? 1 : ZOOMS[i + 1]!;
    const r = el.getBoundingClientRect();
    const cx = e.clientX - r.left;
    const cy = e.clientY - r.top;
    anchor.current = { x: el.scrollLeft + cx, y: el.scrollTop + cy, cx, cy, f: next / zoom };
    setZoom(next);
  };
  return (
    <div className="relative min-h-0 bg-muted">
      <div ref={box} onClick={click} className={cn("h-full overflow-auto p-4 pb-16", images.length > 0 && (zoom >= top ? "cursor-zoom-out" : "cursor-zoom-in"))}>
        {images.length === 0 && <div className="p-6 text-center text-text-3">원본 그림 없음 (입력한 답안)</div>}
        <div className="flex flex-col gap-4" style={{ width: `${zoom * 100}%` }}>
          {images.map((p) => (
            <img key={p} src={examFileUrl(examId, p)} alt="원본 쪽" draggable={false} className="w-full rounded-md border border-line bg-white" />
          ))}
        </div>
      </div>
      {images.length > 0 && (
        <div className="absolute bottom-4 left-1/2 flex -translate-x-1/2 items-center gap-0.5 whitespace-nowrap rounded-full border border-line bg-card p-1 text-[12px] text-text-2">
          <ZoomIn className="mx-1.5 size-3.5 text-text-3" />
          {ZOOMS.map((z) => (
            <button
              key={z}
              type="button"
              onClick={() => setZoom(z)}
              className={cn("h-7 cursor-pointer rounded-full px-2.5 hover:bg-muted", zoom === z && "bg-accent-soft font-medium text-accent-text hover:bg-accent-soft")}
            >
              {z * 100}%
            </button>
          ))}
          <span className="mx-2 text-text-3" title="원본을 누르면 확대 · Esc 100%">
            {images.length}쪽
          </span>
        </div>
      )}
    </div>
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
  const tone = holes ? "bad" : shaky ? "warn" : null;
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
  const tool = "grid size-8 shrink-0 cursor-pointer place-items-center rounded-md text-text-3 hover:bg-muted hover:text-text [&_svg]:size-4";

  return (
    <div className="group">
      <div className="flex items-start gap-1.5">
        <div className="min-w-0 flex-1">
          <AutoText ref={ref} value={value} onChange={onText} onFocus={() => ref.current && onFocusBox(ref.current)} struck={l.struck} mono={isMath} active={active} tone={tone} />
        </div>
        {/* 줄 도구는 폭을 고정해 칸의 오른쪽 끝을 맞춘다. 늘 켜 둔 것(식 줄·지운 줄)이 아니면 마우스를 올리거나 칸을 누를 때만 */}
        <div className="flex h-10 w-[136px] shrink-0 items-center justify-end gap-0.5">
          <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
            {parts.length > 1 && <PartPicker value={l.part} parts={parts} label={partLabel} onChange={onPart} className={tool} />}
          </div>
          <div className={cn("flex items-center gap-0.5 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100", (isMath || l.struck) && "opacity-100")}>
            <button type="button" onClick={onMath} title={isMath ? "글 줄로 바꾸기" : "식 줄로 바꾸기 (줄 전체가 식)"} aria-label="식 줄" className={cn(tool, isMath && "bg-accent-soft text-accent-text hover:bg-accent-soft hover:text-accent-text")}>
              <Sigma />
            </button>
            <button
              type="button"
              onMouseDown={keep}
              onClick={strike}
              title={l.struck ? "줄 전체 지운 표시 풀기" : "학생이 지운 글씨 — 글을 끌어 고르고 누르면 그 부분만, 고르지 않으면 줄 전체"}
              aria-label="지운 글씨"
              className={cn(tool, l.struck && "bg-muted text-text")}
            >
              {l.struck ? <Minus /> : <Strikethrough />}
            </button>
            <button type="button" onClick={onRemove} title="줄 빼기" aria-label="줄 빼기" className={cn(tool, "hover:bg-bad-soft hover:text-bad")}>
              <Trash2 />
            </button>
          </div>
        </div>
      </div>
      {holes > 0 && (
        <div className="mt-1.5 mr-[136px] flex flex-wrap items-center gap-1.5 pl-1 text-[12px]">
          <span className="text-bad">{holes > 1 ? `읽지 못한 곳 ${holes}곳 — 앞에서부터:` : "읽지 못한 곳 — 원본을 보고:"}</span>
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
        <div className={cn("mt-1.5 mr-[136px] px-3 text-[14px] leading-[1.9] text-text-2", l.struck && "text-text-3 line-through")} title="보이는 모양 (미리보기)">
          {isMath ? <TeX latex={tidyMath(value)} /> : <Inline text={fromDollars(value)} />}
        </div>
      )}
      {shaky && !holes && (
        <div className="mt-1.5 mr-[136px] flex items-start gap-2 pl-1 text-[12px] leading-relaxed text-warn">
          <CircleAlert className="mt-[3px] size-3.5 shrink-0" />
          <span className="min-w-0 flex-1" title="AI가 애매하다고 본 곳">
            {l.uncertain.length > 0 ? l.uncertain.join(" · ") : "AI가 확신하지 못한 줄"}
          </span>
          <button
            type="button"
            onClick={onOk}
            title="AI가 읽은 그대로 맞습니다 — 노란 표시를 지웁니다"
            className="-mt-0.5 flex h-6 shrink-0 cursor-pointer items-center gap-1 rounded-md border border-line bg-card px-2 text-[12px] font-medium text-text hover:bg-muted [&_svg]:size-3.5"
          >
            <Check /> 맞음
          </button>
        </div>
      )}
      {holes > 0 && l.uncertain.length > 0 && (
        <div className="mt-1.5 flex items-start gap-2 pl-1 text-[12px] leading-relaxed text-warn" title="AI가 애매하다고 본 곳">
          <CircleAlert className="mt-[3px] size-3.5 shrink-0" />
          <span>{l.uncertain.join(" · ")}</span>
        </div>
      )}
    </div>
  );
}

/** 줄을 다른 소문항으로 옮기기 — 드물게 쓰므로 아이콘 하나 (누르면 목록). */
function PartPicker({ value, parts, label, onChange, className }: { value: string | null; parts: (string | null)[]; label: (p: string | null) => string; onChange: (p: string | null) => void; className: string }) {
  return (
    <Sel.Root value={value ?? NO_PART} onValueChange={(p) => onChange(p === NO_PART ? null : p)}>
      <Sel.Trigger className={className} title={`소문항 옮기기 (지금 ${label(value)})`} aria-label="소문항">
        <ListTree />
      </Sel.Trigger>
      <Sel.Portal>
        <Sel.Content position="popper" side="bottom" align="end" sideOffset={4} className="z-50 min-w-32 rounded-lg border border-line bg-card p-1 text-[13px] text-text">
          <div className="px-2 pt-1 pb-1.5 text-[11px] text-text-3">소문항 옮기기</div>
          <Sel.Viewport>
            {parts.map((p) => (
              <Sel.Item key={p ?? NO_PART} value={p ?? NO_PART} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 outline-none data-[highlighted]:bg-muted">
                <Sel.ItemText>{label(p)}</Sel.ItemText>
                <Sel.ItemIndicator className="ml-auto">
                  <Check className="size-3.5" />
                </Sel.ItemIndicator>
              </Sel.Item>
            ))}
          </Sel.Viewport>
        </Sel.Content>
      </Sel.Portal>
    </Sel.Root>
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

/** 내용만큼 늘어나는 입력 칸 — 흰 바탕에 얇은 테두리, 누르면 파란 테두리와 옅은 둘레 (보통 입력 칸 모양).
 * 노랑·빨강 테두리는 AI가 애매하다고 본 줄·[판독불가]가 남은 줄. */
function AutoText({
  value,
  onChange,
  onFocus,
  struck,
  mono,
  active,
  tone,
  ref,
}: {
  value: string;
  onChange: (v: string) => void;
  onFocus: () => void;
  struck: boolean;
  mono: boolean;
  active: boolean;
  tone: "warn" | "bad" | null;
  ref: React.RefObject<HTMLTextAreaElement | null>;
}) {
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const fit = () => {
      // border-box라 scrollHeight에 테두리를 더해야 한다 — 모자라면 윈도우가 ▲▼ 스크롤 막대를 그린다
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight + el.offsetHeight - el.clientHeight}px`;
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
      placeholder="여기에 쓰기"
      className={cn(
        "block min-h-10 w-full cursor-text resize-none overflow-hidden rounded-lg border bg-card px-3 py-[7px] text-[15px] leading-[1.6] text-text outline-none transition-[border-color,box-shadow] placeholder:text-text-3",
        "hover:border-text-3 focus:border-accent focus:bg-card focus:ring-3 focus:ring-accent-soft",
        tone === "bad" ? "border-bad/70" : tone === "warn" ? "border-warn/60 bg-warn-soft/40" : "border-line",
        active && "border-accent",
        struck && "text-text-3 line-through",
        mono && "font-mono text-[14px]",
      )}
      spellCheck={false}
    />
  );
}
