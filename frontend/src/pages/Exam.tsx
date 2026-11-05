import { Check } from "lucide-react";
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
import { RubricStep } from "./exam/RubricStep";
import { ScanStep } from "./exam/ScanStep";

type ExamCtx = { id: string; base: string; detail: ExamDetail; reload: () => void };
const Ctx = createContext<ExamCtx | null>(null);

export function useExam(): ExamCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("시험 화면 밖");
  return c;
}

/** 시험 화면: 위에 단계 표시줄, 아래에 지금 단계 하나 (PLAN §11-1-1). */
export function ExamPage({ examId, step }: { examId: string; step?: string }) {
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
    <Ctx value={{ id: examId, base, detail: data, reload }}>
      <div className="flex h-full flex-col">
        <div className="shrink-0 border-b border-line bg-card px-6 pt-4">
          <div className="flex items-baseline gap-3">
            <h1 className="m-0 text-[18px] font-medium">{data.exam.title}</h1>
            <span className="text-[13px] text-text-3">
              {data.scope.level ?? data.scope.title} · 문항 {s.questions}개 · {s.students}명
            </span>
          </div>
          <nav className="flex gap-2 py-3">
            {STEPS.map((x, i) => {
              const done = i < progressIdx || (s.step === "export" && i < STEPS.length - 1);
              const here = x.key === current;
              const label = x.key === "grade" && cells > 0 && s.graded > 0 ? `${x.label} ${s.graded}/${cells}` : x.label;
              return (
                <a
                  key={x.key}
                  href={href("exams", examId, x.key)}
                  className={cn(
                    "flex h-9 items-center gap-1.5 rounded-full border px-4 text-[13px] no-underline",
                    here
                      ? "border-accent bg-accent-soft font-medium text-accent-text"
                      : done
                        ? "border-transparent bg-ok-soft text-ok"
                        : "border-line text-text-2 hover:bg-muted",
                  )}
                >
                  {done && !here && <Check className="size-4" />}
                  {label}
                  <Count n={counts[x.key] ?? 0} tone={x.key === "scan" ? "bad" : "warn"} />
                </a>
              );
            })}
          </nav>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
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
