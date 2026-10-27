import { useState } from "react";
import {
  Badge,
  Loading,
  Button,
  Card,
  Check,
  Empty,
  ErrorBox,
  Field,
  JobProgress,
  Notice,
  Row,
  Select,
  Stack,
  TextArea,
  TextInput,
} from "../components/ui";
import { api, errorText, useJob, useLoad } from "../lib/api";
import { go } from "../lib/router";
import type { Exam, ExamSummary, ScopeSummary } from "../lib/types";

type ExamRow = { id: string; title: string; scope_id: string; created_at: string; summary: ExamSummary };

export function SummaryBadges({ s }: { s: ExamSummary }) {
  return (
    <Row>
      <Badge tone={s.solved === s.questions ? "ok" : "neutral"}>해설 {s.solved}/{s.questions}</Badge>
      <Badge tone={s.approved === s.questions ? "ok" : "warn"}>루브릭 승인 {s.approved}/{s.questions}</Badge>
      <Badge>학생 {s.students}</Badge>
      {s.pending_transcripts > 0 && <Badge tone="warn">판독 확인 대기 {s.pending_transcripts}</Badge>}
      <Badge tone={s.graded ? "accent" : "neutral"}>채점 {s.graded}</Badge>
      {s.needs_review > 0 && <Badge tone="bad">검토 필요 {s.needs_review}</Badge>}
    </Row>
  );
}

function NewExam({ scopes, onCreated }: { scopes: ScopeSummary[]; onCreated: (id: string) => void }) {
  const [mode, setMode] = useState<"pdf" | "json">("pdf");
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [scopeId, setScopeId] = useState(scopes[0]?.id ?? "");
  const [force, setForce] = useState(false);
  const [json, setJson] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const job = useJob((j) => onCreated((j.result as { exam_id: string }).exam_id));
  if (!scopes.length) {
    return <Notice>시험을 만들려면 먼저 범위 프로필이 있어야 합니다 (왼쪽 “범위 프로필”).</Notice>;
  }
  const fromPdf = () => {
    const f = new FormData();
    f.append("file", file!);
    f.append("title", title);
    f.append("scope_id", scopeId);
    f.append("force", String(force));
    void job.run(() => api.upload("/exams/from-pdf", f));
  };
  const fromJson = () => {
    setErr(null);
    let questions: unknown;
    try {
      questions = JSON.parse(json);
    } catch {
      setErr("JSON 형식이 아닙니다.");
      return;
    }
    api
      .post<Exam>("/exams", { title, scope_id: scopeId, questions })
      .then((e) => onCreated(e.id))
      .catch((e: unknown) => setErr(errorText(e)));
  };
  return (
    <Card
      title="새 시험"
      actions={
        <Row>
          <Button small variant={mode === "pdf" ? "primary" : "default"} onClick={() => setMode("pdf")}>
            시험지 PDF
          </Button>
          <Button small variant={mode === "json" ? "primary" : "default"} onClick={() => setMode("json")}>
            문항 JSON
          </Button>
        </Row>
      }
    >
      <Stack>
        <Row>
          <div style={{ flex: 2, minWidth: 220 }}>
            <Field label="시험 이름">
              <TextInput value={title} onChange={setTitle} placeholder="2학기 중간고사 수학" />
            </Field>
          </div>
          <div style={{ flex: 1, minWidth: 220 }}>
            <Field label="범위 프로필">
              <Select value={scopeId} onChange={setScopeId} options={scopes.map((s) => ({ value: s.id, label: `${s.title} (${s.level})` }))} />
            </Field>
          </div>
        </Row>
        {mode === "pdf" ? (
          <>
            <span style={{ color: "var(--text-2)" }}>
              디지털 PDF(글자가 선택되는 PDF)만 지원합니다. 문항 영역·그림을 코드가 나누고, AI가 문장을 구조화합니다.
            </span>
            <Row>
              <input type="file" accept="application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              <Check checked={force} onChange={setForce}>
                이미 DB화한 파일이어도 다시
              </Check>
            </Row>
            <Row>
              <Button variant="primary" onClick={fromPdf} disabled={!file || !title || job.running}>
                시험지 DB화
              </Button>
            </Row>
            <JobProgress job={job.job} />
          </>
        ) : (
          <>
            <Field label="문항 JSON" hint='[{"number": "1", "points": 4, "item": {...}}] — `dawoni schema ItemContent` 형식'>
              <TextArea value={json} onChange={setJson} rows={6} mono />
            </Field>
            <Row>
              <Button variant="primary" onClick={fromJson} disabled={!json.trim() || !title}>
                시험 등록
              </Button>
            </Row>
          </>
        )}
        <ErrorBox error={job.error ?? err} />
      </Stack>
    </Card>
  );
}

export function ExamsPage() {
  const exams = useLoad<ExamRow[]>("/exams");
  const scopes = useLoad<ScopeSummary[]>("/scopes");
  return (
    <Stack gap={16}>
      <h2>시험 · 채점</h2>
      <ErrorBox error={exams.error ?? scopes.error} />
      {!exams.data && !exams.error && <Loading />}
      {exams.data && exams.data.length === 0 && <Empty>아직 시험이 없습니다.</Empty>}
      {exams.data?.map((e) => (
        <Card
          key={e.id}
          title={e.title}
          actions={
            <Button small variant="primary" onClick={() => go("exams", e.id)}>
              열기
            </Button>
          }
        >
          <SummaryBadges s={e.summary} />
        </Card>
      ))}
      {scopes.data && <NewExam scopes={scopes.data} onCreated={(id) => go("exams", id)} />}
    </Stack>
  );
}
