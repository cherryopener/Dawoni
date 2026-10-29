import { useEffect, useState } from "react";
import { AiTag } from "../../components/AiTag";
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
import { api, errorText, examFileUrl, useJob, useLoad } from "../../lib/api";
import type {
  AnswerCandidate,
  AnswerKey,
  CandidateMatches,
  ExamQuestion,
  KeyPart,
  PartAnswerSides,
  PartCheck,
  Rubric,
  RubricDraft,
  Scope,
  Solution,
  SolutionStep,
  StudentAnswerSide,
} from "../../lib/types";
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

const KIND: Record<string, string> = { essay: "서술형", short_answer: "단답형" };

/** 누가·언제 만들었는지 — 공유 작업공간에서 다른 강사가 이미 만든 것인지 보이게. */
function Made({ by, at }: { by?: string | null; at?: string | null }) {
  if (!by && !at) return null;
  const when = at ? new Date(at).toLocaleString([], { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
  return <Badge title="만든 사람 · 때">{[by, when].filter(Boolean).join(" · ")}</Badge>;
}

function creditText(p: KeyPart): string {
  switch (p.credit_policy) {
    case "listed":
      return p.credits.map((c, i) => `${i + 1}) ${c.what} ${c.points}점`).join(" / ");
    case "none":
      return "부분 점수 없음 (맞으면 만점, 아니면 0점)";
    case "range":
      return `${p.credit_min ?? 0}~${p.credit_max ?? 0}점 (정도에 따라)`;
    default:
      return "표기 없음";
  }
}

function KeyView({ k, q, examId, onChanged }: { k: AnswerKey; q: ExamQuestion; examId: string; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [showPage, setShowPage] = useState(false);
  const [edit, setEdit] = useState<KeyPart[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const doubtful = new Set(k.doubtful ?? []);
  const save = (parts: KeyPart[] | null) => {
    setErr(null);
    api
      .put(`/exams/${examId}/questions/${encodeURIComponent(q.number)}/key`, { parts, confirm: true, by: teacherName() || null })
      .then(() => {
        setEdit(null);
        onChanged();
      })
      .catch((e: unknown) => setErr(errorText(e)));
  };
  const setPart = (i: number, patch: Partial<KeyPart>) => edit && setEdit(edit.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  return (
    <Stack gap={8}>
      <Row>
        <b>강사 해설지</b>
        <Badge>{k.source}</Badge>
        {k.confirmed ? (
          <Badge tone="ok">확인함{k.confirmed_by ? ` · ${k.confirmed_by}` : ""}</Badge>
        ) : (
          <Badge tone="warn">판독 확인 전 — 원본과 비교하세요</Badge>
        )}
        <span style={{ flex: 1 }} />
        {(k.pages ?? []).length > 0 && (
          <Button small variant="ghost" onClick={() => setShowPage(!showPage)}>
            {showPage ? "원본 닫기" : "원본 보기"}
          </Button>
        )}
        <Button small variant="ghost" onClick={() => setOpen(!open)}>
          {open ? "해설 접기" : "해설 보기"}
        </Button>
        {edit ? (
          <>
            <Button small variant="primary" onClick={() => save(edit)}>
              저장하고 확인
            </Button>
            <Button small variant="ghost" onClick={() => setEdit(null)}>
              취소
            </Button>
          </>
        ) : (
          <>
            <Button small onClick={() => setEdit(k.parts.map((x) => ({ ...x })))}>
              고치기
            </Button>
            {!k.confirmed && (
              <Button small variant="primary" onClick={() => save(null)}>
                원본과 같음 — 확인
              </Button>
            )}
          </>
        )}
      </Row>
      {showPage && (k.pages ?? []).map((pg) => <img key={pg} src={examFileUrl(examId, pg)} alt="해설지 원본" style={{ maxWidth: "100%", border: "1px solid var(--border)" }} />)}
      <table className={tableClass}>
        <thead>
          <tr>
            <th style={{ width: 70 }}>소문항</th>
            <th>답</th>
            <th style={{ width: "38%" }}>부분 점수</th>
          </tr>
        </thead>
        <tbody>
          {(edit ?? k.parts).map((p, i) => {
            const part = q.parts?.find((x) => x.label === p.label);
            return (
              <tr key={p.label}>
                <td>
                  <b>{p.label || "전체"}</b>
                  {part && (
                    <div style={{ fontSize: 12, color: "var(--text-2)" }}>
                      {part.points}점{part.kind ? ` · ${KIND[part.kind]}` : ""}
                    </div>
                  )}
                  {doubtful.has(p.label) && (
                    <div style={{ marginTop: 4 }}>
                      <Badge tone="warn" title="두 번 읽은 결과의 숫자가 달랐습니다">
                        원본 확인
                      </Badge>
                    </div>
                  )}
                </td>
                <td>
                  {edit ? (
                    <Stack gap={4}>
                      <TextInput value={p.answer ?? ""} onChange={(v) => setPart(i, { answer: v || null, answer_expr: null })} />
                      <TextArea value={p.solution} rows={4} onChange={(v) => setPart(i, { solution: v })} />
                    </Stack>
                  ) : (
                    <>
                      {p.answer ? <Loose text={p.answer} /> : <span style={{ color: "var(--text-2)" }}>(따로 없음)</span>}
                      {(open || doubtful.has(p.label)) && p.solution && (
                        <div style={{ marginTop: 6, fontSize: 13 }}>
                          <Loose text={p.solution} />
                        </div>
                      )}
                    </>
                  )}
                </td>
                <td>
                  {creditText(p)}
                  {p.zero_if.map((z, j) => (
                    <div key={j} style={{ fontSize: 12, color: "var(--text-2)" }}>
                      0점: {z}
                    </div>
                  ))}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {[...k.notes, ...k.warnings].map((n, i) => (
        <Notice key={i}>
          <Loose text={n} />
        </Notice>
      ))}
      <ErrorBox error={err} />
    </Stack>
  );
}

const VERDICT: Record<PartCheck["verdict"], [string, "ok" | "warn" | "bad" | "accent"]> = {
  same: ["해설지와 같음", "ok"],
  ai_wrong: ["AI 풀이 오답 → 해설지 따름", "warn"],
  other_valid: ["AI 답도 정답일 수 있음", "accent"],
  key_doubt: ["해설지·문제에 의문", "bad"],
};
const CHECK: Record<"valid" | "invalid" | "unsure", [string, "ok" | "bad" | "warn"]> = {
  valid: ["검산 통과", "ok"],
  invalid: ["검산 실패", "bad"],
  unsure: ["해석에 따라 다름", "warn"],
};

const sidesOf = (m: CandidateMatches | null, c: AnswerCandidate): PartAnswerSides | null =>
  m?.parts.find((p) => p.part === c.part && p.candidates[c.id] === c.answer) ?? null;

function StudentSides({ p, c, unconfirmed }: { p: PartAnswerSides; c: AnswerCandidate; unconfirmed: string[] }) {
  const [open, setOpen] = useState(false);
  const pick = (side: StudentAnswerSide["side"], id: string | null = null) =>
    p.sides.filter((x) => x.side === side && (x.candidate_id ?? null) === id);
  const groups: [string, StudentAnswerSide[]][] = [
    [`이 후보 ${c.id}`, pick("candidate", c.id)],
    ["해설지 답", pick("key")],
    ...Object.keys(p.candidates)
      .filter((id) => id !== c.id)
      .map((id): [string, StudentAnswerSide[]] => [`후보 ${id}`, pick("candidate", id)]),
    ["그 외", pick("other")],
  ];
  const pending = new Set(unconfirmed);
  return (
    <div style={{ fontSize: 13 }}>
      <Row>
        <b>학생 답</b>
        {groups.map(([name, xs]) => (
          <Badge key={name} tone={name.startsWith("이 후보") && xs.length ? "accent" : undefined}>
            {name} {xs.length}명
          </Badge>
        ))}
        {p.blank.length > 0 && <Badge>빈 답 {p.blank.length}명</Badge>}
        <Button small variant="ghost" onClick={() => setOpen(!open)}>
          {open ? "예시 닫기" : "예시 답안 보기"}
        </Button>
      </Row>
      {open && (
        <Stack gap={6}>
          {groups
            .filter(([, xs]) => xs.length)
            .map(([name, xs]) => (
              <div key={name}>
                <b>{name}</b>
                <ul style={{ margin: "2px 0 0", paddingLeft: 20 }}>
                  {xs.slice(0, 5).map((x) => (
                    <li key={x.student}>
                      {x.student}
                      {pending.has(x.student) && <Badge tone="warn">판독 확인 전</Badge>} “<Loose text={x.quote} />”
                    </li>
                  ))}
                  {xs.length > 5 && <li>… 외 {xs.length - 5}명</li>}
                </ul>
              </div>
            ))}
        </Stack>
      )}
    </div>
  );
}

function KeyReview({ examId, q, rubric, matches, onChanged }: {
  examId: string;
  q: ExamQuestion;
  rubric: Rubric;
  matches: CandidateMatches | null;
  onChanged: () => void;
}) {
  const [err, setErr] = useState<string | null>(null);
  const match = useJob(onChanged);
  const checks = rubric.checks ?? [];
  const cands = rubric.candidates ?? [];
  const notes = rubric.review_notes ?? [];
  if (!checks.length && !cands.length && !notes.length) return null;
  const runMatch = () =>
    void match.run(() => api.post(`/exams/${examId}/questions/${encodeURIComponent(q.number)}/rubric/candidates/match`, {}));
  const decide = (c: AnswerCandidate, to: "accept" | "reject") => {
    setErr(null);
    api
      .post(`/exams/${examId}/questions/${encodeURIComponent(q.number)}/rubric/candidates`, { [to]: [c.id] })
      .then(onChanged)
      .catch((e: unknown) => setErr(errorText(e)));
  };
  return (
    <Stack gap={8}>
      <b>해설지와 AI 풀이 비교</b>
      {checks.map((c) => (
        <div key={c.part}>
          <Row>
            <b>{c.part || "전체"}</b>
            <Badge tone={VERDICT[c.verdict][1]}>{VERDICT[c.verdict][0]}</Badge>
          </Row>
          {c.verdict !== "same" && (
            <div style={{ fontSize: 13, marginTop: 4 }}>
              해설지 <Loose text={c.key_answer} /> {c.key_check && <Badge tone={CHECK[c.key_check][1]} title={c.key_check_note ?? ""}>{CHECK[c.key_check][0]}</Badge>} · AI{" "}
              <Loose text={c.ai_answer} /> {c.ai_check && <Badge tone={CHECK[c.ai_check][1]} title={c.ai_check_note ?? ""}>{CHECK[c.ai_check][0]}</Badge>}
              {c.note && (
                <div style={{ color: "var(--text-2)" }}>
                  <Loose text={c.note} />
                </div>
              )}
            </div>
          )}
        </div>
      ))}
      {cands.length > 0 && (
        <Stack gap={4}>
          <Row>
            <span style={{ fontSize: 13, color: "var(--text-2)" }}>
              정하기 전에 판독한 학생 답이 해설지 답·후보 중 어느 쪽인지 볼 수 있습니다
              {matches && ` (${new Date(matches.created_at).toLocaleString()} 비교)`}.
            </span>
            <span style={{ flex: 1 }} />
            <Button small disabled={match.running} onClick={runMatch}>
              {matches ? "학생 답과 다시 비교" : "학생 답과 비교 (AI)"}
            </Button>
          </Row>
          <JobProgress job={match.job} />
          <ErrorBox error={match.error} />
        </Stack>
      )}
      {cands.map((c) => (
        <Notice key={c.id}>
          <Stack gap={6}>
            <Row>
              <b>
                다른 정답 후보 {c.id} ({c.part || "전체"})
              </b>
              <Loose text={c.answer} />
              {c.check && <Badge tone={CHECK[c.check][1]}>{CHECK[c.check][0]}</Badge>}
              <Badge tone={c.status === "accepted" ? "ok" : c.status === "rejected" ? "bad" : "warn"}>
                {c.status === "accepted" ? "인정" : c.status === "rejected" ? "제외" : "강사 미확인 — 점수 없음"}
              </Badge>
              <span style={{ flex: 1 }} />
              <Button small disabled={c.status === "accepted"} onClick={() => decide(c, "accept")}>
                정답으로 인정
              </Button>
              <Button small variant="ghost" disabled={c.status === "rejected"} onClick={() => decide(c, "reject")}>
                제외
              </Button>
            </Row>
            <span style={{ fontSize: 13 }}>
              <Loose text={c.argument} />
            </span>
            {c.check_note && (
              <span style={{ fontSize: 13, color: "var(--text-2)" }}>
                독립 검산: <Loose text={c.check_note} />
              </span>
            )}
            {(() => {
              const p = sidesOf(matches, c);
              return p && <StudentSides p={p} c={c} unconfirmed={matches?.unconfirmed ?? []} />;
            })()}
          </Stack>
        </Notice>
      ))}
      {notes.map((n, i) => (
        <Notice key={i}>
          <Loose text={n} />
        </Notice>
      ))}
      <ErrorBox error={err} />
    </Stack>
  );
}

export function SolutionView({ sol, scope, withKey = false }: { sol: Solution; scope: Scope | null; withKey?: boolean }) {
  return (
    <Stack gap={8}>
      <Row>
        <b>{withKey ? "AI 독립 풀이 (해설지를 보지 않고)" : "범위 해설"}</b>
        <AiTag agent={sol.agent} model={sol.model} what="풀이" />
        <Made by={sol.created_by} at={sol.created_at} />
        {sol.verified ? <Badge tone="ok">독립 재풀이와 답 일치</Badge> : <Badge>재풀이 안 함</Badge>}
      </Row>
      {sol.out_of_scope_note && <Notice>범위 안에서 풀기 어렵다는 AI 판단: {sol.out_of_scope_note}</Notice>}
      {sol.check_notes.map((n, i) => (
        <Notice key={i}>
          <Loose text={n} />
        </Notice>
      ))}
      <Steps steps={sol.steps} scope={scope} />
      <div>
        <b>답: </b>
        <Tex latex={sol.final_answer.latex} />
      </div>
      {(sol.part_answers ?? []).length > 0 && (
        <Row>
          {sol.part_answers!.map((a) => (
            <span key={a.label}>
              <b>{a.label}</b> <Tex latex={a.latex} />
            </span>
          ))}
        </Row>
      )}
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
  checks: (r.checks ?? []).map((x) => ({ ...x })),
  candidates: (r.candidates ?? []).map((x) => ({ ...x })),
  key_conflicts: [...(r.key_conflicts ?? [])],
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
        <AiTag agent={rubric.agent} model={rubric.model} what="루브릭 초안" />
        <Made by={rubric.created_by} at={rubric.created_at} />
        {rubric.status === "approved" && !dirty ? (
          <Badge tone="ok">승인됨{rubric.approved_by ? ` · ${rubric.approved_by}` : ""}</Badge>
        ) : (
          <Badge tone="warn">{dirty ? "고친 내용 저장 전" : "승인 전 — 채점에 쓰려면 승인"}</Badge>
        )}
        <Badge tone={total === q.points ? "ok" : "bad"}>
          합계 {total} / 배점 {q.points}
        </Badge>
        {(q.parts ?? []).map((p) => {
          const sum = d.items.filter((x) => (x.part ?? "") === p.label).reduce((a, x) => a + (x.points || 0), 0);
          return (
            <Badge key={p.label} tone={sum === p.points ? "neutral" : "warn"}>
              {p.label} {sum}/{p.points}
            </Badge>
          );
        })}
        {rubric.based_on_key ? <Badge tone="ok">해설지 기준</Badge> : <Badge tone="warn">해설지 없음 — 정답 확인</Badge>}
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
            {(q.parts ?? []).length > 0 && <th style={{ width: 86 }}>소문항</th>}
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
              {(q.parts ?? []).length > 0 && (
                <td>
                  <select
                    value={it.part ?? ""}
                    onChange={(e) => upd({ items: d.items.map((x, k) => (k === i ? { ...x, part: e.target.value } : x)) })}
                  >
                    {q.parts!.map((p) => (
                      <option key={p.label}>{p.label}</option>
                    ))}
                  </select>
                  {it.key_credit != null && (
                    <div style={{ marginTop: 4 }}>
                      <Badge tone="ok" title="해설지 부분 점수 표기의 몇 번째인지">
                        해설지 {it.key_credit})
                      </Badge>
                    </div>
                  )}
                </td>
              )}
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
        <Button small onClick={() => upd({ items: [...d.items, { id: nextId, points: 1, criterion: "", evidence_hint: "", solution_steps: [], part: q.parts?.[0]?.label ?? null }] })}>
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
              {!p.from_key && <Badge tone="accent">AI 제안</Badge>}
              <Button small variant="ghost" onClick={() => upd({ partial: d.partial.filter((_, k) => k !== i) })}>
                ✕
              </Button>
            </Row>
          ))}
          <Row>
            <Button small onClick={() => d.items[0] && upd({ partial: [...d.partial, { item_id: d.items[0].id, condition: "", points: 0, from_key: true }] })}>
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

type QDetail = {
  question: ExamQuestion;
  key: AnswerKey | null;
  solution: Solution | null;
  rubric: Rubric | null;
  candidate_matches: CandidateMatches | null;
};

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
        {data.key ? (
          <KeyView
            k={data.key}
            q={q}
            examId={examId}
            onChanged={() => {
              reload();
              onChanged();
            }}
          />
        ) : <Info>해설지가 없습니다 — 루브릭이 AI 풀이만으로 만들어집니다. 위에서 해설지를 붙일 수 있습니다.</Info>}
        {data.solution ? <SolutionView sol={data.solution} scope={scope} withKey={!!data.key} /> : <Info>아직 해설이 없습니다.</Info>}
        {data.rubric && (
          <KeyReview
            examId={examId}
            q={q}
            rubric={data.rubric}
            matches={data.candidate_matches}
            onChanged={() => {
              reload();
              onChanged();
            }}
          />
        )}
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

function KeyUpload({ examId, onDone, missing }: { examId: string; onDone: () => void; missing: number }) {
  const [file, setFile] = useState<File | null>(null);
  const [pages, setPages] = useState("");
  const job = useJob(onDone);
  const send = () => {
    const f = new FormData();
    f.append("file", file!);
    f.append("pages", pages);
    void job.run(() => api.upload(`/exams/${examId}/answer-key`, f));
  };
  return (
    <Stack gap={6}>
      <Row>
        <b>해설지</b>
        {missing ? <Badge tone="warn">{missing}문항 없음</Badge> : <Badge tone="ok">모든 문항</Badge>}
        <input type="file" accept="application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        <div style={{ width: 130 }}>
          <TextInput value={pages} onChange={setPages} placeholder="쪽 (자동)" />
        </div>
        <Button small disabled={!file || job.running} onClick={send}>
          해설지 붙이기
        </Button>
      </Row>
      <JobProgress job={job.job} />
      <ErrorBox error={job.error} />
    </Stack>
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
              onClick={() => void solve.run(() => api.post(`/exams/${id}/solve`, { questions: unsolved.length ? unsolved : null, verify, by: teacherName() || null }))}
            >
              {unsolved.length ? `해설 만들기 (${unsolved.length}문항)` : "해설 다시 만들기 (전체)"}
            </Button>
            <Check checked={verify} onChange={setVerify}>
              독립 재풀이로 답 확인
            </Check>
            <span style={{ width: 16 }} />
            <Button
              disabled={rubric.running || detail.summary.solved === 0}
              onClick={() => void rubric.run(() => api.post(`/exams/${id}/rubrics`, { questions: noRubric.length ? noRubric : null, by: teacherName() || null }))}
            >
              {noRubric.length ? `루브릭 초안 만들기 (${noRubric.length}문항)` : "루브릭 다시 만들기 (전체)"}
            </Button>
          </Row>
          <span style={{ color: "var(--text-2)", fontSize: 13 }}>
            해설은 AI가 해설지를 보지 않고 범위 안에서 먼저 풉니다. 루브릭은 강사 해설지(정답·배점·부분 점수)를 기준으로 만들고, AI 풀이와
            소문항마다 비교해 AI 오답·다른 정답 후보·해설지 의문을 표시합니다. 다른 정답 후보는 강사가 인정해야 점수가 나갑니다.
          </span>
          <KeyUpload examId={id} onDone={refresh} missing={detail.questions.filter((q) => !q.has_key).length} />
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
