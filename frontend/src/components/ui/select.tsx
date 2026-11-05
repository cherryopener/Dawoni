import * as S from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";

/** 많은 것 중 하나 고르기 → 드롭다운. */
export function Select({
  value,
  onChange,
  options,
  placeholder = "기본",
  className = "",
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  placeholder?: string;
  className?: string;
}) {
  return (
    <S.Root value={value || undefined} onValueChange={onChange}>
      <S.Trigger
        className={
          "inline-flex h-9 min-w-40 cursor-pointer items-center justify-between gap-2 rounded-lg border border-line bg-card px-3 text-left data-[placeholder]:text-text-3 " +
          className
        }
      >
        <S.Value placeholder={placeholder} />
        <S.Icon>
          <ChevronDown className="size-4 text-text-3" />
        </S.Icon>
      </S.Trigger>
      <S.Portal>
        <S.Content
          position="popper"
          sideOffset={4}
          className="z-50 min-w-[var(--radix-select-trigger-width)] rounded-lg border border-line bg-card p-1 text-text"
        >
          <S.Viewport>
            {options.map((o) => (
              <S.Item
                key={o.value}
                value={o.value}
                className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 outline-none data-[highlighted]:bg-muted"
              >
                <S.ItemText>{o.label}</S.ItemText>
                <S.ItemIndicator className="ml-auto">
                  <Check className="size-4" />
                </S.ItemIndicator>
              </S.Item>
            ))}
          </S.Viewport>
        </S.Content>
      </S.Portal>
    </S.Root>
  );
}
