import { useEffect, useState } from "react";
import {
  Badge,
  Loading,
  Button,
  Card,
  Empty,
  ErrorBox,
  Field,
  Info,
  JobProgress,
  Row,
  Stack,
  TextArea,
  TextInput,
  tableClass,
  Notice,
  Select,
} from "../components/ui";
import { api, errorText, useJob, useLoad } from "../lib/api";
import { go } from "../lib/router";
import { UnitTreeNav } from "../components/UnitTree";
import type { Concept, Scope, ScopeSummary, Skill, UnitTree } from "../lib/types";
import { unitLabel, walk } from "../lib/units";

function FromTextbook({ onDone, trees }: { onDone: (id: string) => void; trees: UnitTree[] }) {
  const [file, setFile] = useState<File | null>(null);
  const [unit, setUnit] = useState("");
  const [title, setTitle] = useState("");
  const [level, setLevel] = useState("");
  const [subject, setSubject] = useState("");
  const [pages, setPages] = useState("");
  const job = useJob((j) => onDone((j.result as { scope_id: string }).scope_id));
  const start = () => {
    const f = new FormData();
    f.append("file", file!);
    f.append("title", title);
    f.append("level", level);
    if (subject) f.append("subject", subject);
    if (pages) f.append("pages", pages);
    if (unit) f.append("unit_ids", unit);
    void job.run(() => api.upload("/scopes/from-textbook", f));
  };
  return (
    <Card title="교재 PDF에서 만들기">
      <Stack>
        <span style={{ color: "var(--text-2)" }}>
          교재 단원에서 개념·풀이 기법·금지 기법 후보·답안 관례를 뽑아 초안을 만듭니다. 만든 뒤 반드시 검토하세요.
        </span>
        <Row>
          <input type="file" accept="application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </Row>
        <Row>
          <div style={{ flex: 2, minWidth: 200 }}>
            <Field label="단원 제목">
              <TextInput value={title} onChange={setTitle} placeholder="이차함수의 최대·최소" />
            </Field>
          </div>
          <div style={{ flex: 1, minWidth: 160 }}>
            <Field label="학년·과정">
              <TextInput value={level} onChange={setLevel} placeholder="고등학교 1학년" />
            </Field>
          </div>
          <div style={{ flex: 1, minWidth: 120 }}>
            <Field label="과목 (선택)">
              <TextInput value={subject} onChange={setSubject} placeholder="대수" />
            </Field>
          </div>
          <div style={{ flex: 1, minWidth: 120 }}>
            <Field label="쪽 (선택)">
              <TextInput value={pages} onChange={setPages} placeholder="12-20" />
            </Field>
          </div>
        </Row>
        <Field label="공통 단원표의 단원 (선택)" hint="고르면 '단원으로 만들기'에서 이 교재 단원의 개념이 쓰입니다">
          <Select value={unit} onChange={setUnit} options={[
            { value: "", label: "고르지 않음" },
            ...trees.flatMap((t) => walk(t).map(({ unit: u, path }) => ({
              value: u.id, label: [t.subject, ...path, u.title].join(" > "),
            }))),
          ]} />
        </Field>
        <Row>
          <Button variant="primary" onClick={start} disabled={!file || !title || !level || job.running}>
            범위 프로필 초안 만들기
          </Button>
        </Row>
        <JobProgress job={job.job} />
        <ErrorBox error={job.error} />
      </Stack>
    </Card>
  );
}

function FromUnits({ trees, onDone }: { trees: UnitTree[]; onDone: (id: string) => void }) {
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [title, setTitle] = useState("");
  const [level, setLevel] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [res, setRes] = useState<{ scope_id: string; used: string[]; warnings: string[] } | null>(null);
  const make = () =>
    api
      .post<{ scope_id: string; used: string[]; warnings: string[] }>("/scopes/from-units", {
        unit_ids: [...checked], title, level: level || null,
      })
      .then(setRes)
      .catch((e: unknown) => setErr(errorText(e)));
  return (
    <Card title="단원으로 만들기 (여러 교재)">
      <Stack>
        <span style={{ color: "var(--text-2)", fontSize: 13 }}>
          공통 단원표에서 시험 범위의 단원을 체크하면, 그 단원으로 DB화한 교재들의 개념·기법·금지 기법·관례를 합쳐 범위
          프로필 하나를 만듭니다 (AI 호출 없음). 같은 이름의 개념은 하나로 합칩니다.
        </span>
        <div style={{ maxHeight: 360, overflow: "auto", border: "1px solid var(--line)", borderRadius: 8, padding: 6 }}>
          <UnitTreeNav trees={trees} checked={checked} onToggle={(u, on) => {
            const n = new Set(checked);
            if (on) n.add(u);
            else n.delete(u);
            setChecked(n);
          }} />
        </div>
        <Row>
          {[...checked].map((u) => <Badge key={u} tone="accent">{unitLabel(trees, u)}</Badge>)}
        </Row>
        <Row>
          <div style={{ flex: 2, minWidth: 200 }}>
            <Field label="범위 이름"><TextInput value={title} onChange={setTitle} placeholder="1학기 중간고사 범위" /></Field>
          </div>
          <div style={{ flex: 1, minWidth: 160 }}>
            <Field label="학년·과정 (선택)"><TextInput value={level} onChange={setLevel} placeholder="교재 프로필에서 가져옴" /></Field>
          </div>
        </Row>
        <Row>
          <Button variant="primary" onClick={make} disabled={checked.size === 0 || !title}>범위 프로필 만들기</Button>
        </Row>
        <ErrorBox error={err} />
        {res && (
          <Stack gap={6}>
            <Info>합친 교재 범위: {res.used.length ? res.used.join(", ") : "없음 (단원 이름만 개념으로)"}</Info>
            {res.warnings.map((w, i) => <Notice key={i}>{w}</Notice>)}
            <Row><Button small onClick={() => onDone(res.scope_id)}>만든 범위 열기 · 검토</Button></Row>
          </Stack>
        )}
      </Stack>
    </Card>
  );
}

export function ScopesPage() {
  const { data, error } = useLoad<ScopeSummary[]>("/scopes");
  const trees = useLoad<UnitTree[]>("/units");
  const [err, setErr] = useState<string | null>(null);
  const blank = () => {
    api
      .post<Scope>("/scopes", {
        title: "새 범위",
        level: "고등학교 1학년",
        concepts: [{ id: "C1", name: "개념 이름", summary: "핵심 내용" }],
      })
      .then((s) => go("scopes", s.id))
      .catch((e: unknown) => setErr(errorText(e)));
  };
  return (
    <Stack gap={16}>
      <Row>
        <h2>범위 프로필</h2>
        <span style={{ flex: 1 }} />
        <Button onClick={blank}>직접 만들기</Button>
      </Row>
      <span style={{ color: "var(--text-2)" }}>
        시험에서 쓸 수 있는 개념·기법과 쓰지 않는 기법(예: 미분)입니다. 해설·루브릭·채점이 모두 이 범위 안에서만 움직입니다.
      </span>
      <ErrorBox error={error ?? err} />
      {data && data.length === 0 && <Empty>아직 범위 프로필이 없습니다. 교재 PDF로 만들거나 직접 만드세요.</Empty>}
      {data?.map((s) => (
        <Card
          key={s.id}
          title={`${s.title}`}
          actions={<Button small onClick={() => go("scopes", s.id)}>열기</Button>}
        >
          <Row>
            <Badge>{s.level}</Badge>
            {s.subject && <Badge>{s.subject}</Badge>}
            <span>개념 {s.concepts} · 기법 {s.skills}</span>
            {s.forbidden.map((f) => (
              <Badge key={f} tone="bad">
                금지: {f}
              </Badge>
            ))}
            {trees.data && s.unit_ids?.map((u) => <Badge key={u} tone="accent">{unitLabel(trees.data!, u, true)}</Badge>)}
            <span style={{ color: "var(--text-3)" }}>v{s.version}</span>
          </Row>
        </Card>
      ))}
      {trees.data && <FromUnits trees={trees.data} onDone={(id) => go("scopes", id)} />}
      <FromTextbook onDone={(id) => go("scopes", id)} trees={trees.data ?? []} />
    </Stack>
  );
}

const nextId = (prefix: string, ids: string[]) => {
  const n = ids.map((i) => parseInt(i.slice(1), 10)).filter((x) => !isNaN(x));
  return `${prefix}${(n.length ? Math.max(...n) : 0) + 1}`;
};

export function ScopeEditPage({ id }: { id: string }) {
  const { data, error } = useLoad<Scope>(`/scopes/${id}`);
  const [s, setS] = useState<Scope | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => setS(data), [data]);
  if (error) return <ErrorBox error={error} />;
  if (!s) return <Loading />;
  const upd = (p: Partial<Scope>) => {
    setS({ ...s, ...p });
    setSaved(false);
  };
  const setConcept = (i: number, p: Partial<Concept>) =>
    upd({ concepts: s.concepts.map((c, k) => (k === i ? { ...c, ...p } : c)) });
  const setSkill = (i: number, p: Partial<Skill>) =>
    upd({ skills: s.skills.map((c, k) => (k === i ? { ...c, ...p } : c)) });
  const save = () => {
    setErr(null);
    api
      .put<Scope>(`/scopes/${id}`, s)
      .then((x) => {
        setS(x);
        setSaved(true);
      })
      .catch((e: unknown) => setErr(errorText(e)));
  };
  return (
    <Stack gap={16}>
      <Row>
        <Button variant="ghost" onClick={() => go("scopes")}>
          ← 범위 프로필
        </Button>
        <h2>{s.title}</h2>
        <Badge>v{s.version}</Badge>
        <span style={{ flex: 1 }} />
        {saved && <Badge tone="ok">저장됨</Badge>}
        <Button variant="primary" onClick={save}>
          저장
        </Button>
      </Row>
      <ErrorBox error={err} />
      <Card title="기본">
        <Row>
          <div style={{ flex: 2, minWidth: 200 }}>
            <Field label="제목">
              <TextInput value={s.title} onChange={(v) => upd({ title: v })} />
            </Field>
          </div>
          <div style={{ flex: 1, minWidth: 160 }}>
            <Field label="학년·과정">
              <TextInput value={s.level} onChange={(v) => upd({ level: v })} />
            </Field>
          </div>
          <div style={{ flex: 1, minWidth: 120 }}>
            <Field label="과목">
              <TextInput value={s.subject ?? ""} onChange={(v) => upd({ subject: v || null })} />
            </Field>
          </div>
        </Row>
      </Card>

      <Card
        title="허용 개념"
        actions={
          <Button small onClick={() => upd({ concepts: [...s.concepts, { id: nextId("C", s.concepts.map((c) => c.id)), name: "", summary: "" }] })}>
            + 개념
          </Button>
        }
      >
        <table className={tableClass}>
          <thead>
            <tr>
              <th style={{ width: 50 }}>ID</th>
              <th style={{ width: 200 }}>이름</th>
              <th>핵심 내용 (공식·정의 — AI에게 그대로 전달)</th>
              <th style={{ width: 110 }}>출처</th>
              <th style={{ width: 40 }} />
            </tr>
          </thead>
          <tbody>
            {s.concepts.map((c, i) => (
              <tr key={c.id}>
                <td>
                  <b>{c.id}</b>
                </td>
                <td>
                  <TextInput value={c.name} onChange={(v) => setConcept(i, { name: v })} />
                </td>
                <td>
                  <TextArea value={c.summary} onChange={(v) => setConcept(i, { summary: v })} rows={2} />
                </td>
                <td>
                  <TextInput value={c.source ?? ""} onChange={(v) => setConcept(i, { source: v || null })} />
                </td>
                <td>
                  <Button small variant="ghost" onClick={() => upd({ concepts: s.concepts.filter((_, k) => k !== i) })}>
                    ✕
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card
        title="허용 풀이 기법"
        actions={
          <Button small onClick={() => upd({ skills: [...s.skills, { id: nextId("S", s.skills.map((c) => c.id)), name: "", description: "", concept_ids: [] }] })}>
            + 기법
          </Button>
        }
      >
        <table className={tableClass}>
          <thead>
            <tr>
              <th style={{ width: 50 }}>ID</th>
              <th style={{ width: 200 }}>이름</th>
              <th>설명</th>
              <th style={{ width: 160 }}>관련 개념 (쉼표)</th>
              <th style={{ width: 40 }} />
            </tr>
          </thead>
          <tbody>
            {s.skills.map((k, i) => (
              <tr key={k.id}>
                <td>
                  <b>{k.id}</b>
                </td>
                <td>
                  <TextInput value={k.name} onChange={(v) => setSkill(i, { name: v })} />
                </td>
                <td>
                  <TextArea value={k.description} onChange={(v) => setSkill(i, { description: v })} rows={2} />
                </td>
                <td>
                  <TextInput
                    value={k.concept_ids.join(", ")}
                    onChange={(v) => setSkill(i, { concept_ids: v.split(",").map((x) => x.trim()).filter(Boolean) })}
                    mono
                  />
                </td>
                <td>
                  <Button small variant="ghost" onClick={() => upd({ skills: s.skills.filter((_, x) => x !== i) })}>
                    ✕
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card
        title="쓰지 않는 기법 (범위 밖)"
        actions={<Button small onClick={() => upd({ forbidden: [...s.forbidden, { name: "", reason: "" }] })}>+ 추가</Button>}
      >
        <Stack>
          <Info>
            여기 적은 기법은 해설에 쓰이지 않고, “이 기법을 안 썼다”는 이유로 감점되지 않습니다. 채점 공정성의 기준입니다.
          </Info>
          {s.forbidden.map((f, i) => (
            <Row key={i}>
              <div style={{ width: 200 }}>
                <TextInput
                  value={f.name}
                  placeholder="미분"
                  onChange={(v) => upd({ forbidden: s.forbidden.map((x, k) => (k === i ? { ...x, name: v } : x)) })}
                />
              </div>
              <div style={{ flex: 1 }}>
                <TextInput
                  value={f.reason ?? ""}
                  placeholder="이유 (예: 고1 범위 밖)"
                  onChange={(v) => upd({ forbidden: s.forbidden.map((x, k) => (k === i ? { ...x, reason: v || null } : x)) })}
                />
              </div>
              <Button small variant="ghost" onClick={() => upd({ forbidden: s.forbidden.filter((_, k) => k !== i) })}>
                ✕
              </Button>
            </Row>
          ))}
        </Stack>
      </Card>

      <Card title="답안 관례" actions={<Button small onClick={() => upd({ conventions: [...s.conventions, ""] })}>+ 추가</Button>}>
        <Stack>
          {s.conventions.map((c, i) => (
            <Row key={i}>
              <div style={{ flex: 1 }}>
                <TextInput
                  value={c}
                  placeholder="예: 최솟값을 구할 때는 그때의 x값을 함께 쓴다"
                  onChange={(v) => upd({ conventions: s.conventions.map((x, k) => (k === i ? v : x)) })}
                />
              </div>
              <Button small variant="ghost" onClick={() => upd({ conventions: s.conventions.filter((_, k) => k !== i) })}>
                ✕
              </Button>
            </Row>
          ))}
        </Stack>
      </Card>
    </Stack>
  );
}
