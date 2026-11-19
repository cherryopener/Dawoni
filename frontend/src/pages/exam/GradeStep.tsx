import { ArrowRight, Check, CircleAlert, FileImage, Minus, PenLine, Plus, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { RichText, TeX } from "@/components/Math";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Help } from "@/components/ui/help";
import { ErrorNote, Input, Progress } from "@/components/ui/misc";
import { Segmented } from "@/components/ui/segmented";
import { Select } from "@/components/ui/select";
import { api, enc, useAction, useApi, waitJob } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useTeacher } from "@/lib/session";
import { pts } from "@/lib/steps";
import type { BoardRow, ExamStudent, Job, Piece, ReviewSheet } from "@/lib/types";
import { useExam } from "../Exam";
import { type EditTarget, PagePane, TranscriptEditor } from "./TranscriptEditor";

type Assignments = Record<string, { teacher: string | null }>;
type Group = "검토 필요" | "확인 전" | "입력 대기" | "채점 전" | "완료";
const GROUPS: Group[] = ["검토 필요", "확인 전", "입력 대기", "채점 전", "완료"];

function groupOf(s: ExamStudent, q: string): Group | null {
  const c = s.cells[q];
  if (!c?.transcript) return null;
  const g = c.grade;
  if (!g) return c.transcript.unreadable ? "입력 대기" : "채점 전";
  if (g.stale && g.status !== "teacher_confirmed") return "채점 전";
  if (g.status === "needs_review") return "검토 필요";
  return g.status === "teacher_confirmed" ? "완료" : "확인 전";
}

/** ④ 채점 검토: 왼쪽 학생(할 일 순서), 가운데 원본, 오른쪽 판독문·점수·이유 (PLAN §11-1-4·5, uiexample.png). */
export function GradeStep() {
  const { base, detail, focus } = useExam();
  const me = useTeacher();
  const assign = useApi<Assignments>(`${base}/assign`);
  const [view, setView] = useState<"review" | "board">("review");
  const numbers = detail.exam.questions.map((q) => q.number);
  const mine = numbers.find((n) => assign.data?.[n]?.teacher === me);
  // 학생 검색에서 `#/exams/<시험>/grade/<문항>/<학생>`으로 바로 연다
  const [picked, setPicked] = useState<string | null>(numbers.includes(focus[0] ?? "") ? focus[0]! : null);
  const q = picked ?? mine ?? numbers[0]!;
  const label = (n: string) => {
    const t = assign.data?.[n]?.teacher;
    return `${n}번 · ${t === me ? "내 문항" : (t ?? "맡은 사람 없음")}`;
  };
  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-3 border-b border-line bg-card px-4 py-2">
        <Segmented
          value={view}
          onChange={setView}
          options={[
            { value: "review", label: "검토" },
            { value: "board", label: "진행판" },
          ]}
        />
        {view === "review" && <Select value={q} onChange={setPicked} className="min-w-40" options={numbers.map((n) => ({ value: n, label: label(n) }))} />}
      </div>
      <div className="min-h-0 flex-1">{view === "review" ? <Review key={q} q={q} start={q === focus[0] ? focus[1] : undefined} /> : <Board assign={assign.data ?? {}} onChanged={assign.reload} />}</div>
    </div>
  );
}

function Pieces({ pieces, uncertain }: { pieces: Piece[]; uncertain: string[] }) {
  return (
    <>
      {pieces.map((p, i) => {
        const cls = p.kind === "good" ? "quote-ok" : p.kind === "bad" ? "quote-bad" : undefined;
        if (p.math) return <TeX key={i} latex={p.text} className={cn(cls, p.kind === "good" && "text-ok", p.kind === "bad" && "text-bad")} />;
        return (
          <span key={i} className={cls}>
            <Marked text={p.text} uncertain={uncertain} />
          </span>
        );
      })}
    </>
  );
}

/** 불확실한 글자(노랑)·[판독불가](분홍) — 판독문 고치기 화면과 같은 색. */
function Marked({ text, uncertain }: { text: string; uncertain: string[] }) {
  const words = ["[판독불가]", ...uncertain.filter((u) => u.length > 0)];
  const re = new RegExp(`(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "g");
  return (
    <>
      {text.split(re).map((t, i) =>
        t === "[판독불가]" ? (
          <span key={i} className="unreadable">
            {t}
          </span>
        ) : words.includes(t) ? (
          <span key={i} className="unsure">
            {t}
          </span>
        ) : (
          <span key={i}>{t}</span>
        ),
      )}
    </>
  );
}

const GROUP_DOT: Record<Group, string> = {
  "검토 필요": "bg-warn",
  "확인 전": "bg-accent",
  "입력 대기": "bg-bad",
  "채점 전": "bg-line-strong",
  완료: "bg-ok",
};

/** 채점 검토 (2026-11-20 다시 그림 — "윈도우98 느낌"에서): 왼쪽 할 일 순서 학생 목록, 가운데 원본(누르면 확대),
 * 오른쪽 점수 판. 점수는 따로 '고치기' 모드 없이 소문항마다 −/+로 바로 바꾸고, 바꾸면 AI 점수와 이유 칸이
 * 보인다. 아래 줄에 판독문 고치기·확정하고 다음. */
function Review({ q, start }: { q: string; start?: string }) {
  const { id, base, reload } = useExam();
  const by = useTeacher();
  const students = useApi<ExamStudent[]>(`${base}/students`);
  const order = useMemo(() => {
    const list = (students.data ?? []).map((s) => ({ s, g: groupOf(s, q) })).filter((x) => x.g !== null);
    return GROUPS.flatMap((g) => list.filter((x) => x.g === g));
  }, [students.data, q]);
  const [sel, setSel] = useState<string | null>(start ?? null);
  const [fixing, setFixing] = useState<EditTarget | null>(null);
  const [zoom, setZoom] = useState(1);
  const cur = sel ?? order[0]?.s.student ?? null;
  const sheet = useApi<ReviewSheet>(cur ? `${base}/review/${enc(cur)}/${enc(q)}` : null);
  const [scores, setScores] = useState<Record<string, number>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const act = useAction();
  const sh = sheet.data?.student === cur ? sheet.data : undefined;
  const parts = (sh?.parts ?? []).filter((p) => p.awarded !== null);
  const done = order.filter((x) => x.g === "완료").length;
  const curGroup = order.find((x) => x.s.student === cur)?.g ?? null;
  const total = sh && sh.total !== null ? parts.reduce((n, p) => n + (scores[p.label] ?? p.awarded ?? 0), 0) : null;
  const changedAny = parts.some((p) => scores[p.label] !== undefined && scores[p.label] !== p.awarded);

  const pick = (student: string) => {
    setSel(student);
    setScores({});
    setNotes({});
    setZoom(1);
  };
  const next = () => {
    const i = order.findIndex((x) => x.s.student === cur);
    const n = order.slice(i + 1).find((x) => x.g !== "완료") ?? order[i + 1];
    if (n) pick(n.s.student);
  };
  const confirm = () =>
    act.run(async () => {
      if (!sh || sh.total === null) return next();
      const key = (label: string) => (label.startsWith(`${q}-`) ? label.slice(q.length + 1) : "");
      const part_scores = Object.fromEntries(parts.map((p) => [key(p.label), scores[p.label] ?? p.awarded!]));
      // 바꾼 소문항이 있으면 채점 사례집에 남는다 (이유는 선택)
      const changed = parts.filter((p) => scores[p.label] !== undefined && scores[p.label] !== p.awarded);
      const part_notes = Object.fromEntries(changed.map((p) => [key(p.label), (notes[p.label] ?? "").trim()]).filter(([, v]) => v));
      await api.put(`${base}/students/${enc(sh.student)}/grades/${enc(q)}/parts`, { part_scores, part_notes, by, learn: changed.length > 0 });
      students.reload();
      reload();
      next();
    });
  const gradeNow = () =>
    act.run(async () => {
      await waitJob(await api.post<Job>(`${base}/grade`, { questions: [q], students: cur ? [cur] : undefined, by }));
      students.reload();
      sheet.reload();
    });

  useEffect(() => {
    const f = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      // 판독문 고치기 화면·창이 열려 있으면 그쪽의 키
      if (fixing || document.querySelector("[role=dialog], [data-workspace], [data-radix-popper-content-wrapper]")) return;
      if (e.key === "Enter") void confirm();
      else if (e.key === "Escape" && zoom !== 1) setZoom(1);
      else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (e.key === "ArrowDown") next();
        else {
          const i = order.findIndex((x) => x.s.student === cur);
          if (i > 0) pick(order[i - 1]!.s.student);
        }
      }
    };
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  });

  if (students.data && order.length === 0) {
    return <div className="p-8 text-center text-text-2">{q}번 판독문이 아직 없습니다. 답안 판독을 먼저 하세요.</div>;
  }
  return (
    <div className="flex h-full">
      <aside className="flex w-64 shrink-0 flex-col border-r border-line bg-card">
        <div className="shrink-0 border-b border-line px-4 py-3">
          <div className="flex items-baseline justify-between text-[12px] text-text-2">
            <span>확정</span>
            <span className="tabular-nums">
              <span className="font-medium text-text">{done}</span> / {order.length}
            </span>
          </div>
          <div className="mt-2">
            <Progress value={order.length ? (done / order.length) * 100 : 0} tone={done === order.length && done > 0 ? "ok" : "accent"} />
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-2">
          {GROUPS.map((g) => {
            const list = order.filter((x) => x.g === g);
            if (list.length === 0) return null;
            return (
              <div key={g} className="mb-2">
                <div className="flex items-center gap-2 px-2.5 pt-2 pb-1 text-[12px] text-text-3">
                  <span className={cn("size-1.5 rounded-full", GROUP_DOT[g])} />
                  {g}
                  <span className="tabular-nums">{list.length}</span>
                </div>
                {list.map(({ s }) => {
                  const c = s.cells[q]!;
                  const here = cur === s.student;
                  return (
                    <button
                      key={s.student}
                      type="button"
                      onClick={() => pick(s.student)}
                      className={cn(
                        "relative flex w-full cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-left hover:bg-muted",
                        here && "bg-muted before:absolute before:inset-y-2 before:left-0 before:w-[3px] before:rounded-full before:bg-primary",
                      )}
                    >
                      <span className="min-w-0 flex-1">
                        <span className={cn("block truncate", here && "font-medium")}>{s.record?.name ?? s.student}</span>
                        <span className="block truncate text-[12px] text-text-3">
                          {s.record?.class_name}
                          {c.grade?.disagreements ? " · 두 채점이 다름" : ""}
                          {c.transcript?.unreadable ? ` · [판독불가] ${c.transcript.unreadable}곳` : ""}
                        </span>
                      </span>
                      <span className={cn("shrink-0 text-[13px] tabular-nums", g === "완료" ? "text-text-2" : g === "입력 대기" ? "text-bad" : "text-text")}>
                        {c.grade ? (
                          <>
                            {pts(c.grade.total)}
                            <span className="text-text-3">/{pts(c.grade.max)}</span>
                          </>
                        ) : (
                          "—"
                        )}
                      </span>
                    </button>
                  );
                })}
              </div>
            );
          })}
        </div>
      </aside>

      <div className="min-w-0 flex-1">
        {sh && sh.page_images.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 bg-muted text-text-3">
            <FileImage className="size-8" />
            원본 그림 없음 (입력한 답안)
          </div>
        ) : (
          <div className="grid h-full">
            <PagePane examId={id} images={sh?.page_images ?? []} zoom={zoom} setZoom={setZoom} />
          </div>
        )}
      </div>

      <div className="flex w-[44%] min-w-[400px] max-w-[640px] shrink-0 flex-col border-l border-line bg-card">
        <div className="shrink-0 border-b border-line px-6 py-4">
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate text-[17px] font-medium">{sh?.name ?? cur}</span>
                {curGroup && curGroup !== "완료" && curGroup !== "확인 전" && <Badge tone={curGroup === "입력 대기" ? "bad" : curGroup === "검토 필요" ? "warn" : "neutral"}>{curGroup}</Badge>}
                {curGroup === "완료" && (
                  <Badge tone="ok">
                    <Check /> 확정
                  </Badge>
                )}
              </div>
              <div className="mt-0.5 flex items-center gap-1.5 text-[13px] text-text-3">
                {sh?.class_name && <span>{sh.class_name}</span>}
                {sh?.class_name && <span>·</span>}
                <span>{q}번</span>
                {sh?.graded_before_confirm && (
                  <>
                    <span>·</span>
                    <span className="flex items-center gap-1">
                      판독문 확인 전 채점 <Help>판독문을 확정하기 전에 채점했습니다. 판독문을 고쳐 반영하면 다시 채점합니다.</Help>
                    </span>
                  </>
                )}
              </div>
            </div>
            {total !== null && sh && (
              <div className="text-right">
                <div className="text-[26px] leading-none font-medium tabular-nums">
                  {pts(total)}
                  <span className="text-[16px] text-text-3"> / {pts(sh.max)}</span>
                </div>
                {changedAny && <div className="mt-1 text-[12px] text-accent-text">AI {pts(sh.total)}점에서 바꿈</div>}
              </div>
            )}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-auto px-6 py-5">
          {!sh && <div className="text-text-3">불러오는 중…</div>}
          {sh && sh.total === null && (
            <div className="rounded-xl bg-muted px-4 py-3 text-[13px] text-text-2">
              {sh.unreadable ? "[판독불가]가 남아 있어 채점 전입니다. '판독문 고치기'에서 원본을 보고 채우세요." : "아직 채점하지 않았습니다."}
            </div>
          )}
          {sh?.disagreements.map((d) => (
            <div key={d} className="mb-4 flex items-start gap-2 rounded-xl bg-warn-soft px-4 py-3 text-[13px] text-warn">
              <CircleAlert className="mt-0.5 size-4 shrink-0" />
              두 채점이 다름: {d}
            </div>
          ))}
          {sh?.parts.map((p) => {
            const score = scores[p.label] ?? p.awarded;
            const moved = score !== null && p.awarded !== null && score !== p.awarded;
            const setScore = (v: number) => setScores({ ...scores, [p.label]: Math.max(0, Math.min(p.points, v)) });
            return (
              <section key={p.label} className="mb-7 last:mb-0">
                <div className="mb-3 flex items-center gap-2">
                  <span className="text-[15px] font-medium">{p.label}</span>
                  <div className="flex-1" />
                  {score !== null && (
                    <div
                      className={cn(
                        "flex items-center rounded-full border p-0.5",
                        moved ? "border-accent bg-accent-soft" : score === p.points ? "border-transparent bg-ok-soft" : "border-transparent bg-warn-soft",
                      )}
                    >
                      <button type="button" aria-label="1점 빼기" title="1점 빼기" disabled={score <= 0} onClick={() => setScore(score - 1)} className="grid size-7 cursor-pointer place-items-center rounded-full text-text-2 hover:bg-card disabled:cursor-default disabled:opacity-30 disabled:hover:bg-transparent">
                        <Minus className="size-3.5" />
                      </button>
                      <span className={cn("min-w-14 text-center text-[14px] font-medium tabular-nums", moved ? "text-accent-text" : score === p.points ? "text-ok" : "text-warn")}>
                        {pts(score)} / {pts(p.points)}
                      </span>
                      <button type="button" aria-label="1점 더하기" title="1점 더하기" disabled={score >= p.points} onClick={() => setScore(score + 1)} className="grid size-7 cursor-pointer place-items-center rounded-full text-text-2 hover:bg-card disabled:cursor-default disabled:opacity-30 disabled:hover:bg-transparent">
                        <Plus className="size-3.5" />
                      </button>
                    </div>
                  )}
                </div>
                {moved && (
                  <div className="mb-3">
                    <div className="mb-1.5 flex items-center gap-2 text-[12px] text-text-2">
                      AI {pts(p.awarded)}점 → {pts(score)}점
                      <button type="button" onClick={() => setScore(p.awarded!)} className="cursor-pointer text-text-3 underline-offset-2 hover:text-text hover:underline">
                        되돌리기
                      </button>
                    </div>
                    <Input
                      value={notes[p.label] ?? ""}
                      onChange={(e) => setNotes({ ...notes, [p.label]: e.target.value })}
                      placeholder="왜 바꿨나요? (선택 — 예: '더 많이 꺾인다'도 굴절률이 크다는 뜻으로 인정)"
                      className="w-full"
                    />
                    <div className="mt-1 text-[12px] text-text-3">확정하면 채점 사례집(배우기)에 남아 다음 시험의 해설 다듬기·루브릭에 쓰입니다.</div>
                  </div>
                )}
                <div className="rounded-xl bg-muted px-4 py-3 text-[15px] leading-[2]">
                  {p.lines.length === 0 && <span className="text-text-3">빈 답안</span>}
                  {p.lines.map((line, i) => (
                    <div key={i} className={cn(line.struck && "text-text-3 line-through")}>
                      <Pieces pieces={line.pieces} uncertain={line.uncertain} />
                    </div>
                  ))}
                </div>
                {(p.good.length > 0 || p.bad.length > 0) && (
                  <ul className="m-0 mt-3 flex list-none flex-col gap-2 p-0">
                    {p.good.map((r, i) => (
                      <li key={`g${i}`} className="flex items-start gap-2.5 text-[14px] leading-relaxed">
                        <span className="mt-[3px] grid size-[18px] shrink-0 place-items-center rounded-full bg-ok-soft text-ok">
                          <Check className="size-3" strokeWidth={3} />
                        </span>
                        <RichText text={r.reason} className="flex-1" />
                      </li>
                    ))}
                    {p.bad.map((r, i) => (
                      <li key={`b${i}`} className="flex items-start gap-2.5 text-[14px] leading-relaxed">
                        <span className="mt-[3px] grid size-[18px] shrink-0 place-items-center rounded-full bg-bad-soft text-bad">
                          <X className="size-3" strokeWidth={3} />
                        </span>
                        <RichText text={r.reason} className="flex-1" />
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            );
          })}
          {act.error && (
            <div className="mt-4">
              <ErrorNote message={act.error} />
            </div>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2 border-t border-line bg-card px-6 py-3">
          {sh && (
            <Button variant="ghost" size="sm" className="h-9" onClick={() => setFixing({ student: sh.student, question: q })} title="원본과 나란히 보고 판독문을 고쳐 확인 — 고치면 다시 채점">
              <PenLine /> 판독문 고치기
            </Button>
          )}
          {sh && sh.total === null && !sh.unreadable && (
            <Button variant="outline" size="sm" className="h-9" disabled={act.busy} onClick={() => void gradeNow()}>
              {act.busy ? "채점 중…" : "이 학생 채점"}
            </Button>
          )}
          <div className="flex-1" />
          <span className="text-[12px] text-text-3">Enter 확정 · ↑↓ 학생</span>
          <Button variant="primary" className="h-10 px-5" disabled={act.busy || !sh} onClick={() => void confirm()}>
            {sh?.total === null ? "다음" : "확정하고 다음"} <ArrowRight />
          </Button>
        </div>
      </div>
      <TranscriptEditor
        target={fixing}
        onClose={() => setFixing(null)}
        onChanged={() => {
          students.reload();
          sheet.reload();
          reload();
        }}
      />
    </div>
  );
}

function Board({ assign, onChanged }: { assign: Assignments; onChanged: () => void }) {
  const { base, reload } = useExam();
  const me = useTeacher();
  const board = useApi<BoardRow[]>(`${base}/board`);
  const act = useAction();
  const mine = (board.data ?? []).filter((r) => assign[r.question]?.teacher === me).map((r) => r.question);
  const call = (f: () => Promise<unknown>) =>
    act.run(async () => {
      await f();
      onChanged();
      board.reload();
      reload();
    });
  const take = (q: string, force: boolean) => call(() => api.put(`${base}/assign/${enc(q)}`, { by: me, take: force }));
  const release = (q: string) => call(() => api.del(`${base}/assign/${enc(q)}?by=${enc(me ?? "")}`));
  const grade = () => call(async () => waitJob(await api.post<Job>(`${base}/grade`, { questions: mine, by: me })));
  return (
    <div className="mx-auto max-w-4xl px-6 py-6">
      <Card className="overflow-hidden">
        <table className="w-full border-collapse text-left">
          <thead className="text-[12px] text-text-3">
            <tr>
              <th className="px-5 py-2.5 font-normal">문항</th>
              <th className="px-2 py-2.5 font-normal">맡은 강사</th>
              <th className="w-56 px-2 py-2.5 font-normal">진행</th>
              <th className="px-2 py-2.5 font-normal" />
              <th className="px-5 py-2.5" />
            </tr>
          </thead>
          <tbody>
            {(board.data ?? []).map((r) => {
              const total = r.students;
              return (
                <tr key={r.question} className="border-t border-line">
                  <td className="px-5 py-3 font-medium">{r.question}번</td>
                  <td className="px-2 py-3">{r.teacher ?? <span className="text-text-3">없음</span>}</td>
                  <td className="px-2 py-3">
                    <Progress value={total ? (r.graded / total) * 100 : 0} tone={total > 0 && r.graded === total ? "ok" : "accent"} />
                    <div className="mt-1 text-[12px] text-text-3">
                      완료 {r.graded} / {total}
                    </div>
                  </td>
                  <td className="px-2 py-3">
                    <div className="flex flex-wrap gap-1">
                      {r.needs_review > 0 && <Badge tone="warn">검토 {r.needs_review}</Badge>}
                      {r.waiting_input > 0 && <Badge tone="bad">입력 대기 {r.waiting_input}</Badge>}
                      {r.ungraded > 0 && <Badge>채점 전 {r.ungraded}</Badge>}
                    </div>
                  </td>
                  <td className="px-5 py-3 text-right">
                    {r.teacher === me ? (
                      <Button size="sm" variant="ghost" disabled={act.busy} onClick={() => void release(r.question)}>
                        내려놓기
                      </Button>
                    ) : r.teacher ? (
                      <Button size="sm" variant="ghost" disabled={act.busy} onClick={() => void take(r.question, true)}>
                        가져오기
                      </Button>
                    ) : (
                      <Button size="sm" variant="outline" disabled={act.busy} onClick={() => void take(r.question, false)}>
                        내가 맡기
                      </Button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>
      {act.error && (
        <div className="mt-3">
          <ErrorNote message={act.error} />
        </div>
      )}
      <div className="mt-4 flex items-center justify-end gap-3">
        {act.busy && <span className="text-[13px] text-text-3">채점 중… 끝나면 알려 드립니다.</span>}
        <Button variant="primary" disabled={act.busy || mine.length === 0} onClick={() => void grade()}>
          맡은 문항 채점 시작 {mine.length > 0 && `(${mine.join(", ")}번)`}
        </Button>
      </div>
    </div>
  );
}
