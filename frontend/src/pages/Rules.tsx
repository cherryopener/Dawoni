import { useState } from "react";
import {
  Badge,
  Button,
  Card,
  Check,
  Empty,
  ErrorBox,
  Field,
  JobProgress,
  Notice,
  Row,
  Stack,
  TextArea,
  TextInput,
  teacherName,
} from "../components/ui";
import { api, errorText, useJob, useLoad } from "../lib/api";
import type { GlobalRule, RefinedRule, RuleScope } from "../lib/types";

export const TARGET_LABELS: Record<RuleScope, string> = {
  all: "모든 작업",
  solve: "해설",
  rubric: "루브릭",
  transcribe: "손글씨 판독",
  grade: "채점",
  feedback: "첨삭",
  ingest: "자료 DB화",
  variant: "동형문제",
  lecture: "강의 요약",
};
const TARGETS = Object.keys(TARGET_LABELS) as RuleScope[];

function TargetPicker({ value, onChange }: { value: RuleScope[]; onChange: (v: RuleScope[]) => void }) {
  return (
    <Row>
      {TARGETS.map((t) => (
        <Check
          key={t}
          checked={value.includes(t)}
          onChange={(on) => {
            if (t === "all") onChange(on ? ["all"] : []);
            else onChange(on ? [...value.filter((x) => x !== "all"), t] : value.filter((x) => x !== t));
          }}
        >
          {TARGET_LABELS[t]}
        </Check>
      ))}
    </Row>
  );
}

function NewRule({ rules, onSaved }: { rules: GlobalRule[]; onSaved: () => void }) {
  const [request, setRequest] = useState("");
  const [draft, setDraft] = useState<RefinedRule | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refine = useJob((j) => setDraft((j.result as { draft: RefinedRule }).draft));
  const save = () => {
    if (!draft) return;
    api
      .post("/rules", { request, draft: { ...draft, conflicts: [] , clarification_needed: null }, by: teacherName() || null })
      .then(() => {
        setDraft(null);
        setRequest("");
        onSaved();
      })
      .catch((e: unknown) => setError(errorText(e)));
  };
  const title = (id: string) => rules.find((r) => r.id === id)?.title ?? id;
  return (
    <Card title="새 규칙">
      <Stack>
        <Field label="원하는 것을 평소 말로" hint="예: 첨삭은 존댓말로, 두 문장 이내로 / 단위를 안 쓰면 1점 감점">
          <TextArea value={request} onChange={setRequest} rows={2} />
        </Field>
        <Row>
          <Button
            variant="primary"
            disabled={!request.trim() || refine.running}
            onClick={() => void refine.run(() => api.post("/rules/refine", { request }))}
          >
            AI로 규칙 문장 다듬기
          </Button>
        </Row>
        <JobProgress job={refine.job} />
        <ErrorBox error={refine.error ?? error} />
        {draft && (
          <Stack>
            {draft.clarification_needed && <Notice>확인 필요: {draft.clarification_needed}</Notice>}
            {draft.conflicts.map((c) => (
              <Notice key={c.rule_id}>
                기존 규칙 “{title(c.rule_id)}”와 충돌: {c.reason}
              </Notice>
            ))}
            <Field label="제목">
              <TextInput value={draft.title} onChange={(v) => setDraft({ ...draft, title: v })} />
            </Field>
            <Field label="AI에게 전달되는 규칙 문장">
              <TextArea value={draft.text} onChange={(v) => setDraft({ ...draft, text: v })} rows={3} />
            </Field>
            <Field label="적용할 작업">
              <TargetPicker value={draft.applies_to} onChange={(v) => setDraft({ ...draft, applies_to: v })} />
            </Field>
            <Row>
              <Button variant="primary" onClick={save} disabled={!draft.applies_to.length}>
                이대로 저장
              </Button>
              <Button variant="ghost" onClick={() => setDraft(null)}>
                버리기
              </Button>
            </Row>
          </Stack>
        )}
      </Stack>
    </Card>
  );
}

export function RulesPage() {
  const { data, error, reload } = useLoad<GlobalRule[]>("/rules");
  const [err, setErr] = useState<string | null>(null);
  const toggle = (r: GlobalRule) =>
    api.patch(`/rules/${r.id}`, { enabled: !r.enabled }).then(reload).catch((e: unknown) => setErr(errorText(e)));
  const remove = (r: GlobalRule) => {
    if (!confirm(`규칙 “${r.title}”을 지울까요?`)) return;
    api.del(`/rules/${r.id}`).then(reload).catch((e: unknown) => setErr(errorText(e)));
  };
  return (
    <Stack gap={16}>
      <h2>전역 규칙</h2>
      <span style={{ color: "var(--text-2)" }}>
        켜 둔 규칙은 해당 작업의 모든 AI 요청에 들어갑니다. 끄면 바로 빠집니다.
      </span>
      <ErrorBox error={error ?? err} />
      {data && data.length === 0 && <Empty>아직 규칙이 없습니다.</Empty>}
      {data?.map((r) => (
        <Card
          key={r.id}
          title={
            <Row>
              {r.title}
              {r.applies_to.map((t) => (
                <Badge key={t} tone="accent">
                  {TARGET_LABELS[t]}
                </Badge>
              ))}
            </Row>
          }
          actions={
            <Row>
              <Check checked={r.enabled} onChange={() => void toggle(r)}>
                {r.enabled ? "켜짐" : "꺼짐"}
              </Check>
              <Button small variant="danger" onClick={() => remove(r)}>
                삭제
              </Button>
            </Row>
          }
        >
          <div style={{ opacity: r.enabled ? 1 : 0.55 }}>{r.text}</div>
          {r.request && <div style={{ fontSize: 12, color: "var(--text-3)", marginTop: 6 }}>처음 요청: {r.request}</div>}
        </Card>
      ))}
      {data && <NewRule rules={data} onSaved={reload} />}
    </Stack>
  );
}
