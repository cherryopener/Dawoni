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
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title: ReactNode;
  desc?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  /** 넓은 창 (원본 쪽에서 영역 지정 등) */
  wide?: boolean;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-40 bg-black/40" />
        <D.Content
          // 바깥을 눌러도 닫지 않는다 — 적던 내용이 사라지지 않게 (2026-11-05). 닫기는 ×·취소·Esc로
          onInteractOutside={(e) => e.preventDefault()}
          className={
            "fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2 rounded-[var(--radius-card)] border border-line bg-card p-6 text-text " +
            (wide ? "max-h-[94vh] w-[min(1180px,96vw)] overflow-y-auto" : "w-[min(520px,92vw)]")
          }
        >
          <D.Title className="m-0 text-[16px] font-medium">{title}</D.Title>
          {desc && <D.Description className="m-0 mt-1.5 text-text-2">{desc}</D.Description>}
          <div className="mt-4">{children}</div>
          {footer && <div className="mt-6 flex justify-end gap-2">{footer}</div>}
          <D.Close className="absolute right-4 top-4 cursor-pointer text-text-3 hover:text-text" aria-label="닫기">
            <X className="size-4" />
          </D.Close>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
