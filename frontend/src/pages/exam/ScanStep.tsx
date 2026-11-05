import { AlertTriangle, FileText, FolderOpen, Printer, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge, type Tone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Help } from "@/components/ui/help";
import { Chips, Dropzone, ErrorNote, Input, Progress } from "@/components/ui/misc";
import { api, enc, examFileUrl, onJobsChanged, useAction, useApi, waitJob } from "@/lib/api";
import { useTeacher } from "@/lib/session";
import type { ExamStudent, InboxPdf, Job, RosterStudent } from "@/lib/types";
import { useExam } from "../Exam";

type Status = "이름 확인 대기" | "입력 대기" | "확인 전" | "확인 완료" | "판독 전";
const ORDER: Status[] = ["이름 확인 대기", "입력 대기", "확인 전", "판독 전", "확인 완료"];
const TONE: Record<Status, Tone> = { "이름 확인 대기": "bad", "입력 대기": "bad", "확인 전": "warn", "판독 전": "neutral", "확인 완료": "ok" };

function statusOf(s: ExamStudent): { status: Status; unreadable: number } {
  const cells = Object.values(s.cells);
  const unreadable = cells.reduce((a, c) => a + (c.transcript?.unreadable ?? 0), 0);
  if (namePending(s)) return { status: "이름 확인 대기", unreadable };
  if (unreadable) return { status: "입력 대기", unreadable };
  const ts = cells.map((c) => c.transcript).filter((t) => t !== null);
  if (ts.length === 0) return { status: "판독 전", unreadable };
  return { status: ts.every((t) => t.confirmed) ? "확인 완료" : "확인 전", unreadable };
}

/** 이름 확인 전: 같은 반 동명이인, 또는 명부와 맞추지 못함 — 판독·채점은 하지만 판독본 HWPX는 확인 뒤에. */
function namePending(s: ExamStudent): boolean {
  return Boolean(s.record?.needs_rename || s.record?.roster_note);
}

/** 이 시험의 판독 작업 진행 (작업 목록에서). */
function useScanJobs(examId: string): Job[] {
  const [jobs, setJobs] = useState<Job[]>([]);
  useEffect(() => {
    const load = () =>
      api
        .get<Job[]>("/jobs")
        .then((js) => setJobs(js.filter((j) => j.params.exam === examId && ["scan_batch", "scan", "confirm_files"].includes(j.kind) && ["queued", "running"].includes(j.status))))
        .catch(() => undefined);
    load();
    const t = setInterval(load, 2000);
    const off = onJobsChanged(load);
    return () => {
      clearInterval(t);
      off();
    };
  }, [examId]);
  return jobs;
}

function BatchDialog({ pdf, onClose }: { pdf: InboxPdf | null; onClose: () => void }) {
  const { base, detail } = useExam();
  const roster = useApi<{ classes: string[] }>(pdf ? "/students" : null, { refreshOnJobs: false });
  const guess = Math.max(2, ...detail.exam.questions.flatMap((q) => q.answer_pages ?? []));
  const [per, setPer] = useState(String(guess));
  const [classes, setClasses] = useState<string[]>([]);
  const act = useAction();
  const n = pdf?.pages && Number(per) > 0 ? pdf.pages / Number(per) : null;
  const start = () =>
    act.run(async () => {
      const job = await api.post<Job>(`${base}/scan-batch`, { path: pdf!.path, per_student: Number(per), classes });
      onClose();
      void waitJob(job).catch(() => undefined); // 진행은 위 막대와 알림으로
    });
  return (
    <Dialog
      open={pdf !== null}
      onOpenChange={(v) => !v && onClose()}
      title="답안지 판독"
      desc={pdf ? `${pdf.name}${pdf.pages ? ` · ${pdf.pages}쪽` : ""}` : ""}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            취소
          </Button>
          <Button variant="primary" disabled={act.busy || !(Number(per) > 0)} onClick={() => void start()}>
            판독 시작
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <label className="flex items-center gap-3">
          <span className="flex-1">
            학생 한 명의 쪽 수 <span className="text-[12px] text-text-3">(표지 포함)</span>
          </span>
          <Input type="number" min={1} value={per} onChange={(e) => setPer(e.target.value)} className="w-20 text-right" />
        </label>
        {n !== null && (
          <div className={"text-[13px] " + (Number.isInteger(n) ? "text-text-2" : "text-bad")}>
            {Number.isInteger(n) ? `학생 ${n}명으로 나눕니다.` : `쪽 수가 나누어떨어지지 않습니다 (${pdf?.pages}쪽 ÷ ${per}).`}
          </div>
        )}
        {(roster.data?.classes.length ?? 0) > 0 && (
          <div>
            <div className="mb-1.5 text-[13px] text-text-2">
              반 <Help>고르면 표지의 반을 이 안에서만 읽습니다. 비우면 명부의 반 전체.</Help>
            </div>
            <Chips options={roster.data!.classes} value={classes} onChange={setClasses} />
          </div>
        )}
        {act.error && <ErrorNote message={act.error} />}
      </div>
    </Dialog>
  );
}

type Fix = { name?: string; off?: boolean };

function RenameDialog({ open, onClose, students }: { open: boolean; onClose: () => void; students: ExamStudent[] }) {
  const { id, base } = useExam();
  const roster = useApi<{ students: RosterStudent[] }>(open ? "/students" : null, { refreshOnJobs: false });
  const [fixes, setFixes] = useState<Record<string, Fix>>({});
  const act = useAction();
  const pending = students.filter(namePending);
  const everyone = roster.data?.students ?? [];
  const inClass = (s: ExamStudent) => everyone.filter((r) => (r.class_name ?? "") === (s.record?.class_name ?? ""));
  const set = (sid: string, f: Fix) => setFixes({ ...fixes, [sid]: { ...fixes[sid], ...f } });
  const save = () =>
    act.run(async () => {
      for (const s of pending) {
        const f = fixes[s.student] ?? {};
        const name = f.name?.trim();
        const changed = Boolean(name && name !== s.record?.name);
        if (!changed && !f.off) continue;
        // 명부에서 고른 이름이면 그 학생과 잇는다 (서버도 이름·반으로 다시 맞춰 본다)
        const hit = changed ? inClass(s).find((r) => r.name === name) : undefined;
        await api.put(`${base}/students/${enc(s.student)}/identity`, hit ? { roster_id: hit.id } : { name: changed ? name : undefined, off_roster: Boolean(f.off) });
      }
      onClose();
    });
  return (
    <Dialog
      open={open}
      onOpenChange={(v) => !v && onClose()}
      title="이름 확인"
      desc="표지 그림을 보고 이름을 고르거나 고쳐 주세요. 같은 반에 같은 이름이면 김다원A, 김다원B처럼 나눕니다."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            나중에
          </Button>
          <Button variant="primary" disabled={act.busy} onClick={() => void save()}>
            {act.busy ? "판독본 다시 쓰는 중…" : "확인"}
          </Button>
        </>
      }
    >
      <div className="flex max-h-[55vh] flex-col gap-3 overflow-auto">
        {pending.map((s) => {
          const f = fixes[s.student] ?? {};
          const list = `roster-${s.student}`;
          return (
            <div key={s.student} className="flex items-start gap-3">
              <a href={examFileUrl(id, `scans/${s.student}/page_01.png`)} target="_blank" rel="noreferrer" title="표지 크게 보기" className="shrink-0">
                <img
                  src={examFileUrl(id, `scans/${s.student}/page_01.png`)}
                  alt="표지"
                  className="h-28 w-20 rounded-lg bg-white object-contain"
                  onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                />
              </a>
              <div className="min-w-0 flex-1 text-[13px]">
                <div className="text-text-2">
                  {s.record?.class_name ?? "반 모름"} · {s.record?.school ?? "학교 모름"}
                  {s.record?.cover_name && s.record.cover_name !== s.record.name && <span className="text-text-3"> · 표지: {s.record.cover_name}</span>}
                </div>
                <div className={s.record?.needs_rename ? "text-bad" : "text-warn"}>{s.record?.needs_rename ? "같은 반에 같은 이름이 또 있음" : s.record?.roster_note}</div>
                {!s.record?.needs_rename && (
                  <label className="mt-1 flex items-center gap-1.5 text-text-2">
                    <input type="checkbox" checked={Boolean(f.off)} onChange={(e) => set(s.student, { off: e.target.checked })} />
                    명부에 없는 학생으로 두기
                  </label>
                )}
              </div>
              <Input
                list={list}
                value={f.name ?? s.record?.name ?? ""}
                onChange={(e) => set(s.student, { name: e.target.value })}
                className="w-32"
                aria-label={`${s.record?.name ?? s.student} 이름`}
              />
              <datalist id={list}>
                {inClass(s).map((r) => (
                  <option key={r.id} value={r.name} />
                ))}
              </datalist>
            </div>
          );
        })}
        {act.error && <ErrorNote message={act.error} />}
      </div>
    </Dialog>
  );
}

/** ③ 답안 판독: 스캔 넣기 → 표지·손글씨 판독 → 판독본 HWPX → 강사가 한글에서 고치고 "반영". */
export function ScanStep() {
  const { id, base, detail, reload } = useExam();
  const by = useTeacher();
  const students = useApi<ExamStudent[]>(`${base}/students`);
  const inbox = useApi<InboxPdf[]>("/scanner/inbox");
  const fileMap = useApi<Record<string, Record<string, string>>>(`${base}/student-files`);
  const jobs = useScanJobs(id);
  const [pdf, setPdf] = useState<InboxPdf | null>(null);
  const [renameOpen, setRenameOpen] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const act = useAction();

  const rows = (students.data ?? []).map((s) => ({ s, ...statusOf(s) })).sort((a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status));
  const waiting = rows.filter((r) => r.status === "입력 대기").length;
  const dup = rows.filter((r) => r.status === "이름 확인 대기").length;
  const twins = rows.filter((r) => r.s.record?.needs_rename).length;
  const offRoster = dup - twins;

  const upload = (files: File[]) =>
    act.run(async () => {
      const f = files[0];
      if (!f) return;
      const form = new FormData();
      form.append("file", f);
      const up = await api.upload<{ path: string; name: string }>("/scanner/upload", form);
      const list = await api.get<InboxPdf[]>("/scanner/inbox");
      inbox.reload();
      setPdf(list.find((p) => p.path === up.path) ?? { path: up.path, name: up.name, pages: null, size: f.size, modified: 0 });
    });
  const openScanner = () => act.run(() => api.post("/scanner/open"));
  const openFile = (path: string) => act.run(() => api.post("/files/open", { path }));
  const reveal = () => act.run(async () => api.post("/files/reveal", { path: (await api.get<{ folder: string }>(`${base}/outputs`)).folder }));
  const confirm = () =>
    act.run(async () => {
      const j = await waitJob(await api.post<Job>(`${base}/confirm-files`, { by, learn: true }));
      const r = j.result as { confirmed: { changed: boolean }[]; problems: { student: string; question: string; reason: string }[]; graded: number };
      const changed = r.confirmed.filter((c) => c.changed).length;
      setResult(
        `확정 ${r.confirmed.length}건 (고친 것 ${changed}) · 다시 채점 ${r.graded}건` +
          (r.problems.length ? ` · 읽지 못함: ${r.problems.map((p) => `${p.student} ${p.question}번 ${p.reason}`).join("; ")}` : ""),
      );
      reload();
    });

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4 px-6 py-6">
      <div className="grid grid-cols-2 gap-4">
        <Card>
          <CardHeader
            icon={<Printer />}
            title="스캔본 폴더"
            desc="복합기 공유 폴더에서 옮긴 PDF (새것 먼저)"
            right={
              <div className="flex gap-1">
                <Button size="sm" variant="ghost" onClick={() => void openScanner()}>
                  복합기 폴더 열기
                </Button>
                <Button size="icon" variant="ghost" aria-label="새로 고침" onClick={inbox.reload}>
                  <RefreshCw />
                </Button>
              </div>
            }
          />
          <CardBody className="max-h-48 overflow-auto pt-3">
            {(inbox.data ?? []).length === 0 && <div className="px-2 text-[13px] text-text-3">PDF가 없습니다.</div>}
            {(inbox.data ?? []).slice(0, 8).map((f) => (
              <div key={f.path} className="flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-muted">
                <FileText className="size-4 text-text-3" />
                <span className="flex-1 truncate">
                  {f.name} {f.pages && <span className="text-text-3">· {f.pages}쪽</span>}
                </span>
                <Button size="sm" variant="outline" onClick={() => setPdf(f)}>
                  판독
                </Button>
              </div>
            ))}
          </CardBody>
        </Card>
        <Dropzone title="스캔 PDF를 끌어다 놓기" hint="여러 학생이 한 파일이어도 됩니다 (학생당 쪽 수로 나눔)" accept=".pdf" onFiles={(f) => void upload(f)} />
      </div>

      {jobs.map((j) => (
        <Card key={j.id}>
          <CardBody className="flex items-center gap-4">
            <div className="flex-1">
              <div className="flex text-[13px]">
                <span className="flex-1">{j.progress.message || "판독 시작하는 중"}</span>
                <span className="text-text-3">{j.progress.total ? `${j.progress.done}/${j.progress.total}` : ""}</span>
              </div>
              <div className="mt-2">
                <Progress value={j.progress.total ? (j.progress.done / j.progress.total) * 100 : 5} />
              </div>
            </div>
            <Button size="sm" variant="ghost" onClick={() => void api.post(`/jobs/${j.id}/cancel`)}>
              멈추기
            </Button>
          </CardBody>
        </Card>
      ))}

      {dup > 0 && (
        <div className="flex items-center gap-2 rounded-[var(--radius-card)] border border-line bg-bad-soft px-4 py-3 text-bad">
          <AlertTriangle className="size-4" />
          <span className="flex-1">
            이름을 확인할 시험지가 있습니다 ({dup}장{twins ? ` · 같은 반 같은 이름 ${twins}` : ""}{offRoster ? ` · 명부에 없음 ${offRoster}` : ""}). 판독·채점은
            계속하고, 판독본은 이름을 확인하면 만듭니다.
          </span>
          <Button size="sm" variant="outline" onClick={() => setRenameOpen(true)}>
            이름 확인
          </Button>
        </div>
      )}
      {act.error && <ErrorNote message={act.error} />}
      {result && <div className="rounded-lg bg-muted px-4 py-2.5 text-[13px]">{result}</div>}

      <Card className="overflow-hidden">
        <div className="flex items-center gap-3 border-b border-line px-5 py-3">
          <span className="font-medium">학생 {rows.length}명</span>
          {waiting > 0 && <Badge tone="bad">입력 대기 {waiting}</Badge>}
          <Help>
            [판독불가]가 남은 답안은 "입력 대기"입니다. 한글에서 판독본을 열어 채우고 저장한 뒤 "한글에서 고친 것 반영"을 누르면 그 학생·문항만
            채점합니다. 나머지는 확인 전에도 채점까지 갑니다.
          </Help>
          <div className="flex-1" />
          <Button size="sm" variant="ghost" onClick={() => void reveal()}>
            <FolderOpen /> 판독본 폴더
          </Button>
          <Button variant="primary" size="sm" disabled={act.busy || rows.length === 0} onClick={() => void confirm()}>
            {act.busy ? "읽는 중…" : "한글에서 고친 것 반영"}
          </Button>
        </div>
        {rows.length === 0 && <div className="px-5 py-8 text-center text-text-3">아직 판독한 답안이 없습니다. 스캔 PDF를 넣어 시작하세요.</div>}
        {rows.length > 0 && (
          <table className="w-full border-collapse text-left">
            <thead className="text-[12px] text-text-3">
              <tr>
                <th className="px-5 py-2 font-normal">반</th>
                <th className="px-2 py-2 font-normal">이름</th>
                <th className="px-2 py-2 font-normal">학교</th>
                <th className="px-2 py-2 font-normal">상태</th>
                <th className="px-5 py-2 text-right font-normal">한글로 열기</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ s, status, unreadable }) => (
                <tr key={s.student} className="border-t border-line hover:bg-muted">
                  <td className="px-5 py-2.5 text-text-2">{s.record?.class_name ?? "—"}</td>
                  <td className="px-2 py-2.5">
                    {s.record?.name ?? s.student}
                    {s.record?.roster_note && (
                      <span className="ml-2" title={s.record.roster_note}>
                        <Badge tone="warn">명부 확인</Badge>
                      </span>
                    )}
                  </td>
                  <td className="px-2 py-2.5 text-text-2">{s.record?.school ?? ""}</td>
                  <td className="px-2 py-2.5">
                    <Badge tone={TONE[status]}>
                      {status}
                      {unreadable ? ` · [판독불가] ${unreadable}곳` : ""}
                    </Badge>
                  </td>
                  <td className="px-5 py-2 text-right">
                    {detail.exam.questions.map((q) => {
                      const p = fileMap.data?.[s.student]?.[q.number];
                      return p ? (
                        <Button key={q.number} size="sm" variant="ghost" onClick={() => void openFile(p)}>
                          {q.number}번
                        </Button>
                      ) : null;
                    })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <BatchDialog pdf={pdf} onClose={() => setPdf(null)} />
      <RenameDialog
        open={renameOpen}
        onClose={() => {
          setRenameOpen(false);
          students.reload();
          fileMap.reload(); // 이름이 바뀌면 HWPX 파일 이름도 바뀐다
          reload();
        }}
        students={students.data ?? []}
      />
    </div>
  );
}
