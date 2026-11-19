import { AlertTriangle, ArrowUpRight, Check, ChevronDown, FileSpreadsheet, FileText, FolderOpen, Info, Play, Upload, X } from "lucide-react";
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
  const sums = rows.map((r) => r.sum).filter((v): v is number => v !== null);
  const avg = sums.length ? sums.reduce((x, y) => x + y, 0) / sums.length : null;
  const qAvg = qs.map((_, k) => {
    const v = rows.map((r) => r.totals[k]).filter((t): t is number => t !== null);
    return v.length ? v.reduce((x, y) => x + y, 0) / v.length : null;
  });
  const cells = rows.length * qs.length;
  const classes = [...new Set(rows.map((r) => r.st.record?.class_name ?? ""))];
  const files = [
    ...(out.data?.marked ?? []).map((name) => ({ key: name, icon: <FileText />, tone: "bg-bad-soft text-bad", name: name.replace(/\.pdf$/, ""), sub: "채점 답안지 PDF · 빨간 펜", path: `${out.data!.marked_folder}/${name}` })),
    ...(out.data?.scores_exists ? [{ key: "scores", icon: <FileSpreadsheet />, tone: "bg-ok-soft text-ok", name: "점수표", sub: "엑셀 · 반마다 시트", path: out.data.scores }] : []),
  ];
  const round = (n: number) => pts(Math.round(n * 10) / 10);

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-5 px-6 py-6">
      {running && (
        <Card className="p-5">
          <div className="flex items-baseline text-[14px]">
            <span className="flex-1 font-medium">채점하는 중{s?.stage ? ` — ${s.stage}` : ""}</span>
            <span className="text-[13px] text-text-3 tabular-nums">{job?.progress.total ? `${job.progress.done}/${job.progress.total}` : ""}</span>
          </div>
          <div className="mt-2.5">
            <Progress value={job?.progress.total ? (job.progress.done / job.progress.total) * 100 : 8} />
          </div>
          <div className="mt-2 text-[13px] text-text-3">{job?.progress.message}</div>
        </Card>
      )}
      {s?.state === "failed" && <ErrorNote message={`채점이 중간에 멈췄습니다: ${s.error ?? ""}`} detail="끝난 학생·문항은 저장돼 있습니다. 답안지를 다시 넣으면 남은 것만 합니다." />}

      {/* 한눈에: 학생 · 채점 · 평균 · 검토 */}
      <Card className="grid grid-cols-2 divide-line sm:grid-cols-4 sm:divide-x">
        <Stat label="학생" value={`${rows.length}`} unit="명" />
        <Stat label="채점" value={`${detail.summary.graded}`} unit={`/ ${cells}`}>
          <div className="mt-2">
            <Progress value={cells ? (detail.summary.graded / cells) * 100 : 0} tone={cells > 0 && detail.summary.graded >= cells ? "ok" : "accent"} />
          </div>
        </Stat>
        <Stat label="평균" value={avg === null ? "—" : round(avg)} unit={`/ ${pts(full)}`} />
        <Stat label="검토 권장" value={`${detail.summary.needs_review}`} unit="건" tone={detail.summary.needs_review > 0 ? "warn" : undefined} note={detail.summary.needs_review > 0 ? "점수는 들어가 있습니다" : "없음"} />
      </Card>

      {(files.length > 0 || out.data?.folder_exists) && (
        <section>
          <div className="mb-2 flex items-center gap-2">
            <span className="text-[13px] font-medium text-text-2">결과 파일</span>
            <div className="flex-1" />
            {out.data?.folder_exists && (
              <Button size="sm" variant="ghost" onClick={() => void act.run(() => api.post("/files/reveal", { path: out.data!.folder }))} title={out.data.folder}>
                <FolderOpen /> 폴더 열기
              </Button>
            )}
          </div>
          {files.length > 0 && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {files.map((f) => (
                <button
                  key={f.key}
                  type="button"
                  onClick={() => void openFile(f.path)}
                  title="열기"
                  className="group flex cursor-pointer items-center gap-3 rounded-[var(--radius-card)] border border-line bg-card px-4 py-3.5 text-left transition-colors hover:border-line-strong hover:bg-muted/50"
                >
                  <span className={cn("grid size-10 shrink-0 place-items-center rounded-xl [&_svg]:size-5", f.tone)}>{f.icon}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{f.name}</span>
                    <span className="block truncate text-[12px] text-text-3">{f.sub}</span>
                  </span>
                  <ArrowUpRight className="size-4 shrink-0 text-text-3 transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-text" />
                </button>
              ))}
            </div>
          )}
        </section>
      )}
      {act.error && <ErrorNote message={act.error} />}

      {(s?.asks.length ?? 0) > 0 && (
        <Card className="overflow-hidden">
          <div className="flex items-center gap-2 px-5 pt-4 pb-3">
            <span className="grid size-6 place-items-center rounded-full bg-warn-soft text-warn">
              <AlertTriangle className="size-3.5" />
            </span>
            <span className="text-[15px] font-medium">확인할 것</span>
            <span className="text-[13px] text-text-3 tabular-nums">{s!.asks.length}</span>
            <span className="ml-2 text-[12px] text-text-3">채점은 끝났습니다 — 고치면 그 학생·문항만 다시 채점합니다</span>
          </div>
          <div className="divide-y divide-line border-t border-line">
            {s!.asks.map((a, i) => (
              <div key={i} className="flex items-center gap-3 px-5 py-2.5">
                <Badge tone={a.kind === "candidate" || a.kind === "rubric" ? "warn" : "bad"} className="w-16 justify-center">
                  {ASK_LABEL[a.kind]}
                </Badge>
                <span className="min-w-0 flex-1 text-[13px]">{a.text}</span>
                {a.kind === "unreadable" && a.student && a.question && (
                  <Button size="sm" variant="outline" onClick={() => setFix({ student: a.student!, question: a.question! })}>
                    원본 보고 채우기
                  </Button>
                )}
                {a.kind === "name" && (
                  <Button size="sm" variant="outline" asChild>
                    <a href={href("exams", id, "scan")} className="text-text no-underline">
                      이름 고치기
                    </a>
                  </Button>
                )}
                {(a.kind === "candidate" || a.kind === "rubric") && (
                  <Button size="sm" variant="outline" asChild>
                    <a href={href("exams", id, "rubric")} className="text-text no-underline">
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
        <div className="flex items-center gap-2 px-5 pt-4 pb-3">
          <span className="text-[15px] font-medium">점수</span>
          <span className="text-[12px] text-text-3">칸을 누르면 채점 검토에서 그 답안을 엽니다 · 노랑은 검토 권장</span>
          <div className="flex-1" />
          <Button size="sm" variant={more ? "outline" : "ghost"} disabled={running} onClick={() => setMore(!more)}>
            <Upload /> 답안지 더 넣기
          </Button>
        </div>
        {more && (
          <div className="flex flex-col gap-2 border-t border-line bg-muted/40 px-5 py-4">
            <Dropzone
              compact={staged.length > 0}
              title={staged.length > 0 ? "PDF 더 놓기" : "학생 답안지 PDF를 끌어다 놓기"}
              hint="PDF마다 반을 고르면 그 반 명단에서 이름을 고릅니다 — 이미 읽은 학생은 건너뜁니다"
              accept=".pdf"
              multiple
              onFiles={stage}
            />
            {staged.length > 0 && (
              <div className="flex flex-col divide-y divide-line rounded-xl border border-line bg-card">
                {staged.map((f, i) => (
                  <div key={`${f.file.name}-${i}`} className="flex items-center gap-3 px-3 py-2">
                    <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-ok-soft text-ok">
                      <FileText className="size-4" />
                    </span>
                    <span className="min-w-0 flex-1 truncate">{f.file.name}</span>
                    <ClassField
                      value={f.klass}
                      onChange={(v) => setStaged(staged.map((p, k) => (k === i ? { ...p, klass: v } : p)))}
                      classes={roster.data?.classes ?? []}
                      className="w-28"
                      title="이 답안지 묶음의 반 — 고르면 그 반 명단에서 이름을 고릅니다"
                    />
                    <button type="button" aria-label="빼기" onClick={() => setStaged(staged.filter((_, k) => k !== i))} className="grid size-8 shrink-0 cursor-pointer place-items-center rounded-lg text-text-3 hover:bg-muted hover:text-text">
                      <X className="size-4" />
                    </button>
                  </div>
                ))}
              </div>
            )}
            {staged.length > 0 && (
              <div className="flex justify-end">
                <Button variant="primary" disabled={act.busy} onClick={() => void addAnswers()}>
                  <Play /> 읽고 채점
                </Button>
              </div>
            )}
          </div>
        )}
        <table className="w-full border-collapse text-left">
          <thead className="border-t border-line bg-muted/50 text-[12px] text-text-3">
            <tr>
              <th className="px-5 py-2 font-normal">이름</th>
              {qs.map((q) => (
                <th key={q.number} className="px-2 py-2 text-center font-normal">
                  {q.number}번 <span className="text-text-3">/{pts(q.points)}</span>
                </th>
              ))}
              <th className="px-5 py-2 text-right font-normal">합계 /{pts(full)}</th>
            </tr>
          </thead>
          <tbody>
            {classes.map((klass) => {
              const mine = rows.filter((r) => (r.st.record?.class_name ?? "") === klass);
              const ks = mine.map((r) => r.sum).filter((v): v is number => v !== null);
              return [
                classes.length > 1 || klass ? (
                  <tr key={`c-${klass}`} className="border-t border-line">
                    <td colSpan={qs.length + 2} className="px-5 pt-3 pb-1.5 text-[12px] text-text-2">
                      <span className="font-medium text-text">{klass || "반 모름"}</span>
                      <span className="ml-2 text-text-3">
                        {mine.length}명{ks.length ? ` · 평균 ${round(ks.reduce((x, y) => x + y, 0) / ks.length)}` : ""}
                      </span>
                    </td>
                  </tr>
                ) : null,
                ...mine.map(({ st, totals, sum }) => (
                  <tr key={st.student} className="group border-t border-line/60 hover:bg-muted/50">
                    <td className="px-5 py-2">
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
                        <td key={q.number} className="px-2 py-1.5 text-center">
                          <a
                            href={href("exams", id, "grade", q.number, st.student)}
                            className={cn(
                              "inline-block min-w-10 rounded-md px-2 py-0.5 tabular-nums no-underline transition-colors",
                              g?.status === "needs_review" ? "bg-warn-soft text-warn hover:ring-1 hover:ring-warn/40" : "text-text hover:bg-muted",
                              !g && "text-text-3",
                            )}
                            title="채점 검토에서 보기"
                          >
                            {totals[k] === null ? "—" : pts(totals[k])}
                          </a>
                        </td>
                      );
                    })}
                    <td className="px-5 py-2 text-right font-medium tabular-nums">{sum === null ? "—" : pts(sum)}</td>
                  </tr>
                )),
              ];
            })}
          </tbody>
          {rows.length > 0 && (
            <tfoot className="border-t border-line bg-muted/50 text-[12px] text-text-2">
              <tr>
                <td className="px-5 py-2">평균</td>
                {qAvg.map((v, k) => (
                  <td key={k} className="px-2 py-2 text-center tabular-nums">
                    {v === null ? "—" : round(v)}
                  </td>
                ))}
                <td className="px-5 py-2 text-right font-medium text-text tabular-nums">{avg === null ? "—" : round(avg)}</td>
              </tr>
            </tfoot>
          )}
        </table>
        {rows.length === 0 && <div className="border-t border-line px-5 py-10 text-center text-text-3">{running ? "답안지를 읽는 중입니다." : "아직 학생 답안지가 없습니다. '답안지 더 넣기'로 넣으세요."}</div>}
      </Card>

      {s && (s.decisions.length > 0 || s.warnings.length > 0) && (
        <div>
          <button type="button" onClick={() => setShowAuto(!showAuto)} className="flex cursor-pointer items-center gap-1 text-[13px] text-text-3 hover:text-text">
            <ChevronDown className={cn("size-4 transition", showAuto && "rotate-180")} />
            자동으로 정한 것 {s.decisions.length} · 알림 {s.warnings.length}
          </button>
          {showAuto && (
            <div className="mt-2 flex flex-col gap-1.5 rounded-xl border border-line bg-card px-4 py-3 text-[13px] text-text-2">
              {s.decisions.map((d) => (
                <div key={d} className="flex gap-2">
                  <Check className="mt-[3px] size-3.5 shrink-0 text-ok" />
                  {d}
                </div>
              ))}
              {s.warnings.map((w, i) => (
                <div key={`w${i}`} className="flex gap-2 text-text-3">
                  <Info className="mt-[3px] size-3.5 shrink-0" />
                  {w}
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

/** 위 요약 칸 하나. */
function Stat({ label, value, unit, note, tone, children }: { label: string; value: string; unit?: string; note?: string; tone?: "warn"; children?: React.ReactNode }) {
  return (
    <div className="px-5 py-4">
      <div className="text-[12px] text-text-3">{label}</div>
      <div className="mt-1 flex items-baseline gap-1">
        <span className={cn("text-[24px] leading-none font-medium tabular-nums", tone === "warn" && "text-warn")}>{value}</span>
        {unit && <span className="text-[13px] text-text-3">{unit}</span>}
      </div>
      {note && <div className="mt-1.5 text-[12px] text-text-3">{note}</div>}
      {children}
    </div>
  );
}
