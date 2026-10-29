import { useState } from "react";
import { SolveButton } from "../../components/SolveButton";
import {
  Badge,
  Button,
  Check,
  ErrorBox,
  Info,
  JobProgress,
  Notice,
  Row,
  Select,
  Stack,
  TextInput,
  tableClass,
  teacherName,
} from "../../components/ui";
import { api, errorText, useJob } from "../../lib/api";
import { go } from "../../lib/router";
import type { BankSource, ChunkStatus, SourceChunk, UnitTree } from "../../lib/types";
import { walk } from "../../lib/units";
import { pagesText, parsePages } from "../../lib/pages";

const STATUS: Record<ChunkStatus, { l: string; t: "neutral" | "warn" | "ok" | "bad" }> = {
  new: { l: "대기", t: "neutral" },
  ingesting: { l: "DB화 중", t: "warn" },
  ingested: { l: "완료", t: "ok" },
  failed: { l: "실패", t: "bad" },
};

type Row = { index: number; label: string; unit: string; concept: string; problem: string; key: string };

const toRow = (c: SourceChunk): Row => ({
  index: c.index,
  label: c.label ?? "",
  unit: c.unit_hint ?? "",
  concept: pagesText(c.concept_pages ?? []),
  problem: pagesText(c.pages),
  key: pagesText(c.key_pages),
});

const toChunk = (r: Row): Partial<SourceChunk> => ({
  index: r.index,
  label: r.label || null,
  unit_hint: r.unit || null,
  concept_pages: parsePages(r.concept),
  pages: parsePages(r.problem),
  key_pages: parsePages(r.key),
});

/** 교재: 챕터마다 개념 쪽(→ 범위 프로필) + 문제 쪽(→ 문제은행) + 정답·해설 쪽. 풀이는 요청할 때만. */
export function ChapterTable({ src, trees, onChanged }: { src: BankSource; trees: UnitTree[]; onChanged: () => void }) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const [solve, setSolve] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const suggest = useJob((j) => {
    const r = j.result as { chapters: SourceChunk[]; notes: string[] };
    // 제안은 새 번호로 (기존 챕터의 DB화 기록과 섞이지 않게)
    const base = Math.max(0, ...src.chunks.map((c) => c.index));
    setRows(r.chapters.map((c, i) => toRow({ ...c, index: src.chunks.length ? base + i + 1 : i + 1 })));
    setNotes(r.notes);
  });
  const ingest = useJob(onChanged);
  const editing = rows !== null;
  const shown = rows ?? src.chunks.map(toRow);
  const byIndex = new Map(src.chunks.map((c) => [c.index, c]));
  const unitOptions = [{ value: "", label: "단원 모름" },
    ...trees.flatMap((t) => walk(t).map(({ unit: u, path }) => ({
      value: u.id, label: `${"  ".repeat(path.length)}${path.length ? "" : `${t.subject} · `}${u.title}`,
    })))];
  const set = (i: number, patch: Partial<Row>) =>
    setRows(shown.map((r, k) => (k === i ? { ...r, ...patch } : r)));
  const save = () => {
    setErr(null);
    api
      .put(`/bank/sources/${src.source_id}/chunks`, { chunks: shown.map(toChunk) })
      .then(() => { setRows(null); setNotes([]); onChanged(); })
      .catch((e: unknown) => setErr(errorText(e)));
  };
  const run = (body: Record<string, unknown>) =>
    void ingest.run(() => api.post(`/bank/sources/${src.source_id}/chapters/ingest`, { ...body, solve, by: teacherName() || null }));
  const busy = ingest.running || suggest.running;
  return (
    <Stack gap={10}>
      <Row>
        <b>챕터 표</b>
        <span style={{ fontSize: 13, color: "var(--text-2)" }}>
          개념 쪽은 범위 프로필로, 문제 쪽은 문제은행으로 DB화합니다. 문제는 풀지 않고 넣고, 풀이는 필요할 때 만듭니다.
        </span>
        <span style={{ flex: 1 }} />
        <Button small disabled={busy} onClick={() => void suggest.run(() => api.post(`/bank/sources/${src.source_id}/chapters/suggest`, {}))}>
          챕터 제안 (목차·쪽 분류)
        </Button>
        {!editing && <Button small variant="ghost" onClick={() => setRows(src.chunks.map(toRow))}>표 고치기</Button>}
        <Button small variant="ghost" onClick={() => setRows([...shown, { index: 0, label: "", unit: "", concept: "", problem: "", key: "" }])}>
          + 챕터
        </Button>
      </Row>
      <JobProgress job={suggest.job} />
      <ErrorBox error={suggest.error ?? err} />
      {notes.map((n, i) => <Info key={i}>{n}</Info>)}
      {shown.length === 0 ? (
        <Notice>아직 챕터가 없습니다. “챕터 제안”을 누르면 PDF 목차와 쪽 내용으로 챕터 표를 만들어 줍니다.</Notice>
      ) : (
        <div style={{ overflowX: "auto" }}>
        <table className={tableClass} style={{ minWidth: 820 }}>
          <thead>
            <tr>
              <th style={{ minWidth: 170 }}>챕터</th>
              <th style={{ width: 190 }}>단원</th>
              <th style={{ width: 80 }}>개념 쪽</th>
              <th style={{ width: 80 }}>문제 쪽</th>
              <th style={{ width: 80 }}>해설 쪽</th>
              <th style={{ width: 220 }}>상태</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r, i) => {
              const c = editing ? undefined : byIndex.get(r.index);
              const cs = STATUS[c?.concept_status ?? "new"];
              const ps = STATUS[c?.status ?? "new"];
              return (
                <tr key={`${r.index}-${i}`}>
                  <td>
                    {editing ? <TextInput value={r.label} onChange={(v) => set(i, { label: v })} placeholder={`${i + 1}챕터`} />
                      : <b>{r.label || `${i + 1}챕터`}</b>}
                  </td>
                  <td>
                    {editing ? <Select value={r.unit} onChange={(v) => set(i, { unit: v })} options={unitOptions} />
                      : <span style={{ fontSize: 13 }}>{unitOptions.find((o) => o.value === r.unit)?.label.trim() ?? "-"}</span>}
                  </td>
                  {(["concept", "problem", "key"] as const).map((k) => (
                    <td key={k}>
                      {editing ? <TextInput value={r[k]} onChange={(v) => set(i, { [k]: v })} placeholder="3-12" />
                        : <span style={{ fontSize: 13 }}>{r[k] || "-"}</span>}
                    </td>
                  ))}
                  <td>
                    {c && (
                      <Stack gap={4}>
                        <Row style={{ gap: 6 }}>
                          {(c.concept_pages ?? []).length > 0 && (
                            <Badge tone={cs.t} title={c.concept_message ?? ""}>개념 {cs.l}</Badge>
                          )}
                          {c.pages.length > 0 && <Badge tone={ps.t} title={c.message ?? ""}>문제 {ps.l}{c.item_ids.length ? ` · ${c.item_ids.length}문항` : ""}</Badge>}
                          {c.scope_id && (
                            <Button small variant="ghost" onClick={() => go("scopes", c.scope_id!)}>범위 보기</Button>
                          )}
                        </Row>
                        <Row style={{ gap: 6 }}>
                          <Button small disabled={busy} onClick={() => run({ indexes: [c.index] })}>
                            {c.concept_status === "ingested" || c.status === "ingested" ? "다시 DB화" : "DB화"}
                          </Button>
                          {c.item_ids.length > 0 && (
                            <SolveButton ids={[]} url={`/bank/sources/${src.source_id}/chunks/${c.index}/solve`}
                                         label="풀이 만들기" onDone={onChanged} />
                          )}
                        </Row>
                        {(c.concept_status === "failed" || c.status === "failed") && (
                          <span style={{ fontSize: 12, color: "var(--bad)" }}>{c.concept_status === "failed" ? c.concept_message : c.message}</span>
                        )}
                      </Stack>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
      )}
      <Row>
        {editing ? (
          <>
            <Button small variant="primary" onClick={save}>표 저장</Button>
            <Button small variant="ghost" onClick={() => { setRows(null); setNotes([]); }}>취소</Button>
            <span style={{ fontSize: 12.5, color: "var(--text-2)" }}>
              쪽은 “3-12, 15”처럼 적습니다. 이미 DB화한 챕터는 쪽을 바꿀 수 없습니다 (이름·단원은 됩니다).
            </span>
          </>
        ) : src.chunks.length > 0 && (
          <>
            <Button small variant="primary" disabled={busy} onClick={() => run({})}>
              남은 챕터 모두 DB화{solve ? "" : " (풀이 없이)"}
            </Button>
            <Check checked={solve} onChange={setSolve}>문제 풀이도 만들기 (느림)</Check>
          </>
        )}
      </Row>
      <JobProgress job={ingest.job} />
      <ErrorBox error={ingest.error} />
    </Stack>
  );
}
