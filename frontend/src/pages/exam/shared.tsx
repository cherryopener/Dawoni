import { useEffect, useState } from "react";
import { api, enc, errorText, onJobsChanged } from "@/lib/api";
import type { QuestionDetail } from "@/lib/types";

/** 문항마다 해설지·루브릭 (`GET …/questions/{n}`) — 문항 수만큼 한꺼번에. */
export function useQuestionDetails(
  base: string,
  numbers: string[],
): { details: Record<string, QuestionDetail>; error: string | null; reload: () => void } {
  const [details, setDetails] = useState<Record<string, QuestionDetail>>({});
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const key = numbers.join("|");
  useEffect(() => {
    let live = true;
    Promise.all(numbers.map((n) => api.get<QuestionDetail>(`${base}/questions/${enc(n)}`)))
      .then((list) => {
        if (!live) return;
        setDetails(Object.fromEntries(list.map((d) => [d.question.number, d])));
        setError(null);
      })
      .catch((e: unknown) => live && setError(errorText(e)));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base, key, tick]);
  useEffect(() => onJobsChanged(() => setTick((t) => t + 1)), []);
  return { details, error, reload: () => setTick((t) => t + 1) };
}

export function partLabel(q: string, part: string | null | undefined): string {
  return part ? `${q}-${part}` : q;
}
