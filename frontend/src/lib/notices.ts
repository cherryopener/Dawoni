import { useEffect, useSyncExternalStore } from "react";
import { api, onJobsChanged } from "./api";
import type { Notice, NoticeKind, StepKey } from "./types";

// 알림 한 곳 (`GET /api/notifications`): 종 버튼·위 메뉴·시험 단계 표시줄의 숫자가 모두 여기서 나온다.
// "할 일"(강사 확인 필요)은 서버에서 처리되면 사라지고, "소식"(작업 끝남·실패)은 읽으면 사라진다.

export const NEWS: ReadonlySet<NoticeKind> = new Set(["job_done", "job_failed"]);

export const STEP_OF: Partial<Record<NoticeKind, StepKey>> = {
  key: "read",
  rubric: "rubric",
  unreadable: "scan",
  rename: "scan",
  review: "grade",
};

const READ_KEY = "notices-read";
const POLL_MS = 8000;

function loadRead(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(READ_KEY) ?? "[]") as string[]);
  } catch {
    return new Set();
  }
}

type State = { notices: Notice[]; read: ReadonlySet<string> };
let state: State = { notices: [], read: loadRead() };
const subs = new Set<() => void>();
const set = (s: State) => {
  state = s;
  subs.forEach((f) => f());
};

export function refreshNotices(): void {
  api
    .get<Notice[]>("/notifications")
    .then((notices) => set({ ...state, notices }))
    .catch(() => undefined);
}

let started = false;
function start() {
  if (started) return;
  started = true;
  refreshNotices();
  setInterval(refreshNotices, POLL_MS);
  onJobsChanged(refreshNotices);
}

export function useNotices(): State {
  useEffect(start, []);
  return useSyncExternalStore(
    (f) => {
      subs.add(f);
      return () => subs.delete(f);
    },
    () => state,
  );
}

export function markRead(ids: string[]): void {
  const read = new Set([...state.read, ...ids]);
  try {
    localStorage.setItem(READ_KEY, JSON.stringify([...read].slice(-200)));
  } catch {
    /* 저장소를 못 쓰면 이번 창에서만 */
  }
  set({ ...state, read });
}

export const isTodo = (n: Notice) => !NEWS.has(n.kind);

export function bellCount(notices: Notice[], read: ReadonlySet<string>): number {
  return notices.filter((n) => isTodo(n) || !read.has(n.id)).length;
}

export function stepCounts(notices: Notice[], examId: string): Partial<Record<StepKey, number>> {
  const out: Partial<Record<StepKey, number>> = {};
  for (const n of notices) {
    const step = STEP_OF[n.kind];
    if (n.exam_id !== examId || !step) continue;
    out[step] = (out[step] ?? 0) + n.count;
  }
  return out;
}

export function navCounts(notices: Notice[]): Record<string, number> {
  return {
    exams: notices.filter((n) => n.exam_id && isTodo(n)).length,
    learning: notices.filter((n) => n.kind === "proposal").reduce((a, n) => a + n.count, 0),
  };
}
