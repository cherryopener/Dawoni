import { useCallback, useEffect, useState } from "react";
import s from "./App.module.css";
import { Badge, Button, JobProgress } from "./components/ui";
import { api, onJobsChanged } from "./lib/api";
import { go, useRoute } from "./lib/router";
import type { Job, Status } from "./lib/types";
import { ExamPage } from "./pages/Exam";
import { ExamsPage } from "./pages/Exams";
import { LecturesPage } from "./pages/Lectures";
import { RulesPage } from "./pages/Rules";
import { ScopeEditPage, ScopesPage } from "./pages/Scopes";
import { SetupPage } from "./pages/Setup";

const JOB_NAMES: Record<string, string> = {
  agent_test: "AI 연결 시험",
  refine_rule: "규칙 다듬기",
  ingest_textbook: "교재 → 범위 프로필",
  ingest_exam: "시험지 PDF → 문항",
  solve: "해설 만들기",
  rubric: "루브릭 초안",
  scan: "손글씨 판독",
  grade: "채점",
  lecture_summary: "강의 요약",
};

function JobsButton() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [open, setOpen] = useState(false);
  const refresh = useCallback(() => {
    api.get<Job[]>("/jobs").then(setJobs).catch(() => undefined);
  }, []);
  useEffect(() => {
    refresh();
    const off = onJobsChanged(refresh);
    const t = setInterval(refresh, 2000);
    return () => {
      off();
      clearInterval(t);
    };
  }, [refresh]);
  const running = jobs.filter((j) => j.status === "running" || j.status === "queued");
  return (
    <div style={{ position: "relative" }}>
      <Button onClick={() => setOpen(!open)} small>
        작업 {running.length > 0 ? <Badge tone="accent">{running.length}개 진행 중</Badge> : "기록"}
      </Button>
      {open && (
        <div className={s.jobs}>
          {jobs.length === 0 && <span style={{ color: "var(--text-3)" }}>아직 작업이 없습니다.</span>}
          {jobs.slice(0, 15).map((j) => (
            <div key={j.id} className={s.jobItem}>
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <b>{JOB_NAMES[j.kind] ?? j.kind}</b>
                <span style={{ flex: 1 }} />
                {j.status === "succeeded" && <Badge tone="ok">완료</Badge>}
                {j.status === "failed" && <Badge tone="bad">실패</Badge>}
                {j.status === "cancelled" && <Badge>취소</Badge>}
              </div>
              <JobProgress job={j} />
              {j.error && <span style={{ color: "var(--bad)" }}>{j.error.message}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const NAV: { key: string; label: string }[] = [
  { key: "exams", label: "시험 · 채점" },
  { key: "scopes", label: "범위 프로필" },
  { key: "lectures", label: "강의 요약" },
  { key: "rules", label: "전역 규칙" },
  { key: "setup", label: "연결 · 설정" },
];

export function App() {
  const route = useRoute();
  const [status, setStatus] = useState<Status | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const loadStatus = useCallback(() => {
    api
      .get<Status>("/status")
      .then((st) => {
        setStatus(st);
        setErr(null);
      })
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);
  useEffect(loadStatus, [loadStatus]);

  if (err) {
    return (
      <div style={{ padding: 40 }}>
        <h2>서버에 연결할 수 없습니다</h2>
        <p>{err}</p>
        <p>터미널에서 <code>dawoni ui</code>로 다시 여세요.</p>
      </div>
    );
  }
  if (!status) return null;

  const page = !status.workspace_ok ? "setup" : (route[0] ?? "exams");
  const agent = status.agents[status.default_agent];
  let body;
  if (page === "setup") body = <SetupPage status={status} onChanged={loadStatus} />;
  else if (page === "rules") body = <RulesPage />;
  else if (page === "scopes" && route[1]) body = <ScopeEditPage id={route[1]} />;
  else if (page === "scopes") body = <ScopesPage />;
  else if (page === "lectures") body = <LecturesPage id={route[1]} />;
  else if (page === "exams" && route[1]) body = <ExamPage id={route[1]} tab={route[2]} />;
  else body = <ExamsPage />;

  return (
    <div className={s.shell}>
      <aside className={s.side}>
        <div className={s.brand}>
          다워니
          <small>강사 기준 서술형 채점</small>
        </div>
        {NAV.map((n) => (
          <button
            key={n.key}
            className={`${s.nav} ${page === n.key ? s.navActive : ""}`}
            onClick={() => go(n.key)}
            disabled={!status.workspace_ok && n.key !== "setup"}
          >
            {n.label}
          </button>
        ))}
        <div className={s.sideFoot}>
          작업공간
          <br />
          <b title={status.workspace ?? ""}>{status.workspace?.split(/[\\/]/).pop() ?? "없음"}</b>
          <br />v{status.version}
        </div>
      </aside>
      <div className={s.main}>
        <header className={s.top}>
          <span style={{ color: "var(--text-2)" }}>기본 AI</span>
          <Badge tone="accent">
            {status.default_agent}
            {agent?.model ? ` · ${agent.model}` : ""}
          </Badge>
          <span style={{ flex: 1 }} />
          <JobsButton />
        </header>
        <main className={s.content}>{body}</main>
      </div>
    </div>
  );
}
