import type { StepKey } from "./types";

export const STEPS: { key: StepKey; label: string }[] = [
  { key: "read", label: "시험 읽기" },
  { key: "rubric", label: "루브릭" },
  { key: "scan", label: "답안 판독" },
  { key: "grade", label: "채점 검토" },
  { key: "export", label: "내보내기" },
];

export function stepIndex(k: StepKey): number {
  return STEPS.findIndex((s) => s.key === k);
}

/** 점수 표기: 4 → "4", 2.5 → "2.5". */
export function pts(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return Number.isInteger(n) ? String(n) : n.toFixed(1).replace(/\.0$/, "");
}
