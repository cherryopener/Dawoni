import { Bot, FolderSync, LogOut, Printer, ScanText, UserRound } from "lucide-react";
import { useEffect, useState } from "react";
import { AgentList } from "@/components/Agents";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Help } from "@/components/ui/help";
import { Advanced, ErrorNote, Input } from "@/components/ui/misc";
import { Segmented } from "@/components/ui/segmented";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { api, useAction, useApi } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { AuthStatus, Config, Role, Sharing, Tier } from "@/lib/types";

const AGENT_LABEL: Record<string, string> = { agy: "agy (Gemini)", claude: "Claude Code", codex: "Codex", local: "로컬 LLM" };
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
    <div className="mx-auto flex max-w-3xl flex-col gap-4 px-6 py-8">
      <h1 className="m-0 text-[20px] font-medium">설정</h1>
      {(act.error || cfg.error) && <ErrorNote message={(act.error ?? cfg.error)!} />}

      <Card>
        <CardHeader icon={<Bot />} title="AI 연결" desc="이미 구독 중인 AI의 공식 프로그램을 일꾼으로 씁니다." />
        <CardBody className="pt-2">
          <AgentList />
          {c && (
            <>
              <div className="mt-3 flex items-center gap-3 border-t border-line pt-3">
                <span className="flex-1">기본 AI</span>
                <Select
                  value={c.default_agent}
                  onChange={(v) => void patch({ default_agent: v })}
                  options={Object.entries(AGENT_LABEL).map(([value, label]) => ({ value, label }))}
                />
              </div>
              <div className="mt-3 flex items-center gap-3">
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
            </>
          )}
          <div className="mt-4">
            <Advanced label="역할별 모델 · 토큰 사용량">
              <table className="w-full border-collapse text-left text-[13px]">
                <thead className="text-[12px] text-text-3">
                  <tr>
                    <th className="py-1.5 font-normal">역할</th>
                    <th className="py-1.5 font-normal">등급</th>
                    <th className="py-1.5 text-right font-normal">최근 {usage.data?.days ?? 30}일 토큰</th>
                  </tr>
                </thead>
                <tbody>
                  {(roles.data ?? []).map((r) => {
                    const u = usage.data?.roles[r.name];
                    return (
                      <tr key={r.name} className="border-t border-line">
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

      <Card>
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
            <Input readOnly value={status.workspace ?? ""} className="flex-1 text-text-2" />
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

      <Card>
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
        <Card>
          <CardHeader icon={<ScanText />} title="손글씨 판독" />
          <CardBody className="flex flex-col">
            <Switch
              checked={c.ocr.cross_check}
              onCheckedChange={(v) => void patch({ ocr: { cross_check: v } })}
              label="교차 판독"
              desc="다른 모델로 한 번 더 읽어 다른 곳을 표시합니다. 판독 토큰이 두 배."
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

      <Card>
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
