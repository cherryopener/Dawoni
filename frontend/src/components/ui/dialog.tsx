import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/** 짧게 묻거나 몇 칸만 채우는 창 (지울까요? · 새 시험 · 학생 추가…). 큰 작업은 창이 아니라 `Workspace`
 * 화면으로 (2026-11-19 — 판독문 확인·영역 지정). 머리·몸·바닥을 나누고 바닥 줄에 단추를 둔다. */
export function Dialog({
  open,
  onOpenChange,
  title,
  desc,
  children,
  footer,
  wide = false,
  medium = false,
  onEscape,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title: ReactNode;
  desc?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  /** 넓은 창 */
  wide?: boolean;
  /** 중간 폭 (목록이 든 창: 이름 확인) */
  medium?: boolean;
  /** Esc를 먼저 받는다 — true를 돌려주면 창을 닫지 않는다. */
  onEscape?: () => boolean;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="dialog-overlay fixed inset-0 z-40 bg-[#1e1d1a]/25 backdrop-blur-[2px] dark:bg-black/50" />
        <D.Content
          // 바깥을 눌러도 닫지 않는다 — 적던 내용이 사라지지 않게 (2026-11-05). 닫기는 ×·취소·Esc로
          onInteractOutside={(e) => e.preventDefault()}
          onEscapeKeyDown={(e) => {
            if (onEscape?.()) e.preventDefault();
          }}
          className={cn(
            "dialog-content fixed left-1/2 top-1/2 z-50 flex max-h-[90vh] -translate-x-1/2 -translate-y-1/2 flex-col rounded-2xl border border-line bg-card text-text",
            wide ? "w-[min(1180px,96vw)]" : medium ? "w-[min(680px,94vw)]" : "w-[min(480px,92vw)]",
          )}
        >
          <div className="shrink-0 px-6 pt-5 pr-12">
            <D.Title className="m-0 text-[16px] font-medium leading-snug">{title}</D.Title>
            {desc && <D.Description className="m-0 mt-1.5 text-[13px] leading-relaxed text-text-2">{desc}</D.Description>}
          </div>
          {children != null && <div className="min-h-0 overflow-y-auto px-6 pt-4 pb-5">{children}</div>}
          {children == null && <div className="pb-5" />}
          {footer && <div className="flex shrink-0 items-center justify-end gap-2 rounded-b-2xl border-t border-line bg-muted/60 px-6 py-3">{footer}</div>}
          <D.Close className="absolute right-4 top-4 grid size-8 cursor-pointer place-items-center rounded-lg text-text-3 hover:bg-muted hover:text-text" aria-label="닫기">
            <X className="size-4" />
          </D.Close>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
