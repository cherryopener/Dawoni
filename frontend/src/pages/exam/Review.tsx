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
  NumberInput,
  Row,
  Stack,
  TextInput,
  tableClass,
  teacherName,
} from "../../components/ui";
import { api, errorText, useJob, useLoad } from "../../lib/api";
import type { ExamQuestion, Grade, ReviewRow, Rubric, Transcript } from "../../lib/types";
import type { TabProps } from "../Exam";

const STATUS: Record<string, { label: string; tone: "bad" | "accent" | "ok" }> = {
  needs_review: { label: "검토 필요", tone: "bad" },
  auto: { label: "자동 채점", tone: "accent" },
  teacher_confirmed: { label: "강사 확정", tone: "ok" },
};

type GDetail = { grade: Grade; transcript: Transcript | null; rubric: Rubric | null; question: ExamQuestion };

// 인용 비교용: 공백·$·\(\) 제거
const norm = (s: string) => s.replace(/\\[()[\]]|\$|\s+/g, "");

function quoteHits(t: Transcript, quotes: string[]): Set<number> {
  const hits = new Set<number>();
  const frags = quotes.flatMap((q) => q.split("…")).map(norm).filter((x) => x.length >= 2);
  t.lines.forEach((l, i) => {
    const line = norm(`${l.text}${l.math ?? ""}`);
    if (frags.some((f) => line.includes(f) || f.includes(line))) hits.add(i);
  });
  return hits;
}

function GradeModal({ examId, row, onClose, onSaved }: {
  examId: string;
  row: ReviewRow;
  onClose: () => void;
  onSaved: () => void;
}) {
  const path = `/exams/${examId}/students/${encodeURIComponent(row.student)}/grades/${encodeURIComponent(row.question)}`;
  const { data, error } = useLoad<GDetail>(path);
  const [awarded, setAwarded] = useState<Record<string, number>>({});
  const [note, setNote] = useState("");
  const [itemNotes, setItemNotes] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!data) return;
    setAwarded(Object.fromEntries(data.grade.judgements.map((j) => [j.rubric_item_id, j.awarded])));
    setNote(data.grade.teacher_note ?? "");
    setItemNotes(data.grade.teacher_item_notes ?? {});
  }, [data]);
  if (!data) return <Modal title="채점 결과" onClose={onClose}><ErrorBox error={error} /></Modal>;
  const { grade: g, transcript: t, rubric: r, question: q } = data;
  const item = (id: string) => r?.items.find((x) => x.id === id);
  const second = Object.fromEntries((g.second_opinion ?? []).map((j) => [j.rubric_item_id, j]));
  const ai = Object.fromEntries((g.ai_judgements ?? g.judgements).map((j) => [j.rubric_item_id, j.awarded]));
  const total = Object.values(awarded).reduce((a, b) => a + (b || 0), 0);
  const hits = t ? quoteHits(t, g.judgements.map((j) => j.evidence_quote ?? "").filter(Boolean)) : new Set<number>();
  const save = () => {
    setErr(null);
    api
      .put(path, { awarded, item_notes: itemNotes, note: note || null, by: teacherName() || null })
      .then(() => {
        onSaved();
        onClose();
      })
      .catch((e: unknown) => setErr(errorText(e)));
  };
  return (
    <Modal
      wide
      title={`${row.student} · 문항 ${row.question}`}
      onClose={onClose}
      actions={
        <Row>
          <Badge tone="accent">
            {total} / {g.max_points}점
          </Badge>
          <Button variant="primary" onClick={save}>
            이 점수로 확정
          </Button>
        </Row>
      }
    >
      <ErrorBox error={err} />
      <Row>
        <Badge tone={STATUS[g.status]!.tone}>{STATUS[g.status]!.label}</Badge>
        {r && g.rubric_version !== r.version && <Badge tone="warn">루브릭 v{g.rubric_version}로 채점 (지금 v{r.version})</Badge>}
        {g.confirmed_by && <Badge tone="ok">확정: {g.confirmed_by}</Badge>}
        {g.agent === null && <Badge tone="accent">강사 직접 채점</Badge>}
        {(g.examples_used ?? []).length > 0 && <Badge title="채점에 넣은 강사 기준 예시">기준 예시: {(g.examples_used ?? []).join(", ")}</Badge>}
      </Row>
      {g.flags.filter((f) => !f.startsWith("이중 채점 불일치")).map((f, i) => (
        <Notice key={i}>
          <Loose text={f} />
        </Notice>
      ))}
      {g.disagreements.length > 0 && (
        <Notice>이중 채점에서 점수가 다른 항목: {g.disagreements.join(", ")} — 두 판정을 비교해 정하세요.</Notice>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 2fr) minmax(0, 3fr)", gap: 16, alignItems: "start" }}>
        <Stack gap={8}>
          <details>
            <summary style={{ cursor: "pointer", color: "var(--text-2)" }}>문항 보기</summary>
            <ItemView number={q.number} points={q.points} item={q.item} examId={examId} />
          </details>
          <b>학생 답안 (판독문)</b>
          {t ? (
            <ol style={{ margin: 0, paddingLeft: 22, display: "flex", flexDirection: "column", gap: 4 }}>
              {t.lines.map((l, i) => (
                <li
                  key={i}
                  style={{
                    background: hits.has(i) ? "#fff6c9" : undefined,
                    textDecoration: l.struck ? "line-through" : undefined,
                    color: l.struck ? "var(--text-3)" : undefined,
                    borderRadius: 4,
                    padding: "1px 4px",
                  }}
                >
                  <Loose text={l.text} /> {l.math && <Tex latex={l.math} />}
                </li>
              ))}
            </ol>
          ) : (
            <Empty>판독문 없음</Empty>
          )}
          <span style={{ fontSize: 12, color: "var(--text-3)" }}>노란 줄 = 점수 근거로 인용된 부분</span>
        </Stack>
        <Stack gap={8}>
          <table className={tableClass}>
            <thead>
              <tr>
                <th style={{ width: 120 }}>항목 · 점수</th>
                <th>판정</th>
              </tr>
            </thead>
            <tbody>
              {g.judgements.map((j) => {
                const it = item(j.rubric_item_id);
                const so = second[j.rubric_item_id];
                const changed = awarded[j.rubric_item_id] !== ai[j.rubric_item_id];
                return (
                  <tr key={j.rubric_item_id} style={{ background: g.disagreements.includes(j.rubric_item_id) ? "var(--warn-soft)" : undefined }}>
                    <td>
                      <b>{j.rubric_item_id}</b>
                      <Row style={{ marginTop: 4 }}>
                        <NumberInput
                          value={awarded[j.rubric_item_id] ?? 0}
                          min={0}
                          max={it?.points}
                          onChange={(v) => setAwarded({ ...awarded, [j.rubric_item_id]: v })}
                        />
                        <span>/ {it?.points ?? "?"}</span>
                      </Row>
                      {changed && <div style={{ fontSize: 12, color: "var(--warn)" }}>AI: {ai[j.rubric_item_id]}점</div>}
                      {changed && (
                        <div style={{ marginTop: 4, width: 220 }}>
                          <TextInput
                            value={itemNotes[j.rubric_item_id] ?? ""}
                            onChange={(v) => setItemNotes({ ...itemNotes, [j.rubric_item_id]: v })}
                            placeholder="고친 이유 (예시로 쓰임)"
                          />
                        </div>
                      )}
                      {so && so.awarded !== j.awarded && <div style={{ fontSize: 12, color: "var(--warn)" }}>재채점: {so.awarded}점</div>}
                      <div style={{ fontSize: 12, color: j.confidence < 0.6 ? "var(--bad)" : "var(--text-3)" }}>
                        확신 {Math.round(j.confidence * 100)}%
                      </div>
                    </td>
                    <td>
                      {it && (
                        <div style={{ fontSize: 12.5, color: "var(--text-2)", marginBottom: 4 }}>
                          기준: <Loose text={it.criterion} />
                        </div>
                      )}
                      {j.evidence_quote && (
                        <div style={{ marginBottom: 4 }}>
                          근거: “<Loose text={j.evidence_quote} />”
                        </div>
                      )}
                      <div>
                        <Loose text={j.reason} />
                      </div>
                      {j.deduction_reason && (
                        <div style={{ color: "var(--bad)", marginTop: 4 }}>
                          감점: <Loose text={j.deduction_reason} />
                        </div>
                      )}
                      {so && so.reason !== j.reason && (
                        <details style={{ marginTop: 4 }}>
                          <summary style={{ cursor: "pointer", fontSize: 12.5, color: "var(--text-2)" }}>재채점 판정</summary>
                          <Loose text={so.reason} />
                        </details>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <Field label="첨삭 (AI)">
            <div>
              <Loose text={g.comment} />
            </div>
          </Field>
          <Field label="강사 메모 (선택)" hint="고친 점수·이유는 이 문항의 기준 예시가 되어 다음 채점에 들어갑니다">
            <TextInput value={note} onChange={setNote} />
          </Field>
        </Stack>
      </div>
    </Modal>
  );
}

export function ReviewTab({ detail, reload }: TabProps) {
  const id = detail.exam.id;
  const { data, error, reload: reloadRows } = useLoad<ReviewRow[]>(`/exams/${id}/review`);
  const [double, setDouble] = useState(false);
  const [regrade, setRegrade] = useState(false);
  const [useExamples, setUseExamples] = useState(true);
  const [open, setOpen] = useState<ReviewRow | null>(null);
  const [report, setReport] = useState<string | null>(null);
  const refresh = () => {
    reloadRows();
    reload();
  };
  const grade = useJob((j) => {
    const r = j.result as { graded: number; with_examples: number; needs_review: number; skipped: { student: string; question: string; reason: string }[]; failed: { student: string; question: string; reason: string }[] };
    const pending = r.skipped.filter((x) => x.reason.startsWith("판독문")).length;
    setReport(
      `채점 ${r.graded}건 (검토 필요 ${r.needs_review}, 기준 예시 사용 ${r.with_examples})` +
        (pending ? ` · 판독문 확인 대기로 건너뜀 ${pending}` : "") +
        (r.failed.length ? ` · 실패 ${r.failed.length}건 — 다시 누르면 실패한 답안만 채점` : ""),
    );
    refresh();
  });
  const unapproved = detail.questions.filter((q) => q.rubric?.status !== "approved").map((q) => q.number);
  return (
    <Stack>
      <Card>
        <Stack>
          {unapproved.length > 0 && <Notice>루브릭이 승인되지 않은 문항: {unapproved.join(", ")} — “2. 해설 · 루브릭”에서 승인하세요.</Notice>}
          {detail.summary.pending_transcripts > 0 && (
            <Notice>확인 대기 판독문 {detail.summary.pending_transcripts}개는 채점하지 않습니다.</Notice>
          )}
          <Row>
            <Button
              variant="primary"
              disabled={grade.running || unapproved.length > 0}
              onClick={() => {
                setReport(null);
                void grade.run(() => api.post(`/exams/${id}/grade`, { double, regrade, use_examples: useExamples }));
              }}
            >
              채점 시작
            </Button>
            <Check checked={double} onChange={setDouble}>
              이중 채점 (새 세션으로 한 번 더 — 다르면 표시)
            </Check>
            <Check checked={regrade} onChange={setRegrade}>
              이미 채점한 답안도 다시
            </Check>
            <Check checked={useExamples} onChange={setUseExamples}>
              강사 기준 예시 넣기 (“5. 보정”)
            </Check>
          </Row>
          <JobProgress job={grade.job} />
          <ErrorBox error={grade.error ?? error} />
          {report && <Info>{report}</Info>}
        </Stack>
      </Card>
      <Card title="검토 목록 (검토 필요 · 불일치 · 낮은 확신 순)">
        {data && data.length === 0 && <Empty>아직 채점 결과가 없습니다.</Empty>}
        {data && data.length > 0 && (
          <table className={tableClass}>
            <thead>
              <tr>
                <th>상태</th>
                <th>학생</th>
                <th>문항</th>
                <th>점수</th>
                <th>확인할 점</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.map((r) => (
                <tr key={`${r.student}/${r.question}`}>
                  <td>
                    <Badge tone={STATUS[r.status]!.tone}>{STATUS[r.status]!.label}</Badge>
                    {r.stale && <Badge tone="warn">루브릭 바뀜</Badge>}
                  </td>
                  <td>{r.student}</td>
                  <td>{r.question}</td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    <b>{r.total}</b> / {r.max}
                  </td>
                  <td style={{ fontSize: 13 }}>
                    {r.disagreements.length > 0 && <div>이중 채점 차이: {r.disagreements.join(", ")}</div>}
                    {r.flags.filter((f) => !f.startsWith("이중 채점 불일치")).slice(0, 2).map((f, i) => (
                      <div key={i}>
                        <Loose text={f} />
                      </div>
                    ))}
                    {r.min_confidence < 0.6 && <div>낮은 확신 {Math.round(r.min_confidence * 100)}%</div>}
                  </td>
                  <td>
                    <Button small onClick={() => setOpen(r)}>
                      {r.status === "teacher_confirmed" ? "보기" : "검토"}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {open && <GradeModal examId={id} row={open} onClose={() => setOpen(null)} onSaved={refresh} />}
    </Stack>
  );
}
