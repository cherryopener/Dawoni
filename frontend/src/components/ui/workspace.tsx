import { ArrowLeft } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Button } from "./button";

/** 큰 작업 하나를 화면 전체로 (2026-11-19 사용자 요청 — "전부 모달로만"): 위 줄에 돌아가기·제목·단추,
 * 아래는 작업 칸. 창(Dialog)처럼 화면을 어둡게 덮지 않는다. 판독문 확인·영역 지정. `escape`면 Esc도
 * 돌아가기 (열린 고르기·창이 있으면 그것이 먼저). */
export function Workspace({
  title,
  onBack,
  actions,
  children,
  escape = true,
}: {
  title: ReactNode;
  onBack: () => void;
  actions?: ReactNode;
  children: ReactNode;
  escape?: boolean;
}) {
  const back = useRef(onBack);
  back.current = onBack;
  useEffect(() => {
    if (!escape) return;
    const f = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      if (document.querySelector("[data-radix-popper-content-wrapper], [role='dialog'], [role='alertdialog']")) return;
      back.current();
    };
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  }, [escape]);
  return createPortal(
    <div data-workspace className="fixed inset-0 z-40 flex flex-col bg-bg text-text">
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-card px-3">
        <Button variant="ghost" size="sm" className="h-9" onClick={() => back.current()} title="돌아가기 (Esc)">
          <ArrowLeft /> 돌아가기
        </Button>
        <div className="h-5 w-px bg-line" />
        <div className="flex min-w-0 items-center gap-2">{title}</div>
        <div className="flex-1" />
        {actions}
      </header>
      {children}
    </div>,
    document.body,
  );
}
