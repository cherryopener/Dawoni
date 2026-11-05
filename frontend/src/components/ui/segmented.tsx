import * as TG from "@radix-ui/react-toggle-group";

/** 둘·셋 중 하나 고르기 (PLAN §11-1: 체크박스 대신 구간 버튼). */
export function Segmented<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
}) {
  return (
    <TG.Root
      type="single"
      value={value}
      onValueChange={(v) => v && onChange(v as T)}
      className="inline-flex rounded-lg border border-line bg-muted p-0.5"
    >
      {options.map((o) => (
        <TG.Item
          key={o.value}
          value={o.value}
          className="h-7 cursor-pointer rounded-md px-3 text-[13px] text-text-2 data-[state=on]:bg-card data-[state=on]:font-medium data-[state=on]:text-text"
        >
          {o.label}
        </TG.Item>
      ))}
    </TG.Root>
  );
}
