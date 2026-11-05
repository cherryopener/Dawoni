import { ChevronRight, FileText, Plus, Trash2 } from "lucide-react";
import { useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Dropzone, ErrorNote, Input, Progress } from "@/components/ui/misc";
import { Select } from "@/components/ui/select";
import { api, ApiError, enc, useAction, useApi, waitJob } from "@/lib/api";
import { go, href } from "@/lib/router";
import { useSession } from "@/lib/session";
import { STEPS, stepIndex } from "@/lib/steps";
import type { ExamListItem, Job, Level } from "@/lib/types";

const ACCEPTED = /\.(pdf|docx|hwpx|hwp)$/i;

/** HWPX는 앱이 직접 읽는다. HWP는 이 PC의 한글(자동화)로 HWPX로 바꿔 읽는다 — 안 되면 서버가
 * 이유(자동화 등록 없음 → 복구 설치)와 함께 HWPX·PDF로 저장해 달라고 알린다 (docs/HWP_HANGUL.md). */
function fileProblem(f: File): string | null {
  if (!ACCEPTED.test(f.name)) return `${f.name}: PDF · DOCX · HWPX · HWP 파일만 넣을 수 있습니다.`;
  return null;
}

function NewExam({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const levels = useApi<Level[]>(open ? "/levels" : null, { refreshOnJobs: false });
  const [title, setTitle] = useState("");
  const [level, setLevel] = useState("물리");
  const [file, setFile] = useState<File | null>(null);
  const [fileErr, setFileErr] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  const act = useAction();
  // 읽는 중에 창을 닫아도 작업은 서버에서 계속된다. 닫혀 있으면 끝나도 화면을 옮기지 않는다
  // (끝난 소식은 작업·알림 버튼에, 시험은 목록에 나온다)
  const openRef = useRef(open);
  openRef.current = open;
  const reset = () => {
    setTitle("");
    setFile(null);
    setFileErr(null);
    setMsg("");
  };
  const pick = (files: File[]) => {
    const f = files[0];
    if (!f) return;
    const problem = fileProblem(f);
    setFileErr(problem);
    setFile(problem ? null : f);
  };
  const finish = (examId: string) => {
    reset();
    if (!openRef.current) return;
    onOpenChange(false);
    go("exams", examId, "read");
  };
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
        finish(String(done.result?.exam_id));
      } catch (e) {
        if (e instanceof ApiError && e.code === "already_ingested") {
          finish(String(e.extra.exam_id));
          return;
        }
        throw e;
      }
    });
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="새 시험"
      desc="시험지와 해설지를 넣으면 문항·배점·해설을 읽습니다. 한 파일이어도 됩니다."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {act.busy ? "닫기" : "취소"}
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
          <Input value={title} disabled={act.busy} onChange={(e) => setTitle(e.target.value)} placeholder="예: 2610 월례, 2611 공통, 2612 서울" />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] text-text-2">과목</span>
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
          <Dropzone
            title="시험지 · 해설지를 끌어다 놓기"
            hint="PDF · DOCX · HWPX · HWP (HWP는 이 PC의 한글로 바꿔 읽습니다)"
            accept=".pdf,.docx,.hwpx,.hwp"
            onFiles={pick}
          />
        )}
        {fileErr && <ErrorNote message={fileErr} />}
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

/** 시험 지우기 확인. 함께 쓰는 폴더면 다른 강사 다워니에서도 사라진다고 알린다. */
function DeleteExam({ exam, onClose, onDeleted }: { exam: ExamListItem | null; onClose: () => void; onDeleted: () => void }) {
  const team = useSession().status.workspace_mode === "team";
  const act = useAction();
  const remove = () =>
    act.run(async () => {
      await api.del(`/exams/${enc(exam!.id)}`);
      onClose();
      onDeleted();
    });
  return (
    <Dialog
      open={exam !== null}
      onOpenChange={(v) => !v && !act.busy && onClose()}
      title={exam ? `'${exam.title}' 시험을 지울까요?` : ""}
      desc="해설·루브릭·판독·채점이 모두 지워지고 되돌릴 수 없습니다. 원본 시험지와 내보낸 HWPX·점수표는 남습니다."
      footer={
        <>
          <Button variant="ghost" disabled={act.busy} onClick={onClose}>
            취소
          </Button>
          <Button variant="danger" disabled={act.busy} onClick={() => void remove()}>
            <Trash2 /> {act.busy ? "지우는 중…" : "지우기"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {team && (
          <div className="rounded-lg bg-warn-soft px-3 py-2 text-[13px]">함께 쓰는 폴더라 다른 강사 다워니에서도 이 시험이 사라집니다.</div>
        )}
        {act.error && <ErrorNote message={act.error} />}
      </div>
    </Dialog>
  );
}

export function ExamsPage() {
  const [open, setOpen] = useState(false);
  const [doomed, setDoomed] = useState<ExamListItem | null>(null);
  const { data, error, loading, reload } = useApi<ExamListItem[]>("/exams");
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
            <Card key={e.id} className="flex items-center gap-2 pr-3 hover:bg-muted">
              <a href={href("exams", e.id, s.step)} className="flex min-w-0 flex-1 items-center gap-5 py-4 pl-5 text-text no-underline">
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
              </a>
              <Button size="icon" variant="ghost" aria-label={`${e.title} 지우기`} title="시험 지우기" onClick={() => setDoomed(e)}>
                <Trash2 />
              </Button>
            </Card>
          );
        })}
      </div>
      <NewExam open={open} onOpenChange={setOpen} />
      <DeleteExam exam={doomed} onClose={() => setDoomed(null)} onDeleted={reload} />
    </div>
  );
}
