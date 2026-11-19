import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

// 화면당 강조(primary) 버튼은 하나만 (PLAN §11-1). 나머지는 outline/ghost.
const button = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg font-medium transition-colors " +
    "disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 " +
    "focus-visible:outline-accent [&_svg]:size-4 [&_svg]:shrink-0 cursor-pointer",
  {
    variants: {
      variant: {
        // 꺼진 주 단추는 반투명(회색 단추처럼 보임)이 아니라 옅은 바탕·흐린 글씨 — 켜지면 먹색 (2026-11-19 사용자 요청)
        primary:
          "bg-primary text-primary-fg hover:opacity-85 disabled:border disabled:border-line disabled:bg-muted disabled:text-text-3 disabled:opacity-100",
        outline: "border border-line bg-card text-text hover:bg-muted",
        ghost: "text-text-2 hover:bg-muted hover:text-text",
        danger: "border border-line bg-card text-bad hover:bg-bad-soft",
      },
      size: {
        sm: "h-8 px-3 text-[13px]",
        md: "h-9 px-4",
        lg: "h-11 px-5 text-[15px]",
        icon: "size-9",
      },
    },
    defaultVariants: { variant: "outline", size: "md" },
  },
);

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof button> & { asChild?: boolean };

export function Button({ className, variant, size, asChild, ...props }: ButtonProps) {
  const C = asChild ? Slot : "button";
  return <C data-variant={variant ?? "outline"} className={cn(button({ variant, size }), className)} {...props} />;
}
