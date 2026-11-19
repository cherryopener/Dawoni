import * as C from "@radix-ui/react-collapsible";
import { ChevronRight, Upload } from "lucide-react";
import { useState, type InputHTMLAttributes, type ReactNode } from "react";
import { cn } from "@/lib/cn";

/** 바꿀 일이 드문 설정은 "고급" 접기 안에 (PLAN §11-1-2). */
export function Advanced({ label = "고급", children }: { label?: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <C.Root open={open} onOpenChange={setOpen}>
      <C.Trigger className="flex cursor-pointer items-center gap-1 text-[13px] text-text-2 hover:text-text">
        <ChevronRight className={"size-4 transition-transform " + (open ? "rotate-90" : "")} />
        {label}
      </C.Trigger>
      <C.Content className="mt-3">{children}</C.Content>
    </C.Root>
  );
}

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-9 rounded-lg border border-line bg-card px-3 text-text outline-none transition-[border-color,box-shadow] placeholder:text-text-3 hover:border-line-strong focus:border-accent focus:ring-3 focus:ring-accent-soft",
        className,
      )}
      {...props}
    />
  );
}

/** 파일 → 끌어다 놓는 영역 (PLAN §11-1-3). `compact`: 한 줄짜리 작은 영역 (0단계처럼 고를 일이 드문 곳). */
export function Dropzone({
  title,
  hint,
  onFiles,
  accept,
  multiple = false,
  compact = false,
  className,
}: {
  title: ReactNode;
  hint?: ReactNode;
  onFiles?: (f: File[]) => void;
  accept?: string;
  multiple?: boolean;
  compact?: boolean;
  className?: string;
}) {
  const [over, setOver] = useState(false);
  return (
    <label
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        onFiles?.([...e.dataTransfer.files]);
      }}
      className={cn(
        "group flex cursor-pointer items-center rounded-xl border border-dashed border-line-strong bg-muted/40 text-text-2 transition-colors hover:border-text-3 hover:bg-muted",
        compact ? "gap-3 px-4 py-3 text-left" : "flex-col justify-center gap-3 px-6 py-10 text-center",
        over && "border-accent bg-accent-soft hover:border-accent hover:bg-accent-soft",
        className,
      )}
    >
      <span
        className={cn(
          "grid shrink-0 place-items-center rounded-full border border-line bg-card text-text-2 transition-colors group-hover:text-text",
          compact ? "size-8 [&_svg]:size-4" : "size-11 [&_svg]:size-5",
          over && "border-accent text-accent-text",
        )}
      >
        <Upload />
      </span>
      <span className="min-w-0">
        <span className="block font-medium text-text">{title}</span>
        {hint && <span className={cn("block text-[12px] text-text-3", compact ? "mt-0.5" : "mt-1.5")}>{hint}</span>}
      </span>
      <input
        type="file"
        multiple={multiple}
        accept={accept}
        className="hidden"
        onChange={(e) => {
          onFiles?.([...(e.target.files ?? [])]);
          e.target.value = "";
        }}
      />
    </label>
  );
}

export function Progress({ value, tone = "accent" }: { value: number; tone?: "accent" | "ok" }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
      <div
        className={"h-full rounded-full transition-[width] duration-500 " + (tone === "ok" ? "bg-ok" : "bg-accent")}
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
      />
    </div>
  );
}

/** 칩: 여러 개 고르기 (PLAN §11-1-3). */
export function Chips({
  options,
  value,
  onChange,
}: {
  options: string[];
  value: string[];
  onChange: (v: string[]) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((o) => {
        const on = value.includes(o);
        return (
          <button
            key={o}
            type="button"
            onClick={() => onChange(on ? value.filter((x) => x !== o) : [...value, o])}
            className={cn(
              "h-7 cursor-pointer rounded-full border px-3 text-[13px]",
              on ? "border-accent bg-accent-soft text-accent-text" : "border-line text-text-2 hover:bg-muted",
            )}
          >
            {o}
          </button>
        );
      })}
    </div>
  );
}

/** 오류: 무슨 일이 있었고 무엇을 하면 되는지 한 문장, 원문은 '자세히' 안 (PLAN §11-1-6). */
export function ErrorNote({ message, detail }: { message: string; detail?: string }) {
  return (
    <div className="rounded-lg bg-bad-soft px-3 py-2 text-[13px] text-bad">
      {message}
      {detail && (
        <details className="mt-1 text-text-2">
          <summary className="cursor-pointer">자세히</summary>
          <pre className="m-0 mt-1 whitespace-pre-wrap text-[12px]">{detail}</pre>
        </details>
      )}
    </div>
  );
}

/** 여러 줄 입력 칸 — 한 줄 칸(`Input`)과 같은 테두리·초점 둘레. */
export const textareaClass =
  "w-full resize-y rounded-lg border border-line bg-card px-3 py-2 text-text outline-none transition-[border-color,box-shadow] placeholder:text-text-3 hover:border-line-strong focus:border-accent focus:ring-3 focus:ring-accent-soft";

/** 화면 맨 위 제목 줄: 제목 · 한 줄 설명 · 오른쪽 단추 (학생·배우기·설정). */
export function PageTitle({ title, desc, children }: { title: ReactNode; desc?: ReactNode; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end gap-3">
      <div className="min-w-0 flex-1">
        <h1 className="m-0 text-[22px] font-medium tracking-[-0.01em]">{title}</h1>
        {desc && <p className="m-0 mt-1 text-[13px] text-text-2">{desc}</p>}
      </div>
      {children}
    </div>
  );
}
