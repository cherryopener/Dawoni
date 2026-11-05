import * as P from "@radix-ui/react-popover";
import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/misc";
import { api, jobsChanged, onJobsChanged } from "@/lib/api";
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
  confirm_files: "한글에서 고친 것 반영",
  export: "HWPX 쓰기",
  teacher_scores: "강사 점수 넣기",
  read_marks: "빨간 펜 점수 읽기",
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
        <P.Content align="end" sideOffset={6} className="z-50 w-80 rounded-[var(--radius-card)] border border-line bg-card p-4 text-text">
          <div className="text-[13px] font-medium text-text-2">작업</div>
          {jobs.length === 0 && <div className="mt-3 text-[13px] text-text-3">아직 작업이 없습니다.</div>}
          {jobs.slice(0, 8).map((j) => (
            <div key={j.id} className="mt-3">
              <div className="flex items-center gap-2 text-[13px]">
                <span className="flex-1">{JOB_NAMES[j.kind] ?? j.kind}</span>
                {ACTIVE.has(j.status) ? (
                  <button type="button" onClick={() => cancel(j.id)} className="cursor-pointer text-[12px] text-text-3 hover:text-text">
                    멈추기
                  </button>
                ) : j.status === "succeeded" ? (
                  <Badge tone="ok">끝남</Badge>
                ) : j.status === "failed" ? (
                  <Badge tone="bad">멈춤</Badge>
                ) : (
                  <Badge>취소</Badge>
                )}
              </div>
              {ACTIVE.has(j.status) && (
                <>
                  <div className="mt-1.5">
                    <Progress value={j.progress.total ? (j.progress.done / j.progress.total) * 100 : 5} />
                  </div>
                  <div className="mt-1 text-[12px] text-text-3">
                    {[j.progress.message, eta(j)].filter(Boolean).join(" · ") || "시작하는 중"}
                  </div>
                </>
              )}
              {j.status === "failed" && j.error && <div className="mt-1 text-[12px] text-bad">{j.error.message}</div>}
            </div>
          ))}
        </P.Content>
      </P.Portal>
    </P.Root>
  );
}
