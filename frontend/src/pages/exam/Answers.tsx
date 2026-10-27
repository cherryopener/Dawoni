import { useEffect, useState } from "react";
import { ItemView } from "../../components/Item";
import { Loose, Tex } from "../../components/Math";
import {
  Badge,
  Button,
  Card,
  Check,
  Empty,
  ErrorBox,
  Field,
  Info,
  JobProgress,
  Modal,
  Notice,
  Row,
  Stack,
  TextArea,
  TextInput,
  tableClass,
  teacherName,
} from "../../components/ui";
import { api, errorText, examFileUrl, useJob, useLoad } from "../../lib/api";
import type { Cell, ExamQuestion, Transcript, TranscriptLine } from "../../lib/types";
import type { TabProps } from "../Exam";

type StudentRow = { student: string; cells: Record<string, Cell> };

export function CellBadge({ cell }: { cell: Cell | undefined }) {
  const t = cell?.transcript;
  const g = cell?.grade;
  if (!t) return <Badge>미입력</Badge>;
  if (!t.confirmed)
    return <Badge tone="warn">{t.attention ? "⚑ 확인 대기 (주의)" : "확인 대기"}</Badge>;
  if (!g) return <Badge tone="ok">확인됨</Badge>;
  const tone = g.status === "needs_review" ? "bad" : g.status === "teacher_confirmed" ? "ok" : "accent";
  return (
    <Badge tone={tone}>
      {g.total}/{g.max}
      {g.status === "needs_review" ? " 검토" : g.status === "teacher_confirmed" ? " 확정" : ""}
      {g.stale ? " (루브릭 바뀜)" : ""}
    </Badge>
  );
}

// ---------------------------------------------------------------- 판독문 확인

type TDetail = {
  transcript: Transcript;
  page_images: string[];
  needs_attention: boolean;
  question: ExamQuestion;
};

function LineEditor({ line, onChange, onRemove }: {
  line: TranscriptLine;
  onChange: (l: TranscriptLine) => void;
  onRemove: () => void;
}) {
  const low = line.confidence < 0.7 || line.uncertain.length > 0;
  return (
    <div
      style={{
        border: `1px solid ${low ? "#f0c27a" : "var(--line)"}`,
        background: low ? "var(--warn-soft)" : "var(--panel)",
        borderRadius: 6,
        padding: 8,
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <Row>
        <div style={{ flex: 1 }}>
          <TextInput value={line.text} onChange={(v) => onChange({ ...line, text: v })} placeholder="글 (섞인 식은 \( \)로)" />
        </div>
        <Check checked={line.struck} onChange={(v) => onChange({ ...line, struck: v })}>
          지운 줄
        </Check>
        <Button small variant="ghost" onClick={onRemove}>
          ✕
        </Button>
      </Row>
      <Row>
        <div style={{ flex: 1 }}>
          <TextInput value={line.math ?? ""} onChange={(v) => onChange({ ...line, math: v || null })} placeholder="줄 전체가 식이면 LaTeX" mono />
        </div>
      </Row>
      <div style={{ textDecoration: line.struck ? "line-through" : undefined, color: line.struck ? "var(--text-3)" : undefined }}>
        <Loose text={line.text} /> {line.math && <Tex latex={line.math} />}
      </div>
      {line.uncertain.length > 0 && (
        <Row>
          <span style={{ fontSize: 12.5, color: "var(--warn)" }}>불확실: {line.uncertain.join(" / ")}</span>
          <Button small onClick={() => onChange({ ...line, uncertain: [], confidence: 1 })}>
            확인함
          </Button>
        </Row>
      )}
    </div>
  );
}

export function TranscriptModal({ examId, student, number, onClose, onSaved }: {
  examId: string;
  student: string;
  number: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const path = `/exams/${examId}/students/${encodeURIComponent(student)}/transcripts/${encodeURIComponent(number)}`;
  const { data, error } = useLoad<TDetail>(path);
  const [lines, setLines] = useState<TranscriptLine[]>([]);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (data) setLines(data.transcript.lines);
  }, [data]);
  const save = (confirm: boolean) => {
    setErr(null);
    api
      .put(path, { lines, confirm, by: teacherName() || null })
      .then(() => {
        onSaved();
        onClose();
      })
      .catch((e: unknown) => setErr(errorText(e)));
  };
  const t = data?.transcript;
  const unresolved = lines.filter((l) => l.uncertain.length > 0).length;
  return (
    <Modal
      wide
      title={`${student} · 문항 ${number} 판독문`}
      onClose={onClose}
      actions={
        <Row>
          <Button onClick={() => save(false)}>저장 (확인 전)</Button>
          <Button variant="primary" onClick={() => save(true)}>
            확인 완료 — 채점 가능
          </Button>
        </Row>
      }
    >
      <ErrorBox error={error ?? err} />
      {t && (
        <Row>
          {t.source === "ocr" ? <Badge>손글씨 판독</Badge> : <Badge>직접 입력</Badge>}
          {t.cross_checked && t.agreement != null && (
            <Badge tone={t.agreement >= 0.9 ? "ok" : "warn"}>교차 판독 일치 {Math.round(t.agreement * 100)}%</Badge>
          )}
          {t.confirmed ? <Badge tone="ok">확인됨 {t.confirmed_by ?? ""}</Badge> : <Badge tone="warn">확인 전</Badge>}
          {unresolved > 0 && <Badge tone="warn">불확실 {unresolved}줄</Badge>}
        </Row>
      )}
      {t?.notes.map((n, i) => (
        <Notice key={i}>{n}</Notice>
      ))}
      {data && (
        <div style={{ display: "grid", gridTemplateColumns: data.page_images.length ? "1fr 1fr" : "1fr", gap: 16, alignItems: "start" }}>
          {data.page_images.length > 0 && (
            <div style={{ position: "sticky", top: 0, maxHeight: "75vh", overflow: "auto", border: "1px solid var(--line)", borderRadius: 6 }}>
              {data.page_images.map((p) => (
                <a key={p} href={examFileUrl(examId, p)} target="_blank" rel="noreferrer">
                  <img src={examFileUrl(examId, p)} alt="답안지 원본" style={{ width: "100%", display: "block" }} />
                </a>
              ))}
            </div>
          )}
          <Stack gap={8}>
            <details>
              <summary style={{ cursor: "pointer", color: "var(--text-2)" }}>문항 보기</summary>
              <ItemView number={data.question.number} points={data.question.points} item={data.question.item} examId={examId} />
            </details>
            <Info>원본과 한 줄씩 비교하세요. 판독문은 학생이 쓴 그대로여야 합니다 (틀린 계산도 고치지 않음).</Info>
            {lines.map((l, i) => (
              <LineEditor
                key={i}
                line={l}
                onChange={(nl) => setLines(lines.map((x, k) => (k === i ? nl : x)))}
                onRemove={() => setLines(lines.filter((_, k) => k !== i))}
              />
            ))}
            <Row>
              <Button small onClick={() => setLines([...lines, { text: "", math: null, confidence: 1, uncertain: [], struck: false }])}>
                + 줄 추가
              </Button>
            </Row>
          </Stack>
        </div>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------- 답안 추가

function AddAnswers({ examId, questions, onDone }: { examId: string; questions: ExamQuestion[]; onDone: () => void }) {
  const [student, setStudent] = useState("");
  const [mode, setMode] = useState<"scan" | "typed">("scan");
  const [files, setFiles] = useState<FileList | null>(null);
  const [cross, setCross] = useState(true);
  const [texts, setTexts] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const scan = useJob((j) => {
    const r = j.result as { saved: string[]; kept_confirmed: string[]; unassigned: string[] };
    setMsg(`판독 완료: 문항 ${r.saved.join(", ") || "없음"}. 표에서 “확인 대기”를 눌러 원본과 비교하세요.` +
      (r.unassigned.length ? ` 문항을 알 수 없는 줄 ${r.unassigned.length}개.` : ""));
    onDone();
  });
  const st = student.trim();
  const startScan = () => {
    const f = new FormData();
    Array.from(files ?? []).forEach((x) => f.append("files", x));
    f.append("cross", String(cross));
    setMsg(null);
    void scan.run(() => api.upload(`/exams/${examId}/students/${encodeURIComponent(st)}/scan`, f));
  };
  const saveTyped = async () => {
    setErr(null);
    try {
      for (const q of questions) {
        const text = texts[q.number]?.trim();
        if (text) {
          await api.post(`/exams/${examId}/students/${encodeURIComponent(st)}/answers`, {
            question: q.number,
            text,
            by: teacherName() || null,
          });
        }
      }
      setTexts({});
      setMsg(`${st} 답안을 저장했습니다.`);
      onDone();
    } catch (e) {
      setErr(errorText(e));
    }
  };
  return (
    <Card
      title="학생 답안 추가"
      actions={
        <Row>
          <Button small variant={mode === "scan" ? "primary" : "default"} onClick={() => setMode("scan")}>
            답안지 사진·PDF
          </Button>
          <Button small variant={mode === "typed" ? "primary" : "default"} onClick={() => setMode("typed")}>
            직접 입력
          </Button>
        </Row>
      }
    >
      <Stack>
        <div style={{ width: 240 }}>
          <Field label="학생 (이름 또는 번호)">
            <TextInput value={student} onChange={setStudent} />
          </Field>
        </div>
        {mode === "scan" ? (
          <>
            <Row>
              <input type="file" multiple accept="image/*,.heic,.pdf" onChange={(e) => setFiles(e.target.files)} />
              <Check checked={cross} onChange={setCross}>
                교차 판독
              </Check>
            </Row>
            <Row>
              <Button variant="primary" onClick={startScan} disabled={!st || !files?.length || scan.running}>
                판독 시작
              </Button>
            </Row>
            <JobProgress job={scan.job} />
          </>
        ) : (
          <>
            <span style={{ fontSize: 12.5, color: "var(--text-2)" }}>
              한 줄 = 답안 한 줄. <code>식:</code>으로 시작하면 수식(LaTeX) 줄, <code>~~</code>로 시작하면 지운 줄. 직접 입력한 답안은 확인된 판독문으로 저장됩니다.
            </span>
            {questions.map((q) => (
              <Field key={q.number} label={`문항 ${q.number}`}>
                <TextArea value={texts[q.number] ?? ""} onChange={(v) => setTexts({ ...texts, [q.number]: v })} rows={3} />
              </Field>
            ))}
            <Row>
              <Button variant="primary" onClick={() => void saveTyped()} disabled={!st || !Object.values(texts).some((t) => t.trim())}>
                답안 저장
              </Button>
            </Row>
          </>
        )}
        <ErrorBox error={scan.error ?? err} />
        {msg && <Info>{msg}</Info>}
      </Stack>
    </Card>
  );
}

export function AnswersTab({ detail, reload }: TabProps) {
  const id = detail.exam.id;
  const { data, error, reload: reloadRows } = useLoad<StudentRow[]>(`/exams/${id}/students`);
  const [open, setOpen] = useState<{ student: string; number: string } | null>(null);
  const refresh = () => {
    reloadRows();
    reload();
  };
  return (
    <Stack>
      <ErrorBox error={error} />
      <Card title="학생별 답안">
        {data && data.length === 0 && <Empty>아직 답안이 없습니다. 아래에서 추가하세요.</Empty>}
        {data && data.length > 0 && (
          <table className={tableClass}>
            <thead>
              <tr>
                <th>학생</th>
                {detail.exam.questions.map((q) => (
                  <th key={q.number}>문항 {q.number}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.student}>
                  <td>
                    <b>{r.student}</b>
                  </td>
                  {detail.exam.questions.map((q) => {
                    const cell = r.cells[q.number];
                    return (
                      <td key={q.number}>
                        {cell?.transcript ? (
                          <button
                            style={{ border: "none", background: "none", padding: 0, cursor: "pointer" }}
                            onClick={() => setOpen({ student: r.student, number: q.number })}
                          >
                            <CellBadge cell={cell} />
                          </button>
                        ) : (
                          <CellBadge cell={cell} />
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {detail.summary.pending_transcripts > 0 && (
          <div style={{ marginTop: 10 }}>
            <Notice>확인 대기 판독문 {detail.summary.pending_transcripts}개 — 확인한 판독문만 채점합니다.</Notice>
          </div>
        )}
      </Card>
      <AddAnswers examId={id} questions={detail.exam.questions} onDone={refresh} />
      {open && (
        <TranscriptModal examId={id} student={open.student} number={open.number} onClose={() => setOpen(null)} onSaved={refresh} />
      )}
    </Stack>
  );
}
