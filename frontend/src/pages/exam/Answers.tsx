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

type InboxPdf = { path: string; name: string; pages: number | null; size: number; modified: number };
import type { TabProps } from "../Exam";
import { ManualGradeModal } from "./ManualGrade";

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

export function TranscriptModal({ examId, student, number, onClose, onSaved, onManual }: {
  examId: string;
  student: string;
  number: string;
  onClose: () => void;
  onSaved: () => void;
  onManual?: () => void;
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
          {onManual && t?.confirmed && (
            <Button onClick={onManual} title="기준 예시 만들기">
              직접 채점
            </Button>
          )}
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

function ScanBatch({ examId, questions, onDone }: { examId: string; questions: ExamQuestion[]; onDone: () => void }) {
  const inbox = useLoad<InboxPdf[]>("/scanner/inbox");
  const [path, setPath] = useState("");
  const [perStudent, setPerStudent] = useState("3");
  const [classes, setClasses] = useState("TSS1, TSS2, MSS1-J, MSS1-K");
  const [mine, setMine] = useState<string[]>([]);
  const [pages, setPages] = useState<Record<string, string>>(
    () => Object.fromEntries(questions.map((q) => [q.number, (q.answer_pages ?? []).join(",")])),
  );
  const [note, setNote] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const job = useJob(onDone);
  const pdf = (inbox.data ?? []).find((f) => f.path === path);
  const openFolders = () => {
    setErr(null);
    api
      .post<{ notes: string[] }>("/scanner/open", {})
      .then((r) => {
        setNote(r.notes.join(" "));
        inbox.reload();
      })
      .catch((e: unknown) => setErr(errorText(e)));
  };
  const savePages = () =>
    api.put(`/exams/${examId}/answer-pages`, {
      pages: Object.fromEntries(
        Object.entries(pages)
          .filter(([, v]) => v.trim())
          .map(([k, v]) => [k, v.split(",").map((x) => parseInt(x, 10)).filter((x) => !Number.isNaN(x))]),
      ),
    });
  const run = (coversOnly: boolean) =>
    void job.run(async () => {
      if (mine.length) await savePages();
      return api.post(`/exams/${examId}/scan-batch`, {
        path,
        per_student: parseInt(perStudent, 10),
        classes: classes.split(",").map((c) => c.trim()).filter(Boolean),
        questions: mine.length ? mine : null,
        covers_only: coversOnly,
      });
    });
  const result = job.job?.result as { students?: string[]; skipped?: string[]; warnings?: string[]; failed?: string[] } | undefined;
  return (
    <Card title="복합기 스캔본 — 여러 학생 답안지 한 번에">
      <Stack gap={10}>
        <Row>
          <Button onClick={openFolders}>복합기 스캔본 열기</Button>
          <span style={{ fontSize: 13, color: "var(--text-2)" }}>
            복합기 폴더와 작업공간의 '스캔본' 폴더가 함께 열립니다. PDF를 '스캔본'으로 옮긴 뒤 아래에서 고르세요.
          </span>
        </Row>
        {note && <Info>{note}</Info>}
        <Row>
          <div style={{ minWidth: 280 }}>
            <Field label="스캔본">
              <select value={path} onChange={(e) => setPath(e.target.value)}>
                <option value="">— 고르기 —</option>
                {(inbox.data ?? []).map((f) => (
                  <option key={f.path} value={f.path}>
                    {f.name} ({f.pages ?? "?"}쪽)
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <Button small variant="ghost" onClick={inbox.reload}>새로 고침</Button>
          <div style={{ width: 110 }}>
            <Field label="한 명당 쪽 수" hint="표지 포함">
              <TextInput value={perStudent} onChange={setPerStudent} />
            </Field>
          </div>
          <div style={{ width: 260 }}>
            <Field label="반 목록" hint="손글씨 반 이름을 이 중에서 고름">
              <TextInput value={classes} onChange={setClasses} />
            </Field>
          </div>
        </Row>
        {pdf?.pages && parseInt(perStudent, 10) > 0 && (
          <span style={{ fontSize: 13 }}>
            {pdf.pages % parseInt(perStudent, 10) === 0 ? `학생 ${pdf.pages / parseInt(perStudent, 10)}명` : <Badge tone="bad">쪽 수가 나누어떨어지지 않습니다</Badge>}
          </span>
        )}
        <Field label="맡은 문항 (강사별 분담)" hint="고르면 그 문항의 답안 쪽만 판독합니다 — 비우면 모든 문항. 답안 쪽은 표지가 1쪽">
          <table className={tableClass}>
            <thead>
              <tr>
                <th style={{ width: 60 }}>맡음</th>
                <th>문항</th>
                <th style={{ width: 140 }}>답안 쪽</th>
              </tr>
            </thead>
            <tbody>
              {questions.map((q) => (
                <tr key={q.number}>
                  <td>
                    <Check checked={mine.includes(q.number)} onChange={(v) => setMine(v ? [...mine, q.number] : mine.filter((x) => x !== q.number))}>
                      {" "}
                    </Check>
                  </td>
                  <td>문항 {q.number}</td>
                  <td>
                    <TextInput value={pages[q.number] ?? ""} onChange={(v) => setPages({ ...pages, [q.number]: v })} placeholder="예: 3" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Field>
        <Row>
          <Button variant="primary" disabled={!path || job.running} onClick={() => run(false)}>
            {mine.length ? `문항 ${mine.join(", ")} 판독` : "모든 답안 판독"}
          </Button>
          <Button disabled={!path || job.running} onClick={() => run(true)}>
            표지만 먼저 읽기
          </Button>
          <span style={{ fontSize: 12.5, color: "var(--text-3)" }}>표지(이름·반·학교)는 한 번만 읽고, 다른 강사는 저장된 것을 씁니다.</span>
        </Row>
        <JobProgress job={job.job} />
        <ErrorBox error={err ?? job.error} />
        {result && (
          <Stack gap={4}>
            <span>판독 {result.students?.length ?? 0}명 · 건너뜀 {result.skipped?.length ?? 0}명</span>
            {[...(result.warnings ?? []), ...(result.failed ?? [])].map((w, i) => (
              <Notice key={i}>{w}</Notice>
            ))}
          </Stack>
        )}
      </Stack>
    </Card>
  );
}

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
  const [manual, setManual] = useState<{ student: string; number: string } | null>(null);
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
      <ScanBatch examId={id} questions={detail.exam.questions} onDone={refresh} />
      <AddAnswers examId={id} questions={detail.exam.questions} onDone={refresh} />
      {open && (
        <TranscriptModal
          examId={id}
          student={open.student}
          number={open.number}
          onClose={() => setOpen(null)}
          onSaved={refresh}
          onManual={() => {
            setManual(open);
            setOpen(null);
          }}
        />
      )}
      {manual && (
        <ManualGradeModal examId={id} student={manual.student} number={manual.number} onClose={() => setManual(null)} onSaved={refresh} />
      )}
    </Stack>
  );
}
