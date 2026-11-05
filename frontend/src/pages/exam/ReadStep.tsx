import { AlertTriangle, ArrowRight, Check } from "lucide-react";
import { useState } from "react";
import { RichText } from "@/components/Math";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Help } from "@/components/ui/help";
import { ErrorNote } from "@/components/ui/misc";
import { api, enc, useAction, waitJob } from "@/lib/api";
import { go } from "@/lib/router";
import { useTeacher } from "@/lib/session";
import { pts } from "@/lib/steps";
import type { ExamQuestion, Job, QuestionDetail } from "@/lib/types";
import { useExam } from "../Exam";
import { useQuestionDetails } from "./shared";

const KIND: Record<string, string> = { essay: "서술형", short_answer: "단답형" };

function QuestionCard({ q, d, locked, onSaved }: { q: ExamQuestion; d?: QuestionDetail; locked: boolean; onSaved: () => void }) {
  const { base } = useExam();
  const by = useTeacher();
  const [points, setPoints] = useState<Record<string, string>>({});
  const act = useAction();
  const key = d?.key;
  const rows = q.parts.length ? q.parts : [{ label: "", points: q.points, kind: null }];
  const kindOf = (k: string | null) => (k ? KIND[k] : q.item.item_type === "essay" ? "서술형" : q.item.item_type === "multiple_choice" ? "객관식" : "단답형");
  const save = () =>
    act.run(async () => {
      const value = (label: string, old: number) => Number(points[label] ?? old);
      const body = q.parts.length
        ? { parts: Object.fromEntries(q.parts.map((p) => [p.label, value(p.label, p.points)])) }
        : { points: value("", q.points) };
      await api.put(`${base}/questions/${enc(q.number)}/points`, body);
      setPoints({});
      onSaved();
    });
  const confirmKey = () =>
    act.run(async () => {
      await api.put(`${base}/questions/${enc(q.number)}/key`, { confirm: true, by });
      onSaved();
    });
  const dirty = Object.keys(points).length > 0;
  return (
    <Card className="overflow-hidden">
      <div className="flex items-center gap-3 border-b border-line px-5 py-3">
        <span className="text-[15px] font-medium">{q.number}번</span>
        <span className="text-text-3">{pts(q.points)}점</span>
        <div className="flex-1" />
        {key?.confirmed && (
          <span className="inline-flex items-center gap-1 text-[12px] text-ok">
            <Check className="size-3.5" /> 해설 확인 {key.confirmed_by ?? ""}
          </span>
        )}
        {key && !key.confirmed && key.doubtful.length > 0 && (
          <Button size="sm" variant="outline" disabled={act.busy} onClick={() => void confirmKey()}>
            원본과 맞음
          </Button>
        )}
        {dirty && (
          <Button size="sm" variant="outline" disabled={act.busy} onClick={() => void save()}>
            배점 저장
          </Button>
        )}
      </div>
      <table className="w-full border-collapse text-left">
        <thead className="text-[12px] text-text-3">
          <tr>
            <th className="w-24 px-5 py-2 font-normal">소문항</th>
            <th className="w-20 px-2 py-2 font-normal">유형</th>
            <th className="w-32 px-2 py-2 font-normal">배점</th>
            <th className="px-5 py-2 font-normal">해설지 답</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => {
            const kp = key?.parts.find((x) => x.label === p.label);
            const doubt = key?.doubtful.includes(p.label) && !key.confirmed;
            return (
              <tr key={p.label} className="border-t border-line align-top">
                <td className="px-5 py-2.5">{p.label ? `${q.number}-${p.label}` : q.number}</td>
                <td className="px-2 py-2.5 text-text-2">{kindOf(p.kind)}</td>
                <td className="px-2 py-2.5">
                  <input
                    type="number"
                    min={0}
                    step={0.5}
                    disabled={locked}
                    value={points[p.label] ?? String(p.points)}
                    onChange={(e) => setPoints({ ...points, [p.label]: e.target.value })}
                    className="h-8 w-16 rounded-md border border-line bg-card px-2 text-right outline-none focus:border-accent disabled:opacity-60"
                  />
                  <span className="ml-1 text-text-3">점</span>
                </td>
                <td className="px-5 py-2.5">
                  {!key ? (
                    <span className="text-text-3">해설 없음</span>
                  ) : (
                    <div className="flex flex-col gap-1">
                      <RichText text={kp?.answer ?? (kp?.solution.slice(0, 120) || "—")} />
                      {doubt && (
                        <span>
                          <Badge tone="warn">
                            <AlertTriangle /> 두 번 읽은 숫자가 다름 · 원본과 비교
                          </Badge>
                        </span>
                      )}
                    </div>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {act.error && (
        <div className="px-5 pb-3">
          <ErrorNote message={act.error} />
        </div>
      )}
    </Card>
  );
}

/** ① 시험 읽기: 읽은 문항·소문항·배점·해설지 답을 확인하고 고친다. 배점 표시가 없던 문서는 자리값(2점). */
export function ReadStep() {
  const { id, base, detail, reload } = useExam();
  const by = useTeacher();
  const numbers = detail.exam.questions.map((q) => q.number);
  const { details, error, reload: reloadDetails } = useQuestionDetails(base, numbers);
  const act = useAction();
  const [msg, setMsg] = useState("");
  const total = detail.exam.questions.reduce((a, q) => a + q.points, 0);
  const parts = detail.exam.questions.reduce((a, q) => a + Math.max(1, q.parts.length), 0);
  const doubtful = Object.values(details).reduce((a, d) => a + (d.key && !d.key.confirmed ? d.key.doubtful.length : 0), 0);
  const noRubric = detail.questions.every((q) => q.rubric === null);
  const makeRubrics = () =>
    act.run(async () => {
      await waitJob(await api.post<Job>(`${base}/rubrics`, { by }), (j) => setMsg(j.progress.message));
      reload();
      go("exams", id, "rubric");
    });
  return (
    <div className="mx-auto max-w-4xl px-6 py-6">
      <div className="mb-4 flex items-center gap-3">
        <span className="text-text-2">
          문항 {numbers.length}개 · 소문항 {parts}개 · {pts(total)}점
        </span>
        {doubtful > 0 && (
          <Badge tone="warn">
            <AlertTriangle /> 해설 원본 확인 {doubtful}곳
          </Badge>
        )}
        <Help>배점 표시가 없는 시험지는 소문항마다 2점으로 두었습니다. 루브릭을 만들기 전에 맞게 고쳐 주세요.</Help>
        <div className="flex-1" />
        {noRubric ? (
          <Button variant="primary" disabled={act.busy} onClick={() => void makeRubrics()}>
            {act.busy ? msg || "루브릭 만드는 중…" : "확인하고 루브릭 만들기"} <ArrowRight />
          </Button>
        ) : (
          <Button variant="primary" onClick={() => go("exams", id, "rubric")}>
            루브릭으로 <ArrowRight />
          </Button>
        )}
      </div>
      {(error || act.error) && (
        <div className="mb-3">
          <ErrorNote message={(act.error ?? error)!} />
        </div>
      )}
      <div className="flex flex-col gap-3">
        {detail.exam.questions.map((q) => (
          <QuestionCard
            key={q.number}
            q={q}
            d={details[q.number]}
            locked={detail.questions.find((x) => x.number === q.number)?.rubric != null}
            onSaved={() => {
              reload();
              reloadDetails();
            }}
          />
        ))}
      </div>
    </div>
  );
}
