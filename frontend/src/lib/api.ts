import { useCallback, useEffect, useRef, useState } from "react";
import type { Job } from "./types";

// 서버 API (다워니 api.ts 틀). 오류 응답은 {detail, code} — detail은 강사에게 보여 줄 한국어 한 문장.

export class ApiError extends Error {
  status: number;
  code: string | undefined;
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
      else if (body.detail !== undefined) detail = JSON.stringify(body.detail);
      code = body.code;
      extra = body;
    } catch {
      /* JSON이 아닌 오류 */
    }
    throw new ApiError(res.status, detail, code, extra);
  }
  return (await res.json()) as T;
}

function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  return fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then((r) => handle<T>(r));
}

export const api = {
  get: <T>(path: string) => send<T>("GET", path),
  post: <T>(path: string, body: unknown = {}) => send<T>("POST", path, body),
  put: <T>(path: string, body: unknown) => send<T>("PUT", path, body),
  patch: <T>(path: string, body: unknown) => send<T>("PATCH", path, body),
  del: <T>(path: string) => send<T>("DELETE", path),
  upload<T>(path: string, form: FormData): Promise<T> {
    return fetch(`/api${path}`, { method: "POST", credentials: "same-origin", body: form }).then((r) =>
      handle<T>(r),
    );
  },
};

export const enc = encodeURIComponent;

/** 시험 폴더 안의 그림 주소 (쿠키로 인증되므로 <img>에 그대로). */
export function examFileUrl(examId: string, path: string): string {
  return `/api/exams/${enc(examId)}/file?path=${enc(path)}`;
}

export function errorText(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}

// ---------------------------------------------------------------- 작업 (백그라운드)

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);
const listeners = new Set<() => void>();

/** 작업이 생기거나 끝나면 알림·작업 목록·화면이 다시 읽는다. */
export function onJobsChanged(f: () => void): () => void {
  listeners.add(f);
  return () => listeners.delete(f);
}

export function jobsChanged(): void {
  listeners.forEach((f) => f());
}

/** 작업이 끝날 때까지 기다린다. 실패하면 작업의 한국어 안내로 ApiError. */
export async function waitJob(job: Job, onProgress?: (j: Job) => void): Promise<Job> {
  jobsChanged();
  let j = job;
  while (!TERMINAL.has(j.status)) {
    await new Promise((r) => setTimeout(r, 700));
    j = await api.get<Job>(`/jobs/${j.id}`);
    onProgress?.(j);
  }
  jobsChanged();
  if (j.status === "failed") throw new ApiError(500, j.error?.message ?? "작업이 실패했습니다.", j.error?.code, { detail: j.error?.detail });
  if (j.status === "cancelled") throw new ApiError(499, "작업을 멈췄습니다.");
  return j;
}

// ---------------------------------------------------------------- 화면에서 읽기

export type Loaded<T> = { data: T | undefined; error: string | null; loading: boolean; reload: () => void };

/** GET 하나. path가 null이면 부르지 않는다. 작업이 끝나면 저절로 다시 읽는다(`refreshOnJobs`). */
export function useApi<T>(path: string | null, { refreshOnJobs = true } = {}): Loaded<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const seq = useRef(0);
  const reload = useCallback(() => {
    if (path === null) return;
    const n = ++seq.current;
    setLoading(true);
    api
      .get<T>(path)
      .then((d) => {
        if (n !== seq.current) return;
        setData(d);
        setError(null);
      })
      .catch((e: unknown) => n === seq.current && setError(errorText(e)))
      .finally(() => n === seq.current && setLoading(false));
  }, [path]);
  useEffect(() => {
    setData(undefined);
    reload();
  }, [reload]);
  useEffect(() => (refreshOnJobs ? onJobsChanged(reload) : undefined), [reload, refreshOnJobs]);
  return { data, error, loading, reload };
}

/** 버튼 하나의 동작: 도는 동안 busy, 실패하면 한 문장 오류 (PLAN §11-1-6). */
export function useAction(): {
  busy: boolean;
  error: string | null;
  run: (f: () => Promise<unknown>) => Promise<boolean>;
  clear: () => void;
} {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(async (f: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await f();
      return true;
    } catch (e) {
      setError(errorText(e));
      return false;
    } finally {
      setBusy(false);
    }
  }, []);
  return { busy, error, run, clear: () => setError(null) };
}
