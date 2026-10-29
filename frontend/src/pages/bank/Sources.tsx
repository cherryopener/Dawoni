import { useState } from "react";
import { FileActions } from "../../components/FileActions";
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
  Row,
  Select,
  Stack,
  TextInput,
  teacherName,
} from "../../components/ui";
import { api, errorText, useJob, useLoad } from "../../lib/api";
import type { BankSource, BankSourceKind, SourceChunk, UnitTree } from "../../lib/types";
import { unitLabel, walk } from "../../lib/units";
import { SOURCE_KIND } from "../../lib/bank";
import { SolveButton } from "../../components/SolveButton";

/** '3-40, 45' → [3..40, 45] */
export function parsePages(s: string): number[] {
  const out = new Set<number>();
  for (const part of s.split(",").map((x) => x.trim()).filter(Boolean)) {
    const m = /^(\d+)\s*[-~]\s*(\d+)$/.exec(part);
    if (m) for (let p = Number(m[1]); p <= Number(m[2]); p++) out.add(p);
    else if (/^\d+$/.test(part)) out.add(Number(part));
  }
  return [...out].sort((a, b) => a - b);
}

export function pagesText(pages: number[]): string {
  const out: string[] = [];
  let start = pages[0];
  for (let i = 1; i <= pages.length; i++) {
    const prev = pages[i - 1] ?? 0;
    if (pages[i] !== prev + 1) {
      out.push(start === prev ? `${start}` : `${start}-${prev}`);
      start = pages[i];
    }
  }
  return out.join(", ");
}

const KIND_OPTIONS = (["problem_bank", "mock_exam", "textbook"] as BankSourceKind[]).map((k) => ({
  value: k, label: SOURCE_KIND[k] ?? k,
}));

export function SourcesTab() {
  const trees = useLoad<UnitTree[]>("/units");
  const sources = useLoad<BankSource[]>("/bank/sources");
  if (trees.error || sources.error) return <ErrorBox error={trees.error ?? sources.error} />;
  if (!trees.data || !sources.data) return <Loading />;
  return (
    <Stack gap={14}>
      <Upload trees={trees.data} onDone={sources.reload} />
      {sources.data.length === 0 ? (
        <Empty>아직 넣은 자료가 없습니다.</Empty>
      ) : (
        sources.data.map((s) => (
          <SourceCard key={s.source_id} src={s} trees={trees.data!} onChanged={sources.reload} />
        ))
      )}
    </Stack>
  );
}

function TreePicker({ trees, value, onChange }: { trees: UnitTree[]; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <Row style={{ gap: 6 }}>
      {trees.map((t) => (
        <label key={t.id} style={{ fontSize: 13, display: "inline-flex", gap: 4, alignItems: "center" }}>
          <input type="checkbox" checked={value.includes(t.id)}
                 onChange={(e) => onChange(e.target.checked ? [...value, t.id] : value.filter((x) => x !== t.id))} />
          {t.subject}
        </label>
      ))}
    </Row>
  );
}

function Upload({ trees, onDone }: { trees: UnitTree[]; onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState<BankSourceKind>("problem_bank");
  const [title, setTitle] = useState("");
  const [publisher, setPublisher] = useState("");
  const [year, setYear] = useState("");
  const [units, setUnits] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const send = () => {
    const f = new FormData();
    f.append("file", file!);
    f.append("kind", kind);
    f.append("title", title);
    f.append("unit_trees", units.join(","));
    if (publisher) f.append("publisher", publisher);
    if (year) f.append("year", year);
    setBusy(true);
    setErr(null);
    api
      .upload<{ source: BankSource; suggested: SourceChunk }>("/bank/sources", f)
      .then(async (r) => {
        if (r.source.chunks.length === 0) {
          await api.post(`/bank/sources/${r.source.source_id}/chunks`, { chunks: [r.suggested] });
        }
        setFile(null);
        setTitle("");
        onDone();
      })
      .catch((e: unknown) => setErr(errorText(e)))
      .finally(() => setBusy(false));
  };
  return (
    <Card title="자료 넣기">
      <Stack>
        <span style={{ color: "var(--text-2)", fontSize: 13 }}>
          PDF를 넣으면 먼저 등록만 합니다 (AI 호출 없음). 두꺼운 책은 아래에서 단원·쪽 범위로 구간을 나눠 구간마다 DB화하세요.
          DB화는 문항과 원본 정답·해설을 옮겨 적기만 합니다. 풀이·해설은 필요할 때 “풀이 만들기”로 만듭니다 — AI가 원본 답을
          보지 않고 먼저 풀어 원본과 비교하고, 해설이 없거나 짧으면 학생용 해설을 답니다.
        </span>
        <Row>
          <input type="file" accept="application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <Select value={kind} onChange={setKind} options={KIND_OPTIONS} />
        </Row>
        <Row>
          <div style={{ flex: 2, minWidth: 220 }}>
            <Field label="출처 이름" hint="문제은행에 보이는 이름">
              <TextInput value={title} onChange={setTitle}
                         placeholder={kind === "mock_exam" ? "2025학년도 6월 모평 물리학I" : "OO 물리 문제집"} />
            </Field>
          </div>
          <div style={{ flex: 1, minWidth: 140 }}>
            <Field label="출판사·저자 (선택)"><TextInput value={publisher} onChange={setPublisher} /></Field>
          </div>
          <div style={{ width: 100 }}>
            <Field label="연도 (선택)"><TextInput value={year} onChange={setYear} /></Field>
          </div>
        </Row>
        <Field label="분류할 단원표" hint="문항을 이 단원표의 단원으로 나눕니다">
          <TreePicker trees={trees} value={units} onChange={setUnits} />
        </Field>
        <Row>
          <Button variant="primary" onClick={send} disabled={busy || !file || !title || units.length === 0}>
            {busy ? "올리는 중…" : "등록"}
          </Button>
        </Row>
        <ErrorBox error={err} />
      </Stack>
    </Card>
  );
}

const STATUS: Record<SourceChunk["status"], { l: string; t: "neutral" | "warn" | "ok" | "bad" }> = {
  new: { l: "대기", t: "neutral" },
  ingesting: { l: "DB화 중", t: "warn" },
  ingested: { l: "완료", t: "ok" },
  failed: { l: "실패", t: "bad" },
};

function SourceCard({ src, trees, onChanged }: { src: BankSource; trees: UnitTree[]; onChanged: () => void }) {
  const mine = trees.filter((t) => src.unit_trees.includes(t.id));
  const [solve, setSolve] = useState(false);
  return (
    <Card
      title={<>{src.title} <Badge>{SOURCE_KIND[src.kind]}</Badge></>}
      actions={<FileActions path={`sources/${src.rel_path}`} openLabel="PDF 열기" />}
    >
      <Stack gap={10}>
        <span style={{ fontSize: 13, color: "var(--text-2)" }}>
          {[src.publisher, src.year, `${src.pages ?? "?"}쪽`, mine.map((t) => t.subject).join(" · "),
            `${src.ingest.item_count}문항`].filter(Boolean).join(" · ")}
        </span>
        {src.chunks.map((c) => <ChunkRow key={c.index} src={src} c={c} trees={trees} solve={solve} onChanged={onChanged} />)}
        <Row>
          <AddChunk src={src} trees={mine} onDone={onChanged} />
          <span style={{ flex: 1 }} />
          <Check checked={solve} onChange={setSolve}>DB화하면서 풀이도 만들기 (느림)</Check>
        </Row>
      </Stack>
    </Card>
  );
}

function ChunkRow({ src, c, trees, solve, onChanged }: {
  src: BankSource; c: SourceChunk; trees: UnitTree[]; solve: boolean; onChanged: () => void;
}) {
  const job = useJob(onChanged);
  const st = STATUS[c.status];
  const start = () =>
    void job.run(() =>
      api.post(`/bank/sources/${src.source_id}/chunks/${c.index}/ingest`, { by: teacherName() || null, solve }),
    );
  return (
    <Stack gap={4}>
      <Row>
        <b>구간 {c.index}</b>
        {c.label && <span>{c.label}</span>}
        <span style={{ fontSize: 13 }}>
          문제 {pagesText(c.pages)}쪽 · 해설 {c.key_pages.length ? `${pagesText(c.key_pages)}쪽` : "없음"}
        </span>
        {c.unit_hint && <Badge tone="accent">{unitLabel(trees, c.unit_hint, true)}</Badge>}
        <Badge tone={st.t}>{st.l}{c.by && c.status === "ingesting" ? ` · ${c.by}` : ""}</Badge>
        {c.message && <span style={{ fontSize: 12.5, color: "var(--text-2)" }}>{c.message}</span>}
        <span style={{ flex: 1 }} />
        <Button small variant={c.status === "ingested" ? "default" : "primary"} onClick={start} disabled={job.running}>
          {c.status === "ingested" ? "다시 (빠진 문항만)" : c.status === "failed" ? "다시 DB화" : "DB화"}
        </Button>
        {c.item_ids.length > 0 && (
          <SolveButton ids={[]} url={`/bank/sources/${src.source_id}/chunks/${c.index}/solve`}
                       label={`이 구간 풀이 만들기 (${c.item_ids.length}문항 중 없는 것)`} onDone={onChanged} />
        )}
      </Row>
      <JobProgress job={job.job} />
      <ErrorBox error={job.error} />
      {job.job?.status === "succeeded" && job.job.result && (
        <Info>
          {String(job.job.result.added)}문항 추가
          {solve ? ` · 답 확인 필요 ${String(job.job.result.needs_check)}` : " · 풀이는 “풀이 만들기”로"}
          {(job.job.result.warnings as string[]).map((w, i) => <div key={i}>! {w}</div>)}
        </Info>
      )}
    </Stack>
  );
}

function AddChunk({ src, trees, onDone }: { src: BankSource; trees: UnitTree[]; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [pages, setPages] = useState("");
  const [keys, setKeys] = useState("");
  const [unit, setUnit] = useState("");
  const [label, setLabel] = useState("");
  const [err, setErr] = useState<string | null>(null);
  if (!open) return <Row><Button small variant="ghost" onClick={() => setOpen(true)}>+ 구간 추가</Button></Row>;
  const add = () =>
    api
      .post(`/bank/sources/${src.source_id}/chunks`, {
        chunks: [{ index: 1, pages: parsePages(pages), key_pages: parsePages(keys), unit_hint: unit || null,
                   label: label || null }],
      })
      .then(() => { setOpen(false); setPages(""); setKeys(""); onDone(); })
      .catch((e: unknown) => setErr(errorText(e)));
  const unitOptions = [{ value: "", label: "모름 (AI가 풀이로 정함)" },
    ...trees.flatMap((t) => walk(t).map(({ unit: u, path }) => ({
      value: u.id, label: `${"  ".repeat(path.length)}${path.length ? "" : `${t.subject} · `}${u.title}`,
    })))];
  return (
    <Stack gap={8}>
      <Row>
        <div style={{ width: 140 }}><Field label="문제 쪽"><TextInput value={pages} onChange={setPages} placeholder="3-40" /></Field></div>
        <div style={{ width: 140 }}><Field label="정답·해설 쪽"><TextInput value={keys} onChange={setKeys} placeholder="121-130" /></Field></div>
        <div style={{ minWidth: 220 }}><Field label="단원 (알면)"><Select value={unit} onChange={setUnit} options={unitOptions} /></Field></div>
        <div style={{ width: 160 }}><Field label="구간 이름"><TextInput value={label} onChange={setLabel} placeholder="1단원" /></Field></div>
      </Row>
      <Row>
        <Button small variant="primary" onClick={add} disabled={parsePages(pages).length === 0}>추가</Button>
        <Button small variant="ghost" onClick={() => setOpen(false)}>취소</Button>
        <span style={{ fontSize: 12.5, color: "var(--text-2)" }}>
          문제집은 단원마다 번호가 1번부터 다시 시작하므로, 해설 번호는 같은 구간 안에서만 맞춥니다.
        </span>
      </Row>
      <ErrorBox error={err} />
    </Stack>
  );
}
