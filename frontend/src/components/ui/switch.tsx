import * as S from "@radix-ui/react-switch";
import type { ReactNode } from "react";

export function Switch({
  checked,
  onCheckedChange,
  label,
  desc,
}: {
  checked: boolean;
  onCheckedChange: (v: boolean) => void;
  label: ReactNode;
  desc?: ReactNode;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-3 py-2">
      <div className="min-w-0 flex-1">
        <div>{label}</div>
        {desc && <div className="text-[12px] text-text-2">{desc}</div>}
      </div>
      <S.Root
        checked={checked}
        onCheckedChange={onCheckedChange}
        className="relative h-6 w-10 shrink-0 cursor-pointer rounded-full bg-line transition-colors data-[state=checked]:bg-accent"
      >
        <S.Thumb className="block size-5 translate-x-0.5 rounded-full bg-white transition-transform data-[state=checked]:translate-x-[18px]" />
      </S.Root>
    </label>
  );
}
