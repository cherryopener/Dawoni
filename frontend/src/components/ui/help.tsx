import * as T from "@radix-ui/react-tooltip";
import { CircleHelp } from "lucide-react";
import type { ReactNode } from "react";

/** 긴 설명은 물음표 아이콘 안에 (PLAN §11-1-7). */
export function Help({ children }: { children: ReactNode }) {
  return (
    <T.Provider delayDuration={150}>
      <T.Root>
        <T.Trigger asChild>
          <button
            type="button"
            className="inline-flex cursor-help align-middle text-text-3 hover:text-text-2"
            aria-label="도움말"
          >
            <CircleHelp className="size-4" />
          </button>
        </T.Trigger>
        <T.Portal>
          <T.Content
            sideOffset={6}
            className="z-50 max-w-72 rounded-lg border border-line bg-card px-3 py-2 text-[13px] leading-relaxed text-text-2"
          >
            {children}
          </T.Content>
        </T.Portal>
      </T.Root>
    </T.Provider>
  );
}
