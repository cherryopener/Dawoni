import { CheckCircle2, CircleAlert, CircleDashed, Loader2, XCircle } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { api, errorText, useApi, waitJob } from "@/lib/api";
import type { DetectedAgent, Job } from "@/lib/types";

const LABEL: Record<string, string> = {
  agy: "agy (Gemini)",
  claude: "Claude Code",
  codex: "Codex (ChatGPT)",
  local: "로컬 LLM",
};

const INSTALL: Record<string, string> = {
  agy: "Antigravity를 설치하면 agy가 함께 깔립니다.",
  claude: "npm install -g @anthropic-ai/claude-code",
  codex: "npm install -g @openai/codex",
  local: "Ollama·LM Studio를 켜고 설정에 주소를 넣으세요.",
};

type Test = { state: "running" | "passed" | "failed"; text: string };

/** CLI 연결 마법사 (PLAN §11-1): 설치·로그인 감지 → 안내 → 동봉 그림으로 연결 시험. */
export function AgentList() {
  const { data, loading, reload } = useApi<DetectedAgent[]>("/agents/detect", { refreshOnJobs: false });
  const [tests, setTests] = useState<Record<string, Test>>({});
  const test = async (name: string) => {
    setTests((t) => ({ ...t, [name]: { state: "running", text: "시험 문제(그림 1장) 보내는 중" } }));
    try {
      const j = await waitJob(await api.post<Job>(`/agents/${name}/test`, {}));
      const r = j.result as { passed: boolean; problems: string[]; duration_s: number; model: string | null };
      setTests((t) => ({
        ...t,
        [name]: r.passed
          ? { state: "passed", text: `통과 · ${r.model ?? ""} · ${r.duration_s}초` }
          : { state: "failed", text: r.problems.join(" ") || "응답을 확인해 주세요" },
      }));
    } catch (e) {
      setTests((t) => ({ ...t, [name]: { state: "failed", text: errorText(e) } }));
    }
  };
  if (loading && !data) return <div className="py-3 text-text-3">AI 프로그램을 찾는 중…</div>;
  return (
    <div>
      {(data ?? []).map((a) => {
        const t = tests[a.name];
        const ok = a.installed && a.logged_in !== false;
        const icon =
          t?.state === "running" ? (
            <Loader2 className="size-5 animate-spin text-accent" />
          ) : t?.state === "failed" ? (
            <XCircle className="size-5 text-bad" />
          ) : ok && (t?.state === "passed" || a.enabled) ? (
            <CheckCircle2 className="size-5 text-ok" />
          ) : a.installed ? (
            <CircleAlert className="size-5 text-warn" />
          ) : (
            <CircleDashed className="size-5 text-text-3" />
          );
        const note = t
          ? t.text
          : !a.installed
            ? `설치 안 됨 · ${INSTALL[a.name] ?? ""}`
            : a.logged_in === false
              ? `로그인 필요 · 터미널에서 ${a.name}를 한 번 실행해 로그인하세요`
              : [a.version, a.model, ...a.notes].filter(Boolean).join(" · ");
        return (
          <div key={a.name} className="flex items-center gap-3 border-t border-line py-3 first:border-t-0">
            {icon}
            <div className="min-w-0 flex-1">
              <div>{LABEL[a.name] ?? a.name}</div>
              <div className={"text-[12px] " + (t?.state === "failed" ? "text-bad" : "text-text-2")}>{note}</div>
            </div>
            {a.installed ? (
              <Button size="sm" variant="ghost" disabled={t?.state === "running"} onClick={() => void test(a.name)}>
                연결 시험
              </Button>
            ) : (
              <Button size="sm" variant="ghost" onClick={reload}>
                다시 찾기
              </Button>
            )}
          </div>
        );
      })}
    </div>
  );
}
