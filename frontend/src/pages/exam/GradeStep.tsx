import { ArrowRight, Check, FileImage, Minus, Plus, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { RichText, TeX } from "@/components/Math";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Help } from "@/components/ui/help";
import { ErrorNote, Progress } from "@/components/ui/misc";
import { Segmented } from "@/components/ui/segmented";
import { Select } from "@/components/ui/select";
import { api, enc, examFileUrl, useAction, useApi, waitJob } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useTeacher } from "@/lib/session";
import { pts } from "@/lib/steps";
import type { BoardRow, ExamStudent, Job, Piece, ReviewSheet } from "@/lib/types";
import { useExam } from "../Exam";

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
  const { base, detail } = useExam();
  const me = useTeacher();
  const assign = useApi<Assignments>(`${base}/assign`);
  const [view, setView] = useState<"review" | "board">("review");
  const numbers = detail.exam.questions.map((q) => q.number);
  const mine = numbers.find((n) => assign.data?.[n]?.teacher === me);
  const [picked, setPicked] = useState<string | null>(null);
  const q = picked ?? mine ?? numbers[0]!;
  const label = (n: string) => {
    const t = assign.data?.[n]?.teacher;
    return `${n}번 · ${t === me ? "내 문항" : (t ?? "맡은 사람 없음")}`;
  };
  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-3 border-b border-line px-6 py-2.5">
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
      <div className="min-h-0 flex-1">{view === "review" ? <Review key={q} q={q} /> : <Board assign={assign.data ?? {}} onChanged={assign.reload} />}</div>
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

/** 불확실한 글자(노랑)·[판독불가](분홍) — HWPX 판독본과 같은 색. */
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

function Review({ q }: { q: string }) {
  const { id, base, reload } = useExam();
  const by = useTeacher();
  const students = useApi<ExamStudent[]>(`${base}/students`);
  const order = useMemo(() => {
    const list = (students.data ?? []).map((s) => ({ s, g: groupOf(s, q) })).filter((x) => x.g !== null);
    return GROUPS.flatMap((g) => list.filter((x) => x.g === g));
  }, [students.data, q]);
  const [sel, setSel] = useState<string | null>(null);
  const cur = sel ?? order[0]?.s.student ?? null;
  const sheet = useApi<ReviewSheet>(cur ? `${base}/review/${enc(cur)}/${enc(q)}` : null);
  const [editing, setEditing] = useState(false);
  const [scores, setScores] = useState<Record<string, number>>({});
  const act = useAction();
  const sh = sheet.data?.student === cur ? sheet.data : undefined;
  const parts = (sh?.parts ?? []).filter((p) => p.awarded !== null);

  const next = () => {
    const i = order.findIndex((x) => x.s.student === cur);
    const n = order.slice(i + 1).find((x) => x.g !== "완료") ?? order[i + 1];
    if (n) setSel(n.s.student);
    setEditing(false);
    setScores({});
  };
  const confirm = () =>
    act.run(async () => {
      if (!sh || sh.total === null) return next();
      const part_scores = Object.fromEntries(
        parts.map((p) => [p.label.startsWith(`${q}-`) ? p.label.slice(q.length + 1) : "", scores[p.label] ?? p.awarded!]),
      );
      await api.put(`${base}/students/${enc(sh.student)}/grades/${enc(q)}/parts`, { part_scores, by, learn: Object.keys(scores).length > 0 });
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
      if (document.querySelector("[role=dialog]")) return;
      if (e.key === "Enter") void confirm();
      if (e.key === "ArrowDown") {
        e.preventDefault();
        next();
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
      <aside className="w-60 shrink-0 overflow-auto border-r border-line p-3">
        {GROUPS.map((g) => {
          const list = order.filter((x) => x.g === g);
          if (list.length === 0) return null;
          return (
            <div key={g} className="mb-3">
              <div className="px-3 pb-1 pt-2 text-[12px] text-text-3">
                {g} {list.length}
              </div>
              {list.map(({ s }) => {
                const c = s.cells[q]!;
                return (
                  <button
                    key={s.student}
                    type="button"
                    onClick={() => {
                      setSel(s.student);
                      setEditing(false);
                      setScores({});
                    }}
                    className={cn("flex w-full cursor-pointer items-center rounded-lg px-3 py-2.5 text-left hover:bg-muted", cur === s.student && "bg-muted")}
                  >
                    <span className="flex-1">
                      {s.record?.name ?? s.student}
                      <span className="block text-[12px] text-text-3">
                        {s.record?.class_name}
                        {c.grade?.disagreements ? " · 두 채점이 다름" : ""}
                        {c.transcript?.unreadable ? ` · [판독불가] ${c.transcript.unreadable}곳` : ""}
                      </span>
                    </span>
                    <span className={cn("font-medium", g === "완료" ? "text-text" : g === "입력 대기" ? "text-bad" : "text-warn")}>
                      {c.grade ? `${pts(c.grade.total)}/${pts(c.grade.max)}` : "—"}
                    </span>
                  </button>
                );
              })}
            </div>
          );
        })}
      </aside>
      <div className="flex min-w-0 flex-1 gap-5 overflow-auto p-5">
        <Card className="flex min-h-[520px] flex-1 flex-col gap-3 overflow-auto bg-muted p-3">
          {(sh?.page_images ?? []).map((p) => (
            <img key={p} src={examFileUrl(id, p)} alt="원본 스캔" className="w-full rounded-lg" />
          ))}
          {sh && sh.page_images.length === 0 && (
            <div className="m-auto flex flex-col items-center gap-2 text-text-3">
              <FileImage className="size-8" />
              원본 그림 없음 (입력한 답안)
            </div>
          )}
        </Card>
        <div className="flex w-[46%] min-w-[380px] flex-col gap-5">
          {!sh && <div className="text-text-3">불러오는 중…</div>}
          {sh && sh.total === null && (
            <div className="text-text-2">{sh.unreadable ? "판독불가가 남아 있어 채점 전입니다. 한글에서 판독본을 채운 뒤 반영하세요." : "아직 채점하지 않았습니다."}</div>
          )}
          {sh?.disagreements.map((d) => (
            <div key={d} className="rounded-lg bg-warn-soft px-3 py-2 text-[13px] text-warn">
              두 채점이 다름: {d}
            </div>
          ))}
          {sh?.parts.map((p) => {
            const score = scores[p.label] ?? p.awarded;
            const setScore = (v: number) => setScores({ ...scores, [p.label]: Math.max(0, Math.min(p.points, v)) });
            return (
              <section key={p.label}>
                <div className="flex items-center gap-2">
                  <span className="text-[16px] font-medium">{p.label}</span>
                  {sh.graded_before_confirm && (
                    <Badge tone="accent">
                      판독문 확인 전 <Help>판독문을 확정하기 전에 채점했습니다. 판독문을 고쳐 반영하면 다시 채점합니다.</Help>
                    </Badge>
                  )}
                  <div className="flex-1" />
                  {score === null ? null : editing ? (
                    <div className="flex items-center gap-1">
                      <Button size="icon" variant="ghost" aria-label="빼기" onClick={() => setScore(score - 1)}>
                        <Minus />
                      </Button>
                      <span className="w-16 text-center text-[15px] font-medium">
                        {pts(score)} / {pts(p.points)}
                      </span>
                      <Button size="icon" variant="ghost" aria-label="더하기" onClick={() => setScore(score + 1)}>
                        <Plus />
                      </Button>
                    </div>
                  ) : (
                    <span className={cn("rounded-full px-3 py-1 text-[14px] font-medium", score === p.points ? "bg-ok-soft text-ok" : "bg-warn-soft text-warn")}>
                      {pts(score)} / {pts(p.points)}
                    </span>
                  )}
                </div>
                <div className="mt-3 text-[15px] leading-[2]">
                  {p.lines.length === 0 && <span className="text-text-3">(빈 답안)</span>}
                  {p.lines.map((line, i) => (
                    <div key={i} className={cn(line.struck && "text-text-3 line-through")}>
                      <Pieces pieces={line.pieces} uncertain={line.uncertain} />
                    </div>
                  ))}
                </div>
                <div className="mt-3 flex flex-col gap-2">
                  {p.good.map((r, i) => (
                    <div key={`g${i}`} className="flex items-start gap-2 rounded-lg bg-ok-soft px-4 py-3 text-ok">
                      <Check className="mt-0.5 size-4 shrink-0" />
                      <RichText text={r.reason} className="flex-1" />
                    </div>
                  ))}
                  {p.bad.map((r, i) => (
                    <div key={`b${i}`} className="flex items-start gap-2 rounded-lg bg-bad-soft px-4 py-3 text-bad">
                      <X className="mt-0.5 size-4 shrink-0" />
                      <RichText text={r.reason} className="flex-1" />
                    </div>
                  ))}
                </div>
              </section>
            );
          })}
          {act.error && <ErrorNote message={act.error} />}
          <div className="flex gap-2">
            {sh && sh.total !== null && (
              <Button size="lg" variant="outline" onClick={() => setEditing(!editing)}>
                {editing ? "고치기 끝" : "점수 고치기"}
              </Button>
            )}
            {sh && sh.total === null && !sh.unreadable && (
              <Button size="lg" variant="outline" disabled={act.busy} onClick={() => void gradeNow()}>
                {act.busy ? "채점 중…" : "이 학생 채점"}
              </Button>
            )}
            <Button size="lg" variant="primary" disabled={act.busy || !sh} onClick={() => void confirm()}>
              {sh?.total === null ? "다음" : "확정하고 다음"} <ArrowRight />
            </Button>
          </div>
          <div className="text-[12px] text-text-3">Enter 확정 · ↓ 다음 학생</div>
        </div>
      </div>
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
