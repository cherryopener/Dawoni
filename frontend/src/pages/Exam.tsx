import { Badge, Loading, Button, ErrorBox, Row, Stack, Tabs } from "../components/ui";
import { useLoad } from "../lib/api";
import { go } from "../lib/router";
import type { Exam, ExamSummary, QuestionState, Scope } from "../lib/types";
import { SummaryBadges } from "./Exams";
import { AnswersTab } from "./exam/Answers";
import { CalibrationTab } from "./exam/Calibration";
import { ExportTab } from "./exam/Export";
import { GenerateTab } from "./exam/Generate";
import { QuestionsTab } from "./exam/Questions";
import { ReviewTab } from "./exam/Review";
import { RubricsTab } from "./exam/Rubrics";

export type ExamDetail = {
  exam: Exam;
  scope: { id: string; title: string; level: string };
  questions: QuestionState[];
  summary: ExamSummary;
};

type Tab = "questions" | "rubrics" | "answers" | "review" | "calibration" | "generate" | "export";

export function ExamPage({ id, tab }: { id: string; tab?: string }) {
  const { data, error, reload } = useLoad<ExamDetail>(`/exams/${id}`);
  const scope = useLoad<Scope>(data ? `/scopes/${data.exam.scope_id}` : null);
  const current = (tab ?? "questions") as Tab;
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const s = data.summary;
  const tabs: { value: Tab; label: React.ReactNode }[] = [
    { value: "questions", label: <>1. 문항 <Badge>{s.questions}</Badge></> },
    {
      value: "rubrics",
      label: (
        <>
          2. 해설 · 루브릭 <Badge tone={s.approved === s.questions ? "ok" : "warn"}>{s.approved}/{s.questions} 승인</Badge>
        </>
      ),
    },
    {
      value: "answers",
      label: (
        <>
          3. 답안 <Badge tone={s.pending_transcripts ? "warn" : "neutral"}>{s.pending_transcripts ? `확인 대기 ${s.pending_transcripts}` : `학생 ${s.students}`}</Badge>
        </>
      ),
    },
    {
      value: "review",
      label: (
        <>
          4. 채점 · 검토 {s.needs_review > 0 ? <Badge tone="bad">검토 {s.needs_review}</Badge> : <Badge>{s.graded}</Badge>}
        </>
      ),
    },
    { value: "calibration", label: <>5. 보정</> },
    { value: "generate", label: <>6. 문항 제작</> },
    { value: "export", label: <>7. 출력</> },
  ];
  const props = { detail: data, scope: scope.data, reload };
  return (
    <Stack gap={14}>
      <Row>
        <Button variant="ghost" onClick={() => go("exams")}>
          ← 시험
        </Button>
        <h2>{data.exam.title}</h2>
        <Badge>{data.scope.title}</Badge>
        <Badge>{data.scope.level}</Badge>
      </Row>
      <SummaryBadges s={s} />
      <Tabs value={current} onChange={(t) => go("exams", id, t)} tabs={tabs} />
      {current === "questions" && <QuestionsTab {...props} />}
      {current === "rubrics" && <RubricsTab {...props} />}
      {current === "answers" && <AnswersTab {...props} />}
      {current === "review" && <ReviewTab {...props} />}
      {current === "calibration" && <CalibrationTab {...props} />}
      {current === "generate" && <GenerateTab {...props} />}
      {current === "export" && <ExportTab {...props} />}
    </Stack>
  );
}

export type TabProps = { detail: ExamDetail; scope: Scope | null; reload: () => void };
