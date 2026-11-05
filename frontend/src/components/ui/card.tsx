import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-[var(--radius-card)] border border-line bg-card", className)} {...props} />;
}

export function CardHeader({
  title,
  desc,
  icon,
  right,
}: {
  title: ReactNode;
  desc?: ReactNode;
  icon?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="flex items-start gap-3 px-5 pt-5">
      {icon && <div className="mt-0.5 text-text-2 [&_svg]:size-5">{icon}</div>}
      <div className="min-w-0 flex-1">
        <h3 className="m-0 text-[15px]">{title}</h3>
        {desc && <p className="m-0 mt-1 text-[13px] text-text-2">{desc}</p>}
      </div>
      {right}
    </div>
  );
}

export function CardBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-5 py-4", className)} {...props} />;
}
