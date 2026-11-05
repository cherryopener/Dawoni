import { cn } from "@/lib/cn";

/** 알림 숫자 배지. 0이면 그리지 않는다. 할 일은 빨강, 덜 급한 것은 노랑. */
export function Count({ n, tone = "bad", className }: { n: number; tone?: "bad" | "warn"; className?: string }) {
  if (n <= 0) return null;
  return (
    <span
      className={cn(
        "inline-grid h-[18px] min-w-[18px] place-items-center rounded-full px-1 text-[11px] font-medium leading-none tabular-nums",
        tone === "bad" ? "bg-bad text-white dark:text-black" : "bg-warn text-white dark:text-black",
        className,
      )}
    >
      {n > 99 ? "99+" : n}
    </span>
  );
}
