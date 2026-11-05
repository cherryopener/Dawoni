import { createContext, useContext } from "react";
import type { Status } from "./types";

/** 앱 전체가 쓰는 상태: 작업공간·강사 이름 (맡기·승인·확정 기록의 `by`). */
export type Session = { status: Status; reload: () => void };

export const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const s = useContext(SessionContext);
  if (!s) throw new Error("SessionContext 밖");
  return s;
}

/** 기록에 남길 강사 이름 (없으면 undefined — 혼자 쓰는 PC는 잠그지 않는다). */
export function useTeacher(): string | undefined {
  return useSession().status.teacher ?? undefined;
}
