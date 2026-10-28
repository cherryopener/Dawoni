import { useState } from "react";
import {
  Badge,
  Button,
  Card,
  Check,
  ErrorBox,
  Field,
  Info,
  Loading,
  Row,
  Select,
  Stack,
  TextArea,
  TextInput,
  teacherName,
} from "../../components/ui";
import { api, errorText, useLoad } from "../../lib/api";
import type { Unit, UnitTree } from "../../lib/types";
import { walk } from "../../lib/units";

let seq = 0;
/** 새 단원 ID: 위 단원 ID + 겹치지 않는 꼬리 (이름을 바꿔도 ID는 그대로). */
const newId = (parent: string) => `${parent}.n${Date.now().toString(36)}${(seq++).toString(36)}`;

function mapUnits(units: Unit[], f: (u: Unit, siblings: Unit[]) => Unit[] | Unit): Unit[] {
  return units.flatMap((u) => {
    const r = f({ ...u, children: mapUnits(u.children, f) }, units);
    return Array.isArray(r) ? r : [r];
  });
}

export function UnitsTab() {
  const { data, error, reload } = useLoad<UnitTree[]>("/units");
  const [sel, setSel] = useState<string | null>(null);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const tree = data.find((t) => t.id === sel);
  return (
    <div style={{ display: "grid", gridTemplateColumns: "260px 1fr", gap: 14, alignItems: "start" }}>
      <Stack gap={8}>
        {data.map((t) => (
          <Button key={t.id} variant={t.id === sel ? "primary" : "default"} onClick={() => setSel(t.id)}>
            {t.subject} {!t.reviewed && <Badge tone="warn">초안</Badge>}
          </Button>
        ))}
        <Button variant="ghost" onClick={() => setSel("__new")}>+ 새 단원표 (목차 붙여 넣기)</Button>
      </Stack>
      {sel === "__new" ? (
        <NewTree onDone={(id) => { reload(); setSel(id); }} />
      ) : tree ? (
        <TreeEditor key={`${tree.id}-${tree.version}`} tree={tree} onSaved={reload} />
      ) : (
        <Info>
          단원표는 과목마다 하나이고, 교재·문제집·기출의 문항이 모두 이 단원으로 모입니다. 기본 단원표는 2015 개정
          과학 I·II 초안이라 학원 교재에 맞게 고쳐 쓰세요. 이름을 바꿔도 문항 분류는 그대로입니다.
        </Info>
      )}
    </div>
  );
}

function TreeEditor({ tree, onSaved }: { tree: UnitTree; onSaved: () => void }) {
  const [t, setT] = useState<UnitTree>(tree);
  const [err, setErr] = useState<string | null>(null);
  const [mergeFrom, setMergeFrom] = useState("");
  const [mergeInto, setMergeInto] = useState("");
  const dirty = JSON.stringify(t) !== JSON.stringify(tree);
  const setUnits = (units: Unit[]) => setT({ ...t, units });
  const edit = (id: string, f: (u: Unit, siblings: Unit[]) => Unit[] | Unit) =>
    setUnits(mapUnits(t.units, (u, sib) => (u.id === id ? f(u, sib) : u)));
  const move = (id: string, d: -1 | 1) => {
    const go = (list: Unit[]): Unit[] => {
      const i = list.findIndex((u) => u.id === id);
      if (i >= 0) {
        const j = i + d;
        if (j < 0 || j >= list.length) return list;
        const n = [...list];
        const [a, b] = [n[i]!, n[j]!];
        n[i] = b;
        n[j] = a;
        return n;
      }
      return list.map((u) => ({ ...u, children: go(u.children) }));
    };
    setUnits(go(t.units));
  };
  const save = () =>
    api
      .put(`/units/${t.id}`, { tree: t, base_version: tree.version, by: teacherName() || null })
      .then(onSaved)
      .catch((e: unknown) => setErr(errorText(e)));
  const merge = () =>
    api
      .post(`/units/${t.id}/merge`, { source: mergeFrom, into: mergeInto, base_version: tree.version, by: teacherName() || null })
      .then(onSaved)
      .catch((e: unknown) => setErr(errorText(e)));

  const rows = (list: Unit[], depth: number): React.ReactNode[] =>
    list.flatMap((u) => [
      <Row key={u.id} style={{ paddingLeft: depth * 18, gap: 4 }}>
        <div style={{ flex: 1, minWidth: 160 }}>
          <TextInput value={u.title} onChange={(v) => edit(u.id, (x) => ({ ...x, title: v }))} />
        </div>
        <Button small variant="ghost" onClick={() => move(u.id, -1)} title="위로">↑</Button>
        <Button small variant="ghost" onClick={() => move(u.id, 1)} title="아래로">↓</Button>
        <Button small variant="ghost" title="아래 단원 추가"
                onClick={() => edit(u.id, (x) => ({ ...x, children: [...x.children, { id: newId(x.id), title: "새 단원", children: [] }] }))}>
          + 아래
        </Button>
        <Button small variant="ghost" title="지우기 (문항이 있는 단원은 저장할 때 거부됩니다 — 합치기를 쓰세요)"
                onClick={() => edit(u.id, () => [])}>×</Button>
      </Row>,
      ...rows(u.children, depth + 1),
    ]);
  const options = [{ value: "", label: "고르세요" }, ...walk(t).map(({ unit, path }) => ({
    value: unit.id, label: [...path, unit.title].join(" > "),
  }))];
  return (
    <Card
      title={<>{t.subject} {t.curriculum && <Badge>{t.curriculum}</Badge>}</>}
      actions={<Button variant="primary" small onClick={save} disabled={!dirty}>저장</Button>}
    >
      <Stack gap={10}>
        <Row>
          <div style={{ width: 200 }}><Field label="과목 이름"><TextInput value={t.subject} onChange={(v) => setT({ ...t, subject: v })} /></Field></div>
          <div style={{ width: 140 }}><Field label="과목군"><TextInput value={t.group} onChange={(v) => setT({ ...t, group: v })} /></Field></div>
          <Check checked={t.reviewed} onChange={(v) => setT({ ...t, reviewed: v })}>검토 완료</Check>
          <span style={{ fontSize: 12.5, color: "var(--text-2)" }}>
            판 {tree.version}{tree.updated_by ? ` · ${tree.updated_by}` : ""}
          </span>
        </Row>
        {rows(t.units, 0)}
        <Row>
          <Button small variant="ghost" onClick={() => setUnits([...t.units, { id: newId(t.id), title: "새 단원", children: [] }])}>
            + 대단원
          </Button>
        </Row>
        <Card title="단원 합치기">
          <Stack gap={8}>
            <Info>문항이 들어 있는 단원은 지우지 말고 합치세요. 합친 단원의 문항과 아래 단원은 새 단원으로 옮겨집니다.</Info>
            <Row>
              <Select value={mergeFrom} onChange={setMergeFrom} options={options} />
              <span>→</span>
              <Select value={mergeInto} onChange={setMergeInto} options={options} />
              <Button small onClick={merge} disabled={!mergeFrom || !mergeInto || dirty}
                      title={dirty ? "고친 것을 먼저 저장하세요" : undefined}>합치기</Button>
            </Row>
          </Stack>
        </Card>
        <ErrorBox error={err} />
      </Stack>
    </Card>
  );
}

function NewTree({ onDone }: { onDone: (id: string) => void }) {
  const [id, setId] = useState("");
  const [subject, setSubject] = useState("");
  const [group, setGroup] = useState("물리");
  const [toc, setToc] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const make = () =>
    api
      .post<UnitTree>("/units", { id, subject, group, toc, by: teacherName() || null })
      .then((t) => onDone(t.id))
      .catch((e: unknown) => setErr(errorText(e)));
  return (
    <Card title="새 단원표">
      <Stack>
        <Row>
          <div style={{ width: 160 }}><Field label="ID (영문 소문자·숫자)"><TextInput value={id} onChange={(v) => setId(v.toLowerCase())} placeholder="gift" mono /></Field></div>
          <div style={{ width: 200 }}><Field label="과목 이름"><TextInput value={subject} onChange={setSubject} placeholder="영재 물리" /></Field></div>
          <div style={{ width: 140 }}><Field label="과목군"><TextInput value={group} onChange={setGroup} /></Field></div>
        </Row>
        <Field label="목차 붙여 넣기 (선택)" hint="Ⅰ. / 1. / (1) / ① 처럼 번호 모양이 다른 줄이 아래 단원이 됩니다. 끝의 쪽수는 지웁니다.">
          <TextArea value={toc} onChange={setToc} rows={10} placeholder={"Ⅰ. 역학과 에너지\n  1. 힘과 운동\n    (1) 물체의 운동\n  2. 에너지와 열"} />
        </Field>
        <Row><Button variant="primary" onClick={make} disabled={!/^[a-z0-9]+$/.test(id) || !subject}>만들기</Button></Row>
        <ErrorBox error={err} />
      </Stack>
    </Card>
  );
}
