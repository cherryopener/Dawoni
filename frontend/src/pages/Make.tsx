import { useState } from "react";
import { ItemView } from "../components/Item";
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
  JobProgress,
  Loading,
  Notice,
  NumberInput,
  Row,
  Select,
  Stack,
  Tabs,
  TextArea,
} from "../components/ui";
import { api, useJob, useLoad } from "../lib/api";
import { go } from "../lib/router";
import type {
  AnswerFormat,
  BankItem,
  BankSummary,
  Extension,
  GeneratedSet,
  GenKind,
  ScopeSummary,
  UnitTree,
} from "../lib/types";
import { unitLabel } from "../lib/units";
import { bankFileUrl } from "../lib/bank";
import { BankBrowser } from "./Bank";
import { ExtensionForm, KINDS, SetCard, emptyExt } from "./exam/Generate";

type Tab = "variant" | "new";

export function MakePage({ tab, itemId }: { tab?: string; itemId?: string }) {
  const t: Tab = tab === "new" ? "new" : "variant";
  return (
    <Stack gap={14}>
      <Row>
        <h2 style={{ margin: 0 }}>문제 만들기</h2>
        <span style={{ color: "var(--text-2)", fontSize: 13 }}>
          만든 문항은 초안입니다. 앱이 검사하고 다른 AI 세션이 정답을 가린 채 다시 풀어 확인한 뒤, 강사가 승인하면 문제은행에 들어갑니다.
        </span>
      </Row>
      <Tabs
        value={t}
        onChange={(v) => go("make", v)}
        tabs={[
          { value: "variant", label: "동형 문제 (기준 문항 고르기)" },
          { value: "new", label: "새 문제 (아이디어로)" },
        ]}
      />
      {t === "variant" ? <VariantTab itemId={itemId} /> : <NewTab />}
    </Stack>
  );
}

function ScopePick({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const scopes = useLoad<ScopeSummary[]>("/scopes");
  return (
    <Field label="풀이 범위" hint="학생이 쓸 수 있는 개념·기법. 자동은 단원표의 단원 이름만으로 범위를 정합니다.">
      <Select value={value} onChange={onChange} options={[
        { value: "", label: "자동 (단원표 범위)" },
        ...(scopes.data ?? []).filter((s) => !s.id.startsWith("units-"))
          .map((s) => ({ value: s.id, label: `${s.title} (${s.level})` })),
      ]} />
    </Field>
  );
}

function Results({ url, toBank = true }: { url: string; toBank?: boolean }) {
  const list = useLoad<GeneratedSet[]>(url);
  const sets = list.data ?? [];
  return (
    <Stack gap={10}>
      <ErrorBox error={list.error} />
      {list.data && sets.length === 0 && <Empty>아직 만든 문항이 없습니다.</Empty>}
      {sets.map((s) => (
        <SetCard key={s.id} s={s} scope={null} selected={false} onSelect={() => undefined}
                 onChanged={list.reload} toBank={toBank} />
      ))}
    </Stack>
  );
}

// ---------------------------------------------------------------- 동형

const VARIANT_KINDS = KINDS;

function VariantTab({ itemId }: { itemId?: string }) {
  const trees = useLoad<UnitTree[]>("/units");
  const items = useLoad<BankSummary[]>("/bank/items");
  const [picked, setPicked] = useState<string | null>(itemId ?? null);
  if (trees.error || items.error) return <ErrorBox error={trees.error ?? items.error} />;
  if (!trees.data || !items.data) return <Loading />;
  return (
    <Stack gap={14}>
      {picked ? (
        <VariantForm key={picked} itemId={picked} trees={trees.data} onChange={() => setPicked(null)} />
      ) : (
        <Info>아래 문제은행에서 기준 문항을 고르세요. 단원별로 정리되어 있습니다.</Info>
      )}
      <Card title="기준 문항 고르기">
        <BankBrowser trees={trees.data} items={items.data} picked={picked}
                     onPick={(id) => { setPicked(id); window.scrollTo({ top: 0, behavior: "smooth" }); }} />
      </Card>
    </Stack>
  );
}

function VariantForm({ itemId, trees, onChange }: { itemId: string; trees: UnitTree[]; onChange: () => void }) {
  const base = useLoad<{ item: BankItem; unit_labels: Record<string, string> }>(`/bank/items/${encodeURIComponent(itemId)}`);
  const [kind, setKind] = useState<GenKind>("condition");
  const [scope, setScope] = useState("");
  const [count, setCount] = useState(2);
  const [maxDen, setMaxDen] = useState(20);
  const [sqrt, setSqrt] = useState(false);
  const [ext, setExt] = useState<Extension>(emptyExt());
  const [n, setN] = useState(0);
  const job = useJob(() => setN((x) => x + 1));
  const it = base.data?.item;
  if (base.error) return <ErrorBox error={base.error} />;
  if (!it) return <Loading />;
  const figureChoices = it.question.item.choices_figure_id != null || it.question.item.choices.some((c) => c.figure_id);
  const blocked = it.answer_check && !it.answer_check.resolved && ["differs", "unsure"].includes(it.answer_check.verdict);
  const start = () =>
    void job.run(() =>
      api.post(`/bank/items/${encodeURIComponent(itemId)}/generate`, {
        kind, scope_id: scope || null, count, max_den: maxDen, allow_sqrt: sqrt,
        extension: kind === "novel_b" ? ext : null,
      }),
    );
  return (
    <Stack gap={12}>
      <Card title="기준 문항" actions={<Button small variant="ghost" onClick={onChange}>다른 문항 고르기</Button>}>
        <Stack gap={8}>
          <Row>
            <span style={{ fontSize: 13, color: "var(--text-2)" }}>{it.source.title}</span>
            {it.unit_ids.map((u) => <Badge key={u} tone="accent">{unitLabel(trees, u, true)}</Badge>)}
            {figureChoices && <Badge tone="accent">그림 선지 문항</Badge>}
          </Row>
          <ItemView number={it.question.number} points={it.question.points} item={it.question.item} examId="" fileUrl={bankFileUrl} />
          {blocked && <Notice>이 문항은 원본 답과 AI 답이 달라 확인이 필요합니다. 문제은행에서 맞는 답을 먼저 고르세요.</Notice>}
        </Stack>
      </Card>
      <Card title="만들 종류">
        <Stack>
          <Stack gap={6}>
            {VARIANT_KINDS.map((k) => {
              const disabled = k.value === "numeric" && figureChoices;
              return (
                <label key={k.value} style={{ display: "flex", gap: 8, alignItems: "baseline", opacity: disabled ? 0.45 : 1 }}>
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
              <Check checked={sqrt} onChange={setSqrt}>√n 꼴 답 허용</Check>
            </Row>
          )}
          {kind === "novel_b" && (
            <ExtensionForm proposeUrl={`/bank/items/${encodeURIComponent(itemId)}/extensions`}
                           proposeBody={{ scope_id: scope || null }} ext={ext} setExt={setExt} />
          )}
          <div style={{ maxWidth: 420 }}><ScopePick value={scope} onChange={setScope} /></div>
          <Row>
            <Button variant="primary" onClick={start}
                    disabled={job.running || !!blocked || (kind === "novel_b" && (!ext.name || !ext.definition))}>
              만들기
            </Button>
            <span style={{ fontSize: 12.5, color: "var(--text-3)" }}>몇 분 걸립니다. 다른 화면으로 가도 계속 진행됩니다.</span>
          </Row>
          <JobProgress job={job.job} />
          <ErrorBox error={job.error} />
        </Stack>
      </Card>
      <Results key={n} url={`/bank/items/${encodeURIComponent(itemId)}/generated`} />
    </Stack>
  );
}

// ---------------------------------------------------------------- 새 문제

const FORMATS: { value: AnswerFormat; label: string; desc: string }[] = [
  { value: "choice5", label: "객관식", desc: "5지선다, 정답 하나. 오답 선지는 흔한 실수에서" },
  { value: "bogi", label: "ㄱㄴㄷ 합답형", desc: "<보기> 진술 ㄱ·ㄴ·ㄷ의 참·거짓을 앱이 정답 조합과 대조" },
  { value: "short", label: "단답형", desc: "값이나 식 하나" },
  { value: "essay", label: "서술형", desc: "풀이 과정을 쓰게. 소문항 수를 정할 수 있음, 루브릭 초안 포함" },
];

function NewTab() {
  const trees = useLoad<UnitTree[]>("/units");
  const [units, setUnits] = useState<Set<string>>(new Set());
  const [scope, setScope] = useState("");
  const [format, setFormat] = useState<AnswerFormat>("essay");
  const [points, setPoints] = useState(6);
  const [difficulty, setDifficulty] = useState(3);
  const [parts, setParts] = useState(1);
  const [idea, setIdea] = useState("");
  const [usePassage, setUsePassage] = useState(false);
  const [concept, setConcept] = useState("");
  const [ext, setExt] = useState<Extension | null>(null);
  const [n, setN] = useState(0);
  const extJob = useJob((j) => setExt(j.result as unknown as Extension));
  const job = useJob(() => setN((x) => x + 1));
  if (trees.error) return <ErrorBox error={trees.error} />;
  if (!trees.data) return <Loading />;
  const unitIds = [...units];
  const draftExt = () =>
    void extJob.run(() => api.post("/make/extension", { concept, idea, unit_ids: unitIds, scope_id: scope || null }));
  const start = () =>
    void job.run(() =>
      api.post("/make/new", {
        idea, answer_format: format, points, unit_ids: unitIds, scope_id: scope || null, difficulty,
        n_parts: format === "essay" ? parts : 1, extension: usePassage ? ext : null,
      }),
    );
  const ready = idea.trim() && (unitIds.length > 0 || scope) && (!usePassage || (ext && ext.name && ext.definition));
  return (
    <Stack gap={14}>
      <div style={{ display: "grid", gridTemplateColumns: "280px 1fr", gap: 14, alignItems: "start" }}>
        <Card title="단원">
          <div style={{ maxHeight: 520, overflow: "auto" }}>
            <UnitTreeNav trees={trees.data} checked={units} onToggle={(u, on) => {
              const s = new Set(units);
              if (on) s.add(u);
              else s.delete(u);
              setUnits(s);
            }} />
          </div>
        </Card>
        <Stack gap={12}>
          <Card title="답 형태">
            <Stack gap={6}>
              {FORMATS.map((f) => (
                <label key={f.value} style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                  <input type="radio" name="format" checked={format === f.value} onChange={() => setFormat(f.value)} />
                  <b style={{ minWidth: 110 }}>{f.label}</b>
                  <span style={{ fontSize: 13, color: "var(--text-2)" }}>{f.desc}</span>
                </label>
              ))}
              <Row>
                <span>배점</span>
                <NumberInput value={points} min={1} max={100} step={1} onChange={(v) => setPoints(v || 1)} />
                <span>난이도</span>
                <div style={{ width: 110 }}>
                  <Select value={String(difficulty)} onChange={(v) => setDifficulty(Number(v))}
                          options={[1, 2, 3, 4, 5].map((x) => ({ value: String(x), label: "★".repeat(x) }))} />
                </div>
                {format === "essay" && (
                  <>
                    <span>소문항</span>
                    <NumberInput value={parts} min={1} max={5} step={1} onChange={(v) => setParts(Math.round(v || 1))} />
                  </>
                )}
              </Row>
            </Stack>
          </Card>
          <Card title="강사 아이디어">
            <Stack>
              <TextArea value={idea} onChange={setIdea} rows={5}
                placeholder={"예: 빗면 위 두 물체가 줄로 연결된 상황에서, 마찰이 있는 구간과 없는 구간을 지날 때 가속도가 어떻게 바뀌는지 묻고 싶다. 학생이 흔히 장력을 무게와 같다고 놓는 실수를 오답으로 쓰고 싶다."} />
              <Check checked={usePassage} onChange={setUsePassage}>교과 외 개념을 제시문으로 주기 (어려운 문제)</Check>
              {usePassage && (
                <Stack gap={8}>
                  <Field label="추가할 개념을 말로 적으세요" hint="AI가 제시문용 정의·가정·학생이 유추할 내용으로 정리합니다. 정리된 것을 확인·수정한 뒤 만드세요.">
                    <TextArea value={concept} onChange={setConcept} rows={3}
                      placeholder="예: 공기 저항력이 속력에 비례한다는 것 (f = bv). 학생이 종단 속도를 스스로 유도하게 하고 싶다." />
                  </Field>
                  <Row>
                    <Button small onClick={draftExt} disabled={!concept.trim() || extJob.running || (unitIds.length === 0 && !scope)}>
                      제시문 개념으로 정리
                    </Button>
                  </Row>
                  <JobProgress job={extJob.job} />
                  <ErrorBox error={extJob.error} />
                  {ext && <ExtensionForm proposeUrl="" ext={ext} setExt={setExt} hidePropose />}
                </Stack>
              )}
            </Stack>
          </Card>
          <div style={{ maxWidth: 420 }}><ScopePick value={scope} onChange={setScope} /></div>
          <Row>
            {unitIds.map((u) => <Badge key={u} tone="accent">{unitLabel(trees.data!, u)}</Badge>)}
          </Row>
          <Row>
            <Button variant="primary" onClick={start} disabled={!ready || job.running}>새 문제 만들기</Button>
            {!ready && <span style={{ fontSize: 12.5, color: "var(--text-3)" }}>단원(또는 범위)과 아이디어가 필요합니다{usePassage ? ", 제시문 개념도 정리하세요" : ""}.</span>}
          </Row>
          <JobProgress job={job.job} />
          <ErrorBox error={job.error} />
        </Stack>
      </div>
      <h3 style={{ margin: "6px 0 0" }}>만든 새 문제</h3>
      <NewResults key={n} />
    </Stack>
  );
}

function NewResults() {
  const list = useLoad<GeneratedSet[]>("/generated");
  const sets = (list.data ?? []).filter((s) => s.kind === "new");
  const [m, setM] = useState(0);
  return (
    <Stack gap={10}>
      <ErrorBox error={list.error} />
      {list.data && sets.length === 0 && <Empty>아직 만든 새 문제가 없습니다.</Empty>}
      {sets.map((s) => (
        <SetCard key={`${s.id}-${m}`} s={s} scope={null} selected={false} onSelect={() => undefined}
                 onChanged={() => { list.reload(); setM((x) => x + 1); }} toBank />
      ))}
    </Stack>
  );
}
