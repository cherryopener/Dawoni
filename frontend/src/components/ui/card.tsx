import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-[var(--radius-card)] border border-line bg-card", className)} {...props} />;
}

/** 카드 머리: (아이콘 칸) 제목 · 설명 · 오른쪽 단추. `tone`은 아이콘 칸의 색 (2026-11-20 — 결과 화면의 파일 칸과 같은 모양). */
export function CardHeader({
  title,
  desc,
  icon,
  right,
  tone = "muted",
}: {
  title: ReactNode;
  desc?: ReactNode;
  icon?: ReactNode;
  right?: ReactNode;
  tone?: "muted" | "accent" | "ok" | "warn" | "bad" | "primary";
}) {
  return (
    <div className="flex items-start gap-3.5 px-5 pt-5">
      {icon && (
        <div
          className={cn(
            "grid size-10 shrink-0 place-items-center rounded-xl [&_svg]:size-5",
            tone === "muted" && "bg-muted text-text-2",
            tone === "accent" && "bg-accent-soft text-accent-text",
            tone === "ok" && "bg-ok-soft text-ok",
            tone === "warn" && "bg-warn-soft text-warn",
            tone === "bad" && "bg-bad-soft text-bad",
            tone === "primary" && "bg-primary text-primary-fg",
          )}
        >
          {icon}
        </div>
      )}
      <div className={cn("min-w-0 flex-1", icon && "pt-0.5")}>
        <h3 className="m-0 text-[15px] font-medium">{title}</h3>
        {desc && <p className="m-0 mt-1 text-[13px] text-text-2">{desc}</p>}
      </div>
      {right}
    </div>
  );
}

export function CardBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-5 py-4", className)} {...props} />;
}
