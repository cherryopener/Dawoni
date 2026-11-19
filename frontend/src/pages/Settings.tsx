import { Bot, FolderSync, LogOut, Printer, ScanText, UserRound } from "lucide-react";
import { useEffect, useState } from "react";
import { AgentList } from "@/components/Agents";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Help } from "@/components/ui/help";
import { Advanced, ErrorNote, Input, PageTitle } from "@/components/ui/misc";
import { Segmented } from "@/components/ui/segmented";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { api, useAction, useApi } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { AuthStatus, Config, Role, Sharing, Tier, TierModel } from "@/lib/types";

const AGENT_LABEL: Record<string, string> = { agy: "agy (Gemini)", claude: "Claude Code", codex: "Codex", local: "로컬 LLM" };
const TIER_LABEL: Record<Tier, string> = { fast: "빠름", standard: "보통", strong: "강함" };
/** 등급에 고를 수 있는 모델 (낮은 것부터). 지금 설정에 다른 이름이 있으면 그것도 보인다. */
const MODELS: Record<string, { value: string; label: string }[]> = {
  claude: [
    { value: "claude-haiku-5-5", label: "Haiku 5.5" },
    { value: "claude-sonnet-5-5", label: "Sonnet 5.5" },
    { value: "claude-opus-5-5", label: "Opus 5.5" },
    { value: "claude-fable-5-2", label: "Fable 5.2" },
  ],
  agy: [
    { value: "gemini-3.8-flash-medium", label: "Gemini 3.8 Flash (medium)" },
    { value: "gemini-3.8-flash-high", label: "Gemini 3.8 Flash (high)" },
    { value: "gemini-4-argon-low", label: "Gemini 4 Argon (low)" },
    { value: "gemini-4-argon-medium", label: "Gemini 4 Argon (medium)" },
    { value: "gemini-4-hydrogen-low", label: "Gemini 4 Hydrogen (low)" },
    { value: "gemini-4-hydrogen-medium", label: "Gemini 4 Hydrogen (medium)" },
    { value: "gemini-4-hydrogen-high", label: "Gemini 4 Hydrogen (high)" },
  ],
};
/** 생각 깊이 (claude `--effort`, codex). "auto"는 CLI 기본 — 설정에는 null로 둔다. */
const EFFORTS = [
  { value: "auto", label: "기본" },
  { value: "low", label: "낮음" },
  { value: "medium", label: "보통" },
  { value: "high", label: "높음" },
  { value: "xhigh", label: "아주 높음" },
  { value: "max", label: "최대" },
];
type Usage = { days: number; roles: Record<string, { calls: number; tokens: number; repairs: number }> };

/** 설정은 카드 몇 장: AI 연결 / 저장소 / 스캐너 / 판독 / 계정 (PLAN §11-1-2). 바꿀 일이 드문 것은 "고급" 안. */
export function SettingsPage() {
  const { status, reload: reloadStatus } = useSession();
  const cfg = useApi<Config>("/config", { refreshOnJobs: false });
  const roles = useApi<Role[]>("/roles", { refreshOnJobs: false });
  const usage = useApi<Usage>("/usage");
  const sharing = useApi<Sharing>("/workspace/sharing", { refreshOnJobs: false });
  const auth = useApi<AuthStatus>("/auth/status", { refreshOnJobs: false });
  const [teacher, setTeacher] = useState(status.teacher ?? "");
  const [scanner, setScanner] = useState({ smb_url: "", username: "" });
  const [ocrKeys, setOcrKeys] = useState<Record<string, string>>({});
  const act = useAction();
  const c = cfg.data;

  useEffect(() => {
    if (c) setScanner({ smb_url: c.scanner.smb_url ?? "", username: c.scanner.username ?? "" });
  }, [c]);

  const patch = (body: unknown) =>
    act.run(async () => {
      await api.patch("/config", body);
      cfg.reload();
      roles.reload();
    });
  const saveTeacher = () =>
    act.run(async () => {
      if (!teacher.trim()) throw new Error("강사 이름을 비울 수 없습니다.");
      await api.patch("/config", { teacher: teacher.trim() });
      reloadStatus();
    });
  const setMode = (mode: "personal" | "team") =>
    act.run(async () => {
      await api.put("/workspace/mode", { mode });
      sharing.reload();
    });
  const changeFolder = () =>
    act.run(async () => {
      const r = await api.post<{ path: string | null }>("/system/choose-folder");
      if (!r.path) return;
      await api.post("/workspace", { path: r.path, create: false });
      reloadStatus();
    });
  const openScanner = () =>
    act.run(async () => {
      await api.patch("/config", { scanner: scanner });
      await api.post("/scanner/open");
    });
  const logout = () =>
    act.run(async () => {
      await api.post("/auth/logout");
      window.location.reload();
    });

  const maxTokens = Math.max(1, ...Object.values(usage.data?.roles ?? {}).map((r) => r.tokens));

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5 px-6 py-8">
      <PageTitle title="설정" desc="AI 연결 · 저장소 · 스캐너 · 손글씨 판독 · 계정. 바꿀 일이 드문 것은 '고급' 안에 있습니다." />
      {(act.error || cfg.error) && <ErrorNote message={(act.error ?? cfg.error)!} />}

      <Card className="overflow-hidden">
        <CardHeader icon={<Bot />} tone="accent" title="AI 연결" desc="이미 구독 중인 AI의 공식 프로그램을 일꾼으로 씁니다." />
        <CardBody>
          <AgentList />
          {c && (
            <div className="mt-3 divide-y divide-line rounded-xl border border-line px-4">
              <div className="flex items-center gap-3 py-2.5">
                <span className="flex-1">기본 AI</span>
                <Select
                  value={c.default_agent}
                  onChange={(v) => void patch({ default_agent: v })}
                  options={Object.entries(AGENT_LABEL).map(([value, label]) => ({ value, label }))}
                />
              </div>
              <div className="flex items-center gap-3 py-2.5">
                <span className="flex-1">
                  총괄 AI <Help>"2반 3번만 다시 채점해" 같은 부탁을 작업으로 바꾸는 AI입니다. 학생 답안은 보지 않습니다. 강사마다 구독하는 AI로 고르세요.</Help>
                </span>
                <Select
                  value={c.coordinator_agent ?? ""}
                  placeholder="기본 AI와 같게"
                  onChange={(v) => void patch({ coordinator_agent: v })}
                  options={Object.entries(AGENT_LABEL).map(([value, label]) => ({ value, label }))}
                />
              </div>
            </div>
          )}
          <div className="mt-4">
            <Advanced label="역할별 모델 · 토큰 사용량">
              {c && <TierTable agent={c.default_agent} config={c} roles={roles.data ?? []} onPatch={(body) => void patch(body)} />}
              <table className="mt-5 w-full border-collapse text-left text-[13px]">
                <thead className="border-b border-line text-[12px] text-text-3">
                  <tr>
                    <th className="py-2 font-normal">역할</th>
                    <th className="py-2 font-normal">등급</th>
                    <th className="py-2 text-right font-normal">최근 {usage.data?.days ?? 30}일 토큰</th>
                  </tr>
                </thead>
                <tbody>
                  {(roles.data ?? []).map((r) => {
                    const u = usage.data?.roles[r.name];
                    return (
                      <tr key={r.name} className="border-t border-line/70">
                        <td className="py-1.5">
                          {r.title}
                          {r.tier !== r.default_tier && <span className="ml-1 text-[11px] text-text-3">(기본 {r.default_tier})</span>}
                        </td>
                        <td className="py-1.5">
                          <Segmented<Tier>
                            value={r.tier}
                            onChange={(v) => void patch({ role_tiers: { ...(c?.role_tiers ?? {}), [r.name]: v } })}
                            options={[
                              { value: "fast", label: "빠름" },
                              { value: "standard", label: "보통" },
                              { value: "strong", label: "강함" },
                            ]}
                          />
                        </td>
                        <td className="w-40 py-1.5 text-right tabular-nums">
                          {u ? `${Math.round(u.tokens / 1000).toLocaleString()}k · ${u.calls}회${u.repairs ? ` · 수리 ${u.repairs}` : ""}` : "—"}
                          {u && <div className="ml-auto mt-1 h-1 rounded-full bg-accent" style={{ width: `${(u.tokens / maxTokens) * 100}%` }} />}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <p className="m-0 mt-2 text-[12px] text-text-3">수리가 쌓이는 역할만 등급을 올리세요. 손글씨 판독은 "빠름"을 쓰지 않습니다 (㉠→㉢ 오독).</p>
            </Advanced>
          </div>
        </CardBody>
      </Card>

      <Card className="overflow-hidden">
        <CardHeader icon={<FolderSync />} title="저장소" desc="시험·학생·판독본이 놓이는 작업공간 폴더" />
        <CardBody className="flex flex-col gap-3">
          {sharing.data && (
            <Segmented
              value={sharing.data.mode}
              onChange={(v) => void setMode(v)}
              options={[
                { value: "personal", label: "혼자 쓰기" },
                { value: "team", label: "함께 쓰기" },
              ]}
            />
          )}
          <div className="flex items-center gap-2">
            <Input readOnly value={status.workspace ?? ""} className="flex-1 bg-muted/60 font-mono text-[12px] text-text-2" />
            <Button variant="outline" onClick={() => void changeFolder()}>
              바꾸기
            </Button>
          </div>
          {sharing.data && (
            <div className="flex items-center gap-2 text-[13px] text-text-2">
              {sharing.data.provider && <Badge tone="ok">{sharing.data.provider} 동기화 폴더</Badge>}
              {sharing.data.conflicts.length > 0 ? <Badge tone="warn">충돌 사본 {sharing.data.conflicts.length}개</Badge> : <span>충돌 사본 없음</span>}
            </div>
          )}
        </CardBody>
      </Card>

      <Card className="overflow-hidden">
        <CardHeader icon={<Printer />} title="복합기 스캐너" desc="공유 폴더 주소와 사용자 이름만 저장합니다. 비밀번호는 Windows가 처음 열 때 묻고 보관합니다." />
        <CardBody className="flex gap-2">
          <Input value={scanner.smb_url} onChange={(e) => setScanner({ ...scanner, smb_url: e.target.value })} placeholder="smb://192.168.0.100/share" className="flex-1" />
          <Input value={scanner.username} onChange={(e) => setScanner({ ...scanner, username: e.target.value })} placeholder="사용자" className="w-36" />
          <Button variant="outline" disabled={act.busy} onClick={() => void openScanner()}>
            저장하고 열기
          </Button>
        </CardBody>
      </Card>

      {c && (
        <Card className="overflow-hidden">
          <CardHeader icon={<ScanText />} title="손글씨 판독" desc="AI가 손글씨 답안을 읽는 방법" />
          <CardBody className="flex flex-col divide-y divide-line/70">
            <Switch
              checked={c.ocr.cross_check}
              onCheckedChange={(v) => void patch({ ocr: { cross_check: v } })}
              label="교차 판독"
              desc="다른 모델로 한 번 더 읽어 다른 곳을 표시합니다. 판독 토큰이 두 배."
            />
            <Switch
              checked={c.ocr.auto_accept}
              onCheckedChange={(v) => void patch({ ocr: { auto_accept: v } })}
              label="AI가 판독을 바로 확정"
              desc="[판독불가]가 없는 답안은 AI가 읽은 그대로 확정하고 채점합니다. 강사는 [판독불가]와 채점 검토만 봅니다. 끄면 모든 판독문이 강사 확인 전으로 남습니다."
            />
            <Switch checked={c.ocr.clova_enabled} onCheckedChange={(v) => void patch({ ocr: { clova_enabled: v } })} label="CLOVA OCR 참고" desc="결과를 참고 자료로 붙입니다." />
            {c.ocr.clova_enabled && (
              <div className="flex gap-2 pb-2">
                <Input defaultValue={c.ocr.clova_invoke_url ?? ""} placeholder="Invoke URL" className="flex-1" onChange={(e) => setOcrKeys({ ...ocrKeys, clova_invoke_url: e.target.value })} />
                <Input type="password" defaultValue={c.ocr.clova_secret ?? ""} placeholder="Secret" className="w-40" onChange={(e) => setOcrKeys({ ...ocrKeys, clova_secret: e.target.value })} />
              </div>
            )}
            <Switch checked={c.ocr.mathpix_enabled} onCheckedChange={(v) => void patch({ ocr: { mathpix_enabled: v } })} label="Mathpix 참고" desc="수식 OCR 결과를 참고 자료로 붙입니다." />
            {c.ocr.mathpix_enabled && (
              <div className="flex gap-2 pb-2">
                <Input defaultValue={c.ocr.mathpix_app_id ?? ""} placeholder="App ID" className="flex-1" onChange={(e) => setOcrKeys({ ...ocrKeys, mathpix_app_id: e.target.value })} />
                <Input type="password" defaultValue={c.ocr.mathpix_app_key ?? ""} placeholder="App Key" className="w-40" onChange={(e) => setOcrKeys({ ...ocrKeys, mathpix_app_key: e.target.value })} />
              </div>
            )}
            {Object.keys(ocrKeys).length > 0 && (
              <div className="flex justify-end">
                <Button size="sm" variant="outline" onClick={() => void patch({ ocr: ocrKeys }).then(() => setOcrKeys({}))}>
                  키 저장
                </Button>
              </div>
            )}
          </CardBody>
        </Card>
      )}

      <Card className="overflow-hidden">
        <CardHeader
          icon={<UserRound />}
          title="계정"
          desc={auth.data?.required ? `${auth.data.email ?? ""} · 허용 명단 확인됨${auth.data.offline ? " · 오프라인" : ""}` : "이 PC에서만 씁니다 (로그인 없음)."}
          right={
            auth.data?.required ? (
              <Button variant="ghost" onClick={() => void logout()}>
                <LogOut /> 로그아웃
              </Button>
            ) : undefined
          }
        />
        <CardBody className="flex items-center gap-2">
          <span className="w-20 text-[13px] text-text-2">강사 이름</span>
          {status.teacher_locked ? (
            <span className="flex-1 text-[13px]">
              {status.teacher} <span className="text-text-3">· 이름은 관리자 명단에서 바뀝니다</span>
            </span>
          ) : (
            <>
              <Input value={teacher} onChange={(e) => setTeacher(e.target.value)} className="flex-1" />
              <Button variant="outline" disabled={act.busy || teacher === status.teacher} onClick={() => void saveTeacher()}>
                저장
              </Button>
            </>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

/** 등급별 모델: 이 AI에서 빠름·보통·강함이 어느 모델·생각 깊이인지. 역할은 등급만 고르므로, 같은 등급의
 * 역할(예: 손글씨 판독과 해설지 판독의 '보통')은 모두 같은 모델을 쓴다 — 표의 '쓰는 역할'에 보인다. */
function TierTable({ agent, config, roles, onPatch }: { agent: string; config: Config; roles: Role[]; onPatch: (body: unknown) => void }) {
  const table = config.tiers[agent] ?? {};
  const models = MODELS[agent];
  const effort = agent === "claude" || agent === "codex"; // agy는 모델 이름에 깊이가 들어 있다
  const set = (tier: Tier, change: Partial<TierModel>) => {
    const now = table[tier] ?? { model: null, effort: null };
    onPatch({ tiers: { [agent]: { [tier]: { ...now, ...change } } } });
  };
  return (
    <div>
      <div className="mb-1.5 text-[13px] text-text-2">
        등급별 모델 — {AGENT_LABEL[agent] ?? agent}
        <Help>역할마다 등급(빠름·보통·강함)을 고르고, 등급마다 모델은 AI별로 하나입니다. 같은 등급의 역할은 모두 같은 모델을 씁니다.</Help>
      </div>
      <table className="w-full border-collapse text-left text-[13px]">
        <thead className="border-b border-line text-[12px] text-text-3">
          <tr>
            <th className="py-2 font-normal">등급</th>
            <th className="py-1.5 font-normal">모델</th>
            {effort && <th className="py-1.5 font-normal">생각 깊이</th>}
            <th className="py-1.5 font-normal">쓰는 역할</th>
          </tr>
        </thead>
        <tbody>
          {(["fast", "standard", "strong"] as Tier[]).map((tier) => {
            const t = table[tier] ?? { model: null, effort: null };
            const options = models ? [...models] : [];
            if (t.model && !options.some((o) => o.value === t.model)) options.push({ value: t.model, label: t.model });
            return (
              <tr key={tier} className="border-t border-line/70 align-middle">
                <td className="w-14 py-1.5">{TIER_LABEL[tier]}</td>
                <td className="py-1.5">
                  {options.length > 0 ? (
                    <Select value={t.model ?? ""} placeholder="AI 기본 모델" onChange={(v) => set(tier, { model: v })} options={options} className="w-52 min-w-0" />
                  ) : (
                    <span className="text-text-3">{t.model ?? "AI 기본 모델"}</span>
                  )}
                </td>
                {effort && (
                  <td className="py-1.5">
                    <Select value={t.effort ?? "auto"} onChange={(v) => set(tier, { effort: v === "auto" ? null : v })} options={EFFORTS} className="w-28 min-w-0" />
                  </td>
                )}
                <td className="py-1.5 text-[12px] text-text-2">
                  {roles
                    .filter((r) => r.tier === tier)
                    .map((r) => r.title)
                    .join(" · ") || "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
