import { BookOpenCheck, GraduationCap, Loader2, Moon, Search, Settings, Sparkles, Sun, Users } from "lucide-react";
import { Activity, useCallback, useEffect, useState, type ReactNode } from "react";
import logoMark from "@/assets/logo-mark.png";
import { Coordinator } from "@/components/Coordinator";
import { JobsButton } from "@/components/Jobs";
import { NotificationsButton } from "@/components/Notifications";
import { Count } from "@/components/ui/count";
import { ErrorNote } from "@/components/ui/misc";
import { api, ApiError, errorText } from "@/lib/api";
import { cn } from "@/lib/cn";
import { navCounts, useNotices } from "@/lib/notices";
import { go, href, useRoute } from "@/lib/router";
import { SessionContext } from "@/lib/session";
import type { AuthStatus, Status } from "@/lib/types";
import { ExamPage } from "@/pages/Exam";
import { ExamsPage } from "@/pages/Exams";
import { LearningPage } from "@/pages/Learning";
import { LoginPage } from "@/pages/Login";
import { SettingsPage } from "@/pages/Settings";
import { SetupWizard } from "@/pages/Setup";
import { StudentsPage } from "@/pages/Students";

const NAV = [
  { key: "exams", label: "시험", icon: <GraduationCap /> },
  { key: "students", label: "학생", icon: <Users /> },
  { key: "learning", label: "배우기", icon: <BookOpenCheck /> },
  { key: "settings", label: "설정", icon: <Settings /> },
];

function useTheme(): [boolean, () => void] {
  const [dark, setDark] = useState(() => {
    try {
      const saved = localStorage.getItem("theme");
      if (saved) return saved === "dark";
    } catch {
      /* 저장소를 못 쓰면 시스템 설정 */
    }
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  });
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try {
      localStorage.setItem("theme", dark ? "dark" : "light");
    } catch {
      /* 무시 */
    }
  }, [dark]);
  return [dark, () => setDark(!dark)];
}

/** 화면을 오갈 때 입력·스크롤을 잃지 않게 한 번 연 화면은 숨겨 둔다 (옛 다워니 `Activity` 틀). */
function Keep({ show, children }: { show: boolean; children: ReactNode }) {
  const [seen, setSeen] = useState(show);
  useEffect(() => {
    if (show) setSeen(true);
  }, [show]);
  if (!seen && !show) return null;
  return <Activity mode={show ? "visible" : "hidden"}>{children}</Activity>;
}

function initials(name: string | null | undefined): string {
  if (!name) return "?";
  return name.length <= 2 ? name : name.slice(0, 1) + "T";
}

export function App() {
  const [dark, toggleTheme] = useTheme();
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const loadAuth = useCallback(() => {
    api.get<AuthStatus>("/auth/status").then(setAuth).catch((e: unknown) => setErr(errorText(e)));
  }, []);
  const loadStatus = useCallback(() => {
    api
      .get<Status>("/status")
      .then(setStatus)
      .catch((e: unknown) => {
        // 로그인이 풀렸으면(오프라인 기한 지남 등) 오류가 아니라 로그인 화면으로
        if (e instanceof ApiError && e.code === "needs_login") loadAuth();
        else setErr(errorText(e));
      });
  }, [loadAuth]);
  useEffect(loadAuth, [loadAuth]);
  // 로그인 전에는 /api/auth/* 말고는 서버가 needs_login으로 거절한다 — 로그인이 끝난 뒤에 읽는다
  const signedIn = auth !== null && (!auth.required || auth.state === "ok");
  useEffect(() => {
    if (signedIn) loadStatus();
  }, [signedIn, loadStatus]);
  // 로그인을 기다리는 동안은 기본 브라우저에서 끝났는지 2초마다 본다
  useEffect(() => {
    if (!auth || auth.state === "ok" || auth.state === "off") return;
    const t = setInterval(loadAuth, 2000);
    return () => clearInterval(t);
  }, [auth, loadAuth]);

  if (err) {
    return (
      <div className="mx-auto max-w-md p-10">
        <ErrorNote message="다워니 서버에 연결하지 못했습니다. 앱을 다시 열어 주세요." detail={err} />
      </div>
    );
  }
  if (auth && !signedIn) return <LoginPage auth={auth} />;
  if (!auth || !status) {
    return (
      <div className="grid h-full place-items-center text-text-3">
        <Loader2 className="size-6 animate-spin" />
      </div>
    );
  }
  const session = { status, reload: loadStatus };
  if (!status.workspace_ok || !status.teacher) {
    return (
      <SessionContext value={session}>
        <SetupWizard />
      </SessionContext>
    );
  }
  return (
    <SessionContext value={session}>
      <Shell dark={dark} toggleTheme={toggleTheme} teacher={status.teacher} email={auth.email} />
    </SessionContext>
  );
}

function Shell({ dark, toggleTheme, teacher, email }: { dark: boolean; toggleTheme: () => void; teacher: string; email: string | null }) {
  const route = useRoute();
  const [askOpen, setAskOpen] = useState(false);
  const top = route[0] ?? "exams";
  const { notices } = useNotices();
  const nav = navCounts(notices);

  useEffect(() => {
    const f = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setAskOpen(true);
      }
    };
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  }, []);

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-14 shrink-0 items-center gap-1 border-b border-line bg-card px-4">
        <a href={href("exams")} className="mr-4 flex items-center gap-2 text-[15px] font-medium text-text no-underline">
          <img src={logoMark} alt="" className="size-7 object-contain" />
          다워니
        </a>
        {NAV.map((n) => (
          <a
            key={n.key}
            href={href(n.key)}
            className={cn(
              "flex h-9 items-center gap-1.5 rounded-lg px-3 text-text-2 no-underline hover:bg-muted hover:text-text [&_svg]:size-4",
              top === n.key && "bg-muted text-text",
            )}
          >
            {n.icon}
            {n.label}
            <Count n={nav[n.key] ?? 0} tone={n.key === "exams" ? "bad" : "warn"} />
          </a>
        ))}
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => setAskOpen(true)}
          className="flex h-9 w-64 cursor-pointer items-center gap-2 rounded-lg border border-line bg-muted px-3 text-left text-text-3 hover:text-text-2"
        >
          <Sparkles className="size-4" />
          <span className="flex-1">총괄 AI에게 부탁하기</span>
          <kbd className="rounded border border-line px-1.5 text-[11px]">Ctrl K</kbd>
        </button>
        <a href={href("students")} className="grid size-9 place-items-center rounded-lg text-text-2 hover:bg-muted" aria-label="학생 검색">
          <Search className="size-4" />
        </a>
        <JobsButton />
        <NotificationsButton />
        <button type="button" onClick={toggleTheme} className="grid size-9 cursor-pointer place-items-center rounded-lg text-text-2 hover:bg-muted" aria-label="밝기">
          {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
        </button>
        <button
          type="button"
          onClick={() => go("settings")}
          className="ml-1 grid size-8 cursor-pointer place-items-center rounded-full bg-accent-soft text-[12px] font-medium text-accent-text"
          title={email ? `${teacher} (${email})` : teacher}
        >
          {initials(teacher)}
        </button>
      </header>
      <main className="min-h-0 flex-1 overflow-auto">
        <Keep show={top === "exams" && !route[1]}>
          <ExamsPage />
        </Keep>
        {top === "exams" && route[1] && <ExamPage key={route[1]} examId={route[1]} step={route[2]} />}
        <Keep show={top === "students"}>
          <StudentsPage />
        </Keep>
        <Keep show={top === "learning"}>
          <LearningPage />
        </Keep>
        <Keep show={top === "settings"}>
          <SettingsPage />
        </Keep>
      </main>
      <Coordinator open={askOpen} onOpenChange={setAskOpen} />
    </div>
  );
}
