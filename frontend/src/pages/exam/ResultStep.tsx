import { AlertTriangle, ChevronDown, FileSpreadsheet, FileText, FolderOpen, Play, Upload, X } from "lucide-react";
import { useEffect, useState } from "react";
import { ClassField } from "@/components/ClassField";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dropzone, ErrorNote, Progress } from "@/components/ui/misc";
import { api, onJobsChanged, useAction, useApi, waitJob } from "@/lib/api";
import { cn } from "@/lib/cn";
import { guessClass } from "@/lib/roles";
import { href } from "@/lib/router";
import { useTeacher } from "@/lib/session";
import { pts } from "@/lib/steps";
import type { AutoAsk, AutoState, ExamStudent, Job, Outputs } from "@/lib/types";
import { useExam } from "../Exam";
import { type EditTarget, TranscriptEditor } from "./TranscriptEditor";

/** 이 시험의 한 번에 채점 작업 (돌고 있으면). */
function useAutoJob(examId: string): Job | null {
  const [job, setJob] = useState<Job | null>(null);
  useEffect(() => {
    const load = () =>
      api
        .get<Job[]>("/jobs")
        .then((js) => setJob(js.find((j) => j.kind === "autopilot" && j.params.exam === examId && ["queued", "running"].includes(j.status)) ?? null))
        .catch(() => undefined);
    load();
    const t = setInterval(load, 2000);
    const off = onJobsChanged(load);
    return () => {
      clearInterval(t);
      off();
    };
  }, [examId]);
  return job;
}

const ASK_LABEL: Record<AutoAsk["kind"], string> = {
  name: "이름",
  unreadable: "판독불가",
  candidate: "정답 후보",
  rubric: "루브릭",
  pages: "쪽 나누기",
};

/** ⓪ 결과 (한 번에 채점, 2026-11-18): 점수, 채점 답안지 PDF·점수표, 답안지 더 넣기, 확인할 것.
 * 자세한 단계(루브릭·판독·채점 검토)는 위 단계 표시줄에서 그대로 연다. */
export function ResultStep() {
  const { id, base, detail, reload } = useExam();
  const by = useTeacher();
  const auto = useApi<AutoState | null>(`${base}/autopilot`);
  const students = useApi<ExamStudent[]>(`${base}/students`);
  const out = useApi<Outputs>(`${base}/outputs`);
  const job = useAutoJob(id);
  const [fix, setFix] = useState<EditTarget | null>(null);
  const [more, setMore] = useState(false);
  const [staged, setStaged] = useState<{ file: File; klass: string }[]>([]); // 더 넣을 답안지와 그 반
  const roster = useApi<{ classes: string[] }>(more ? "/students" : null, { refreshOnJobs: false });
  const [showAuto, setShowAuto] = useState(false);
  const act = useAction();
  const s = auto.data;
  const qs = detail.exam.questions;
  const full = qs.reduce((a, q) => a + q.points, 0);

  const openFile = (path: string) => act.run(() => api.post("/files/open", { path }));
  const stage = (files: File[]) => {
    const pdfs = files.filter((f) => /\.pdf$/i.test(f.name) && !staged.some((p) => p.file.name === f.name && p.file.size === f.size));
    setStaged([...staged, ...pdfs.map((f) => ({ file: f, klass: guessClass(f.name, roster.data?.classes ?? []) }))]);
  };
  const addAnswers = () =>
    act.run(async () => {
      if (staged.length === 0) throw new Error("답안지 PDF를 넣어 주세요.");
      const form = new FormData();
      for (const f of staged) {
        form.append("answers", f.file);
        form.append("answer_classes", f.klass.trim()); // 파일과 같은 차례 (빈 칸 = 고르지 않음)
      }
      form.append("exam_id", id);
      if (by) form.append("by", by);
      setMore(false);
      setStaged([]);
      await waitJob(await api.upload<Job>("/quick", form));
      auto.reload();
      students.reload();
      out.reload();
      reload();
    });

  const rows = (students.data ?? [])
    .map((st) => {
      const totals = qs.map((q) => st.cells[q.number]?.grade?.total ?? null);
      const review = qs.some((q) => st.cells[q.number]?.grade?.status === "needs_review");
      const done = totals.every((t) => t !== null);
      return { st, totals, review, sum: done ? totals.reduce<number>((a, t) => a + (t ?? 0), 0) : null };
    })
    .sort((a, b) => (a.st.record?.class_name ?? "").localeCompare(b.st.record?.class_name ?? "") || (a.st.record?.name ?? a.st.student).localeCompare(b.st.record?.name ?? b.st.student));
  const running = job !== null || s?.state === "running";

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4 px-6 py-6">
      {running && (
        <Card className="p-5">
          <div className="flex text-[14px]">
            <span className="flex-1 font-medium">채점하는 중{s?.stage ? ` — ${s.stage}` : ""}</span>
            <span className="text-text-3">{job?.progress.total ? `${job.progress.done}/${job.progress.total}` : ""}</span>
          </div>
          <div className="mt-2">
            <Progress value={job?.progress.total ? (job.progress.done / job.progress.total) * 100 : 8} />
          </div>
          <div className="mt-1.5 text-[13px] text-text-3">{job?.progress.message}</div>
        </Card>
      )}
      {s?.state === "failed" && <ErrorNote message={`채점이 중간에 멈췄습니다: ${s.error ?? ""}`} detail="끝난 학생·문항은 저장돼 있습니다. 답안지를 다시 넣으면 남은 것만 합니다." />}

      <Card className="flex flex-wrap items-center gap-3 p-5">
        <div className="flex-1">
          <div className="text-[16px] font-medium">
            학생 {rows.length}명 · 채점 {detail.summary.graded}/{rows.length * qs.length}
          </div>
          <div className="mt-0.5 text-[13px] text-text-3">
            {detail.summary.needs_review > 0 ? `검토 권장 ${detail.summary.needs_review}건 (노랑) — 점수는 들어가 있습니다` : "모두 채점됨"}
          </div>
        </div>
        {(out.data?.marked ?? []).map((name) => (
          <Button key={name} variant="primary" onClick={() => void openFile(`${out.data!.marked_folder}/${name}`)} title="학생 답안지 위에 빨간 글씨로 점수·이유">
            <FileText /> {name.replace(/\.pdf$/, "")}
          </Button>
        ))}
        {out.data?.scores_exists && (
          <Button variant="outline" onClick={() => void openFile(out.data!.scores)}>
            <FileSpreadsheet /> 점수표
          </Button>
        )}
        {out.data?.folder_exists && (
          <Button variant="ghost" onClick={() => void act.run(() => api.post("/files/reveal", { path: out.data!.folder }))}>
            <FolderOpen /> 폴더
          </Button>
        )}
        <Button variant="ghost" disabled={running} onClick={() => setMore(!more)}>
          <Upload /> 답안지 더 넣기
        </Button>
      </Card>
      {more && (
        <Card className="flex flex-col gap-2 p-4">
          <Dropzone title="학생 답안지 PDF를 끌어다 놓기" hint="PDF마다 반을 고르면 그 반 명단에서 이름을 고릅니다 — 이미 읽은 학생은 건너뜁니다" accept=".pdf" multiple onFiles={stage} />
          {staged.map((f, i) => (
            <div key={`${f.file.name}-${i}`} className="flex items-center gap-2 rounded-lg border border-line px-3 py-1.5">
              <FileText className="size-4 text-text-3" />
              <span className="flex-1 truncate">{f.file.name}</span>
              <ClassField
                value={f.klass}
                onChange={(v) => setStaged(staged.map((p, k) => (k === i ? { ...p, klass: v } : p)))}
                classes={roster.data?.classes ?? []}
                title="이 답안지 묶음의 반 — 고르면 그 반 명단에서 이름을 고릅니다"
              />
              <Button size="icon" variant="ghost" aria-label="빼기" onClick={() => setStaged(staged.filter((_, k) => k !== i))}>
                <X />
              </Button>
            </div>
          ))}
          {staged.length > 0 && (
            <div className="flex justify-end">
              <Button variant="primary" disabled={act.busy} onClick={() => void addAnswers()}>
                <Play /> 읽고 채점
              </Button>
            </div>
          )}
        </Card>
      )}
      {act.error && <ErrorNote message={act.error} />}

      {(s?.asks.length ?? 0) > 0 && (
        <Card className="p-5">
          <div className="flex items-center gap-2 text-[15px] font-medium">
            <AlertTriangle className="size-4 text-warn" /> 확인할 것 {s!.asks.length}
            <span className="text-[13px] font-normal text-text-3">채점은 끝났습니다 — 고치면 그 학생·문항만 다시 채점합니다</span>
          </div>
          <div className="mt-3 flex flex-col gap-1">
            {s!.asks.map((a, i) => (
              <div key={i} className="flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-muted">
                <Badge tone={a.kind === "candidate" || a.kind === "rubric" ? "warn" : "bad"}>{ASK_LABEL[a.kind]}</Badge>
                <span className="flex-1 text-[13px]">{a.text}</span>
                {a.kind === "unreadable" && a.student && a.question && (
                  <Button size="sm" variant="outline" onClick={() => setFix({ student: a.student!, question: a.question! })}>
                    원본 보고 채우기
                  </Button>
                )}
                {a.kind === "name" && (
                  <Button size="sm" variant="outline" asChild>
                    <a href={href("exams", id, "scan")} className="no-underline">
                      이름 고치기
                    </a>
                  </Button>
                )}
                {(a.kind === "candidate" || a.kind === "rubric") && (
                  <Button size="sm" variant="outline" asChild>
                    <a href={href("exams", id, "rubric")} className="no-underline">
                      루브릭 보기
                    </a>
                  </Button>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card className="overflow-hidden">
        <table className="w-full border-collapse text-left">
          <thead className="text-[12px] text-text-3">
            <tr>
              <th className="px-5 py-2 font-normal">반</th>
              <th className="px-2 py-2 font-normal">이름</th>
              {qs.map((q) => (
                <th key={q.number} className="px-2 py-2 text-center font-normal">
                  {q.number}번 <span className="text-text-3">/{pts(q.points)}</span>
                </th>
              ))}
              <th className="px-5 py-2 text-right font-normal">합계 /{pts(full)}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ st, totals, sum }) => (
              <tr key={st.student} className="border-t border-line hover:bg-muted">
                <td className="px-5 py-2 text-text-2">{st.record?.class_name ?? "—"}</td>
                <td className="px-2 py-2">
                  {st.record?.name ?? st.student}
                  {(st.record?.needs_rename || st.record?.roster_note) && (
                    <span className="ml-2">
                      <Badge tone="bad">이름 확인</Badge>
                    </span>
                  )}
                </td>
                {qs.map((q, k) => {
                  const g = st.cells[q.number]?.grade;
                  return (
                    <td key={q.number} className="px-2 py-2 text-center">
                      <a
                        href={href("exams", id, "grade", q.number, st.student)}
                        className={cn("rounded px-2 py-0.5 no-underline", g?.status === "needs_review" ? "bg-warn-soft text-warn" : "text-text", !g && "text-text-3")}
                        title="채점 검토에서 보기"
                      >
                        {totals[k] === null ? "—" : pts(totals[k])}
                      </a>
                    </td>
                  );
                })}
                <td className="px-5 py-2 text-right font-medium">{sum === null ? "—" : pts(sum)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <div className="px-5 py-8 text-center text-text-3">{running ? "답안지를 읽는 중입니다." : "아직 학생 답안지가 없습니다. '답안지 더 넣기'로 넣으세요."}</div>}
      </Card>

      {s && (s.decisions.length > 0 || s.warnings.length > 0) && (
        <div>
          <button type="button" onClick={() => setShowAuto(!showAuto)} className="flex cursor-pointer items-center gap-1 text-[13px] text-text-3 hover:text-text">
            <ChevronDown className={cn("size-4 transition", showAuto && "rotate-180")} />
            자동으로 정한 것 {s.decisions.length} · 알림 {s.warnings.length}
          </button>
          {showAuto && (
            <div className="mt-2 flex flex-col gap-1 rounded-lg bg-muted px-4 py-3 text-[12px] text-text-2">
              {s.decisions.map((d) => (
                <div key={d}>· {d}</div>
              ))}
              {s.warnings.map((w, i) => (
                <div key={`w${i}`} className="text-text-3">
                  ! {w}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      <TranscriptEditor
        target={fix}
        onClose={() => setFix(null)}
        onChanged={() => {
          auto.reload();
          students.reload();
          reload();
        }}
      />
    </div>
  );
}
