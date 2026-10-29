import { useCallback, useEffect, useRef, useState } from "react";
import type { Job } from "./types";

export class ApiError extends Error {
  status: number;
  code: string | undefined;
  /** 오류 응답의 나머지 필드 (예: 이미 등록된 시험의 exam_id). */
  extra: Record<string, unknown>;
  constructor(status: number, message: string, code?: string, extra: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.extra = extra;
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    let code: string | undefined;
    let extra: Record<string, unknown> = {};
    try {
      const body = (await res.json()) as { detail?: unknown; code?: string } & Record<string, unknown>;
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = JSON.stringify(body.detail);
      code = body.code;
      extra = body;
    } catch {
      /* JSON이 아닌 오류 */
    }
    throw new ApiError(res.status, detail, code, extra);
  }
  return (await res.json()) as T;
}

export const api = {
  get<T>(path: string): Promise<T> {
    return fetch(`/api${path}`, { credentials: "same-origin" }).then((r) => handle<T>(r));
  },
  send<T>(method: "POST" | "PUT" | "PATCH" | "DELETE", path: string, body?: unknown): Promise<T> {
    return fetch(`/api${path}`, {
      method,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then((r) => handle<T>(r));
  },
  post<T>(path: string, body?: unknown): Promise<T> {
    return api.send<T>("POST", path, body ?? {});
  },
  put<T>(path: string, body: unknown): Promise<T> {
    return api.send<T>("PUT", path, body);
  },
  patch<T>(path: string, body: unknown): Promise<T> {
    return api.send<T>("PATCH", path, body);
  },
  del<T>(path: string): Promise<T> {
    return api.send<T>("DELETE", path);
  },
  upload<T>(path: string, form: FormData): Promise<T> {
    return fetch(`/api${path}`, { method: "POST", credentials: "same-origin", body: form }).then(
      (r) => handle<T>(r),
    );
  },
};

/** 시험 폴더 안의 그림·답안지 이미지 주소 (쿠키로 인증되므로 <img>에 그대로). */
export function examFileUrl(examId: string, path: string): string {
  return `/api/exams/${encodeURIComponent(examId)}/file?path=${encodeURIComponent(path)}`;
}

export function errorText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}

// ---------------------------------------------------------------- 작업 (백그라운드)

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);
export const isTerminal = (j: Job) => TERMINAL.has(j.status);

type Listener = () => void;
const listeners = new Set<Listener>();
/** 작업 목록이 바뀌었음을 상단 작업 표시줄에 알린다. */
export function notifyJobs(): void {
  listeners.forEach((f) => f());
}
export function onJobsChanged(f: Listener): () => void {
  listeners.add(f);
  return () => listeners.delete(f);
}

export function waitJob(id: string, onUpdate?: (j: Job) => void): Promise<Job> {
  return new Promise((resolve, reject) => {
    const tick = () => {
      api
        .get<Job>(`/jobs/${id}`)
        .then((j) => {
          onUpdate?.(j);
          if (isTerminal(j)) {
            notifyJobs();
            resolve(j);
          } else setTimeout(tick, 800);
        })
        .catch(reject);
    };
    tick();
  });
}

/**
 * 버튼 하나 = 작업 하나. `run(() => api.post(...))`으로 시작하면 끝날 때까지 진행률을 보여 주고,
 * 성공하면 `onDone`을 부른다. 실패 문구는 `error`로.
 */
export function useJob(onDone?: (j: Job) => void) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const done = useRef(onDone);
  done.current = onDone;
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const run = useCallback(async (start: () => Promise<Job>) => {
    setError(null);
    try {
      const first = await start();
      notifyJobs();
      setJob(first);
      const last = await waitJob(first.id, (j) => alive.current && setJob(j));
      if (!alive.current) return;
      if (last.status === "succeeded") done.current?.(last);
      else if (last.status === "failed") setError(last.error?.message ?? "작업 실패");
      else if (last.status === "cancelled") setError("취소했습니다.");
    } catch (e) {
      if (alive.current) setError(errorText(e));
    }
  }, []);

  const running = job !== null && !isTerminal(job);
  return { job, running, error, run, setError };
}

/** GET 한 번 + 다시 읽기. */
export function useLoad<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    if (path === null) return;
    let ok = true;
    api
      .get<T>(path)
      .then((d) => {
        if (ok) {
          setData(d);
          setError(null);
        }
      })
      .catch((e: unknown) => ok && setError(errorText(e)));
    return () => {
      ok = false;
    };
  }, [path, n]);
  const reload = useCallback(() => setN((x) => x + 1), []);
  return { data, error, reload, setData };
}
