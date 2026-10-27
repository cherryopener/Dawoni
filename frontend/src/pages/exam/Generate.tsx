import { useState } from "react";
import { ItemView } from "../../components/Item";
import { Loose } from "../../components/Math";
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
  Notice,
  NumberInput,
  Row,
  Select,
  Stack,
  TextArea,
  TextInput,
  teacherName,
} from "../../components/ui";
import { api, errorText, useJob, useLoad } from "../../lib/api";
import { go } from "../../lib/router";
import type { Exam, Extension, GeneratedSet, GenKind, Scope } from "../../lib/types";
import type { TabProps } from "../Exam";
import { RubricPreview } from "./Calibration";
import { SolutionView } from "./Rubrics";

const KINDS: { value: GenKind; label: string; desc: string }[] = [
  { value: "numeric", label: "동형 · 수치형", desc: "숫자만 바꿈. 값은 앱이 골라 답이 정수·기약분수로 깔끔하게 나오는 조합만 씀" },
  { value: "symbolic", label: "동형 · 미지수형", desc: "수치 대신 문자로 조건을 주고 답도 문자식 (sympy로 답식 확인)" },
  { value: "condition", label: "동형 · 조건 변형", desc: "조건을 바꿔 다른 답이 나오게. 그림 선지는 그대로 두고 다른 선지가 정답이 되게" },
  { value: "novel_a", label: "신규 A · 다른 기법", desc: "같은 개념 범위에서, 기준 문항과 다른 풀이 기법이 핵심인 새 문항" },
  { value: "novel_b", label: "신규 B · 확장 개념", desc: "교과 밖 개념 하나를 제시문으로 알려 주고 유추·서술하게" },
];
const KIND_LABEL = Object.fromEntries(KINDS.map((k) => [k.value, k.label])) as Record<GenKind, string>;
const STATUS = { draft: { l: "초안", t: "warn" }, approved: { l: "승인", t: "ok" }, rejected: { l: "버림", t: "neutral" } } as const;

const emptyExt = (): Extension => ({ id: "X1", name: "", definition: "", assumptions: [], expected_inference: "" });

function ExtensionForm({ examId, number, ext, setExt }: {
  examId: string;
  number: string;
  ext: Extension;
  setExt: (e: Extension) => void;
}) {
  const [cands, setCands] = useState<{ candidates: Extension[]; rationale: string[] } | null>(null);
  const propose = useJob((j) => setCands(j.result as { candidates: Extension[]; rationale: string[] }));
  return (
    <Stack gap={8}>
      <Row>
        <span style={{ fontSize: 13, color: "var(--text-2)" }}>확장 개념을 직접 적거나 AI 후보에서 고르세요.</span>
        <Button small onClick={() => void propose.run(() => api.post(`/exams/${examId}/questions/${encodeURIComponent(number)}/extensions`))} disabled={propose.running}>
          AI 후보 받기
        </Button>
      </Row>
      <JobProgress job={propose.job} />
      <ErrorBox error={propose.error} />
      {cands && (
        <Stack gap={6}>
          {cands.candidates.map((c, i) => (
            <button
              key={c.id}
              onClick={() => setExt({ ...c, id: "X1" })}
              style={{ textAlign: "left", border: "1px solid var(--line)", borderRadius: 6, padding: 8, background: ext.name === c.name ? "var(--accent-soft)" : "var(--panel)", cursor: "pointer" }}
            >
              <b>{c.name}</b> — <Loose text={c.definition} />
              {cands.rationale[i] && <div style={{ fontSize: 12.5, color: "var(--text-2)" }}>{cands.rationale[i]}</div>}
            </button>
          ))}
        </Stack>
      )}
      <Row>
        <div style={{ flex: 1, minWidth: 200 }}>
          <Field label="이름">
            <TextInput value={ext.name} onChange={(v) => setExt({ ...ext, name: v })} placeholder="공기 저항" />
          </Field>
        </div>
        <div style={{ flex: 2, minWidth: 260 }}>
          <Field label="정의 (제시문에서 주는 식)">
            <TextInput value={ext.definition} onChange={(v) => setExt({ ...ext, definition: v })} placeholder="공기 저항력의 크기는 속력에 비례한다 (f = bv)" />
          </Field>
        </div>
      </Row>
      <Row>
        <div style={{ flex: 1, minWidth: 200 }}>
          <Field label="가정 (줄마다 하나, 선택)">
            <TextArea value={ext.assumptions.join("\n")} rows={2} onChange={(v) => setExt({ ...ext, assumptions: v.split("\n").map((x) => x.trim()).filter(Boolean) })} />
          </Field>
        </div>
        <div style={{ flex: 1, minWidth: 200 }}>
          <Field label="학생이 유추할 내용 (선택)">
            <TextArea value={ext.expected_inference ?? ""} rows={2} onChange={(v) => setExt({ ...ext, expected_inference: v })} />
          </Field>
        </div>
      </Row>
    </Stack>
  );
}

function SetCard({ s, scope, selected, onSelect, onChanged }: {
  s: GeneratedSet;
  scope: Scope | null;
  selected: boolean;
  onSelect: (v: boolean) => void;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(s.status === "draft");
  const [err, setErr] = useState<string | null>(null);
  const review = (status: GeneratedSet["status"]) =>
    api
      .put(`/generated/${s.id}/review`, { status, by: teacherName() || null })
      .then(onChanged)
      .catch((e: unknown) => setErr(errorText(e)));
  const st = STATUS[s.status];
  return (
    <Card
      title={
        <Row>
          {s.status === "approved" && <Check checked={selected} onChange={onSelect}>{""}</Check>}
          <Badge tone="accent">{KIND_LABEL[s.kind]}</Badge>
          <Badge tone={st.t}>{st.l}</Badge>
          {s.solution.verified ? <Badge tone="ok">독립 풀이와 답 일치</Badge> : <Badge tone="bad">답 미확인</Badge>}
          {s.rubric ? <Badge>루브릭 초안</Badge> : <Badge tone="warn">루브릭 없음</Badge>}
          <span style={{ fontSize: 12.5, color: "var(--text-3)" }}>원본 {s.source.question_number}번 · {s.points}점</span>
        </Row>
      }
      actions={
        <Row>
          {s.status !== "approved" && (
            <Button small variant="primary" disabled={!s.rubric} onClick={() => void review("approved")}>
              승인
            </Button>
          )}
          {s.status !== "rejected" && (
            <Button small variant="ghost" onClick={() => void review("rejected")}>
              버리기
            </Button>
          )}
          {s.status !== "draft" && (
            <Button small variant="ghost" onClick={() => void review("draft")}>
              초안으로
            </Button>
          )}
          <Button small variant="ghost" onClick={() => setOpen(!open)}>
            {open ? "접기" : "펼치기"}
          </Button>
        </Row>
      }
    >
      <Stack gap={10}>
        <ErrorBox error={err} />
        {s.note && (
          <div style={{ fontSize: 13, color: "var(--text-2)" }}>
            <Loose text={s.note} />
          </div>
        )}
        {s.extension && (
          <Info>
            확장 개념 {s.extension.id} {s.extension.name}: <Loose text={s.extension.definition} />
          </Info>
        )}
        {s.warnings.map((w, i) => (
          <Notice key={i}>
            <Loose text={w} />
          </Notice>
        ))}
        {open && (
          <>
            <ItemView number="새" points={s.points} item={s.item} examId={s.source.exam_id} renderBase={`/api/generated/${s.id}/figures`} />
            <SolutionView sol={s.solution} scope={scope} />
            {s.skill_ids.length > 0 && <div style={{ fontSize: 12.5, color: "var(--text-2)" }}>쓴 기법: {s.skill_ids.join(", ")}</div>}
            {s.rubric && (
              <Stack gap={4}>
                <b style={{ fontSize: 13 }}>루브릭 초안</b>
                <RubricPreview r={s.rubric} />
              </Stack>
            )}
          </>
        )}
      </Stack>
    </Card>
  );
}

export function GenerateTab({ detail, scope }: TabProps) {
  const id = detail.exam.id;
  const numbers = detail.exam.questions.map((q) => q.number);
  const [number, setNumber] = useState(numbers[0] ?? "");
  const [kind, setKind] = useState<GenKind>("numeric");
  const [count, setCount] = useState(2);
  const [maxDen, setMaxDen] = useState(20);
  const [sqrt, setSqrt] = useState(false);
  const [ext, setExt] = useState<Extension>(emptyExt());
  const [selected, setSelected] = useState<string[]>([]);
  const [title, setTitle] = useState(`${detail.exam.title} 동형`);
  const [err, setErr] = useState<string | null>(null);
  const list = useLoad<GeneratedSet[]>(`/exams/${id}/generated`);
  const job = useJob(() => list.reload());
  const q = detail.exam.questions.find((x) => x.number === number);
  const figureChoices = !!q && (q.item.choices_figure_id != null || q.item.choices.some((c) => c.figure_id));
  const hasSolution = detail.questions.find((x) => x.number === number)?.solution != null;
  const start = () =>
    void job.run(() =>
      api.post(`/exams/${id}/generate`, {
        question: number,
        kind,
        count,
        max_den: maxDen,
        allow_sqrt: sqrt,
        extension: kind === "novel_b" ? ext : null,
      }),
    );
  const bundle = () => {
    setErr(null);
    api
      .post<Exam>("/generated/to-exam", { ids: selected, title })
      .then((e) => go("exams", e.id, "rubrics"))
      .catch((e: unknown) => setErr(errorText(e)));
  };
  const sets = list.data ?? [];
  return (
    <Stack>
      <Card title="새 문항 만들기">
        <Stack>
          <Row>
            <span>기준 문항</span>
            <div style={{ width: 140 }}>
              <Select value={number} onChange={setNumber} options={numbers.map((n) => ({ value: n, label: `문항 ${n}` }))} />
            </div>
            {figureChoices && <Badge tone="accent">그림 선지 문항</Badge>}
          </Row>
          {!hasSolution && <Notice>기준 문항의 해설이 없으면 원래 정답과 대조할 수 없습니다. “2. 해설 · 루브릭”에서 먼저 해설을 만드는 것을 권합니다.</Notice>}
          <Stack gap={6}>
            {KINDS.map((k) => {
              const disabled = k.value === "numeric" && figureChoices;
              return (
                <label key={k.value} style={{ display: "flex", gap: 8, alignItems: "baseline", opacity: disabled ? 0.45 : 1, cursor: disabled ? "default" : "pointer" }}>
                  <input type="radio" name="kind" checked={kind === k.value} disabled={disabled} onChange={() => setKind(k.value)} />
                  <b style={{ minWidth: 120 }}>{k.label}</b>
                  <span style={{ fontSize: 13, color: "var(--text-2)" }}>{disabled ? "그림 선지 문항은 ‘조건 변형’을 쓰세요" : k.desc}</span>
                </label>
              );
            })}
          </Stack>
          {kind === "numeric" && (
            <Row>
              <span>문항 수</span>
              <NumberInput value={count} min={1} max={6} step={1} onChange={(v) => setCount(Math.round(v || 1))} />
              <span>분모 상한</span>
              <NumberInput value={maxDen} min={1} max={1000} step={1} onChange={(v) => setMaxDen(Math.round(v || 1))} />
              <Check checked={sqrt} onChange={setSqrt}>
                √n 꼴 답 허용
              </Check>
            </Row>
          )}
          {kind === "novel_b" && <ExtensionForm examId={id} number={number} ext={ext} setExt={setExt} />}
          <Row>
            <Button variant="primary" onClick={start} disabled={job.running || (kind === "novel_b" && (!ext.name || !ext.definition))}>
              만들기
            </Button>
            <span style={{ fontSize: 12.5, color: "var(--text-3)" }}>AI가 초안 → 앱이 검사 → 다른 세션이 독립으로 풀어 답 확인 → 루브릭 초안 (몇 분 걸림)</span>
          </Row>
          <JobProgress job={job.job} />
          <ErrorBox error={job.error} />
        </Stack>
      </Card>

      {sets.some((s) => s.status === "approved") && (
        <Card title="승인한 제작물로 시험 만들기">
          <Row>
            <div style={{ flex: 1, minWidth: 220 }}>
              <TextInput value={title} onChange={setTitle} />
            </div>
            <Button variant="primary" disabled={!selected.length || !title.trim()} onClick={bundle}>
              선택한 {selected.length}개로 새 시험
            </Button>
          </Row>
          <ErrorBox error={err} />
        </Card>
      )}

      <ErrorBox error={list.error} />
      {list.data && sets.length === 0 && <Empty>아직 만든 문항이 없습니다.</Empty>}
      {sets.map((s) => (
        <SetCard
          key={s.id}
          s={s}
          scope={scope}
          selected={selected.includes(s.id)}
          onSelect={(v) => setSelected(v ? [...selected, s.id] : selected.filter((x) => x !== s.id))}
          onChanged={list.reload}
        />
      ))}
    </Stack>
  );
}
