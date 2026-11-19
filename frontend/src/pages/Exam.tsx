import { Check, LayoutGrid } from "lucide-react";
import { createContext, useContext } from "react";
import { Count } from "@/components/ui/count";
import { ErrorNote } from "@/components/ui/misc";
import { enc, useApi } from "@/lib/api";
import { cn } from "@/lib/cn";
import { stepCounts, useNotices } from "@/lib/notices";
import { href } from "@/lib/router";
import { STEPS, stepIndex } from "@/lib/steps";
import type { ExamDetail, StepKey } from "@/lib/types";
import { ExportStep } from "./exam/ExportStep";
import { GradeStep } from "./exam/GradeStep";
import { ReadStep } from "./exam/ReadStep";
import { ResultStep } from "./exam/ResultStep";
import { RubricStep } from "./exam/RubricStep";
import { ScanStep } from "./exam/ScanStep";

/** `focus`: 단계 뒤의 주소 (`#/exams/<시험>/grade/<문항>/<학생>`, `…/scan/<학생>/<문항>`) — 학생 검색·알림에서 바로 연다. */
type ExamCtx = { id: string; base: string; detail: ExamDetail; reload: () => void; focus: string[] };
const Ctx = createContext<ExamCtx | null>(null);

export function useExam(): ExamCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("시험 화면 밖");
  return c;
}

/** 시험 화면: 위에 단계 표시줄, 아래에 지금 단계 하나 (PLAN §11-1-1). */
export function ExamPage({ examId, step, focus = [] }: { examId: string; step?: string; focus?: string[] }) {
  const base = `/exams/${enc(examId)}`;
  const { data, error, reload } = useApi<ExamDetail>(base);
  const { notices } = useNotices();
  if (error) {
    return (
      <div className="mx-auto max-w-xl p-8">
        <ErrorNote message={error} />
      </div>
    );
  }
  if (!data) return <div className="p-8 text-text-3">불러오는 중…</div>;
  const s = data.summary;
  const current = (STEPS.find((x) => x.key === step)?.key ?? s.step) as StepKey;
  const progressIdx = stepIndex(s.step);
  const counts = stepCounts(notices, examId);
  const cells = s.students * s.questions;
  return (
    <Ctx value={{ id: examId, base, detail: data, reload, focus }}>
      <div className="flex h-full flex-col">
        <div className="shrink-0 border-b border-line bg-card px-6 pt-5">
          <div className="flex items-baseline gap-3">
            <h1 className="m-0 text-[19px] font-medium tracking-[-0.01em]">{data.exam.title}</h1>
            <span className="text-[13px] text-text-3">
              {data.scope.level ?? data.scope.title} · 문항 {s.questions}개 · {s.students}명
            </span>
          </div>
          {/* 단계: 밑줄 탭 + 번호 (끝난 단계는 초록 ✓). 2026-11-19 — 초록 알약 줄에서 */}
          <nav className="-mb-px mt-3 flex gap-1 overflow-x-auto">
            {STEPS.map((x, i) => {
              // 한 번에 채점한 시험은 단계가 '결과'에 머무르므로 단계마다 끝났는지를 숫자로 본다 (2026-11-20)
              const done =
                x.key !== "result" &&
                (s.step === "result"
                  ? (x.key === "read" && s.questions > 0) ||
                    (x.key === "rubric" && s.questions > 0 && s.approved >= s.questions) ||
                    (x.key === "scan" && s.students > 0 && s.needs_input === 0 && s.pending_transcripts === 0) ||
                    (x.key === "grade" && cells > 0 && s.graded >= cells && s.needs_review === 0)
                  : i < progressIdx || (s.step === "export" && i < STEPS.length - 1));
              const here = x.key === current;
              const label = x.key === "grade" && cells > 0 && s.graded > 0 ? `${x.label} ${s.graded}/${cells}` : x.label;
              return (
                <a
                  key={x.key}
                  href={href("exams", examId, x.key)}
                  className={cn(
                    "flex h-11 shrink-0 items-center gap-2 border-b-2 px-3 text-[13px] no-underline transition-colors",
                    here ? "border-primary font-medium text-text" : "border-transparent text-text-2 hover:text-text",
                    x.key === "result" && "mr-3",
                  )}
                >
                  {x.key === "result" ? (
                    <LayoutGrid className={cn("size-4", here ? "text-text" : "text-text-3")} />
                  ) : (
                    <span
                      className={cn(
                        "grid size-5 place-items-center rounded-full text-[11px] tabular-nums",
                        here ? "bg-primary text-primary-fg" : done ? "bg-ok-soft text-ok" : "border border-line-strong text-text-3",
                      )}
                    >
                      {done && !here ? <Check className="size-3" strokeWidth={3} /> : i}
                    </span>
                  )}
                  {label}
                  <Count n={counts[x.key] ?? 0} tone={x.key === "scan" ? "bad" : "warn"} />
                </a>
              );
            })}
          </nav>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          {current === "result" && <ResultStep />}
          {current === "read" && <ReadStep />}
          {current === "rubric" && <RubricStep />}
          {current === "scan" && <ScanStep />}
          {current === "grade" && <GradeStep />}
          {current === "export" && <ExportStep />}
        </div>
      </div>
    </Ctx>
  );
}
