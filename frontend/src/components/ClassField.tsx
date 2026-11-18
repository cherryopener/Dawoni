import { useId } from "react";
import { Input } from "@/components/ui/misc";
import { cn } from "@/lib/cn";

/** 파일의 반: 명부의 반을 고르거나 적는다. 답안지 PDF에 반을 정하면 그 반 명단 안에서만 표지 이름을
 * 고른다 (2026-11-18 실측: 그 반 명단만이면 18명 중 17~18명, 7반 명단이면 86%). */
export function ClassField({
  value,
  onChange,
  classes,
  title = "이 파일의 반",
  className,
}: {
  value: string;
  onChange: (v: string) => void;
  classes: string[];
  title?: string;
  className?: string;
}) {
  const id = useId();
  return (
    <>
      <Input value={value} onChange={(e) => onChange(e.target.value)} list={id} placeholder="반" aria-label="반" title={title} className={cn("w-32 min-w-0", className)} />
      <datalist id={id}>
        {classes.map((c) => (
          <option key={c} value={c} />
        ))}
      </datalist>
    </>
  );
}
