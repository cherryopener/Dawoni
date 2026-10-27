import { useState } from "react";
import { FileActions } from "../components/FileActions";
import { Loose, Tex } from "../components/Math";
import { Badge, Button, Card, Check, Empty, ErrorBox, Field, Info, JobProgress, Loading, Notice, Row, Select, Stack, TextInput, tableClass } from "../components/ui";
import { api, errorText, useJob, useLoad } from "../lib/api";
import { go } from "../lib/router";
import type { ExportResult, LectureSummary, Scope, ScopeSummary } from "../lib/types";

export function stamp(sec: number): string {
  const s = Math.floor(sec);
  const mm = String(Math.floor((s % 3600) / 60));
  const ss = String(s % 60).padStart(2, "0");
  return s >= 3600 ? `${Math.floor(s / 3600)}:${mm.padStart(2, "0")}:${ss}` : `${mm}:${ss}`;
}

const PAGES = { "1": "A4 1장", "2": "양면 1장 (2쪽)" } as const;

function NewSummary({ scopes, onDone }: { scopes: ScopeSummary[]; onDone: (id: string) => void }) {
  const [url, setUrl] = useState("");
  const [scopeId, setScopeId] = useState(scopes[0]?.id ?? "");
  const [pages, setPages] = useState<"1" | "2">("1");
  const [refresh, setRefresh] = useState(false);
  const job = useJob((j) => onDone((j.result as { id: string }).id));
  const start = () =>
    void job.run(() => api.post("/lectures/summarize", { url: url.trim(), scope_id: scopeId || null, pages: Number(pages), refresh }));
  return (
    <Card title="새 강의 요약">
      <Stack>
        <Field label="유튜브 주소" hint="자막(자동 자막 포함)이 있는 영상만 됩니다. 영상은 내려받지 않고 자막만 읽습니다.">
          <TextInput value={url} onChange={setUrl} placeholder="https://www.youtube.com/watch?v=…" />
        </Field>
        <Row>
          <Field label="비교할 교재 범위">
            <Select
              value={scopeId}
              onChange={setScopeId}
              options={[{ value: "", label: "비교하지 않음" }, ...scopes.map((s) => ({ value: s.id, label: `${s.title} (${s.level})` }))]}
            />
          </Field>
          <Field label="분량">
            <Select value={pages} onChange={setPages} options={[{ value: "1", label: PAGES["1"] }, { value: "2", label: PAGES["2"] }]} />
          </Field>
        </Row>
        <Row>
          <Button variant="primary" onClick={start} disabled={job.running || !url.trim()}>
            요약 만들기
          </Button>
          <Check checked={refresh} onChange={setRefresh}>
            저장된 자막 대신 다시 받기
          </Check>
        </Row>
        <JobProgress job={job.job} />
        <ErrorBox error={job.error} />
      </Stack>
    </Card>
  );
}

function Coverage({ s, scope }: { s: LectureSummary; scope: Scope | null }) {
  if (!s.scope_id) return null;
  const name = (id: string) => scope?.concepts.find((c) => c.id === id)?.name ?? scope?.skills.find((k) => k.id === id)?.name ?? id;
  return (
    <Stack gap={6}>
      <Row>
        <b style={{ fontSize: 13 }}>교재 범위 {scope ? `· ${scope.title}` : ""}</b>
      </Row>
      <Row>
        <span style={{ fontSize: 12.5, color: "var(--text-2)" }}>다룬 개념</span>
        {s.covered.length ? s.covered.map((c) => <Badge key={c} tone="ok" title={c}>{name(c)}</Badge>) : <span style={{ fontSize: 12.5 }}>없음</span>}
      </Row>
      {s.missing.length > 0 && (
        <Row>
          <span style={{ fontSize: 12.5, color: "var(--text-2)" }}>강의에 없는 교재 개념</span>
          {s.missing.map((c) => <Badge key={c} tone="bad" title={c}>{name(c)}</Badge>)}
        </Row>
      )}
      {s.beyond.length > 0 && (
        <Row>
          <span style={{ fontSize: 12.5, color: "var(--text-2)" }}>범위 밖 내용</span>
          {s.beyond.map((b) => <Badge key={b}>{b}</Badge>)}
        </Row>
      )}
    </Stack>
  );
}

function SummaryView({ id, onDeleted }: { id: string; onDeleted: () => void }) {
  const { data: s, error } = useLoad<LectureSummary>(`/lectures/${id}`);
  const { data: scope } = useLoad<Scope>(s?.scope_id ? `/scopes/${s.scope_id}` : null);
  const [res, setRes] = useState<ExportResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (error) return <ErrorBox error={error} />;
  if (!s) return <Loading />;
  const exportDocx = () => {
    setBusy(true);
    setErr(null);
    api
      .post<ExportResult>(`/lectures/${id}/export`)
      .then(setRes)
      .catch((e: unknown) => setErr(errorText(e)))
      .finally(() => setBusy(false));
  };
  const remove = () => {
    if (!confirm("이 요약을 지울까요? (자막은 남겨 둡니다)")) return;
    api
      .del(`/lectures/${id}`)
      .then(onDeleted)
      .catch((e: unknown) => setErr(errorText(e)));
  };
  return (
    <Card
      title={s.title}
      actions={
        <Row>
          <Button small variant="primary" onClick={exportDocx} disabled={busy}>
            Word로 내보내기
          </Button>
          <Button small variant="ghost" onClick={remove}>
            지우기
          </Button>
        </Row>
      }
    >
      <Stack>
        <span style={{ fontSize: 13, color: "var(--text-2)" }}>
          {s.video_title} · {s.channel ?? "채널 미상"} · {stamp(s.duration_s)} · {PAGES[String(s.pages) as "1" | "2"]}
          {s.caption_generated && " · 자동 자막"}
          {s.agent && ` · ${s.agent}${s.model ? ` ${s.model}` : ""}`}
        </span>
        <span style={{ fontSize: 12, color: "var(--text-3)", wordBreak: "break-all" }}>{s.url}</span>
        {s.caption_generated && <Notice>자동 자막으로 만든 요약입니다. 식·용어가 잘못 들렸을 수 있으니 확인하세요.</Notice>}
        <ErrorBox error={err} />
        {res && (
          <Stack gap={6}>
            <Row>
              {res.ok ? <Badge tone="ok">자동 검사 통과</Badge> : <Badge tone="bad">자동 검사 실패</Badge>}
              <span style={{ fontSize: 13, color: "var(--text-2)" }}>
                {res.name} · {res.summary}
              </span>
            </Row>
            {res.errors.map((e, i) => (
              <ErrorBox key={i} error={e} />
            ))}
            <FileActions path={res.path} />
          </Stack>
        )}
        <Coverage s={s} scope={scope} />
        <Loose text={s.overview} />
        {s.sections.map((sec, i) => (
          <Stack key={i} gap={4}>
            <Row>
              <Badge tone="accent">{stamp(sec.start_s)}</Badge>
              <b>{sec.heading}</b>
            </Row>
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {sec.bullets.map((b, k) => (
                <li key={k}>
                  <Loose text={b} />
                </li>
              ))}
            </ul>
            {sec.formulas.map((f, k) => (
              <Tex key={k} latex={f} display />
            ))}
          </Stack>
        ))}
        {s.key_formulas.length > 0 && (
          <Info>
            <b>핵심 공식</b>
            {s.key_formulas.map((f, k) => (
              <Tex key={k} latex={f} display />
            ))}
          </Info>
        )}
        {s.tips.length > 0 && (
          <Stack gap={4}>
            <b>강사가 강조한 실수·요령</b>
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {s.tips.map((t, k) => (
                <li key={k}>
                  <Loose text={t} />
                </li>
              ))}
            </ul>
          </Stack>
        )}
        {s.review_questions.length > 0 && (
          <Stack gap={4}>
            <b>스스로 확인</b>
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {s.review_questions.map((q, k) => (
                <li key={k}>
                  <Loose text={q} />
                </li>
              ))}
            </ul>
          </Stack>
        )}
      </Stack>
    </Card>
  );
}

export function LecturesPage({ id }: { id?: string }) {
  const scopes = useLoad<ScopeSummary[]>("/scopes");
  const list = useLoad<LectureSummary[]>("/lectures");
  if (scopes.error || list.error) return <ErrorBox error={scopes.error ?? list.error} />;
  if (!scopes.data || !list.data) return <Loading />;
  const done = (newId: string) => {
    list.reload();
    go("lectures", newId);
  };
  return (
    <Stack>
      <h2 style={{ margin: 0 }}>강의 요약</h2>
      <NewSummary scopes={scopes.data} onDone={done} />
      {id && (
        <SummaryView
          key={id}
          id={id}
          onDeleted={() => {
            list.reload();
            go("lectures");
          }}
        />
      )}
      <Card title="지난 요약">
        {list.data.length === 0 ? (
          <Empty>아직 만든 요약이 없습니다.</Empty>
        ) : (
          <table className={tableClass}>
            <thead>
              <tr>
                <th>제목</th>
                <th>영상</th>
                <th>분량</th>
                <th>만든 날</th>
              </tr>
            </thead>
            <tbody>
              {list.data.map((s) => (
                <tr key={s.id} onClick={() => go("lectures", s.id)} style={{ cursor: "pointer", fontWeight: s.id === id ? 600 : undefined }}>
                  <td>{s.title}</td>
                  <td>
                    {s.video_title} ({stamp(s.duration_s)})
                  </td>
                  <td>{PAGES[String(s.pages) as "1" | "2"]}</td>
                  <td>{new Date(s.created_at).toLocaleDateString("ko-KR")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </Stack>
  );
}
