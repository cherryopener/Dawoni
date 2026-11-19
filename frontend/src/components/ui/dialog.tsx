import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";

export function Dialog({
  open,
  onOpenChange,
  title,
  desc,
  children,
  footer,
  wide = false,
  full = false,
  onEscape,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title: ReactNode;
  desc?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  /** 넓은 창 (원본 쪽에서 영역 지정 등) */
  wide?: boolean;
  /** 화면 가득 (판독문 고치기: 원본과 판독문을 나란히). 안쪽은 스스로 넘친 부분을 굴린다. */
  full?: boolean;
  /** Esc를 먼저 받는다 — true를 돌려주면 창을 닫지 않는다 (예: 확대한 원본을 100%로). */
  onEscape?: () => boolean;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-40 bg-black/40" />
        <D.Content
          // 바깥을 눌러도 닫지 않는다 — 적던 내용이 사라지지 않게 (2026-11-05). 닫기는 ×·취소·Esc로
          onInteractOutside={(e) => e.preventDefault()}
          onEscapeKeyDown={(e) => {
            if (onEscape?.()) e.preventDefault();
          }}
          className={
            "fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2 rounded-[var(--radius-card)] border border-line bg-card p-6 text-text " +
            (full
              ? "flex h-[94vh] w-[min(1500px,97vw)] flex-col"
              : wide
                ? "max-h-[94vh] w-[min(1180px,96vw)] overflow-y-auto"
                : "w-[min(520px,92vw)]")
          }
        >
          <D.Title className="m-0 text-[16px] font-medium">{title}</D.Title>
          {desc && <D.Description className="m-0 mt-1.5 text-text-2">{desc}</D.Description>}
          <div className={full ? "mt-4 min-h-0 flex-1" : "mt-4"}>{children}</div>
          {footer && <div className={(full ? "mt-4" : "mt-6") + " flex items-center justify-end gap-2"}>{footer}</div>}
          <D.Close className="absolute right-4 top-4 cursor-pointer text-text-3 hover:text-text" aria-label="닫기">
            <X className="size-4" />
          </D.Close>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
