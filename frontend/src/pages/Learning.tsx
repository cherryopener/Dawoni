import { useState } from "react";
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
  Row,
  Select,
  Stack,
  TextArea,
  TextInput,
  teacherName,
} from "../components/ui";
import { api, errorText, useJob, useLoad } from "../lib/api";
import type { MemoryList, MemoryNote, RuleScope, RuleTarget, AgentSkill } from "../lib/types";
import { TARGET_LABELS, TargetPicker } from "./Rules";

const TARGET_OPTIONS = [
  { value: "", label: "정하지 않음 (AI가 판단)" },
  ...(Object.keys(TARGET_LABELS) as RuleScope[])
    .filter((t) => t !== "all")
    .map((t) => ({ value: t, label: TARGET_LABELS[t] })),
];

const words = (s: string) =>
  s
    .split(",")
    .map((w) => w.trim())
    .filter(Boolean);

function Targets({ value }: { value: RuleScope[] }) {
  return (
    <>
      {value.map((t) => (
        <Badge key={t} tone="accent">
          {TARGET_LABELS[t]}
        </Badge>
      ))}
    </>
  );
}

/** 강사의 말·수정 → 메모리 또는 스킬 제안. */
function Teach({ onDone }: { onDone: () => void }) {
  const [note, setNote] = useState("");
  const [target, setTarget] = useState("");
  const [situation, setSituation] = useState("");
  const [before, setBefore] = useState("");
  const [after, setAfter] = useState("");
  const [more, setMore] = useState(false);
  const [reason, setReason] = useState<string | null>(null);
  const job = useJob((j) => {
    const r = j.result as { lesson: { kind: string; reason: string }; saved_id: string | null };
    setReason(r.saved_id ? `제안을 만들었습니다 — ${r.lesson.reason}` : `배울 것이 없었습니다 — ${r.lesson.reason}`);
    if (r.saved_id) {
      setNote("");
      setBefore("");
      setAfter("");
    }
    onDone();
  });
  const start = () => {
    setReason(null);
    void job.run(() =>
      api.post("/learn", {
        note,
        before,
        after,
        situation,
        target: target || null,
        by: teacherName() || null,
      }),
    );
  };
  return (
    <Card title="다워니에게 가르치기">
      <Stack>
        <Field
          label="앞으로 이렇게 해 줘"
          hint="예: 김승규 교재는 소문항을 (가), (나)로 나눠서 / 물리 채널 강의 요약은 개념 → 공식 → 예제 순서로"
        >
          <TextArea value={note} onChange={setNote} rows={2} />
        </Field>
        <Row>
          <Field label="어떤 작업에서">
            <Select value={target} onChange={setTarget} options={TARGET_OPTIONS} />
          </Field>
          <Field label="어떤 상황에서" hint="교재 이름, 출판사, 유튜브 채널, 시험 이름 등">
            <TextInput value={situation} onChange={setSituation} />
          </Field>
        </Row>
        <Check checked={more} onChange={setMore}>
          AI 결과와 고친 결과를 붙여 넣기
        </Check>
        {more && (
          <Row>
            <Field label="AI가 낸 결과">
              <TextArea value={before} onChange={setBefore} rows={5} />
            </Field>
            <Field label="내가 고친 결과">
              <TextArea value={after} onChange={setAfter} rows={5} />
            </Field>
          </Row>
        )}
        <Row>
          <Button variant="primary" disabled={!(note.trim() || after.trim()) || job.running} onClick={start}>
            메모리·스킬로 정리하기
          </Button>
        </Row>
        <JobProgress job={job.job} />
        <ErrorBox error={job.error} />
        {reason && <Info>{reason}</Info>}
      </Stack>
    </Card>
  );
}

function SkillEditor({ skill, onSave, onCancel }: {
  skill: Partial<AgentSkill>;
  onSave: (s: Partial<AgentSkill>) => void;
  onCancel: () => void;
}) {
  const [s, setS] = useState(skill);
  const [triggers, setTriggers] = useState((skill.triggers ?? []).join(", "));
  return (
    <Stack>
      <Field label="이름">
        <TextInput value={s.name ?? ""} onChange={(v) => setS({ ...s, name: v })} />
      </Field>
      <Field label="설명" hint="무엇을, 언제 쓰는 스킬인지">
        <TextInput value={s.description ?? ""} onChange={(v) => setS({ ...s, description: v })} />
      </Field>
      <Field label="적용할 작업">
        <TargetPicker value={s.applies_to ?? []} onChange={(v) => setS({ ...s, applies_to: v })} />
      </Field>
      <Field label="상황 단어 (쉼표로 구분)" hint="하나라도 자료 이름·강의 제목·채널·시험 이름에 들어 있으면 적용. 비우면 그 작업마다 적용">
        <TextInput value={triggers} onChange={setTriggers} />
      </Field>
      <Field label="본문 (AI에게 그대로 전달)">
        <TextArea value={s.body ?? ""} onChange={(v) => setS({ ...s, body: v })} rows={8} />
      </Field>
      <Row>
        <Button
          variant="primary"
          disabled={!s.name?.trim() || !s.description?.trim() || !s.body?.trim() || !s.applies_to?.length}
          onClick={() => onSave({ ...s, triggers: words(triggers) })}
        >
          저장
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          취소
        </Button>
      </Row>
    </Stack>
  );
}

function SkillCard({ skill, all, onChanged, setErr }: {
  skill: AgentSkill;
  all: AgentSkill[];
  onChanged: () => void;
  setErr: (e: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [open, setOpen] = useState(skill.proposed);
  const fail = (e: unknown) => setErr(errorText(e));
  const act = (p: Promise<unknown>) => p.then(onChanged).catch(fail);
  const revises = skill.revises ? all.find((x) => x.id === skill.revises) : undefined;
  return (
    <Card
      title={
        <Row>
          {skill.proposed && <Badge tone="warn">{revises ? `“${revises.name}” 고치기 제안` : "새 스킬 제안"}</Badge>}
          {skill.name}
          <Targets value={skill.applies_to} />
        </Row>
      }
      actions={
        <Row>
          {skill.proposed ? (
            <Button small variant="primary" onClick={() => void act(api.post(`/skills/${skill.id}/approve`))}>
              승인
            </Button>
          ) : (
            <Check checked={skill.enabled} onChange={(on) => void act(api.patch(`/skills/${skill.id}`, { enabled: on }))}>
              {skill.enabled ? "켜짐" : "꺼짐"}
            </Check>
          )}
          <Button small onClick={() => setOpen(!open)}>
            {open ? "접기" : "본문"}
          </Button>
          <Button small onClick={() => setEditing(true)}>
            고치기
          </Button>
          <Button
            small
            variant="danger"
            onClick={() => confirm(`스킬 “${skill.name}”을 ${skill.proposed ? "버릴" : "지울"}까요?`) && void act(api.del(`/skills/${skill.id}`))}
          >
            {skill.proposed ? "버리기" : "삭제"}
          </Button>
        </Row>
      }
    >
      {editing ? (
        <SkillEditor
          skill={skill}
          onCancel={() => setEditing(false)}
          onSave={(s) => {
            const { name, description, applies_to, triggers, body } = s;
            void act(api.patch(`/skills/${skill.id}`, { name, description, applies_to, triggers, body })).then(() =>
              setEditing(false),
            );
          }}
        />
      ) : (
        <div style={{ opacity: skill.enabled || skill.proposed ? 1 : 0.55 }}>
          <div>{skill.description}</div>
          <div style={{ fontSize: 12, color: "var(--text-3)", marginTop: 4 }}>
            {skill.triggers.length ? `상황 단어: ${skill.triggers.join(", ")}` : "상황 단어 없음 — 해당 작업마다 적용"}
          </div>
          {open && <pre style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>{skill.body}</pre>}
          {skill.request && <div style={{ fontSize: 12, color: "var(--text-3)", marginTop: 6 }}>배운 곳: {skill.request}</div>}
        </div>
      )}
    </Card>
  );
}

function MemoryCard({ note, all, onChanged, setErr }: {
  note: MemoryNote;
  all: MemoryNote[];
  onChanged: () => void;
  setErr: (e: string) => void;
}) {
  const act = (p: Promise<unknown>) => p.then(onChanged).catch((e: unknown) => setErr(errorText(e)));
  const merged = note.replaces.map((id) => all.find((n) => n.id === id)?.text ?? id);
  return (
    <Card
      title={
        <Row>
          {note.proposed && <Badge tone="warn">{merged.length ? "합치기 제안" : "새 메모 제안"}</Badge>}
          <Targets value={note.applies_to} />
        </Row>
      }
      actions={
        <Row>
          {note.proposed ? (
            <Button small variant="primary" onClick={() => void act(api.post(`/memory/${note.id}/approve`))}>
              승인
            </Button>
          ) : (
            <Check checked={note.enabled} onChange={(on) => void act(api.patch(`/memory/${note.id}`, { enabled: on }))}>
              {note.enabled ? "켜짐" : "꺼짐"}
            </Check>
          )}
          <Button small variant="danger" onClick={() => void act(api.del(`/memory/${note.id}`))}>
            {note.proposed ? "버리기" : "삭제"}
          </Button>
        </Row>
      }
    >
      <div style={{ opacity: note.enabled || note.proposed ? 1 : 0.55 }}>{note.text}</div>
      {merged.length > 0 && (
        <div style={{ fontSize: 12, color: "var(--text-3)", marginTop: 6 }}>승인하면 대신 지울 메모: {merged.join(" / ")}</div>
      )}
      {note.request && <div style={{ fontSize: 12, color: "var(--text-3)", marginTop: 6 }}>배운 곳: {note.request}</div>}
    </Card>
  );
}

export function LearningPage() {
  const memory = useLoad<MemoryList>("/memory");
  const skills = useLoad<AgentSkill[]>("/skills");
  const [err, setErr] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const reload = () => {
    setErr(null);
    memory.reload();
    skills.reload();
  };
  const notes = memory.data?.notes ?? [];
  const allSkills = skills.data ?? [];
  const proposals = [...notes.filter((n) => n.proposed), ...allSkills.filter((s) => s.proposed)];
  const used = memory.data
    ? (Object.entries(memory.data.usage) as [RuleTarget, number][]).filter(([, n]) => n > 0)
    : [];
  return (
    <Stack gap={16}>
      <h2>메모리 · 스킬</h2>
      <span style={{ color: "var(--text-2)" }}>
        <b>메모리</b>는 다워니가 배운 짧은 메모로, 해당 작업마다 들어갑니다 (작업마다 {memory.data?.limit ?? "…"}자까지).{" "}
        <b>스킬</b>은 상황별 작업 방식으로, 상황 단어가 자료 이름·강의 제목·채널·시험 이름에 맞을 때만 들어가 토큰을
        아낍니다. 다워니가 배운 것은 승인해야 적용됩니다.
      </span>
      <ErrorBox error={memory.error ?? skills.error ?? err} />
      <Teach onDone={reload} />

      {proposals.length > 0 && <h3>승인 대기 ({proposals.length})</h3>}
      {notes
        .filter((n) => n.proposed)
        .map((n) => (
          <MemoryCard key={n.id} note={n} all={notes} onChanged={reload} setErr={setErr} />
        ))}
      {allSkills
        .filter((s) => s.proposed)
        .map((s) => (
          <SkillCard key={s.id} skill={s} all={allSkills} onChanged={reload} setErr={setErr} />
        ))}

      <h3>메모리</h3>
      {used.length > 0 && (
        <span style={{ fontSize: 13, color: "var(--text-2)" }}>
          사용: {used.map(([t, n]) => `${TARGET_LABELS[t]} ${n}/${memory.data?.limit}자`).join(" · ")}
        </span>
      )}
      {memory.data && !notes.some((n) => !n.proposed) && <Empty>아직 배운 메모가 없습니다.</Empty>}
      {notes
        .filter((n) => !n.proposed)
        .map((n) => (
          <MemoryCard key={n.id} note={n} all={notes} onChanged={reload} setErr={setErr} />
        ))}

      <h3>스킬</h3>
      {skills.data && !allSkills.some((s) => !s.proposed) && <Empty>아직 스킬이 없습니다.</Empty>}
      {allSkills
        .filter((s) => !s.proposed)
        .map((s) => (
          <SkillCard key={s.id} skill={s} all={allSkills} onChanged={reload} setErr={setErr} />
        ))}
      {adding ? (
        <Card title="스킬 직접 쓰기">
          <SkillEditor
            skill={{ applies_to: [], triggers: [] }}
            onCancel={() => setAdding(false)}
            onSave={(s) =>
              void api
                .post("/skills", { ...s, by: teacherName() || null })
                .then(() => {
                  setAdding(false);
                  reload();
                })
                .catch((e: unknown) => setErr(errorText(e)))
            }
          />
        </Card>
      ) : (
        <Row>
          <Button onClick={() => setAdding(true)}>스킬 직접 쓰기</Button>
        </Row>
      )}
    </Stack>
  );
}
