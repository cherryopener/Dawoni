import { Check, Lock, Pencil, Plus, RefreshCw, ShieldCheck, Sparkles, Trash2, X } from "lucide-react";
import { useState } from "react";
import { RichText } from "@/components/Math";
import { Badge, type Tone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Help } from "@/components/ui/help";
import { ErrorNote, Input, Progress } from "@/components/ui/misc";
import { api, enc, useAction, useApi, waitJob } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useTeacher } from "@/lib/session";
import { pts } from "@/lib/steps";
import type { Compare, Decision, ExamQuestion, Job, KeyChoice, Rubric } from "@/lib/types";
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

/** 해설지와 다른 기준 (2026-11-11): 해설지의 0점 조건이 문제가 요구하지 않은 것을 요구할 때, AI는 문제
 * 기준으로 항목을 쓰고 해설지대로의 기준을 함께 적는다. 강사가 고른 쪽이 그 항목의 기준이 된다. */
function KeyConflicts({ r, busy, onChoose }: { r: Rubric; busy: boolean; onChoose: (id: string, v: Exclude<KeyChoice, "edited">) => void }) {
  const option = (id: string, v: Exclude<KeyChoice, "edited">, label: string, text: string, on: boolean) => (
    <button
      type="button"
      disabled={busy}
      aria-pressed={on}
      onClick={() => onChoose(id, v)}
      className={cn(
        "flex cursor-pointer flex-col gap-1 rounded-lg border px-3 py-2 text-left disabled:cursor-default",
        on ? "border-accent bg-accent-soft" : "border-line hover:bg-muted",
      )}
    >
      <span className={cn("inline-flex items-center gap-1 text-[12px] font-medium", on ? "text-accent" : "text-text-2")}>
        {on && <Check className="size-3.5" />} {label}
      </span>
      <span className="text-[13px]">
        <RichText text={text} />
      </span>
    </button>
  );
  return (
    <Card>
      <CardHeader
        title={
          <>
            해설지와 다른 기준{" "}
            <Help>해설지가 문제에서 요구하지 않은 것을 0점 조건으로 건 곳입니다. AI는 먼저 문제가 요구한 것으로 기준을 썼습니다.</Help>
          </>
        }
        desc="어느 쪽으로 채점할지 고르면 그 기준이 채점 항목에 들어갑니다. 모두 정해야 승인할 수 있습니다."
      />
      <CardBody className="pt-3">
        {r.key_conflicts.map((k) => {
          const switchable = Boolean(k.item_id && k.key_criterion && k.question_criterion);
          return (
            <div key={k.id} className="flex flex-col gap-2 border-t border-line py-3 first:border-t-0">
              <div className="flex items-start gap-3">
                <span className="w-8 shrink-0 text-text-3">{k.id}</span>
                <span className="flex-1 text-[13px]">
                  <RichText text={k.note} />
                  {k.item_id && <span className="text-[12px] text-text-3"> → {k.item_id}</span>}
                </span>
                {k.choice === null ? <Badge tone="warn">정하기 전</Badge> : k.choice === "edited" && <Badge tone="ok">직접 고침</Badge>}
              </div>
              {switchable ? (
                <div className="ml-11 grid gap-2 sm:grid-cols-2">
                  {option(k.id, "question", "문제 기준", k.question_criterion!, k.choice === "question")}
                  {option(k.id, "key", "해설지 기준", k.key_criterion!, k.choice === "key")}
                </div>
              ) : (
                <div className="ml-11 flex items-center gap-2">
                  <Button size="sm" variant="ghost" disabled={busy} className={cn(k.choice !== null && "bg-ok-soft text-ok")} onClick={() => onChoose(k.id, "question")}>
                    <Check /> 확인
                  </Button>
                  <span className="text-[12px] text-text-3">해설지대로 채점하려면 [고치기]로 기준을 바꾸세요.</span>
                </div>
              )}
            </div>
          );
        })}
      </CardBody>
    </Card>
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

  const [readNote, setReadNote] = useState<string | null>(null);
  const readMarks = () =>
    act.run(async () => {
      const j = await waitJob(await api.post<Job>(`${base}/marks/read`, {}));
      const r = j.result as { read: string[]; skipped: { student: string; reason: string }[]; failed: { student: string; reason: string }[] };
      const unmarked = r.skipped.filter((k) => k.reason.startsWith("표지에 강사 점수가 없음")).length;
      const parts = [`${r.read.length}명 읽음`];
      if (unmarked) parts.push(`표지에 강사 점수가 없는 ${unmarked}명은 손채점하지 않은 것으로 보고 건너뜀`);
      const fail = r.failed[0];
      if (fail) parts.push(`${r.failed.length}명 실패 (${fail.student}: ${fail.reason})`);
      setReadNote(
        r.read.length === 0 && unmarked > 0 && unmarked === r.skipped.length
          ? "표지에 강사 점수가 있는 답안지가 없습니다. 손채점한 답안지는 표지에 문항 점수를 적어 판독하세요."
          : parts.join(" · "),
      );
      marks.reload();
    });
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
        desc="손으로 채점한 답안 3~5장을 답안 판독에서 함께 넣으면, 빨간 펜 소문항 점수를 읽어 AI 채점과 비교합니다. 표지에 강사 점수가 적힌 답안지만 읽습니다."
        right={
          <Button size="sm" variant="outline" disabled={act.busy} onClick={() => void readMarks()}>
            빨간 펜 점수 읽기
          </Button>
        }
      />
      <CardBody className="flex flex-col gap-3">
        {readNote && <div className="rounded-lg bg-muted px-3 py-2 text-[13px] text-text-2">{readNote}</div>}
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

// ---------------------------------------------------------------- 루브릭 고치기

type ItemRow = { key: number; id: string; part: string | null; points: string; criterion: string; evidence_hint: string };
type PartialRow = { key: number; item_id: string; condition: string; points: string };
let nextKey = 1;

const textarea =
  "w-full rounded-lg border border-line bg-card px-3 py-2 text-text outline-none placeholder:text-text-3 focus:border-accent";

/** 채점 항목(기준·배점·소문항)과 부분 점수를 고친다. 승인·확정한 루브릭이면 새 판(초안)이 된다.
 * 넓힌 표현·정답 후보는 아래 인정·제외로 정한다 (지운 항목을 가리키던 연결은 서버가 뗀다). */
function RubricEditor({ q, r, onDone, onCancel }: { q: ExamQuestion; r: Rubric; onDone: () => void; onCancel: () => void }) {
  const { base } = useExam();
  const act = useAction();
  const [items, setItems] = useState<ItemRow[]>(() =>
    r.items.map((i) => ({ key: nextKey++, id: i.id, part: i.part, points: String(i.points), criterion: i.criterion, evidence_hint: i.evidence_hint })),
  );
  const [partial, setPartial] = useState<PartialRow[]>(() =>
    r.partial.map((x) => ({ key: nextKey++, item_id: x.item_id, condition: x.condition, points: String(x.points) })),
  );
  const coded = new Set(r.answer_rules.map((a) => a.item_id));
  const labels = q.parts.map((p) => p.label);
  const total = items.reduce((a, i) => a + (Number(i.points) || 0), 0);
  const sumOf = (label: string) => items.filter((i) => i.part === label).reduce((a, i) => a + (Number(i.points) || 0), 0);
  const partsOff = q.parts.filter((p) => Math.abs(sumOf(p.label) - p.points) > 1e-9);
  const totalOk = Math.abs(total - q.points) < 1e-9;
  const filled = items.every((i) => i.criterion.trim() && Number(i.points) > 0) && partial.every((x) => x.condition.trim() && x.points !== "");
  const approved = r.status === "approved";

  const setItem = (key: number, patch: Partial<ItemRow>) => setItems((xs) => xs.map((x) => (x.key === key ? { ...x, ...patch } : x)));
  const setPart = (key: number, patch: Partial<PartialRow>) => setPartial((xs) => xs.map((x) => (x.key === key ? { ...x, ...patch } : x)));
  const addItem = (part: string | null) =>
    setItems((xs) => {
      const n = Math.max(0, ...xs.map((x) => Number(x.id.slice(1)) || 0), ...r.items.map((x) => Number(x.id.slice(1)) || 0)) + 1;
      return [...xs, { key: nextKey++, id: `R${n}`, part, points: "", criterion: "", evidence_hint: "" }];
    });
  const removeItem = (row: ItemRow) => {
    setItems((xs) => xs.filter((x) => x.key !== row.key));
    setPartial((xs) => xs.filter((x) => x.item_id !== row.id));
  };
  const save = () =>
    act.run(async () => {
      await api.put(`${base}/questions/${enc(q.number)}/rubric/items`, {
        items: items.map((i) => ({ id: i.id, part: i.part, points: Number(i.points), criterion: i.criterion.trim(), evidence_hint: i.evidence_hint.trim() })),
        partial: partial.map((x) => ({ item_id: x.item_id, condition: x.condition.trim(), points: Number(x.points) })),
      });
      onDone();
    });

  const groups: (string | null)[] = labels.length ? labels : [null];
  return (
    <Card>
      <CardHeader
        title="채점 항목 고치기"
        desc={
          approved
            ? "저장하면 새 판(초안)이 됩니다. 다시 승인·확정해야 하고, 이미 채점한 답안은 '다시 채점 필요'가 됩니다."
            : "항목 배점의 합이 문항 배점과 같아야 저장할 수 있습니다."
        }
      />
      <CardBody className="flex flex-col gap-4 pt-3">
        {groups.map((label) => {
          const part = label === null ? undefined : q.parts.find((p) => p.label === label);
          const rows = items.filter((i) => (i.part ?? null) === label);
          return (
            <div key={label ?? ""} className="flex flex-col gap-2">
              {part && (
                <div className="flex items-center gap-2 text-[13px]">
                  <span className="font-medium">{partLabel(q.number, label)}</span>
                  <span className={cn("text-text-3", Math.abs(sumOf(part.label) - part.points) > 1e-9 && "text-warn")}>
                    항목 합 {pts(sumOf(part.label))}점 / 소문항 {pts(part.points)}점
                  </span>
                </div>
              )}
              {rows.map((it) => (
                <div key={it.key} className="rounded-lg border border-line p-3">
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <span className="w-10 text-text-3">{it.id}</span>
                    {labels.length > 1 && (
                      <select
                        aria-label={`${it.id} 소문항`}
                        value={it.part ?? ""}
                        onChange={(e) => setItem(it.key, { part: e.target.value })}
                        className="h-8 rounded-md border border-line bg-card px-2 outline-none focus:border-accent"
                      >
                        {labels.map((l) => (
                          <option key={l} value={l}>
                            {l}
                          </option>
                        ))}
                      </select>
                    )}
                    <Input
                      type="number"
                      min={0}
                      step={0.5}
                      value={it.points}
                      onChange={(e) => setItem(it.key, { points: e.target.value })}
                      aria-label={`${it.id} 배점`}
                      className="h-8 w-20 text-right"
                    />
                    <span className="text-text-3">점</span>
                    <div className="flex-1" />
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`${it.id} 지우기`}
                      disabled={coded.has(it.id)}
                      title={coded.has(it.id) ? "코드 채점 항목은 지울 수 없습니다 (정답은 정답 고치기로)" : undefined}
                      onClick={() => removeItem(it)}
                    >
                      <Trash2 />
                    </Button>
                  </div>
                  <div className="grid grid-cols-[4.5rem_1fr] items-start gap-x-2 gap-y-1.5">
                    <span className="pt-2 text-[13px] text-text-3">기준</span>
                    <textarea
                      rows={2}
                      value={it.criterion}
                      onChange={(e) => setItem(it.key, { criterion: e.target.value })}
                      placeholder="무엇이 답안에 있으면 이 점수를 주는가 (식은 \( \)로)"
                      className={textarea}
                    />
                    <span className="pt-2 text-[13px] text-text-3">찾을 것</span>
                    <Input value={it.evidence_hint} onChange={(e) => setItem(it.key, { evidence_hint: e.target.value })} placeholder="답안에서 찾을 식·문장의 예" />
                    {partial
                      .filter((x) => x.item_id === it.id)
                      .map((x) => (
                        <div key={x.key} className="contents">
                          <span className="pt-2 text-[13px] text-text-3">부분 점수</span>
                          <div className="flex items-center gap-2">
                            <Input value={x.condition} onChange={(e) => setPart(x.key, { condition: e.target.value })} placeholder="예: 식은 맞고 계산 실수" className="flex-1" />
                            <Input
                              type="number"
                              min={0}
                              step={0.5}
                              value={x.points}
                              onChange={(e) => setPart(x.key, { points: e.target.value })}
                              aria-label="부분 점수"
                              className="h-9 w-20 text-right"
                            />
                            <span className="text-text-3">점</span>
                            <Button size="sm" variant="ghost" aria-label="부분 점수 지우기" onClick={() => setPartial((xs) => xs.filter((y) => y.key !== x.key))}>
                              <X />
                            </Button>
                          </div>
                        </div>
                      ))}
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="mt-1"
                    onClick={() => setPartial((xs) => [...xs, { key: nextKey++, item_id: it.id, condition: "", points: "" }])}
                  >
                    <Plus /> 부분 점수
                  </Button>
                </div>
              ))}
              <div>
                <Button size="sm" variant="ghost" onClick={() => addItem(label)}>
                  <Plus /> 항목{part ? ` (${label})` : ""}
                </Button>
              </div>
            </div>
          );
        })}

        <div className={cn("text-[13px]", totalOk && !partsOff.length ? "text-text-2" : "text-warn")}>
          항목 합 {pts(total)}점 / 문항 {pts(q.points)}점
          {partsOff.length > 0 && ` · 소문항 합이 다름: ${partsOff.map((p) => p.label).join(", ")} (저장은 됩니다)`}
        </div>
        {act.error && <ErrorNote message={act.error} />}
        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" disabled={act.busy} onClick={onCancel}>
            취소
          </Button>
          <Button variant="primary" disabled={act.busy || !totalOk || !filled} onClick={() => void save()}>
            <Check /> 저장
          </Button>
        </div>
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
  const [editing, setEditing] = useState(false);
  const [askRemake, setAskRemake] = useState(false);
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
  const make = () =>
    call(async () => {
      setAskRemake(false);
      await waitJob(await api.post<Job>(`${base}/rubrics`, { questions: [sel], by }));
    });
  const approve = () => call(() => api.post(`${base}/questions/${enc(sel)}/rubric/approve`, { by }));
  const finalize = () => call(() => api.post(`${base}/calibration/${enc(sel)}/finalize`, { by }));
  const decide = (id: string, v: "accept" | "reject") =>
    call(() => api.post(`${base}/questions/${enc(sel)}/rubric/candidates`, v === "accept" ? { accept: [id] } : { reject: [id] }));
  const chooseBasis = (id: string, v: Exclude<KeyChoice, "edited">) =>
    call(() => api.post(`${base}/questions/${enc(sel)}/rubric/key-conflicts`, { choices: { [id]: v } }));
  const proposed = r
    ? r.accepted_alternatives.filter((a) => a.status === "proposed").length +
      r.candidates.filter((c) => c.status === "proposed").length +
      r.key_conflicts.filter((k) => k.choice === null).length
    : 0;

  return (
    <div className="flex h-full">
      <aside className="w-56 shrink-0 border-r border-line p-3">
        {numbers.map((n) => {
          const s = stateOf(details[n]?.rubric);
          return (
            <button
              key={n}
              type="button"
              onClick={() => {
                setPicked(n);
                setEditing(false);
              }}
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
            {r && !editing && (
              <>
                <Button variant="outline" disabled={act.busy} onClick={() => setEditing(true)}>
                  <Pencil /> 고치기
                </Button>
                <Button variant="ghost" disabled={act.busy} onClick={() => setAskRemake(true)}>
                  <RefreshCw /> 다시 만들기
                </Button>
              </>
            )}
            {state === "없음" && (
              <Button variant="primary" disabled={act.busy} onClick={() => void make()}>
                {act.busy ? "만드는 중…" : "루브릭 만들기"}
              </Button>
            )}
            {state === "초안" && !editing && (
              <Button variant="primary" disabled={act.busy || proposed > 0} onClick={() => void approve()}>
                <Check /> 승인 {proposed > 0 && `(남은 확인 ${proposed})`}
              </Button>
            )}
            {state === "승인" && !editing && (
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

          {r && editing && (
            <RubricEditor
              q={q}
              r={r}
              onCancel={() => setEditing(false)}
              onDone={() => {
                setEditing(false);
                changed();
              }}
            />
          )}

          {r && !editing && (
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
                      {r.partial
                        .filter((x) => x.item_id === it.id)
                        .map((x) => (
                          <span key={x.condition} className="block text-[12px] text-text-2">
                            부분 점수: <RichText text={x.condition} /> → {pts(x.points)}점
                          </span>
                        ))}
                    </span>
                    <span className="font-medium">{pts(it.points)}점</span>
                  </div>
                ))}
              </CardBody>
            </Card>
          )}

          {r && !editing && r.key_conflicts.length > 0 && <KeyConflicts r={r} busy={act.busy} onChoose={(id, v) => void chooseBasis(id, v)} />}

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

          {r && r.status === "approved" && !editing && <Calibration q={q} rubric={r} onChanged={changed} />}
        </div>
      </div>
      <Dialog
        open={askRemake}
        onOpenChange={setAskRemake}
        title={`${sel}번 루브릭을 다시 만들까요?`}
        desc="해설지(또는 AI 풀이)로 새 초안을 만듭니다. 고친 채점 항목, 인정·제외한 정답 후보, 해설지와 다른 기준에서 고른 것은 새 초안으로 바뀌고, 다시 승인·확정해야 합니다. 객관식·단답형에서 고친 정답은 그대로 둡니다."
        footer={
          <>
            <Button variant="ghost" onClick={() => setAskRemake(false)}>
              취소
            </Button>
            <Button variant="primary" disabled={act.busy} onClick={() => void make()}>
              {act.busy ? "만드는 중…" : "다시 만들기"}
            </Button>
          </>
        }
      />
    </div>
  );
}
