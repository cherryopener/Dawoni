import { FileText, Search, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Chips, ErrorNote, Input } from "@/components/ui/misc";
import { Select } from "@/components/ui/select";
import { api, enc, useAction, useApi } from "@/lib/api";
import { cn } from "@/lib/cn";
import { href } from "@/lib/router";
import { pts } from "@/lib/steps";
import type { HistoryExam, StudentHistory } from "@/lib/types";

const DAYS: Record<string, number> = { "1m": 31, "3m": 92, "1y": 366 };

/** 한 학생의 한 시험: 문항별 채점본 HWPX를 한글로 연다. */
function ExamRow({ e }: { e: HistoryExam }) {
  const [files, setFiles] = useState<Record<string, string> | null>(null);
  const act = useAction();
  useEffect(() => {
    api
      .get<Record<string, Record<string, string>>>(`/exams/${enc(e.exam_id)}/student-files`)
      .then((m) => setFiles(m[e.student_id] ?? {}))
      .catch(() => setFiles({}));
  }, [e.exam_id, e.student_id]);
  return (
    <div className="flex items-center gap-3 border-t border-line py-3">
      <a href={href("exams", e.exam_id)} className="flex-1 text-text no-underline hover:underline">
        <div>{e.title}</div>
        <div className="text-[12px] text-text-3">{e.date}</div>
      </a>
      <span className="w-24 text-right font-medium">{e.total === null ? `채점 ${e.graded}/${e.questions}` : `${pts(e.total)} / ${pts(e.max)}`}</span>
      <div className="flex">
        {Object.entries(files ?? {}).map(([q, path]) => (
          <Button key={q} size="sm" variant="ghost" onClick={() => void act.run(() => api.post("/files/open", { path }))}>
            <FileText /> {q}번
          </Button>
        ))}
      </div>
    </div>
  );
}

/** 학생 검색 (PLAN §5-4): 이름·반·학교·시험·기간으로 찾아 그 학생의 HWPX를 연다. 명부는 엑셀·CSV로. */
export function StudentsPage() {
  const { data, error, reload } = useApi<StudentHistory[]>("/students/history");
  const [q, setQ] = useState("");
  const [classes, setClasses] = useState<string[]>([]);
  const [period, setPeriod] = useState("all");
  const [sel, setSel] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const act = useAction();

  const allClasses = useMemo(() => [...new Set((data ?? []).map((s) => s.class_name).filter((c): c is string => !!c))].sort(), [data]);
  const since = DAYS[period] ? new Date(Date.now() - DAYS[period]! * 86400_000).toISOString().slice(0, 10) : "";
  const hits = (data ?? [])
    .map((s) => ({ ...s, exams: s.exams.filter((e) => !since || e.date >= since) }))
    .filter((s) => {
      const text = `${s.name} ${s.school ?? ""} ${s.exams.map((e) => e.title).join(" ")}`;
      return (!q || text.includes(q.trim())) && (classes.length === 0 || classes.includes(s.class_name ?? "")) && (!since || s.exams.length > 0);
    });
  const st = hits.find((s) => s.key === sel) ?? hits[0];

  const importRoster = (f: File | undefined) =>
    act.run(async () => {
      if (!f) return;
      const form = new FormData();
      form.append("file", f);
      const r = await api.upload<{ added: unknown[]; updated: unknown[]; problems: string[] }>("/students/import", form);
      setNote(`넣음 ${r.added.length}명 · 고침 ${r.updated.length}명${r.problems.length ? ` · 확인: ${r.problems.join("; ")}` : ""}`);
      reload();
    });

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <div className="flex items-center gap-3">
        <h1 className="m-0 flex-1 text-[20px] font-medium">학생</h1>
        <input ref={fileRef} type="file" accept=".xlsx,.csv" className="hidden" onChange={(e) => void importRoster(e.target.files?.[0])} />
        <Button variant="primary" disabled={act.busy} onClick={() => fileRef.current?.click()}>
          <Upload /> 명부 넣기
        </Button>
      </div>
      {(error || act.error) && (
        <div className="mt-3">
          <ErrorNote message={(act.error ?? error)!} />
        </div>
      )}
      {note && <div className="mt-3 rounded-lg bg-muted px-4 py-2.5 text-[13px]">{note}</div>}
      <div className="mt-5 flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-2.5 size-4 text-text-3" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="이름 · 학교 · 시험" className="w-full pl-9" />
        </div>
        <Select
          value={period}
          onChange={setPeriod}
          className="min-w-32"
          options={[
            { value: "1m", label: "최근 1달" },
            { value: "3m", label: "최근 3달" },
            { value: "1y", label: "최근 1년" },
            { value: "all", label: "전체" },
          ]}
        />
      </div>
      {allClasses.length > 0 && (
        <div className="mt-3">
          <Chips options={allClasses} value={classes} onChange={setClasses} />
        </div>
      )}
      <div className="mt-5 grid grid-cols-[1fr_1.4fr] gap-4">
        <Card className="max-h-[60vh] overflow-auto">
          {hits.map((s) => (
            <button
              key={s.key}
              type="button"
              onClick={() => setSel(s.key)}
              className={cn("flex w-full cursor-pointer items-center gap-3 border-t border-line px-5 py-3 text-left first:border-t-0 hover:bg-muted", st?.key === s.key && "bg-muted")}
            >
              <span className="flex-1">{s.name}</span>
              <span className="text-[13px] text-text-2">{[s.class_name, s.school].filter(Boolean).join(" · ")}</span>
            </button>
          ))}
          {data && hits.length === 0 && <div className="px-5 py-6 text-text-3">{data.length === 0 ? "명부를 넣거나 답안을 판독하면 학생이 생깁니다." : "찾는 학생이 없습니다."}</div>}
        </Card>
        {st && (
          <Card className="p-5">
            <div className="text-[16px] font-medium">{st.name}</div>
            <div className="text-[13px] text-text-2">{[st.class_name, st.school, st.roster_id ?? "명부 밖"].filter(Boolean).join(" · ")}</div>
            <div className="mt-4 flex flex-col">
              {st.exams.length === 0 && <div className="py-3 text-text-3">본 시험이 없습니다.</div>}
              {st.exams.map((e) => (
                <ExamRow key={e.exam_id + e.student_id} e={e} />
              ))}
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}
