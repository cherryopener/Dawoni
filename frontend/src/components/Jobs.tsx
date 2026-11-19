import * as P from "@radix-ui/react-popover";
import { CircleCheck, CircleSlash, CircleX, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/misc";
import { api, jobsChanged, onJobsChanged } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { Job } from "@/lib/types";

export const JOB_NAMES: Record<string, string> = {
  ingest_exam: "시험 읽기",
  answer_key: "해설지 읽기",
  solve: "해설 만들기",
  rubric: "루브릭 만들기",
  match_candidates: "정답 후보 맞추기",
  scan: "손글씨 판독",
  scan_batch: "답안지 판독",
  grade: "채점",
  teacher_scores: "강사 점수 넣기",
  read_marks: "빨간 펜 점수 읽기",
  marked_pdf: "채점 답안지 PDF",
  autopilot: "한 번에 채점",
  confirm_marks: "강사 점수 확정",
  suggest_rubric: "보정 제안",
  calibration_eval: "보정 측정",
  agent_test: "AI 연결 시험",
  refine_rule: "규칙 다듬기",
  learn: "배우기",
};

const ACTIVE = new Set(["queued", "running", "paused"]);

/** 남은 시간: 시작한 뒤 걸린 시간 ÷ 한 만큼 (진행 수를 알 때만). */
function eta(j: Job): string | null {
  const { done, total } = j.progress;
  if (!j.started_at || !total || !done) return null;
  const spent = (Date.now() - new Date(j.started_at).getTime()) / 1000;
  const left = (spent / done) * (total - done);
  if (left < 60) return "1분 안에 끝남";
  return `약 ${Math.round(left / 60)}분 남음`;
}

/** 오래 걸리는 작업: 진행 막대 + 남은 시간 (PLAN §11-1-6). 끝나면 알림 소식으로 넘어간다. */
export function JobsButton() {
  const [jobs, setJobs] = useState<Job[]>([]);
  useEffect(() => {
    let was = 0;
    const load = () =>
      api
        .get<Job[]>("/jobs")
        .then((js) => {
          setJobs(js);
          const now = js.filter((j) => ACTIVE.has(j.status)).length;
          if (now < was) jobsChanged(); // 끝난 작업이 있으면 화면·알림이 다시 읽는다
          was = now;
        })
        .catch(() => undefined);
    load();
    const t = setInterval(load, 2000);
    const off = onJobsChanged(load);
    return () => {
      clearInterval(t);
      off();
    };
  }, []);
  const running = jobs.filter((j) => ACTIVE.has(j.status));
  const cancel = (id: string) => api.post(`/jobs/${id}/cancel`).then(jobsChanged).catch(() => undefined);
  return (
    <P.Root>
      <P.Trigger className="flex h-9 cursor-pointer items-center gap-1.5 rounded-lg px-3 text-text-2 hover:bg-muted data-[state=open]:bg-muted">
        {running.length > 0 && <Loader2 className="size-4 animate-spin text-accent" />}
        <span className="text-[13px]">{running.length > 0 ? `작업 ${running.length}개` : "작업"}</span>
      </P.Trigger>
      <P.Portal>
        <P.Content align="end" sideOffset={6} className="z-50 flex max-h-[min(560px,80vh)] w-[360px] flex-col rounded-[var(--radius-card)] border border-line bg-card text-text">
          <div className="flex items-center gap-2 border-b border-line px-4 py-3">
            <span className="flex-1 text-[15px] font-medium">작업</span>
            {running.length > 0 && <Badge tone="accent">진행 {running.length}</Badge>}
          </div>
          <div className="min-h-0 flex-1 overflow-auto p-1.5">
            {jobs.length === 0 && <div className="px-3 py-8 text-center text-[13px] text-text-3">아직 작업이 없습니다.</div>}
            {jobs.slice(0, 8).map((j) => {
              const active = ACTIVE.has(j.status);
              return (
                <div key={j.id} className="flex items-start gap-3 rounded-lg px-3 py-2.5 hover:bg-muted/50">
                  <span
                    className={cn(
                      "mt-0.5 grid size-8 shrink-0 place-items-center rounded-full [&_svg]:size-4",
                      active ? "bg-accent-soft text-accent-text" : j.status === "succeeded" ? "bg-ok-soft text-ok" : j.status === "failed" ? "bg-bad-soft text-bad" : "bg-muted text-text-3",
                    )}
                  >
                    {active ? <Loader2 className="animate-spin" /> : j.status === "succeeded" ? <CircleCheck /> : j.status === "failed" ? <CircleX /> : <CircleSlash />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 text-[13px]">
                      <span className="flex-1 font-medium">{JOB_NAMES[j.kind] ?? j.kind}</span>
                      {active ? (
                        <button type="button" onClick={() => cancel(j.id)} className="cursor-pointer text-[12px] text-text-3 hover:text-bad">
                          멈추기
                        </button>
                      ) : (
                        <span className="text-[12px] text-text-3">{j.status === "succeeded" ? "끝남" : j.status === "failed" ? "멈춤" : "취소"}</span>
                      )}
                    </div>
                    {active && (
                      <>
                        <div className="mt-2">
                          <Progress value={j.progress.total ? (j.progress.done / j.progress.total) * 100 : 5} />
                        </div>
                        <div className="mt-1.5 text-[12px] text-text-3">{[j.progress.message, eta(j)].filter(Boolean).join(" · ") || "시작하는 중"}</div>
                      </>
                    )}
                    {j.status === "failed" && j.error && <div className="mt-1 text-[12px] leading-snug text-bad">{j.error.message}</div>}
                  </div>
                </div>
              );
            })}
          </div>
        </P.Content>
      </P.Portal>
    </P.Root>
  );
}
