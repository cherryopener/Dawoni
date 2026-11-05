import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export type Tone = "neutral" | "accent" | "ok" | "warn" | "bad";

const TONE: Record<Tone, string> = {
  neutral: "bg-muted text-text-2",
  accent: "bg-accent-soft text-accent-text",
  ok: "bg-ok-soft text-ok",
  warn: "bg-warn-soft text-warn",
  bad: "bg-bad-soft text-bad",
};

export function Badge({ tone = "neutral", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[12px] font-medium [&_svg]:size-3.5",
        TONE[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
