import { ArrowRight, Check } from "lucide-react";
import { useState } from "react";
import { AgentList } from "@/components/Agents";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ErrorNote, Input } from "@/components/ui/misc";
import { Segmented } from "@/components/ui/segmented";
import { api, ApiError, useAction } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useSession } from "@/lib/session";

const STEPS = ["강사 · AI", "저장소", "끝"];

/** 처음 실행 마법사 (PLAN §11-1): 강사 이름·AI 연결 → 저장소 → 끝. 화면마다 "다음" 하나만 강조. */
export function SetupWizard() {
  const { status, reload } = useSession();
  const [step, setStep] = useState(status.teacher ? (status.workspace_ok ? 2 : 1) : 0);
  const [name, setName] = useState(status.teacher ?? "");
  const [mode, setMode] = useState<"personal" | "team">("personal");
  const [path, setPath] = useState(status.workspace ?? "");
  const act = useAction();

  const saveName = () =>
    act.run(async () => {
      if (!name.trim()) throw new Error("강사 이름을 적어 주세요.");
      await api.patch("/config", { teacher: name.trim() });
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
            <h2 className="m-0 text-[18px]">누가 쓰나요?</h2>
            <p className="m-0 mt-1 text-text-2">문항 맡기·승인·확정 기록에 남는 이름입니다.</p>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="예: 김선생" className="mt-3 w-full" autoFocus />
            <h3 className="m-0 mt-6 text-[15px]">쓰고 있는 AI</h3>
            <p className="m-0 mt-1 text-[13px] text-text-2">이 PC에서 찾은 AI 프로그램입니다. 하나만 연결되면 시작할 수 있습니다.</p>
            <div className="mt-2">
              <AgentList />
            </div>
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
                <Input value={path} onChange={(e) => setPath(e.target.value)} placeholder="D:\다워이" className="flex-1" />
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
