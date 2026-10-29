import { useMemo, useState } from "react";
import { FileActions } from "../components/FileActions";
import { ItemView } from "../components/Item";
import { Loose, Tex } from "../components/Math";
import { UnitTreeNav } from "../components/UnitTree";
import {
  Badge,
  Button,
  Card,
  Check,
  Empty,
  ErrorBox,
  Field,
  Info,
  Loading,
  Modal,
  Notice,
  Row,
  Select,
  Stack,
  Tabs,
  TextInput,
  teacherName,
} from "../components/ui";
import { api, errorText, useLoad } from "../lib/api";
import { go } from "../lib/router";
import type { BankItem, BankSummary, ExportResult, Scope, ScopeSummary, UnitTree } from "../lib/types";
import { bankFileUrl, SOURCE_KIND } from "../lib/bank";
import { descendants, resolve, unitCounts, unitLabel } from "../lib/units";
import { SolveButton } from "../components/SolveButton";
import { SolutionView } from "./exam/Rubrics";
import { SourcesTab } from "./bank/Sources";
import { UnitsTab } from "./bank/Units";

const ITEM_TYPE: Record<string, string> = { multiple_choice: "객관식", short_answer: "단답형", essay: "서술형" };
const LEVEL: Record<string, string> = {
  none: "원본 해설 없음",
  answer_only: "원본은 답만",
  brief: "원본 해설 짧음",
  full: "원본 해설 있음",
};

type Tab = "items" | "sources" | "units";

export function BankPage({ tab }: { tab?: string }) {
  const t = (["items", "sources", "units"].includes(tab ?? "") ? tab : "items") as Tab;
  return (
    <Stack gap={14}>
      <Row>
        <h2 style={{ margin: 0 }}>문제은행</h2>
        <span style={{ color: "var(--text-2)", fontSize: 13 }}>
          교재·문제집·기출을 단원별로 모아 둡니다. 함께 쓰는 작업공간이면 모든 강사에게 똑같이 보입니다.
        </span>
      </Row>
      <Tabs
        value={t}
        onChange={(v) => go("bank", v)}
        tabs={[
          { value: "items", label: "문항" },
          { value: "sources", label: "자료 추가 · DB화" },
          { value: "units", label: "단원표" },
        ]}
      />
      {t === "items" && <ItemsTab />}
      {t === "sources" && <SourcesTab />}
      {t === "units" && <UnitsTab />}
    </Stack>
  );
}

// ---------------------------------------------------------------- 문항 목록

type Status = "all" | "unsolved" | "check" | "ai" | "unreviewed" | "reviewed";

export function useBank() {
  const trees = useLoad<UnitTree[]>("/units");
  const items = useLoad<BankSummary[]>("/bank/items");
  return { trees, items };
}

/** 단원 트리 + 필터 + 문항 목록 (문제 만들기에서 기준 문항 고를 때도 쓴다). */
export function BankBrowser({ trees, items, picked, onPick, selected, onSelect }: {
  trees: UnitTree[];
  items: BankSummary[];
  picked?: string | null;
  onPick?: (id: string) => void;
  selected?: Set<string>;
  onSelect?: (ids: Set<string>) => void;
}) {
  const [unit, setUnit] = useState<string | null>(null);
  const [kind, setKind] = useState("all");
  const [itype, setItype] = useState("all");
  const [status, setStatus] = useState<Status>("all");
  const [q, setQ] = useState("");
  const counts = useMemo(() => unitCounts(trees, items.map((i) => i.unit_ids)), [trees, items]);
  const shown = useMemo(() => {
    const want = unit ? descendants(trees, unit) : null;
    return items.filter((i) => {
      if (want && !i.unit_ids.some((u) => want.has(resolve(trees, u)))) return false;
      if (kind !== "all" && i.source_kind !== kind) return false;
      if (itype !== "all" && i.item_type !== itype) return false;
      if (status === "unsolved" && i.solved) return false;
      if (status === "check" && !i.needs_check) return false;
      if (status === "ai" && i.solution_by !== "ai") return false;
      if (status === "unreviewed" && i.reviewed) return false;
      if (status === "reviewed" && !i.reviewed) return false;
      if (q && !`${i.preview} ${i.source}`.includes(q)) return false;
      return true;
    });
  }, [items, trees, unit, kind, itype, status, q]);
  const toggle = (id: string, on: boolean) => {
    if (!selected || !onSelect) return;
    const n = new Set(selected);
    if (on) n.add(id);
    else n.delete(id);
    onSelect(n);
  };
  return (
    <div style={{ display: "grid", gridTemplateColumns: "260px 1fr", gap: 14, alignItems: "start" }}>
      <Card title="단원">
        <UnitTreeNav trees={trees} counts={counts} selected={unit} onSelect={setUnit} />
      </Card>
      <Stack gap={10}>
        <Row>
          <div style={{ width: 150 }}><Select value={kind} onChange={setKind} options={[
            { value: "all", label: "자료 전체" },
            ...Object.entries(SOURCE_KIND).map(([value, label]) => ({ value, label })),
          ]} /></div>
          <div style={{ width: 120 }}><Select value={itype} onChange={setItype} options={[
            { value: "all", label: "유형 전체" },
            ...Object.entries(ITEM_TYPE).map(([value, label]) => ({ value, label })),
          ]} /></div>
          <div style={{ width: 140 }}><Select<Status> value={status} onChange={setStatus} options={[
            { value: "all", label: "상태 전체" },
            { value: "unsolved", label: "풀이 없음" },
            { value: "check", label: "답 확인 필요" },
            { value: "ai", label: "AI 해설" },
            { value: "unreviewed", label: "검토 전" },
            { value: "reviewed", label: "검토함" },
          ]} /></div>
          <div style={{ flex: 1, minWidth: 160 }}>
            <TextInput value={q} onChange={setQ} placeholder="문항·출처 검색" />
          </div>
        </Row>
        <Row>
          <span style={{ fontSize: 13, color: "var(--text-2)" }}>
            {unit ? unitLabel(trees, unit) : "모든 단원"} · {shown.length}문항
          </span>
          {selected && onSelect && shown.length > 0 && (
            <>
              <Button small variant="ghost" onClick={() => onSelect(new Set([...selected, ...shown.map((i) => i.id)]))}>
                보이는 문항 모두 고르기
              </Button>
              {selected.size > 0 && (
                <Button small variant="ghost" onClick={() => onSelect(new Set())}>
                  선택 해제 ({selected.size})
                </Button>
              )}
            </>
          )}
        </Row>
        {items.length === 0 ? (
          <Empty>아직 문항이 없습니다. "자료 추가 · DB화" 탭에서 교재·문제집·기출 PDF를 넣으세요.</Empty>
        ) : shown.length === 0 ? (
          <Empty>조건에 맞는 문항이 없습니다.</Empty>
        ) : (
          <Stack gap={6}>
            {shown.map((i) => (
              <div
                key={i.id}
                onClick={() => onPick?.(i.id)}
                style={{
                  display: "flex", gap: 10, alignItems: "flex-start", padding: "8px 10px", cursor: "pointer",
                  border: `1px solid ${picked === i.id ? "var(--accent)" : "var(--line)"}`,
                  borderRadius: 8, background: picked === i.id ? "var(--accent-soft)" : "var(--panel)",
                }}
              >
                {selected && (
                  <input type="checkbox" checked={selected.has(i.id)} onClick={(e) => e.stopPropagation()}
                         onChange={(e) => toggle(i.id, e.target.checked)} style={{ marginTop: 3 }} />
                )}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13.5 }}><Loose text={i.preview} /></div>
                  <Row style={{ marginTop: 4, gap: 6 }}>
                    <span style={{ fontSize: 12, color: "var(--text-2)" }}>{i.source}</span>
                    <Badge>{ITEM_TYPE[i.item_type]}</Badge>
                    {i.unit_ids[0] && <Badge tone="accent">{unitLabel(trees, i.unit_ids[0], true)}</Badge>}
                    {!i.solved && <Badge tone="warn">풀이 없음</Badge>}
                    {i.needs_check && <Badge tone="bad">답 확인 필요</Badge>}
                    {i.solution_by === "ai" && <Badge>AI 해설</Badge>}
                    {i.origin !== "ingested" && <Badge tone="ok">{i.origin === "variant" ? "동형" : "새 문제"}</Badge>}
                    {i.reviewed && <Badge tone="ok">검토함</Badge>}
                  </Row>
                </div>
              </div>
            ))}
          </Stack>
        )}
      </Stack>
    </div>
  );
}

function ItemsTab() {
  const { trees, items } = useBank();
  const [picked, setPicked] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  if (trees.error || items.error) return <ErrorBox error={trees.error ?? items.error} />;
  if (!trees.data || !items.data) return <Loading />;
  const chosen = items.data.filter((i) => selected.has(i.id));
  const ids = chosen.map((i) => i.id);
  return (
    <Stack gap={12}>
      {ids.length > 0 && (
        <SelectionBar ids={ids} unsolved={chosen.filter((i) => !i.solved).map((i) => i.id)}
                      onSolved={() => items.reload()} />
      )}
      <BankBrowser trees={trees.data} items={items.data} picked={picked} onPick={setPicked}
                   selected={selected} onSelect={setSelected} />
      {picked && (
        <ItemDetail id={picked} trees={trees.data} onClose={() => setPicked(null)}
                    onChanged={() => items.reload()} />
      )}
    </Stack>
  );
}

// ---------------------------------------------------------------- 고른 문항으로

function SelectionBar({ ids, unsolved, onSolved }: { ids: string[]; unsolved: string[]; onSolved: () => void }) {
  const [title, setTitle] = useState("단원 평가");
  const [withQ, setWithQ] = useState(false);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<ExportResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [toExam, setToExam] = useState(false);
  const make = (kind: "paper" | "key") => {
    setBusy(true);
    setErr(null);
    api
      .post<ExportResult>("/bank/export", { ids, kind, title, with_questions: withQ })
      .then(setRes)
      .catch((e: unknown) => setErr(errorText(e)))
      .finally(() => setBusy(false));
  };
  return (
    <Card title={`고른 문항 ${ids.length}개`}>
      <Stack gap={8}>
        <Row>
          <div style={{ width: 220 }}>
            <TextInput value={title} onChange={setTitle} placeholder="제목" />
          </div>
          <Button variant="primary" small onClick={() => make("paper")} disabled={busy}>문제지 Word</Button>
          <Button small onClick={() => make("key")} disabled={busy}>정답 및 해설 Word</Button>
          <Check checked={withQ} onChange={setWithQ}>해설에 문제도</Check>
          <span style={{ flex: 1 }} />
          {unsolved.length > 0 && (
            <SolveButton ids={unsolved} label={`풀이 없는 ${unsolved.length}문항 풀이 만들기`} onDone={onSolved} />
          )}
          <Button small onClick={() => setToExam(true)}>채점용 시험으로 만들기</Button>
          {ids.length === 1 && (
            <Button small onClick={() => go("make", "variant", ids[0] ?? "")}>이 문제로 동형 만들기</Button>
          )}
        </Row>
        <Info>학생용 문서라 출처(교재·쪽·원래 번호)는 넣지 않고 번호를 1번부터 다시 매깁니다.</Info>
        <ErrorBox error={err} />
        {res && (
          <Stack gap={6}>
            <Row>
              {res.ok ? <Badge tone="ok">자동 검사 통과</Badge> : <Badge tone="bad">자동 검사 실패</Badge>}
              <span style={{ fontSize: 13, color: "var(--text-2)" }}>{res.name} · {res.summary}</span>
            </Row>
            {res.warnings.map((w, i) => <Notice key={i}>{w}</Notice>)}
            <FileActions path={res.path} />
          </Stack>
        )}
      </Stack>
      {toExam && <ToExam ids={ids} title={title} onClose={() => setToExam(false)} />}
    </Card>
  );
}

function ToExam({ ids, title, onClose }: { ids: string[]; title: string; onClose: () => void }) {
  const scopes = useLoad<ScopeSummary[]>("/scopes");
  const [scope, setScope] = useState("");
  const [name, setName] = useState(title);
  const [err, setErr] = useState<string | null>(null);
  const make = () =>
    api
      .post<{ exam_id: string }>("/bank/to-exam", { ids, title: name, scope_id: scope })
      .then((r) => go("exams", r.exam_id))
      .catch((e: unknown) => setErr(errorText(e)));
  return (
    <Modal title="채점용 시험으로 만들기" onClose={onClose}
           actions={<Button variant="primary" onClick={make} disabled={!scope || !name}>만들기</Button>}>
      <Stack>
        <Field label="시험 제목"><TextInput value={name} onChange={setName} /></Field>
        <Field label="범위 프로필" hint="해설·루브릭·채점에 쓸 범위">
          <Select value={scope} onChange={setScope} options={[
            { value: "", label: "고르세요" },
            ...(scopes.data ?? []).map((s) => ({ value: s.id, label: `${s.title} (${s.level})` })),
          ]} />
        </Field>
        <Info>원본 정답·해설이 있는 문항은 시험의 해설지로 들어가 루브릭을 해설지 기준으로 만들 수 있습니다.</Info>
        <ErrorBox error={err} />
      </Stack>
    </Modal>
  );
}

// ---------------------------------------------------------------- 문항 하나

/** 단원표로 푼 AI 해설의 개념 ID(C…)를 단원 이름으로 보여 주는 범위. */
function unitScope(it: BankItem, labels: Record<string, string>): Scope {
  return {
    id: "units", title: "단원표", level: "", skills: [], forbidden: [], conventions: [], extensions: [],
    version: 1, updated_at: "",
    concepts: Object.entries(it.concept_units).map(([id, u]) => ({
      id, name: (labels[u] ?? u).split(" > ").pop() ?? u, summary: labels[u] ?? u,
    })),
  };
}

export function ItemDetail({ id, trees, onClose, onChanged }: {
  id: string;
  trees: UnitTree[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const { data, error, reload } = useLoad<{ item: BankItem; unit_labels: Record<string, string> }>(
    `/bank/items/${encodeURIComponent(id)}`,
  );
  const [err, setErr] = useState<string | null>(null);
  const [editUnits, setEditUnits] = useState(false);
  const save = (patch: Partial<BankItem>) => {
    if (!data) return;
    setErr(null);
    api
      .put(`/bank/items/${encodeURIComponent(id)}`, {
        item: { ...data.item, ...patch }, base_revision: data.item.revision, by: teacherName() || null,
      })
      .then(() => { reload(); onChanged(); })
      .catch((e: unknown) => setErr(errorText(e)));
  };
  const resolveSide = (side: "original" | "ai") => {
    if (!data) return;
    api
      .post(`/bank/items/${encodeURIComponent(id)}/resolve`, {
        side, base_revision: data.item.revision, by: teacherName() || null,
      })
      .then(() => { reload(); onChanged(); })
      .catch((e: unknown) => setErr(errorText(e)));
  };
  const remove = () => {
    if (!confirm("이 문항을 문제은행에서 지울까요? (함께 쓰는 모든 강사에게서 지워집니다)")) return;
    api.del(`/bank/items/${encodeURIComponent(id)}`).then(() => { onChanged(); onClose(); })
      .catch((e: unknown) => setErr(errorText(e)));
  };
  const it = data?.item;
  return (
    <Modal wide title={it ? it.source.title : "문항"} onClose={onClose}
           actions={it && <Button small onClick={() => go("make", "variant", it.id)}>이 문제로 동형 만들기</Button>}>
      <ErrorBox error={error ?? err} />
      {!it ? <Loading /> : (
        <Stack gap={14}>
          <Row>
            <span style={{ color: "var(--text-2)", fontSize: 13 }}>
              출처: {[it.source.title, it.source.pages[0] && `${it.source.pages[0]}쪽`,
                     it.source.number_label && `${it.source.number_label}번`].filter(Boolean).join(" ")}
              {it.created_by && ` · 넣은 사람 ${it.created_by}`}
              {it.updated_by && it.updated_by !== it.created_by && ` · 고친 사람 ${it.updated_by}`}
            </span>
          </Row>
          <ItemView number={it.question.number} points={it.question.points} item={it.question.item}
                    examId="" fileUrl={bankFileUrl} />

          <Card title="단원" actions={<Button small variant="ghost" onClick={() => setEditUnits(!editUnits)}>
            {editUnits ? "닫기" : "고치기"}</Button>}>
            <Row>
              {it.unit_ids.length === 0 && <span style={{ color: "var(--text-2)" }}>단원 없음</span>}
              {it.unit_ids.map((u, k) => (
                <Badge key={u} tone={k === 0 ? "accent" : "neutral"}>{unitLabel(trees, u)}</Badge>
              ))}
            </Row>
            {editUnits && (
              <div style={{ marginTop: 10, maxHeight: 320, overflow: "auto" }}>
                <Info>체크한 순서대로 저장됩니다 (첫 단원이 대표 단원).</Info>
                <UnitTreeNav trees={trees} checked={new Set(it.unit_ids.map((u) => resolve(trees, u)))}
                  onToggle={(u, on) => save({
                    unit_ids: on ? [...it.unit_ids, u] : it.unit_ids.filter((x) => resolve(trees, x) !== u),
                  })} />
              </div>
            )}
          </Card>

          {!it.solution && (
            <Notice>
              <Row>
                <span>
                  아직 풀이·해설이 없습니다 (DB화만 한 문항).
                  {it.original?.parts.some((p) => p.answer) && " 원본 정답은 아래에 있습니다."} AI가 원본을 보지 않고 풀어 원본 답과 비교합니다.
                </span>
                <span style={{ flex: 1 }} />
                <SolveButton ids={[it.id]} label="풀이·해설 만들기" onDone={() => { reload(); onChanged(); }} />
              </Row>
            </Notice>
          )}
          {!it.solution && it.original?.parts.some((p) => p.answer) && (
            <Card title="원본 정답">
              <Row>
                {it.original.parts.filter((p) => p.answer).map((p, k) => (
                  <span key={k}>{p.label && <b>{p.label} </b>}<Loose text={p.answer ?? ""} /></span>
                ))}
              </Row>
            </Card>
          )}
          <AnswerCompare it={it} onResolve={resolveSide} />

          {it.original && it.original.parts.some((p) => p.solution.trim()) && (
            <Card title={`원본 해설 (${LEVEL[it.original.level]})`}>
              <Stack gap={6}>
                {it.original.parts.map((p, k) => (
                  <div key={k}><b>{p.label}</b> <Loose text={p.solution} /></div>
                ))}
              </Stack>
            </Card>
          )}
          {it.solution && (
            <Card title={aiTitle(it)} actions={
              <SolveButton ids={[it.id]} redo label="다시 풀기" onDone={() => { reload(); onChanged(); }} />
            }>
              <SolutionView sol={it.solution} scope={unitScope(it, data.unit_labels)} />
            </Card>
          )}

          <Row>
            <Check checked={it.reviewed} onChange={(v) => save({ reviewed: v, reviewed_by: v ? teacherName() || null : null })}>
              검토함
            </Check>
            <Field label="난이도">
              <Select value={String(it.difficulty ?? "")} onChange={(v) => save({ difficulty: v ? Number(v) : null })}
                options={[{ value: "", label: "-" }, ...[1, 2, 3, 4, 5].map((n) => ({ value: String(n), label: "★".repeat(n) }))]} />
            </Field>
            <span style={{ flex: 1 }} />
            <Button small variant="ghost" onClick={remove}>문항 지우기</Button>
          </Row>
        </Stack>
      )}
    </Modal>
  );
}

function aiTitle(it: BankItem): string {
  const c = it.answer_check;
  if (c && !c.resolved && (c.verdict === "differs" || c.verdict === "unsure")) return "AI 풀이 (정답 확인 전)";
  if (c?.resolved === "original" && c.verdict !== "same") return "AI 풀이 (원본 답과 달라 쓰지 않음)";
  return it.solution_by === "ai" ? "AI 해설 (학생용으로 이 해설을 씁니다)" : "AI 풀이 (원본 해설 검산용)";
}

function AnswerCompare({ it, onResolve }: { it: BankItem; onResolve: (side: "original" | "ai") => void }) {
  const c = it.answer_check;
  if (!c) return null;
  const head = {
    same: <Badge tone="ok">원본 답과 AI 답이 같음</Badge>,
    differs: <Badge tone="bad">원본 답과 AI 답이 다름</Badge>,
    unsure: <Badge tone="warn">자동 비교 불가</Badge>,
    no_original: <Badge>원본 답 없음 — AI가 두 번 풀어 같은 답</Badge>,
  }[c.verdict];
  const open = !c.resolved && (c.verdict === "differs" || c.verdict === "unsure");
  return (
    <Card title="정답 확인">
      <Stack gap={8}>
        <Row>
          {head}
          {c.resolved && <Badge tone="ok">{c.resolved === "original" ? "원본 답" : "AI 답"}으로 확정{c.resolved_by ? ` (${c.resolved_by})` : ""}</Badge>}
        </Row>
        {(c.original_answer || c.ai_answer) && (
          <Row>
            {c.original_answer && <span><b>원본</b> <Loose text={c.original_answer} /></span>}
            {c.ai_answer && <span><b>AI</b> <Tex latex={c.ai_answer} /></span>}
          </Row>
        )}
        {c.notes.map((n, i) => <Notice key={i}><Loose text={n} /></Notice>)}
        {open && (
          <Row>
            <span style={{ fontSize: 13 }}>어느 쪽이 맞나요? 원본(해설지)도 틀릴 수 있습니다.</span>
            <Button small onClick={() => onResolve("original")}>원본 답이 맞음</Button>
            <Button small onClick={() => onResolve("ai")}>AI 답이 맞음</Button>
          </Row>
        )}
      </Stack>
    </Card>
  );
}
