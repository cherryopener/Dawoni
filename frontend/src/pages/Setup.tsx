import { useEffect, useState } from "react";
import {
  Badge,
  Button,
  Card,
  Check,
  ErrorBox,
  Field,
  Info,
  JobProgress,
  Notice,
  Row,
  Stack,
  TextInput,
  setTeacherName,
  teacherName,
} from "../components/ui";
import { api, errorText, useJob, useLoad } from "../lib/api";
import type { AgentName, DetectedAgent, Status } from "../lib/types";

const LOGIN_HELP: Record<AgentName, string> = {
  agy: "터미널에서 `agy`를 한 번 실행해 구독 계정으로 로그인하세요.",
  claude: "터미널에서 `claude`를 실행한 뒤 `/login`으로 구독 계정에 로그인하세요.",
  codex: "터미널에서 `codex login`으로 로그인하세요.",
};
const AGENT_LABEL: Record<AgentName, string> = {
  agy: "Antigravity (agy)",
  claude: "Claude Code",
  codex: "Codex CLI",
};

function WorkspaceCard({ status, onChanged }: { status: Status; onChanged: () => void }) {
  const [path, setPath] = useState(status.workspace ?? "");
  const [error, setError] = useState<string | null>(null);
  const submit = (create: boolean) => {
    setError(null);
    api
      .post<Status>("/workspace", { path, create })
      .then(onChanged)
      .catch((e: unknown) => setError(errorText(e)));
  };
  const choose = () => {
    api
      .post<{ path: string | null }>("/system/choose-folder")
      .then((r) => r.path && setPath(r.path))
      .catch((e: unknown) => setError(errorText(e)));
  };
  return (
    <Card title="1. 작업공간">
      <Stack>
        <span style={{ color: "var(--text-2)" }}>
          시험·학생 답안·채점 결과가 저장되는 폴더입니다. Google Drive 폴더를 고르면 다른 강사와 함께 쓸 수 있습니다.
        </span>
        {status.workspace_ok ? (
          <Info>지금 작업공간: {status.workspace}</Info>
        ) : (
          <Notice>{status.workspace_error ?? "작업공간을 먼저 정하세요."}</Notice>
        )}
        <Row>
          <div style={{ flex: 1, minWidth: 280 }}>
            <TextInput value={path} onChange={setPath} placeholder="/Users/이름/Documents/다워니" mono />
          </div>
          <Button onClick={choose}>폴더 고르기…</Button>
          <Button onClick={() => submit(false)} disabled={!path}>
            기존 작업공간 열기
          </Button>
          <Button variant="primary" onClick={() => submit(true)} disabled={!path}>
            여기에 새로 만들기
          </Button>
        </Row>
        <ErrorBox error={error} />
      </Stack>
    </Card>
  );
}

function AgentRow({ a, isDefault, onDefault, reload }: {
  a: DetectedAgent;
  isDefault: boolean;
  onDefault: () => void;
  reload: () => void;
}) {
  const [model, setModel] = useState(a.model ?? "");
  const [saved, setSaved] = useState(false);
  const test = useJob();
  const save = (patch: Record<string, unknown>) =>
    api.patch("/config", { agents: { [a.name]: patch } }).then(() => {
      setSaved(true);
      reload();
    });
  const result = test.job?.status === "succeeded" ? (test.job.result as { passed: boolean; problems: string[]; duration_s: number; model: string | null }) : null;
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
      <Row>
        <b style={{ minWidth: 150 }}>{AGENT_LABEL[a.name]}</b>
        {!a.installed ? (
          <Badge>미설치</Badge>
        ) : a.logged_in === false ? (
          <Badge tone="bad">로그인 필요</Badge>
        ) : (
          <Badge tone="ok">{a.version ?? "설치됨"}</Badge>
        )}
        <span style={{ fontSize: 12, color: "var(--text-3)" }}>{a.path ?? ""}</span>
      </Row>
      {a.installed && a.logged_in === false && <span style={{ fontSize: 12.5, color: "var(--text-2)" }}>{LOGIN_HELP[a.name]}</span>}
      {a.notes.map((n, i) => (
        <span key={i} style={{ fontSize: 12, color: "var(--warn)" }}>
          {n}
        </span>
      ))}
      {a.installed && (
        <Row>
          <span style={{ fontSize: 12.5, color: "var(--text-2)" }}>모델</span>
          <div style={{ width: 240 }}>
            <TextInput value={model} onChange={(v) => { setModel(v); setSaved(false); }} placeholder="CLI 기본 모델" mono />
          </div>
          <Button small onClick={() => void save({ model: model || null })} disabled={saved || model === (a.model ?? "")}>
            저장
          </Button>
          <Check checked={a.enabled} onChange={(v) => void save({ enabled: v })}>
            사용
          </Check>
          <Check checked={isDefault} onChange={(v) => v && onDefault()} disabled={!a.enabled}>
            기본 AI
          </Check>
          <Button small onClick={() => void test.run(() => api.post(`/agents/${a.name}/test`, { model: model || null }))} disabled={test.running}>
            연결 시험
          </Button>
        </Row>
      )}
      <JobProgress job={test.job} />
      {result && (result.passed ? (
        <Row><Badge tone="ok">통과 · {result.duration_s}초 · {result.model ?? "기본 모델"}</Badge></Row>
      ) : (
        <span style={{ color: "var(--bad)", fontSize: 12.5 }}>확인 필요: {result.problems.join(" / ")}</span>
      ))}
      <ErrorBox error={test.error} />
    </div>
  );
}

function AgentsCard({ status, onChanged }: { status: Status; onChanged: () => void }) {
  const { data, error, reload } = useLoad<DetectedAgent[]>("/agents/detect");
  const setDefault = (name: AgentName) =>
    api.patch("/config", { default_agent: name }).then(onChanged);
  return (
    <Card title="2. AI 연결" actions={<Button small onClick={reload}>다시 찾기</Button>}>
      <Stack>
        <span style={{ color: "var(--text-2)" }}>
          이미 구독 중인 AI의 공식 명령줄 도구를 일꾼으로 씁니다 (API 키 불필요). "연결 시험"은 그림 1장으로 판독·수식·한글·JSON 응답을 확인합니다.
        </span>
        <ErrorBox error={error} />
        {!data && !error && <span>찾는 중…</span>}
        {data?.map((a) => (
          <AgentRow
            key={a.name}
            a={a}
            isDefault={status.default_agent === a.name}
            onDefault={() => void setDefault(a.name)}
            reload={() => {
              reload();
              onChanged();
            }}
          />
        ))}
      </Stack>
    </Card>
  );
}

type Config = {
  ocr: { cross_check: boolean; clova_enabled: boolean; mathpix_enabled: boolean };
  docx: { body_font: string; body_size_pt: number };
};

function OptionsCard() {
  const { data, reload } = useLoad<Config>("/config");
  const [name, setName] = useState(teacherName());
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => setMsg(null), [name]);
  if (!data) return null;
  const patch = (p: unknown) => api.patch("/config", p).then(reload);
  return (
    <Card title="3. 채점 · 출력 설정">
      <Stack>
        <Row>
          <div style={{ width: 240 }}>
            <Field label="강사 이름" hint="루브릭 승인·판독 확인·점수 확인 기록에 남습니다 (이 PC에만 저장)">
              <TextInput value={name} onChange={setName} />
            </Field>
          </div>
          <Button small onClick={() => { setTeacherName(name); setMsg("저장했습니다"); }}>저장</Button>
          {msg && <Badge tone="ok">{msg}</Badge>}
        </Row>
        <Check checked={data.ocr.cross_check} onChange={(v) => void patch({ ocr: { cross_check: v } })}>
          손글씨 판독할 때 두 번째 AI로 교차 판독 (다르게 읽은 줄을 표시 — 호출이 2배)
        </Check>
        <span style={{ fontSize: 12.5, color: "var(--text-3)" }}>
          Word 출력 글꼴: {data.docx.body_font} {data.docx.body_size_pt}pt · 외부 OCR(CLOVA·Mathpix)은 학생 답안이 외부로 나가므로 기본으로 꺼 둡니다.
        </span>
      </Stack>
    </Card>
  );
}

export function SetupPage({ status, onChanged }: { status: Status; onChanged: () => void }) {
  return (
    <Stack gap={16}>
      <h2>연결 · 설정</h2>
      <WorkspaceCard status={status} onChanged={onChanged} />
      <AgentsCard status={status} onChanged={onChanged} />
      <OptionsCard />
    </Stack>
  );
}
