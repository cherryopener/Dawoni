import * as D from "@radix-ui/react-dialog";
import { ArrowRight, CornerDownLeft, Loader2, Sparkles, Stethoscope, Wand2, X } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorNote } from "@/components/ui/misc";
import { api, useAction, waitJob } from "@/lib/api";
import { go, useRoute } from "@/lib/router";
import { useTeacher } from "@/lib/session";
import type { Job } from "@/lib/types";

// 총괄 AI 요청창 (PLAN §2-1): 자연어 → 정해진 작업 하나로 바꿔 "이렇게 할까요?"로 보여 주고, 강사가 눌러야 실행.
// 총괄 AI는 학생 답안·해설지 원문을 보지 않는다 — 시험 이름·문항 번호·반과 실패 기록 요약만.

type Plan = {
  kind: "action" | "diagnose" | "unknown";
  action: string | null;
  exam_id: string | null;
  questions: string[];
  classes: string[];
  students: string[];
  regrade: boolean;
  screen: string | null;
  summary: string;
  cause: string | null;
  fix: string | null;
};

const ACTION: Record<string, string> = {
  grade: "채점",
  rubric: "루브릭 만들기",
  confirm_files: "한글에서 고친 것 반영",
  read_marks: "빨간 펜 점수 읽기",
  export: "HWPX 다시 쓰기",
  scores: "점수표 다시 쓰기",
  open: "화면 열기",
};

const EXAMPLES = ["2반 3번만 다시 채점해", "방금 채점이 왜 실패했어?", "점수표를 반마다 시트로 나눠 줘"];

export function Coordinator({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const route = useRoute();
  const by = useTeacher();
  const [text, setText] = useState("");
  const [plan, setPlan] = useState<{ plan: Plan; students: string[] | null } | null>(null);
  const act = useAction();
  const examId = route[0] === "exams" ? route[1] : undefined;

  const close = (v: boolean) => {
    onOpenChange(v);
    if (!v) {
      setPlan(null);
      setText("");
      act.clear();
    }
  };
  const submit = (t: string) =>
    act.run(async () => {
      if (!t.trim()) return;
      setText(t);
      setPlan(null);
      const j = await waitJob(await api.post<Job>("/coordinator/plan", { text: t, exam_id: examId ?? null }));
      setPlan(j.result as { plan: Plan; students: string[] | null });
    });
  const execute = () =>
    act.run(async () => {
      const r = await api.post<{ job: Job | null; link: string[] }>("/coordinator/run", { plan: plan!.plan, by });
      if (r.job) void waitJob(r.job).catch(() => undefined); // 진행은 작업·알림으로
      close(false);
      go(...r.link);
    });

  const p = plan?.plan;
  return (
    <D.Root open={open} onOpenChange={close}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-40 bg-black/40" />
        <D.Content
          onInteractOutside={(e) => e.preventDefault()} // 바깥을 눌러도 적던 부탁을 잃지 않게
          className="fixed left-1/2 top-[14vh] z-50 w-[min(620px,92vw)] -translate-x-1/2 overflow-hidden rounded-[var(--radius-card)] border border-line bg-card text-text"
        >
          <D.Title className="sr-only">총괄 AI에게 부탁하기</D.Title>
          <form
            className="flex items-center gap-2 border-b border-line px-4"
            onSubmit={(e) => {
              e.preventDefault();
              void submit(text);
            }}
          >
            {act.busy ? <Loader2 className="size-4 animate-spin text-accent" /> : <Sparkles className="size-4 text-accent" />}
            <input
              autoFocus
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                setPlan(null);
              }}
              placeholder="무엇을 할까요? 예: 2반 3번만 다시 채점해"
              className="h-14 flex-1 bg-transparent text-[15px] outline-none placeholder:text-text-3"
            />
            <CornerDownLeft className="size-4 text-text-3" />
            <D.Close type="button" className="ml-1 cursor-pointer text-text-3 hover:text-text" aria-label="닫기">
              <X className="size-4" />
            </D.Close>
          </form>
          {act.error && (
            <div className="p-4">
              <ErrorNote message={act.error} />
            </div>
          )}
          {act.busy && !p && <div className="px-5 py-4 text-[13px] text-text-3">부탁을 작업으로 바꾸는 중… (AI 호출 1번)</div>}
          {!p && !act.busy && (
            <div className="p-2">
              <div className="px-2 pb-1 pt-2 text-[12px] text-text-3">이런 것을 부탁할 수 있어요</div>
              {EXAMPLES.map((ex) => (
                <button
                  key={ex}
                  type="button"
                  onClick={() => void submit(ex)}
                  className="flex w-full cursor-pointer items-center gap-2 rounded-lg px-2 py-2 text-left text-text-2 hover:bg-muted hover:text-text"
                >
                  <ArrowRight className="size-4 text-text-3" />
                  {ex}
                </button>
              ))}
            </div>
          )}
          {p?.kind === "action" && (
            <div className="p-5">
              <div className="flex items-center gap-2">
                <Wand2 className="size-4 text-accent" />
                <span className="font-medium">이렇게 할까요?</span>
              </div>
              <div className="mt-3 rounded-lg border border-line p-4">
                <div className="text-[15px] font-medium">{p.summary}</div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <Badge tone="accent">{ACTION[p.action ?? ""] ?? p.action}</Badge>
                  {p.questions.map((q) => (
                    <Badge key={q}>{q}번</Badge>
                  ))}
                  {p.classes.map((c) => (
                    <Badge key={c}>{c}</Badge>
                  ))}
                  {p.students.map((s) => (
                    <Badge key={s}>{s}</Badge>
                  ))}
                  {plan?.students && <Badge>학생 {plan.students.length}명</Badge>}
                  {p.regrade && <Badge tone="warn">이미 채점한 것도 다시</Badge>}
                </div>
              </div>
              <div className="mt-4 flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setPlan(null)}>
                  다시 쓰기
                </Button>
                <Button variant="primary" disabled={act.busy} onClick={() => void execute()}>
                  실행
                </Button>
              </div>
            </div>
          )}
          {p && p.kind !== "action" && (
            <div className="p-5">
              <div className="flex items-center gap-2">
                <Stethoscope className="size-4 text-accent" />
                <span className="font-medium">{p.kind === "diagnose" ? "진단" : "할 수 있는 일"}</span>
              </div>
              <p className="m-0 mt-3 leading-relaxed">{p.cause ?? p.summary}</p>
              {p.fix && <p className="m-0 mt-2 leading-relaxed text-text-2">{p.fix}</p>}
              <div className="mt-4 flex justify-end">
                <Button variant="primary" onClick={() => close(false)}>
                  닫기
                </Button>
              </div>
            </div>
          )}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
