import { FileSpreadsheet, FileText, Play, X } from "lucide-react";
import { useEffect, useState } from "react";
import { ClassField } from "@/components/ClassField";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dropzone, ErrorNote, Input, Progress } from "@/components/ui/misc";
import { Select } from "@/components/ui/select";
import { api, useAction, useApi, waitJob } from "@/lib/api";
import { cn } from "@/lib/cn";
import { onHandedExam } from "@/lib/handoff";
import { go, href } from "@/lib/router";
import { useTeacher } from "@/lib/session";
import { guessClass, guessRole, type Role } from "@/lib/roles";
import type { Job, Level } from "@/lib/types";

const ROLES: { value: Role; label: string }[] = [
  { value: "exam", label: "시험지·해설" },
  { value: "answers", label: "학생 답안지" },
  { value: "roster", label: "학생 명단" },
  { value: "skip", label: "빼기" },
];

/** `klass`: 강사가 고른 이 파일의 반 — 답안지는 그 반 명단에서 표지 이름을, 명단은 반이 빈 줄을 그 반으로. */
type Picked = { file: File; role: Role; klass: string };
const hasClass = (r: Role) => r === "answers" || r === "roster";

/** 시험 이름: 파일 이름에서 확장자와 0단계 꼬리("_다듬음")를 뗀다. */
const examTitle = (name: string) => name.replace(/\.[^.]+$/, "").replace(/_다듬음$/, "");

/** 한 번에 채점 (2026-11-18): 시험지(해설 포함) · 답안지 PDF · (명단)을 놓고 [채점 시작] 하나.
 * 루브릭 승인·학생 쪽 수·이름 확인을 묻지 않고 끝까지 간다 (`dawoni/autopilot.py`). */
export function QuickStart() {
  const levels = useApi<Level[]>("/levels", { refreshOnJobs: false });
  const roster = useApi<{ classes: string[] }>("/students", { refreshOnJobs: false });
  const by = useTeacher();
  const [files, setFiles] = useState<Picked[]>([]);
  const [title, setTitle] = useState("");
  const [level, setLevel] = useState("물리");
  const [job, setJob] = useState<Job | null>(null);
  const act = useAction();

  const exam = files.find((f) => f.role === "exam");
  const answers = files.filter((f) => f.role === "answers");
  const rosters = files.filter((f) => f.role === "roster");
  // 고를 반: 명부의 반 + 이번에 적은 반 (명단 사진에 적은 반을 답안지에서 고를 수 있게)
  const classes = [...new Set([...(roster.data?.classes ?? []), ...files.map((f) => f.klass.trim()).filter(Boolean)])];
  const add = (fs: File[]) => {
    const next = [...files];
    for (const f of fs) {
      if (next.some((p) => p.file.name === f.name && p.file.size === f.size)) continue;
      const role = guessRole(f.name, next.some((p) => p.role === "exam"));
      next.push({ file: f, role, klass: hasClass(role) ? guessClass(f.name, roster.data?.classes ?? []) : "" });
    }
    setFiles(next);
  };
  const setRole = (i: number, role: Role) => setFiles(files.map((f, k) => (k === i ? { ...f, role } : f)));
  const setClass = (i: number, klass: string) => setFiles(files.map((f, k) => (k === i ? { ...f, klass } : f)));
  // 0단계에서 "1단계로"를 누르면 다듬은 해설지가 시험지 자리에 들어온다 (있던 시험지는 빼기로)
  useEffect(
    () =>
      onHandedExam((f) =>
        setFiles((cur) => [
          { file: f, role: "exam", klass: "" },
          ...cur.filter((p) => !(p.file.name === f.name && p.file.size === f.size)).map((p) => (p.role === "exam" ? { ...p, role: "skip" as Role } : p)),
        ]),
      ),
    [],
  );
  const examId = typeof job?.params.exam === "string" ? job.params.exam : null;

  const start = () =>
    act.run(async () => {
      if (!exam) throw new Error("시험지(해설 포함) 파일을 넣어 주세요.");
      if (answers.length === 0) throw new Error("학생 답안지 PDF를 넣어 주세요.");
      const form = new FormData();
      form.append("exam", exam.file);
      for (const a of answers) {
        form.append("answers", a.file);
        form.append("answer_classes", a.klass.trim()); // 파일과 같은 차례 (빈 칸 = 고르지 않음)
      }
      for (const r of rosters) {
        form.append("roster", r.file);
        form.append("roster_classes", r.klass.trim());
      }
      form.append("title", title.trim() || examTitle(exam.file.name));
      form.append("level", level);
      if (by) form.append("by", by);
      const started = await api.upload<Job>("/quick", form);
      setJob(started);
      const done = await waitJob(started, setJob);
      setFiles([]);
      setTitle("");
      go("exams", String(done.result?.exam_id ?? done.params.exam), "result");
    });

  const running = act.busy && job !== null;
  const roleIcon = (r: Role) => (r === "roster" ? <FileSpreadsheet className="size-4" /> : <FileText className="size-4" />);
  return (
    <Card className="overflow-hidden">
      <div className="flex items-start gap-4 px-6 pt-6">
        <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary text-primary-fg">
          <Play className="size-[18px]" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="m-0 text-[17px] font-medium">1단계 · 새 채점</h2>
          <p className="m-0 mt-1 text-[13px] text-text-2">파일을 놓고 [채점 시작] — 루브릭·판독·채점·점수표·채점 답안지까지 알아서 합니다.</p>
        </div>
      </div>
      {!running && (
        <div className="px-6 pt-5">
          <Dropzone
            compact={files.length > 0}
            title={files.length > 0 ? "파일 더 놓기" : "시험지(해설 포함) · 학생 답안지 PDF · 학생 명단을 한꺼번에 끌어다 놓기"}
            hint={
              files.length > 0
                ? "빠진 답안지·명단이 있으면 여기에"
                : "HWP · HWPX · DOCX · PDF · 엑셀 · 명단 사진 — 명단은 없어도 됩니다. 부분 점수 기준이 필요하면 위 0단계에서 다듬은 해설지를 넣으세요"
            }
            accept=".pdf,.hwp,.hwpx,.docx,.xlsx,.xls,.csv,.jpg,.jpeg,.png"
            multiple
            onFiles={add}
          />
        </div>
      )}
      {files.length > 0 && (
        <div className="px-6 pt-4">
          <div className="mb-2 text-[12px] text-text-3">넣은 파일 {files.length}개 — 종류가 틀렸으면 오른쪽에서 고르세요</div>
          <div className="flex flex-col divide-y divide-line rounded-xl border border-line">
            {files.map((f, i) => (
              <div key={`${f.file.name}-${i}`} className={cn("flex items-center gap-3 px-3 py-2", f.role === "skip" && "opacity-50")}>
                <span className={cn("grid size-8 shrink-0 place-items-center rounded-lg", f.role === "exam" ? "bg-accent-soft text-accent-text" : f.role === "answers" ? "bg-ok-soft text-ok" : "bg-muted text-text-2")}>
                  {roleIcon(f.role)}
                </span>
                <span className="min-w-0 flex-1 truncate">{f.file.name}</span>
                {hasClass(f.role) && (
                  <ClassField
                    value={f.klass}
                    onChange={(v) => setClass(i, v)}
                    classes={classes}
                    className="w-28"
                    title={f.role === "answers" ? "이 답안지 묶음의 반 — 고르면 그 반 명단에서 이름을 고릅니다" : "이 명단의 반 — 반이 적히지 않은 학생을 이 반으로"}
                  />
                )}
                <Select value={f.role} onChange={(v) => setRole(i, v as Role)} className="w-32 min-w-0" options={ROLES} />
                {!running && (
                  <button type="button" aria-label="빼기" onClick={() => setFiles(files.filter((_, k) => k !== i))} className="grid size-8 shrink-0 cursor-pointer place-items-center rounded-lg text-text-3 hover:bg-muted hover:text-text">
                    <X className="size-4" />
                  </button>
                )}
              </div>
            ))}
          </div>
          {answers.some((a) => !a.klass.trim()) && !running && (
            <div className="mt-2 text-[12px] text-text-3">
              답안지마다 반을 고르면 그 반 명단 안에서 표지 이름을 고릅니다 (훨씬 정확합니다). 반 명단(엑셀·사진)을 함께 넣고 같은 반을 적어 주세요.
            </div>
          )}
        </div>
      )}
      {running && job && (
        <div className="px-6 pt-5">
          <div className="flex items-baseline justify-between text-[13px]">
            <span className="font-medium">{job.progress.message || "시작하는 중"}</span>
            {examId && (
              <a href={href("exams", examId, "result")} className="text-accent-text">
                결과 화면에서 보기
              </a>
            )}
          </div>
          <div className="mt-2">
            <Progress value={job.progress.total ? (job.progress.done / job.progress.total) * 100 : 8} />
          </div>
          <div className="mt-2 text-[12px] text-text-3">학생 20명에 15~25분쯤 걸립니다. 창을 닫아도 계속되고, 끝나면 알려 드립니다.</div>
        </div>
      )}
      {act.error && (
        <div className="px-6 pt-4">
          <ErrorNote message={act.error} />
        </div>
      )}
      {files.length > 0 && !running ? (
        <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-line bg-muted/50 px-6 py-3">
          <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={exam ? examTitle(exam.file.name) : "시험 이름"} className="w-64" aria-label="시험 이름" />
          <Select value={level} onChange={setLevel} className="w-36 min-w-0" options={(levels.data ?? [{ id: "물리", label: "물리", scope_id: "" }]).map((l) => ({ value: l.label, label: l.label }))} />
          <div className="flex-1" />
          {(!exam || answers.length === 0) && <span className="text-[12px] text-text-3">{!exam ? "시험지(해설 포함)가 필요합니다" : "학생 답안지 PDF가 필요합니다"}</span>}
          <Button variant="primary" className="h-10 px-5" disabled={act.busy || !exam || answers.length === 0} onClick={() => void start()}>
            <Play /> 채점 시작
          </Button>
        </div>
      ) : (
        <div className="pb-6" />
      )}
    </Card>
  );
}
