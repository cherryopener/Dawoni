import { useEffect, useState } from "react";
import { ItemView } from "../../components/Item";
import { Loose, Tex } from "../../components/Math";
import {
  Badge,
  Button,
  Card,
  Check,
  ErrorBox,
  Field,
  Info,
  JobProgress,
  Notice,
  NumberInput,
  Row,
  Stack,
  TextArea,
  TextInput,
  tableClass,
  teacherName,
} from "../../components/ui";
import { api, errorText, useJob, useLoad } from "../../lib/api";
import type { ExamQuestion, Rubric, RubricDraft, Scope, Solution, SolutionStep } from "../../lib/types";
import type { TabProps } from "../Exam";

function Steps({ steps, scope }: { steps: SolutionStep[]; scope: Scope | null }) {
  const name = (id: string) =>
    scope?.concepts.find((c) => c.id === id)?.name ?? scope?.skills.find((c) => c.id === id)?.name ?? id;
  return (
    <ol style={{ margin: 0, paddingLeft: 20, display: "flex", flexDirection: "column", gap: 6 }}>
      {steps.map((st, i) => (
        <li key={i}>
          <Loose text={st.explanation} />{" "}
          {st.uses.map((u) => (
            <Badge key={u} tone="accent" title={name(u)}>
              {u} {name(u)}
            </Badge>
          ))}
          {st.math && <Tex latex={st.math} display />}
        </li>
      ))}
    </ol>
  );
}

function SolutionView({ sol, scope }: { sol: Solution; scope: Scope | null }) {
  return (
    <Stack gap={8}>
      <Row>
        <b>범위 해설</b>
        {sol.verified ? <Badge tone="ok">독립 재풀이와 답 일치</Badge> : <Badge>재풀이 안 함</Badge>}
      </Row>
      {sol.out_of_scope_note && <Notice>범위 안에서 풀기 어렵다는 AI 판단: {sol.out_of_scope_note}</Notice>}
      {sol.check_notes.map((n, i) => (
        <Notice key={i}>{n}</Notice>
      ))}
      <Steps steps={sol.steps} scope={scope} />
      <div>
        <b>답: </b>
        <Tex latex={sol.final_answer.latex} />
      </div>
      {sol.alternatives.map((a, i) => (
        <div key={i}>
          <b>다른 풀이: </b>
          <Loose text={a.summary} />
          <Steps steps={a.steps} scope={scope} />
        </div>
      ))}
    </Stack>
  );
}

const draftOf = (r: Rubric): RubricDraft => ({
  items: r.items.map((x) => ({ ...x })),
  partial: r.partial.map((x) => ({ ...x })),
  accepted_alternatives: r.accepted_alternatives.map((x) => ({ ...x })),
  never_deduct: [...r.never_deduct],
});

function RubricEditor({ examId, q, rubric, onChanged }: {
  examId: string;
  q: ExamQuestion;
  rubric: Rubric;
  onChanged: () => void;
}) {
  const [d, setD] = useState<RubricDraft>(draftOf(rubric));
  const [dirty, setDirty] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setD(draftOf(rubric));
    setDirty(false);
  }, [rubric]);
  const upd = (p: Partial<RubricDraft>) => {
    setD({ ...d, ...p });
    setDirty(true);
  };
  const total = d.items.reduce((a, x) => a + (x.points || 0), 0);
  const base = `/exams/${examId}/questions/${encodeURIComponent(q.number)}/rubric`;
  const save = () =>
    api.put<Rubric>(base, d).then(() => {
      setDirty(false);
      onChanged();
    });
  const act = (f: () => Promise<unknown>) => {
    setErr(null);
    f().catch((e: unknown) => setErr(errorText(e)));
  };
  const approve = () => act(async () => {
    if (dirty) await save();
    await api.post(`${base}/approve`, { by: teacherName() || null });
    onChanged();
  });
  const nextId = `R${Math.max(0, ...d.items.map((x) => parseInt(x.id.slice(1), 10) || 0)) + 1}`;
  return (
    <Stack gap={10}>
      <Row>
        <b>루브릭 v{rubric.version}</b>
        {rubric.status === "approved" && !dirty ? (
          <Badge tone="ok">승인됨{rubric.approved_by ? ` · ${rubric.approved_by}` : ""}</Badge>
        ) : (
          <Badge tone="warn">{dirty ? "고친 내용 저장 전" : "승인 전 — 채점에 쓰려면 승인"}</Badge>
        )}
        <Badge tone={total === q.points ? "ok" : "bad"}>
          합계 {total} / 배점 {q.points}
        </Badge>
        <span style={{ flex: 1 }} />
        <Button small onClick={() => act(save)} disabled={!dirty}>
          저장
        </Button>
        <Button small variant="primary" onClick={approve} disabled={total !== q.points || (rubric.status === "approved" && !dirty)}>
          {dirty ? "저장하고 승인" : "승인"}
        </Button>
      </Row>
      <ErrorBox error={err} />
      <table className={tableClass}>
        <thead>
          <tr>
            <th style={{ width: 44 }}>항목</th>
            <th style={{ width: 90 }}>배점</th>
            <th>채점 기준 (무엇이 있으면 점수)</th>
            <th style={{ width: "30%" }}>답안에서 찾을 예</th>
            <th style={{ width: 36 }} />
          </tr>
        </thead>
        <tbody>
          {d.items.map((it, i) => (
            <tr key={it.id}>
              <td>
                <b>{it.id}</b>
              </td>
              <td>
                <NumberInput value={it.points} min={0.5} onChange={(v) => upd({ items: d.items.map((x, k) => (k === i ? { ...x, points: v } : x)) })} />
              </td>
              <td>
                <TextArea value={it.criterion} rows={2} onChange={(v) => upd({ items: d.items.map((x, k) => (k === i ? { ...x, criterion: v } : x)) })} />
              </td>
              <td>
                <TextInput value={it.evidence_hint} mono onChange={(v) => upd({ items: d.items.map((x, k) => (k === i ? { ...x, evidence_hint: v } : x)) })} />
                <div style={{ marginTop: 4 }}>
                  <Loose text={it.evidence_hint} />
                </div>
              </td>
              <td>
                <Button small variant="ghost" onClick={() => upd({ items: d.items.filter((_, k) => k !== i), partial: d.partial.filter((p) => p.item_id !== it.id) })}>
                  ✕
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <Row>
        <Button small onClick={() => upd({ items: [...d.items, { id: nextId, points: 1, criterion: "", evidence_hint: "", solution_steps: [] }] })}>
          + 항목
        </Button>
      </Row>

      <Field label="부분 점수">
        <Stack gap={6}>
          {d.partial.map((p, i) => (
            <Row key={i}>
              <select
                value={p.item_id}
                onChange={(e) => upd({ partial: d.partial.map((x, k) => (k === i ? { ...x, item_id: e.target.value } : x)) })}
              >
                {d.items.map((it) => (
                  <option key={it.id}>{it.id}</option>
                ))}
              </select>
              <div style={{ flex: 1 }}>
                <TextInput value={p.condition} onChange={(v) => upd({ partial: d.partial.map((x, k) => (k === i ? { ...x, condition: v } : x)) })} />
              </div>
              →
              <NumberInput value={p.points} min={0} onChange={(v) => upd({ partial: d.partial.map((x, k) => (k === i ? { ...x, points: v } : x)) })} />점
              <Button small variant="ghost" onClick={() => upd({ partial: d.partial.filter((_, k) => k !== i) })}>
                ✕
              </Button>
            </Row>
          ))}
          <Row>
            <Button small onClick={() => d.items[0] && upd({ partial: [...d.partial, { item_id: d.items[0].id, condition: "", points: 0 }] })}>
              + 부분 점수
            </Button>
          </Row>
        </Stack>
      </Field>

      <Field label="감점하지 않는 경우" hint="범위 밖 기법을 안 쓴 것, 범위 안의 다른 풀이 등은 기본으로 들어 있습니다">
        <Stack gap={6}>
          {d.never_deduct.map((n, i) => (
            <Row key={i}>
              <div style={{ flex: 1 }}>
                <TextInput value={n} onChange={(v) => upd({ never_deduct: d.never_deduct.map((x, k) => (k === i ? v : x)) })} />
              </div>
              <Button small variant="ghost" onClick={() => upd({ never_deduct: d.never_deduct.filter((_, k) => k !== i) })}>
                ✕
              </Button>
            </Row>
          ))}
          <Row>
            <Button small onClick={() => upd({ never_deduct: [...d.never_deduct, ""] })}>
              + 추가
            </Button>
          </Row>
        </Stack>
      </Field>
      {d.accepted_alternatives.length > 0 && (
        <Field label="인정하는 다른 풀이">
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {d.accepted_alternatives.map((a, i) => (
              <li key={i}>
                <Loose text={a.description} /> <Badge>{a.item_ids.join(", ")}</Badge>
              </li>
            ))}
          </ul>
        </Field>
      )}
    </Stack>
  );
}

type QDetail = { question: ExamQuestion; solution: Solution | null; rubric: Rubric | null };

function QuestionCard({ examId, number, scope, onChanged, version }: {
  examId: string;
  number: string;
  scope: Scope | null;
  onChanged: () => void;
  version: number;
}) {
  const { data, reload } = useLoad<QDetail>(`/exams/${examId}/questions/${encodeURIComponent(number)}?v=${version}`);
  const [showQ, setShowQ] = useState(false);
  if (!data) return null;
  const q = data.question;
  return (
    <Card
      title={
        <Row>
          문항 {q.number} <Badge>{q.points}점</Badge>
        </Row>
      }
      actions={
        <Button small variant="ghost" onClick={() => setShowQ(!showQ)}>
          {showQ ? "문항 접기" : "문항 보기"}
        </Button>
      }
    >
      <Stack gap={14}>
        {showQ && <ItemView number={q.number} points={q.points} item={q.item} examId={examId} />}
        {data.solution ? <SolutionView sol={data.solution} scope={scope} /> : <Info>아직 해설이 없습니다.</Info>}
        {data.rubric ? (
          <RubricEditor
            examId={examId}
            q={q}
            rubric={data.rubric}
            onChanged={() => {
              reload();
              onChanged();
            }}
          />
        ) : (
          data.solution && <Info>해설이 있습니다. 위에서 “루브릭 초안 만들기”를 누르세요.</Info>
        )}
      </Stack>
    </Card>
  );
}

export function RubricsTab({ detail, scope, reload }: TabProps) {
  const [verify, setVerify] = useState(true);
  const [version, setVersion] = useState(0);
  const refresh = () => {
    setVersion((v) => v + 1);
    reload();
  };
  const solve = useJob(refresh);
  const rubric = useJob(refresh);
  const id = detail.exam.id;
  const unsolved = detail.questions.filter((q) => !q.solution).map((q) => q.number);
  const noRubric = detail.questions.filter((q) => q.solution && !q.rubric).map((q) => q.number);
  return (
    <Stack>
      <Card>
        <Stack>
          <Row>
            <Button
              variant="primary"
              disabled={solve.running}
              onClick={() => void solve.run(() => api.post(`/exams/${id}/solve`, { questions: unsolved.length ? unsolved : null, verify }))}
            >
              {unsolved.length ? `해설 만들기 (${unsolved.length}문항)` : "해설 다시 만들기 (전체)"}
            </Button>
            <Check checked={verify} onChange={setVerify}>
              독립 재풀이로 답 확인
            </Check>
            <span style={{ width: 16 }} />
            <Button
              disabled={rubric.running || detail.summary.solved === 0}
              onClick={() => void rubric.run(() => api.post(`/exams/${id}/rubrics`, { questions: noRubric.length ? noRubric : null }))}
            >
              {noRubric.length ? `루브릭 초안 만들기 (${noRubric.length}문항)` : "루브릭 다시 만들기 (전체)"}
            </Button>
          </Row>
          <span style={{ color: "var(--text-2)", fontSize: 13 }}>
            해설은 범위 프로필의 개념·기법만 써서 만들고, 단계마다 쓴 개념 ID를 코드가 확인합니다. 루브릭은 강사가 승인해야 채점에 쓰입니다.
          </span>
          <JobProgress job={solve.job} />
          <JobProgress job={rubric.job} />
          <ErrorBox error={solve.error ?? rubric.error} />
        </Stack>
      </Card>
      {detail.questions.map((q) => (
        <QuestionCard key={q.number} examId={id} number={q.number} scope={scope} onChanged={reload} version={version} />
      ))}
    </Stack>
  );
}
