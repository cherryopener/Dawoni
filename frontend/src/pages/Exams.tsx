import { ChevronRight, FileText, Plus } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Dropzone, ErrorNote, Input, Progress } from "@/components/ui/misc";
import { Select } from "@/components/ui/select";
import { api, ApiError, useAction, useApi, waitJob } from "@/lib/api";
import { go, href } from "@/lib/router";
import { STEPS, stepIndex } from "@/lib/steps";
import type { ExamListItem, Job, Level } from "@/lib/types";

function NewExam({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const levels = useApi<Level[]>(open ? "/levels" : null, { refreshOnJobs: false });
  const [title, setTitle] = useState("");
  const [level, setLevel] = useState("고1 물리");
  const [file, setFile] = useState<File | null>(null);
  const [msg, setMsg] = useState("");
  const act = useAction();
  const start = () =>
    act.run(async () => {
      if (!file) throw new Error("시험지 파일을 넣어 주세요.");
      const form = new FormData();
      form.append("file", file);
      form.append("title", title.trim() || file.name.replace(/\.[^.]+$/, ""));
      form.append("level", level);
      try {
        const job = await api.upload<Job>("/exams/from-file", form);
        const done = await waitJob(job, (j) => setMsg(j.progress.message));
        onOpenChange(false);
        go("exams", String(done.result?.exam_id), "read");
      } catch (e) {
        if (e instanceof ApiError && e.code === "already_ingested") {
          onOpenChange(false);
          go("exams", String(e.extra.exam_id), "read");
          return;
        }
        throw e;
      }
    });
  return (
    <Dialog
      open={open}
      onOpenChange={(v) => !act.busy && onOpenChange(v)}
      title="새 시험"
      desc="시험지와 해설지를 넣으면 문항·배점·해설을 읽습니다. 한 파일이어도 됩니다."
      footer={
        <>
          <Button variant="ghost" disabled={act.busy} onClick={() => onOpenChange(false)}>
            취소
          </Button>
          <Button variant="primary" disabled={act.busy || !file} onClick={() => void start()}>
            {act.busy ? "읽는 중…" : "시험 읽기"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] text-text-2">시험 이름</span>
          <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="예: 11월 중간고사 물리" />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] text-text-2">학년 · 과목</span>
          <Select value={level} onChange={setLevel} options={(levels.data ?? []).map((l) => ({ value: l.label, label: l.label }))} />
        </label>
        {file ? (
          <div className="flex items-center gap-2 rounded-lg border border-line px-3 py-2.5">
            <FileText className="size-4 text-text-3" />
            <span className="flex-1 truncate">{file.name}</span>
            {!act.busy && (
              <Button size="sm" variant="ghost" onClick={() => setFile(null)}>
                바꾸기
              </Button>
            )}
          </div>
        ) : (
          <Dropzone title="시험지 · 해설지를 끌어다 놓기" hint="PDF · DOCX · HWPX · HWP" accept=".pdf,.docx,.hwpx,.hwp" onFiles={(f) => setFile(f[0] ?? null)} />
        )}
        {act.busy && (
          <div>
            <Progress value={30} />
            <div className="mt-1 text-[12px] text-text-3">{msg || "시작하는 중"} · 몇 분 걸릴 수 있습니다. 창을 닫아도 계속됩니다.</div>
          </div>
        )}
        {act.error && <ErrorNote message={act.error} />}
      </div>
    </Dialog>
  );
}

export function ExamsPage() {
  const [open, setOpen] = useState(false);
  const { data, error, loading } = useApi<ExamListItem[]>("/exams");
  return (
    <div className="mx-auto max-w-4xl px-6 py-8">
      <div className="flex items-center">
        <h1 className="m-0 flex-1 text-[20px] font-medium">시험</h1>
        <Button variant="primary" onClick={() => setOpen(true)}>
          <Plus /> 새 시험
        </Button>
      </div>
      {error && (
        <div className="mt-4">
          <ErrorNote message={error} />
        </div>
      )}
      {data && data.length === 0 && (
        <Card className="mt-6 px-6 py-12 text-center text-text-2">아직 시험이 없습니다. "새 시험"에 시험지를 넣어 시작하세요.</Card>
      )}
      {loading && !data && <div className="mt-6 text-text-3">불러오는 중…</div>}
      <div className="mt-6 flex flex-col gap-3">
        {(data ?? []).map((e) => {
          const s = e.summary;
          const idx = stepIndex(s.step);
          const done = s.step === "export";
          return (
            <a key={e.id} href={href("exams", e.id, s.step)} className="text-text no-underline">
              <Card className="flex items-center gap-5 px-5 py-4 hover:bg-muted">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-[15px] font-medium">{e.title}</span>
                    <span className="text-[13px] text-text-3">
                      {e.created_at.slice(0, 10)} · 문항 {s.questions}개 · {s.students}명
                    </span>
                  </div>
                  <div className="mt-3 flex items-center gap-3">
                    <div className="w-48">
                      <Progress value={((idx + (done ? 1 : 0.5)) / STEPS.length) * 100} tone={done ? "ok" : "accent"} />
                    </div>
                    <span className="text-[13px] text-text-2">
                      {done ? "끝남" : STEPS[idx]?.label}
                      {s.step === "grade" && ` ${s.graded}/${s.students * s.questions}`}
                    </span>
                  </div>
                </div>
                <div className="flex gap-1.5">
                  {s.needs_input > 0 && <Badge tone="bad">입력 대기 {s.needs_input}</Badge>}
                  {s.needs_rename + s.not_in_roster > 0 && <Badge tone="bad">이름 확인 {s.needs_rename + s.not_in_roster}</Badge>}
                  {s.needs_review > 0 && <Badge tone="warn">검토 필요 {s.needs_review}</Badge>}
                </div>
                <ChevronRight className="size-4 text-text-3" />
              </Card>
            </a>
          );
        })}
      </div>
      <NewExam open={open} onOpenChange={setOpen} />
    </div>
  );
}
