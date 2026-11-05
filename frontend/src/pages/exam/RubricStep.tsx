import { Check, Lock, ShieldCheck, Sparkles, X } from "lucide-react";
import { useState } from "react";
import { RichText } from "@/components/Math";
import { Badge, type Tone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Help } from "@/components/ui/help";
import { ErrorNote, Progress } from "@/components/ui/misc";
import { api, enc, useAction, useApi, waitJob } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useTeacher } from "@/lib/session";
import { pts } from "@/lib/steps";
import type { Compare, Decision, ExamQuestion, Job, Rubric } from "@/lib/types";
import { useExam } from "../Exam";
import { partLabel, useQuestionDetails } from "./shared";

type State = "없음" | "초안" | "승인" | "확정";
const TONE: Record<State, Tone> = { 없음: "neutral", 초안: "warn", 승인: "accent", 확정: "ok" };

function stateOf(r: Rubric | null | undefined): State {
  if (!r) return "없음";
  if (r.status === "draft") return "초안";
  return r.finalized_by ? "확정" : "승인";
}

type MarksRow = { student_id: string; marks: Record<string, Record<string, number | null>>; confirmed: boolean; problems: string[] };
type Suggestion = { changes: { item_id: string | null; kind: string; summary: string; evidence: string[] }[]; revised: unknown; no_change_reason: string | null };

function Decide({ status, onDecide, busy }: { status: Decision; onDecide: (v: "accept" | "reject") => void; busy: boolean }) {
  return (
    <div className="flex shrink-0 gap-1">
      <Button size="sm" variant="ghost" disabled={busy} className={cn(status === "accepted" && "bg-ok-soft text-ok")} onClick={() => onDecide("accept")}>
        <Check /> 인정
      </Button>
      <Button size="sm" variant="ghost" disabled={busy} className={cn(status === "rejected" && "bg-bad-soft text-bad")} onClick={() => onDecide("reject")}>
        <X /> 빼기
      </Button>
    </div>
  );
}

/** 강사 손채점과 맞춰 보기 (PLAN §6): 빨간 펜 점수 읽기 → 확인 → 소문항 일치도 → 보정 제안 → 확정. */
function Calibration({ q, rubric, onChanged }: { q: ExamQuestion; rubric: Rubric; onChanged: () => void }) {
  const { base } = useExam();
  const by = useTeacher();
  const compare = useApi<Compare>(`${base}/compare`);
  const marks = useApi<MarksRow[]>(`${base}/marks`);
  const [sugg, setSugg] = useState<Suggestion | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const act = useAction();
  const agree = compare.data?.questions[q.number];
  const rows = (marks.data ?? []).filter((r) => r.marks[q.number]);
  const labels = q.parts.length ? q.parts.map((p) => p.label) : [""];
  const ready = rows.filter((r) => !r.confirmed && r.problems.length === 0).map((r) => r.student_id);

  const readMarks = () => act.run(async () => void (await waitJob(await api.post<Job>(`${base}/marks/read`, {}))));
  const confirm = () => act.run(async () => void (await waitJob(await api.post<Job>(`${base}/marks/confirm`, { students: ready, by }))));
  const saveMark = (student: string) =>
    act.run(async () => {
      const cur = rows.find((r) => r.student_id === student)!.marks[q.number]!;
      const parts = Object.fromEntries(
        labels.map((l) => {
          const v = edits[`${student}|${l}`];
          return [l, v === undefined ? (cur[l] ?? null) : v === "" ? null : Number(v)];
        }),
      );
      await api.put(`${base}/students/${enc(student)}/marks/${enc(q.number)}`, { parts });
      setEdits({});
      marks.reload();
    });
  const suggest = () =>
    act.run(async () => {
      const j = await waitJob(await api.post<Job>(`${base}/calibration/${enc(q.number)}/suggest`, {}));
      setSugg((j.result as { suggestion: Suggestion }).suggestion);
    });
  const apply = () =>
    act.run(async () => {
      await api.post(`${base}/calibration/${enc(q.number)}/apply`, { revised: sugg?.revised });
      setSugg(null);
      onChanged();
    });

  return (
    <Card>
      <CardHeader
        title="강사 손채점과 맞춰 보기"
        desc="손으로 채점한 답안 3~5장을 답안 판독에서 함께 넣으면, 빨간 펜 소문항 점수를 읽어 AI 채점과 비교합니다."
        right={
          <Button size="sm" variant="outline" disabled={act.busy} onClick={() => void readMarks()}>
            빨간 펜 점수 읽기
          </Button>
        }
      />
      <CardBody className="flex flex-col gap-3">
        <div className="flex items-center gap-3">
          <span className="w-24 text-[13px] text-text-2">소문항 일치</span>
          <div className="flex-1">
            <Progress value={(agree?.rate ?? 0) * 100} tone="ok" />
          </div>
          <span className="w-32 text-right font-medium">
            {agree?.parts ? `${Math.round((agree.rate ?? 0) * 100)}%` : "—"}{" "}
            <span className="text-[12px] font-normal text-text-3">{agree?.parts ? `(${agree.same}/${agree.parts})` : "비교 전"}</span>
          </span>
        </div>
        {rows.length > 0 && (
          <table className="w-full border-collapse text-left text-[13px]">
            <thead className="text-[12px] text-text-3">
              <tr>
                <th className="py-1.5 font-normal">학생</th>
                {labels.map((l) => (
                  <th key={l} className="py-1.5 font-normal">
                    {partLabel(q.number, l)}
                  </th>
                ))}
                <th className="py-1.5 font-normal" />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.student_id} className="border-t border-line">
                  <td className="py-1.5">{r.student_id}</td>
                  {labels.map((l) => (
                    <td key={l} className="py-1.5">
                      <input
                        value={edits[`${r.student_id}|${l}`] ?? (r.marks[q.number]?.[l] ?? "").toString()}
                        onChange={(e) => setEdits({ ...edits, [`${r.student_id}|${l}`]: e.target.value })}
                        onBlur={() => Object.keys(edits).length > 0 && void saveMark(r.student_id)}
                        className={cn(
                          "h-7 w-12 rounded-md border border-line bg-card px-1.5 text-right outline-none focus:border-accent",
                          r.marks[q.number]?.[l] == null && "border-warn",
                        )}
                      />
                    </td>
                  ))}
                  <td className="py-1.5 text-right">
                    {r.confirmed ? <Badge tone="ok">확인</Badge> : r.problems.length ? <Badge tone="warn">{r.problems[0]}</Badge> : <Badge>확인 전</Badge>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="flex flex-wrap gap-2">
          {ready.length > 0 && (
            <Button size="sm" variant="outline" disabled={act.busy} onClick={() => void confirm()}>
              읽은 점수 맞음 ({ready.length}명)
            </Button>
          )}
          <Button size="sm" variant="ghost" disabled={act.busy || rubric.status !== "approved"} onClick={() => void suggest()}>
            <Sparkles /> 보정 제안 받기
          </Button>
        </div>
        {sugg && (
          <div className="rounded-lg bg-muted px-3 py-2.5 text-[13px]">
            {sugg.changes.length === 0 ? (
              <span>바꿀 것이 없습니다. {sugg.no_change_reason}</span>
            ) : (
              <>
                <div className="font-medium">보정 제안</div>
                <ul className="m-0 mt-1 pl-5">
                  {sugg.changes.map((c, i) => (
                    <li key={i}>
                      {c.summary} <span className="text-text-3">({c.evidence.join(", ")})</span>
                    </li>
                  ))}
                </ul>
                {sugg.revised != null && (
                  <div className="mt-2 flex gap-2">
                    <Button size="sm" variant="outline" disabled={act.busy} onClick={() => void apply()}>
                      제안 적용 (새 초안)
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setSugg(null)}>
                      두기
                    </Button>
                  </div>
                )}
              </>
            )}
          </div>
        )}
        {act.busy && <div className="text-[12px] text-text-3">AI 작업 중… 몇 분 걸릴 수 있습니다.</div>}
        {act.error && <ErrorNote message={act.error} />}
      </CardBody>
    </Card>
  );
}

/** ② 루브릭: 해설지 넓히기 결과를 인정/빼고 승인 → 강사 손채점과 맞춰 보고 확정 (PLAN §4, §6). */
export function RubricStep() {
  const { base, detail, reload } = useExam();
  const by = useTeacher();
  const numbers = detail.exam.questions.map((q) => q.number);
  const { details, error, reload: reloadDetails } = useQuestionDetails(base, numbers);
  const first = numbers.find((n) => stateOf(details[n]?.rubric) !== "확정") ?? numbers[0]!;
  const [picked, setPicked] = useState<string | null>(null);
  const sel = picked ?? first;
  const q = detail.exam.questions.find((x) => x.number === sel)!;
  const r = details[sel]?.rubric;
  const state = stateOf(r);
  const act = useAction();
  const changed = () => {
    reload();
    reloadDetails();
  };
  const call = (f: () => Promise<unknown>) => act.run(async () => void (await f(), changed()));
  const make = () => call(async () => waitJob(await api.post<Job>(`${base}/rubrics`, { questions: [sel], by })));
  const approve = () => call(() => api.post(`${base}/questions/${enc(sel)}/rubric/approve`, { by }));
  const finalize = () => call(() => api.post(`${base}/calibration/${enc(sel)}/finalize`, { by }));
  const decide = (id: string, v: "accept" | "reject") =>
    call(() => api.post(`${base}/questions/${enc(sel)}/rubric/candidates`, v === "accept" ? { accept: [id] } : { reject: [id] }));
  const proposed = r ? r.accepted_alternatives.filter((a) => a.status === "proposed").length + r.candidates.filter((c) => c.status === "proposed").length : 0;

  return (
    <div className="flex h-full">
      <aside className="w-56 shrink-0 border-r border-line p-3">
        {numbers.map((n) => {
          const s = stateOf(details[n]?.rubric);
          return (
            <button
              key={n}
              type="button"
              onClick={() => setPicked(n)}
              className={cn("flex w-full cursor-pointer items-center rounded-lg px-3 py-2.5 text-left hover:bg-muted", sel === n && "bg-muted")}
            >
              <span className="flex-1">{n}번</span>
              <Badge tone={TONE[s]}>{s}</Badge>
            </button>
          );
        })}
      </aside>
      <div className="min-w-0 flex-1 overflow-auto px-6 py-5">
        <div className="mx-auto flex max-w-3xl flex-col gap-4">
          <div className="flex items-center gap-3">
            <h2 className="m-0 text-[17px]">{sel}번 루브릭</h2>
            <Badge tone={TONE[state]}>{state}</Badge>
            {r && <span className="text-[12px] text-text-3">{r.version}판</span>}
            <div className="flex-1" />
            {state === "없음" && (
              <Button variant="primary" disabled={act.busy} onClick={() => void make()}>
                {act.busy ? "만드는 중…" : "루브릭 만들기"}
              </Button>
            )}
            {state === "초안" && (
              <Button variant="primary" disabled={act.busy || proposed > 0} onClick={() => void approve()}>
                <Check /> 승인 {proposed > 0 && `(남은 확인 ${proposed})`}
              </Button>
            )}
            {state === "승인" && (
              <Button variant="primary" disabled={act.busy} onClick={() => void finalize()}>
                <ShieldCheck /> 확정
              </Button>
            )}
            {state === "확정" && r && (
              <span className="inline-flex items-center gap-1 text-[13px] text-text-2">
                <Lock className="size-4" /> {r.finalized_by} 확정 · {r.finalized_at?.slice(5, 10)}
              </span>
            )}
          </div>
          {(error || act.error) && <ErrorNote message={(act.error ?? error)!} />}

          {r && (
            <Card>
              <CardHeader title="채점 항목" desc={r.based_on_key ? "해설지의 부분 점수를 옮겼습니다." : "AI 풀이로 만든 항목입니다."} />
              <CardBody className="pt-3">
                {r.items.map((it) => (
                  <div key={it.id} className="flex items-start gap-3 border-t border-line py-2.5 first:border-t-0">
                    <span className="w-14 shrink-0 text-text-3">{it.part ?? it.id}</span>
                    <span className="flex-1">
                      <RichText text={it.criterion} />
                      {r.answer_rules.filter((a) => a.item_id === it.id).map((a) => (
                        <span key={a.item_id} className="ml-2 text-[12px] text-text-2">
                          정답: {a.all_correct ? "전원 정답" : a.answers.join(" 또는 ")}
                        </span>
                      ))}
                    </span>
                    <span className="font-medium">{pts(it.points)}점</span>
                  </div>
                ))}
                {r.key_conflicts.map((k) => (
                  <div key={k} className="mt-2 rounded-lg bg-warn-soft px-3 py-2 text-[13px] text-warn">
                    해설지와 충돌: {k}
                  </div>
                ))}
              </CardBody>
            </Card>
          )}

          {r && (r.accepted_alternatives.length > 0 || r.candidates.length > 0) && (
            <Card>
              <CardHeader
                title={
                  <>
                    AI가 넓힌 표현 · 다른 정답 후보{" "}
                    <Help>같은 근거·같은 결론을 다르게 쓴 말만 넣었습니다. 해설지에 없는 개념은 범위 검사에서 이미 걸렀습니다.</Help>
                  </>
                }
                desc="인정하면 채점에서 같은 답으로 봅니다. 모두 정해야 승인할 수 있습니다."
              />
              <CardBody className="pt-3">
                {r.accepted_alternatives.map((a, i) => (
                  <div key={a.id ?? i} className="flex items-center gap-3 border-t border-line py-2.5 first:border-t-0">
                    <span className="w-8 text-text-3">{a.id}</span>
                    <span className={cn("flex-1", a.status === "rejected" && "text-text-3 line-through")}>
                      <RichText text={a.description} /> <span className="text-[12px] text-text-3">→ {a.item_ids.join(", ")}</span>
                    </span>
                    {a.id && <Decide status={a.status} busy={act.busy} onDecide={(v) => void decide(a.id!, v)} />}
                  </div>
                ))}
                {r.candidates.map((c) => (
                  <div key={c.id} className="flex items-center gap-3 border-t border-line py-2.5">
                    <span className="w-8 text-text-3">{c.id}</span>
                    <span className={cn("flex-1", c.status === "rejected" && "text-text-3 line-through")}>
                      {c.part && <span className="text-text-3">{c.part} </span>}
                      <RichText text={c.answer} /> <span className="block text-[12px] text-text-2">{c.argument}</span>
                    </span>
                    <Decide status={c.status} busy={act.busy} onDecide={(v) => void decide(c.id, v)} />
                  </div>
                ))}
              </CardBody>
            </Card>
          )}

          {r && r.status === "approved" && <Calibration q={q} rubric={r} onChanged={changed} />}
        </div>
      </div>
    </div>
  );
}
