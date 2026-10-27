import { useState } from "react";
import { Loose } from "../../components/Math";
import {
  Badge,
  Button,
  Card,
  Check,
  Empty,
  ErrorBox,
  Info,
  JobProgress,
  Notice,
  NumberInput,
  Row,
  Select,
  Stack,
  tableClass,
} from "../../components/ui";
import { api, errorText, useJob, useLoad } from "../../lib/api";
import { go } from "../../lib/router";
import type {
  AgreementStats,
  CalibrationEval,
  CalibrationSettings,
  CalibrationView,
  Cell,
  ExemplarKind,
  RubricDraft,
  RubricSuggestion,
} from "../../lib/types";
import type { TabProps } from "../Exam";
import { ManualGradeModal } from "./ManualGrade";

const KIND: Record<ExemplarKind, { label: string; tone: "bad" | "accent" | "ok" }> = {
  correction: { label: "강사가 고침", tone: "bad" },
  manual: { label: "직접 채점", tone: "accent" },
  confirmed: { label: "확인만", tone: "ok" },
};

const pct = (x: number | null | undefined) => (x == null ? "-" : `${Math.round(x * 100)}%`);

function StatTiles({ s, compact }: { s: AgreementStats; compact?: boolean }) {
  if (!s.n) return <span style={{ color: "var(--text-3)" }}>비교할 사례가 아직 없습니다.</span>;
  const tiles: [string, string, string?][] = [
    ["건수", `${s.n}`],
    ["총점 일치", pct(s.exact)],
    ["±1점 이내", pct(s.within1)],
    ["항목 일치", pct(s.item_agreement)],
    ["평균 차", (s.mean_abs_diff ?? 0).toFixed(2)],
    ["AI − 강사", `${(s.bias ?? 0) > 0 ? "+" : ""}${(s.bias ?? 0).toFixed(2)}`, (s.bias ?? 0) > 0 ? "AI가 후함" : (s.bias ?? 0) < 0 ? "AI가 엄격함" : ""],
  ];
  return (
    <Row>
      {tiles.map(([k, v, hint]) => (
        <div key={k} style={{ border: "1px solid var(--line)", borderRadius: 8, padding: compact ? "4px 10px" : "8px 14px", minWidth: compact ? 70 : 96 }}>
          <div style={{ fontSize: 11.5, color: "var(--text-3)" }}>{k}</div>
          <div style={{ fontSize: compact ? 14 : 18, fontWeight: 700 }}>{v}</div>
          {hint && <div style={{ fontSize: 11, color: "var(--text-2)" }}>{hint}</div>}
        </div>
      ))}
    </Row>
  );
}

function RubricPreview({ r }: { r: RubricDraft }) {
  return (
    <Stack gap={6}>
      <table className={tableClass}>
        <tbody>
          {r.items.map((it) => (
            <tr key={it.id}>
              <td style={{ width: 40 }}>
                <b>{it.id}</b>
              </td>
              <td style={{ width: 50 }}>{it.points}점</td>
              <td>
                <Loose text={it.criterion} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {r.partial.map((p, i) => (
        <div key={i} style={{ fontSize: 13 }}>
          · 부분점수 {p.item_id}: <Loose text={p.condition} /> → {p.points}점
        </div>
      ))}
      {r.never_deduct.map((n, i) => (
        <div key={i} style={{ fontSize: 13, color: "var(--text-2)" }}>
          · 감점하지 않음: {n}
        </div>
      ))}
    </Stack>
  );
}

function EvalDetail({ examId, number, id }: { examId: string; number: string; id: string }) {
  const { data } = useLoad<CalibrationEval>(`/exams/${examId}/calibration/${encodeURIComponent(number)}/evals/${id}`);
  if (!data) return null;
  return (
    <table className={tableClass}>
      <thead>
        <tr>
          <th>학생</th>
          <th>강사</th>
          <th>예시 없이</th>
          <th>예시와 함께</th>
          <th>넣은 예시</th>
        </tr>
      </thead>
      <tbody>
        {data.cases.map((c) => {
          const tone = (v: number | null) => (v === null ? undefined : v === c.teacher_total ? "var(--ok)" : "var(--bad)");
          return (
            <tr key={c.student_id}>
              <td>{c.student_id}</td>
              <td>
                <b>{c.teacher_total}</b>
              </td>
              <td style={{ color: tone(c.before_total) }}>{c.error ? "실패" : c.before_total}</td>
              <td style={{ color: tone(c.after_total) }}>{c.error ? "" : c.after_total}</td>
              <td style={{ fontSize: 12.5 }}>{c.error ?? (c.examples_used.join(", ") || "-")}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function QuestionCalibration({ examId, number, cells, reload }: {
  examId: string;
  number: string;
  cells: { student: string; cell: Cell | undefined }[];
  reload: () => void;
}) {
  const base = `/exams/${examId}/calibration/${encodeURIComponent(number)}`;
  const { data, error, reload: reloadView } = useLoad<CalibrationView>(base);
  const [err, setErr] = useState<string | null>(null);
  const [openEval, setOpenEval] = useState<string | null>(null);
  const [manual, setManual] = useState<string | null>(null);
  const [suggestion, setSuggestion] = useState<{ s: RubricSuggestion; cases: string[] } | null>(null);
  const [applied, setApplied] = useState<number | null>(null);
  const refresh = () => {
    reloadView();
    reload();
  };
  const evalJob = useJob((j) => {
    reloadView();
    setOpenEval((j.result as { eval_id: string }).eval_id);
  });
  const suggestJob = useJob((j) => setSuggestion({ s: (j.result as { suggestion: RubricSuggestion }).suggestion, cases: (j.result as { cases: string[] }).cases }));
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;
  const s = data.settings;
  const save = (p: Partial<CalibrationSettings>) => {
    setErr(null);
    api
      .put(`${base}/settings`, { ...s, ...p })
      .then(reloadView)
      .catch((e: unknown) => setErr(errorText(e)));
  };
  const corrections = data.candidates.filter((c) => c.kind === "correction").length;
  const confirmedCount = data.candidates.length;
  const gradeable = cells.filter((c) => c.cell?.transcript?.confirmed && c.cell.grade?.status !== "teacher_confirmed");
  const apply = () => {
    if (!suggestion?.s.revised) return;
    api
      .post<{ version: number }>(`${base}/apply`, { revised: suggestion.s.revised })
      .then((r) => {
        setApplied(r.version);
        refresh();
      })
      .catch((e: unknown) => setErr(errorText(e)));
  };
  return (
    <Stack gap={14}>
      <ErrorBox error={err} />
      <Card
        title="기준 예시"
        actions={
          <Row>
            <Check checked={s.use_examples} onChange={(v) => save({ use_examples: v })}>
              채점에 사용
            </Check>
            <span>최대</span>
            <NumberInput value={s.max_examples} min={0} max={6} step={1} onChange={(v) => Number.isFinite(v) && save({ max_examples: Math.round(v) })} />
            <span>개</span>
          </Row>
        }
      >
        <Stack>
          <span style={{ color: "var(--text-2)", fontSize: 13 }}>
            강사가 직접 채점하거나 AI 점수를 고쳐 확정한 답안이 예시가 됩니다. 채점할 때마다 그 학생 자신의 답안은 빼고, 고친 사례 → 직접 채점 → 확인만 한 순서로, 점수대가 겹치지 않게 고릅니다.
          </span>
          {data.candidates.length === 0 ? (
            <Empty>아직 예시가 없습니다. 아래에서 답안 2~3개를 직접 채점하거나, “4. 채점 · 검토”에서 점수를 확정하세요.</Empty>
          ) : (
            <table className={tableClass}>
              <thead>
                <tr>
                  <th>순서</th>
                  <th>학생</th>
                  <th>종류</th>
                  <th>강사 점수</th>
                  <th>강사가 고친 항목</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.candidates.map((c) => (
                  <tr key={c.student}>
                    <td>{c.order ? <Badge tone="accent">#{c.order}</Badge> : <span style={{ color: "var(--text-3)" }}>후보</span>}</td>
                    <td>
                      <b>{c.student}</b> {c.pinned && <Badge tone="accent">고정</Badge>}
                    </td>
                    <td>
                      <Badge tone={KIND[c.kind].tone}>{KIND[c.kind].label}</Badge>
                    </td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      {c.total} / {c.max}
                    </td>
                    <td style={{ fontSize: 13 }}>
                      {Object.entries(c.changed).map(([k, [a, b]]) => (
                        <span key={k} style={{ marginRight: 8 }}>
                          {k}: AI {a} → {b}
                        </span>
                      ))}
                      {c.teacher_note && <div style={{ color: "var(--text-2)" }}>{c.teacher_note}</div>}
                    </td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      <Button small onClick={() => save({ pinned: c.pinned ? s.pinned.filter((x) => x !== c.student) : [...s.pinned, c.student] })}>
                        {c.pinned ? "고정 해제" : "항상 넣기"}
                      </Button>{" "}
                      <Button small variant="ghost" onClick={() => save({ excluded: [...s.excluded, c.student], pinned: s.pinned.filter((x) => x !== c.student) })}>
                        빼기
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {data.excluded.length > 0 && (
            <Row>
              <span style={{ fontSize: 13, color: "var(--text-2)" }}>예시에서 뺀 답안:</span>
              {data.excluded.map((x) => (
                <Button key={x.student} small variant="ghost" onClick={() => save({ excluded: s.excluded.filter((y) => y !== x.student) })}>
                  {x.student} ({x.total}/{x.max}) 다시 넣기
                </Button>
              ))}
            </Row>
          )}
          {gradeable.length > 0 && (
            <Row>
              <span style={{ fontSize: 13, color: "var(--text-2)" }}>직접 채점해 예시 만들기:</span>
              {gradeable.map((g) => (
                <Button key={g.student} small onClick={() => setManual(g.student)}>
                  {g.student}
                  {g.cell?.grade ? ` (AI ${g.cell.grade.total}점)` : ""}
                </Button>
              ))}
            </Row>
          )}
        </Stack>
      </Card>

      <Card title="AI와 강사의 일치도 (강사가 확정한 AI 채점 기준)">
        <Stack>
          <StatTiles s={data.agreement} />
          {Object.keys(data.agreement.item_disagreements).length > 0 && (
            <Row>
              <span style={{ fontSize: 13, color: "var(--text-2)" }}>강사와 다르게 본 항목:</span>
              {Object.entries(data.agreement.item_disagreements).map(([k, n]) => (
                <Badge key={k} tone="warn">
                  {k} · {n}건
                </Badge>
              ))}
            </Row>
          )}
        </Stack>
      </Card>

      <Card
        title="보정 효과 측정"
        actions={
          <Button variant="primary" small disabled={evalJob.running || confirmedCount < 2} onClick={() => void evalJob.run(() => api.post(`${base}/eval`, {}))}>
            측정하기 ({confirmedCount}개 답안 × 2회 채점)
          </Button>
        }
      >
        <Stack>
          <span style={{ color: "var(--text-2)", fontSize: 13 }}>
            강사가 확정한 답안을 AI가 (가) 예시 없이 (나) 그 답안을 뺀 예시와 함께 다시 채점해, 강사 점수와 얼마나 가까워지는지 봅니다. 저장된 채점 결과는 바뀌지 않습니다.
          </span>
          {confirmedCount < 2 && <Notice>강사가 확정한 답안이 2개 이상 있어야 합니다.</Notice>}
          <JobProgress job={evalJob.job} />
          <ErrorBox error={evalJob.error} />
          {data.evals.map((e) => (
            <div key={e.id} style={{ borderTop: "1px solid var(--line)", paddingTop: 8 }}>
              <Row>
                <b>{new Date(e.created_at).toLocaleString("ko-KR")}</b>
                <Badge>루브릭 v{e.rubric_version}</Badge>
                <span style={{ flex: 1 }} />
                <Button small variant="ghost" onClick={() => setOpenEval(openEval === e.id ? null : e.id)}>
                  {openEval === e.id ? "접기" : "답안별 보기"}
                </Button>
              </Row>
              <Row style={{ marginTop: 6, alignItems: "flex-start" }}>
                <Stack gap={4}>
                  <span style={{ fontSize: 12.5, color: "var(--text-2)" }}>예시 없이</span>
                  <StatTiles s={e.before} compact />
                </Stack>
                <Stack gap={4}>
                  <span style={{ fontSize: 12.5, color: "var(--text-2)" }}>예시와 함께</span>
                  <StatTiles s={e.after} compact />
                </Stack>
              </Row>
              {openEval === e.id && <EvalDetail examId={examId} number={number} id={e.id} />}
            </div>
          ))}
        </Stack>
      </Card>

      <Card
        title="루브릭 개선 제안"
        actions={
          <Button small disabled={suggestJob.running || corrections === 0} onClick={() => { setApplied(null); void suggestJob.run(() => api.post(`${base}/suggest`)); }}>
            강사 수정 {corrections}건으로 제안 받기
          </Button>
        }
      >
        <Stack>
          <span style={{ color: "var(--text-2)", fontSize: 13 }}>
            강사가 AI 점수를 고친 사례를 보고, 다음부터 같은 점수가 나오도록 루브릭 문장을 다듬는 제안입니다. 적용하면 새 버전 초안이 되고 다시 승인해야 채점에 쓰입니다.
          </span>
          <JobProgress job={suggestJob.job} />
          <ErrorBox error={suggestJob.error} />
          {suggestion && suggestion.s.changes.length === 0 && <Info>바꿀 것이 없다는 판단: {suggestion.s.no_change_reason}</Info>}
          {suggestion && suggestion.s.changes.length > 0 && (
            <Stack>
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {suggestion.s.changes.map((c, i) => (
                  <li key={i}>
                    <Badge>{c.item_id ?? "새 규정"}</Badge> <Loose text={c.summary} />{" "}
                    <span style={{ fontSize: 12, color: "var(--text-3)" }}>근거: {c.evidence.join(", ")}</span>
                  </li>
                ))}
              </ul>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <Stack gap={4}>
                  <b style={{ fontSize: 13 }}>지금 루브릭 (v{data.rubric?.version})</b>
                  {data.rubric && <RubricPreview r={data.rubric} />}
                </Stack>
                <Stack gap={4}>
                  <b style={{ fontSize: 13 }}>제안</b>
                  {suggestion.s.revised && <RubricPreview r={suggestion.s.revised} />}
                </Stack>
              </div>
              <Row>
                {applied ? (
                  <>
                    <Badge tone="ok">루브릭 v{applied} 초안으로 저장</Badge>
                    <Button small variant="primary" onClick={() => go("exams", examId, "rubrics")}>
                      검토하고 승인하러 가기
                    </Button>
                  </>
                ) : (
                  <Button variant="primary" onClick={apply}>
                    새 루브릭 초안으로 적용
                  </Button>
                )}
              </Row>
            </Stack>
          )}
        </Stack>
      </Card>
      {manual && <ManualGradeModal examId={examId} student={manual} number={number} onClose={() => setManual(null)} onSaved={refresh} />}
    </Stack>
  );
}

type StudentRow = { student: string; cells: Record<string, Cell> };

export function CalibrationTab({ detail, reload }: TabProps) {
  const id = detail.exam.id;
  const numbers = detail.exam.questions.map((q) => q.number);
  const [number, setNumber] = useState(numbers[0] ?? "");
  const rows = useLoad<StudentRow[]>(`/exams/${id}/students`);
  const agree = useLoad<{ overall: AgreementStats }>(`/exams/${id}/agreement`);
  const cells = (rows.data ?? []).map((r) => ({ student: r.student, cell: r.cells[number] }));
  return (
    <Stack>
      <Card title="시험 전체 일치도">
        {agree.data && <StatTiles s={agree.data.overall} />}
      </Card>
      {numbers.length > 1 && (
        <Row>
          <span>문항</span>
          <div style={{ width: 160 }}>
            <Select value={number} onChange={setNumber} options={numbers.map((n) => ({ value: n, label: `문항 ${n}` }))} />
          </div>
        </Row>
      )}
      <QuestionCalibration
        key={number}
        examId={id}
        number={number}
        cells={cells}
        reload={() => {
          rows.reload();
          agree.reload();
          reload();
        }}
      />
    </Stack>
  );
}
