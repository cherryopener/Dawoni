import { useEffect, useState } from "react";
import { Loose, Tex } from "../../components/Math";
import {
  Badge,
  Button,
  ErrorBox,
  Field,
  Info,
  Modal,
  NumberInput,
  Row,
  Stack,
  TextInput,
  tableClass,
  teacherName,
} from "../../components/ui";
import { api, errorText, useLoad } from "../../lib/api";
import type { ExamQuestion, Rubric, Transcript } from "../../lib/types";

type QDetail = { question: ExamQuestion; rubric: Rubric | null };
type TDetail = { transcript: Transcript };

/** 강사가 AI 없이 직접 채점 — 저장하면 이 문항의 기준 예시가 된다. */
export function ManualGradeModal({ examId, student, number, onClose, onSaved }: {
  examId: string;
  student: string;
  number: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const q = useLoad<QDetail>(`/exams/${examId}/questions/${encodeURIComponent(number)}`);
  const t = useLoad<TDetail>(`/exams/${examId}/students/${encodeURIComponent(student)}/transcripts/${encodeURIComponent(number)}`);
  const rubric = q.data?.rubric ?? null;
  const [awarded, setAwarded] = useState<Record<string, number>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [comment, setComment] = useState("");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (rubric) setAwarded(Object.fromEntries(rubric.items.map((i) => [i.id, NaN])));
  }, [rubric]);
  const missing = rubric?.items.filter((i) => !Number.isFinite(awarded[i.id])) ?? [];
  const total = Object.values(awarded).reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0);
  const save = () => {
    setErr(null);
    api
      .post(`/exams/${examId}/students/${encodeURIComponent(student)}/grades/${encodeURIComponent(number)}/manual`, {
        awarded,
        item_notes: notes,
        comment,
        by: teacherName() || null,
      })
      .then(() => {
        onSaved();
        onClose();
      })
      .catch((e: unknown) => setErr(errorText(e)));
  };
  return (
    <Modal
      wide
      title={`${student} · 문항 ${number} 직접 채점`}
      onClose={onClose}
      actions={
        <Row>
          {rubric && <Badge tone="accent">{total} / {rubric.items.reduce((a, i) => a + i.points, 0)}점</Badge>}
          <Button variant="primary" onClick={save} disabled={!rubric || missing.length > 0}>
            채점 저장 (기준 예시)
          </Button>
        </Row>
      }
    >
      <ErrorBox error={q.error ?? t.error ?? err} />
      <Info>강사가 직접 정한 점수는 이 문항을 AI가 채점할 때 “기준 예시”로 들어갑니다. 2~3개면 충분합니다.</Info>
      {!rubric && q.data && <ErrorBox error="루브릭이 없습니다. 먼저 루브릭을 만드세요." />}
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 2fr) minmax(0, 3fr)", gap: 16, alignItems: "start" }}>
        <Stack gap={6}>
          <b>학생 답안 (판독문)</b>
          <ol style={{ margin: 0, paddingLeft: 22 }}>
            {t.data?.transcript.lines.map((l, i) => (
              <li key={i} style={{ textDecoration: l.struck ? "line-through" : undefined, color: l.struck ? "var(--text-3)" : undefined }}>
                <Loose text={l.text} /> {l.math && <Tex latex={l.math} />}
              </li>
            ))}
          </ol>
        </Stack>
        {rubric && (
          <Stack gap={8}>
            <table className={tableClass}>
              <thead>
                <tr>
                  <th style={{ width: 130 }}>항목 · 점수</th>
                  <th>기준 / 이유 (선택)</th>
                </tr>
              </thead>
              <tbody>
                {rubric.items.map((it) => (
                  <tr key={it.id}>
                    <td>
                      <b>{it.id}</b>
                      <Row style={{ marginTop: 4 }}>
                        <NumberInput value={awarded[it.id] ?? NaN} min={0} max={it.points} onChange={(v) => setAwarded({ ...awarded, [it.id]: v })} />
                        <span>/ {it.points}</span>
                      </Row>
                    </td>
                    <td>
                      <div style={{ fontSize: 12.5, color: "var(--text-2)", marginBottom: 4 }}>
                        <Loose text={it.criterion} />
                      </div>
                      <TextInput value={notes[it.id] ?? ""} onChange={(v) => setNotes({ ...notes, [it.id]: v })} placeholder="왜 이 점수인지 (AI가 배울 점)" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Field label="학생에게 줄 첨삭 (선택)">
              <TextInput value={comment} onChange={setComment} />
            </Field>
          </Stack>
        )}
      </div>
    </Modal>
  );
}
