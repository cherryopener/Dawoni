import { ArrowRight, Check } from "lucide-react";
import { useCallback, useState } from "react";
import { AGENT_NAMES, AgentList } from "@/components/Agents";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ErrorNote, Input } from "@/components/ui/misc";
import { Segmented } from "@/components/ui/segmented";
import { Select } from "@/components/ui/select";
import { api, ApiError, useAction } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useSession } from "@/lib/session";

const STEPS = ["강사 · AI", "저장소", "끝"];

/** 처음 실행 마법사 (PLAN §11-1): 강사 이름·AI 연결 → 저장소 → 끝. 화면마다 "다음" 하나만 강조. */
export function SetupWizard() {
  const { status, reload } = useSession();
  // 배포본은 이름이 처음부터 있으므로 이름만 보고 첫 단계(AI 연결)를 건너뛰지 않는다
  const [step, setStep] = useState(
    status.teacher_locked ? (status.workspace_ok ? 2 : 0) : status.teacher ? (status.workspace_ok ? 2 : 1) : 0,
  );
  const [name, setName] = useState(status.teacher ?? "");
  const [mode, setMode] = useState<"personal" | "team">("personal");
  const [path, setPath] = useState(status.workspace ?? "");
  const act = useAction();
  // 기본 AI는 이 PC에서 쓸 수 있는 것 중에서 고른다 (설정 기본값 claude를 그대로 두지 않게 — agy만 있는 PC)
  const [usable, setUsable] = useState<string[] | null>(null);
  const [agent, setAgent] = useState<string>("");
  // 하나뿐이면 그것으로, 여럿이면 강사가 고르게 비워 둔다
  const onUsable = useCallback((names: string[]) => {
    setUsable(names);
    setAgent((cur) => (names.includes(cur) ? cur : names.length === 1 ? names[0]! : ""));
  }, []);

  const locked = status.teacher_locked === true;

  const saveName = () =>
    act.run(async () => {
      if (!locked) {
        if (!name.trim()) throw new Error("강사 이름을 적어 주세요.");
      }
      if (usable !== null && usable.length === 0)
        throw new Error("쓸 수 있는 AI가 없습니다. 위 안내대로 하나를 설치·로그인한 뒤 '다시 찾기'를 누르세요.");
      if (!agent) throw new Error("기본 AI를 골라 주세요.");
      await api.patch("/config", locked ? { default_agent: agent } : { teacher: name.trim(), default_agent: agent });
      setStep(status.workspace_ok ? 2 : 1);
    });

  const choose = () =>
    act.run(async () => {
      const r = await api.post<{ path: string | null }>("/system/choose-folder");
      if (r.path) setPath(r.path);
    });

  const saveWorkspace = () =>
    act.run(async () => {
      if (!path.trim()) throw new Error("폴더를 골라 주세요.");
      try {
        await api.post("/workspace", { path, create: false });
      } catch (e) {
        if (!(e instanceof ApiError) || e.status !== 400) throw e;
        await api.post("/workspace", { path, create: true, mode }); // 빈 폴더 → 새 작업공간
      }
      setStep(2);
    });

  return (
    <div className="mx-auto flex min-h-full max-w-xl flex-col justify-center px-6 py-10">
      <div className="mb-6 flex items-center gap-2">
        {STEPS.map((s, i) => (
          <div key={s} className="flex items-center gap-2">
            <span
              className={cn(
                "grid size-6 place-items-center rounded-full text-[12px]",
                i < step ? "bg-ok-soft text-ok" : i === step ? "bg-accent text-accent-fg" : "bg-muted text-text-3",
              )}
            >
              {i < step ? <Check className="size-3.5" /> : i + 1}
            </span>
            <span className={cn("text-[13px]", i === step ? "text-text" : "text-text-3")}>{s}</span>
            {i < STEPS.length - 1 && <span className="mx-1 h-px w-8 bg-line" />}
          </div>
        ))}
      </div>
      <Card className="p-6">
        {step === 0 && (
          <>
            {locked ? (
              <>
                <h2 className="m-0 text-[18px]">{status.teacher} 선생님으로 로그인됨</h2>
                <p className="m-0 mt-1 text-text-2">문항 맡기·승인·확정 기록에 이 이름이 남습니다. 이름은 관리자 명단에서 바뀝니다.</p>
              </>
            ) : (
              <>
                <h2 className="m-0 text-[18px]">누가 쓰나요?</h2>
                <p className="m-0 mt-1 text-text-2">문항 맡기·승인·확정 기록에 남는 이름입니다.</p>
                <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="예: 김선생" className="mt-3 w-full" autoFocus />
              </>
            )}
            <h3 className="m-0 mt-6 text-[15px]">쓰고 있는 AI</h3>
            <p className="m-0 mt-1 text-[13px] text-text-2">이 PC에서 찾은 AI 프로그램입니다. 하나만 연결되면 시작할 수 있습니다.</p>
            <div className="mt-2">
              <AgentList onUsable={onUsable} />
            </div>
            {usable !== null && usable.length > 0 && (
              <div className="mt-3 flex items-center gap-3 border-t border-line pt-3">
                <span className="flex-1">
                  기본 AI
                  <span className="block text-[12px] text-text-2">시험 읽기·판독·채점에 먼저 쓰는 AI입니다. 설정에서 바꿀 수 있습니다.</span>
                </span>
                <Select
                  value={agent}
                  placeholder="고르세요"
                  onChange={setAgent}
                  options={usable.map((n) => ({ value: n, label: AGENT_NAMES[n] ?? n }))}
                />
              </div>
            )}
          </>
        )}
        {step === 1 && (
          <>
            <h2 className="m-0 text-[18px]">자료를 둘 폴더</h2>
            <p className="m-0 mt-1 text-text-2">함께 쓰면 구글 드라이브 같은 동기화 폴더를 고르세요. 빈 폴더면 새로 만듭니다.</p>
            <div className="mt-4 flex flex-col gap-3">
              <Segmented
                value={mode}
                onChange={setMode}
                options={[
                  { value: "personal", label: "혼자 쓰기" },
                  { value: "team", label: "함께 쓰기" },
                ]}
              />
              <div className="flex gap-2">
                <Input value={path} onChange={(e) => setPath(e.target.value)} placeholder="D:\다워니" className="flex-1" />
                <Button variant="outline" onClick={choose}>
                  폴더 고르기
                </Button>
              </div>
            </div>
          </>
        )}
        {step === 2 && (
          <>
            <h2 className="m-0 text-[18px]">준비됐습니다</h2>
            <p className="m-0 mt-1 text-text-2">시험지와 해설지를 넣어 첫 시험을 만드세요. 설정은 언제든 바꿀 수 있습니다.</p>
          </>
        )}
        {act.error && (
          <div className="mt-4">
            <ErrorNote message={act.error} />
          </div>
        )}
        <div className="mt-6 flex justify-between">
          <Button variant="ghost" disabled={step === 0} onClick={() => setStep(step - 1)}>
            이전
          </Button>
          <Button
            variant="primary"
            disabled={act.busy}
            onClick={() => (step === 0 ? saveName() : step === 1 ? saveWorkspace() : reload())}
          >
            {step < 2 ? "다음" : "시험 만들러 가기"} <ArrowRight />
          </Button>
        </div>
      </Card>
    </div>
  );
}
