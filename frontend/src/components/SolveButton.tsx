import { api, useJob } from "../lib/api";
import { Button, ErrorBox, JobProgress, Stack, teacherName } from "./ui";

/** 풀이·해설 만들기 (원본을 보지 않고 풀어 원본 답과 비교). 문항 여러 개는 동시에. */
export function SolveButton({ ids, redo = false, label, onDone, url = "/bank/items/solve" }: {
  ids: string[];
  redo?: boolean;
  label: string;
  onDone: () => void;
  url?: string;
}) {
  const job = useJob(() => onDone());
  return (
    <Stack gap={4}>
      <Button small disabled={job.running || (url === "/bank/items/solve" && !ids.length)}
              onClick={() => void job.run(() => api.post(url, { ids, redo, by: teacherName() || null }))}>
        {label}
      </Button>
      <JobProgress job={job.job} />
      <ErrorBox error={job.error} />
    </Stack>
  );
}
