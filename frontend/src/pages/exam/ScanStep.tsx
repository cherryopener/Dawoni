import { AlertTriangle, Check, FileText, Pencil, Printer, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge, type Tone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Help } from "@/components/ui/help";
import { Chips, Dropzone, ErrorNote, Input, Progress } from "@/components/ui/misc";
import { api, enc, examFileUrl, onJobsChanged, useAction, useApi, waitJob } from "@/lib/api";
import { refreshNotices } from "@/lib/notices";
import type { Cell, ExamStudent, InboxPdf, Job, RosterStudent } from "@/lib/types";
import { useExam } from "../Exam";
import { type EditTarget, TranscriptEditor } from "./TranscriptEditor";

type Status = "이름 확인 대기" | "입력 대기" | "확인 전" | "AI 확인" | "확인 완료" | "판독 전";
const ORDER: Status[] = ["이름 확인 대기", "입력 대기", "확인 전", "판독 전", "AI 확인", "확인 완료"];
const TONE: Record<Status, Tone> = { "이름 확인 대기": "bad", "입력 대기": "bad", "확인 전": "warn", "판독 전": "neutral", "AI 확인": "accent", "확인 완료": "ok" };

function statusOf(s: ExamStudent): { status: Status; unreadable: number } {
  const cells = Object.values(s.cells);
  const unreadable = cells.reduce((a, c) => a + (c.transcript?.unreadable ?? 0), 0);
  if (namePending(s)) return { status: "이름 확인 대기", unreadable };
  if (unreadable) return { status: "입력 대기", unreadable };
  const ts = cells.map((c) => c.transcript).filter((t) => t !== null);
  if (ts.length === 0) return { status: "판독 전", unreadable };
  if (!ts.every((t) => t.confirmed)) return { status: "확인 전", unreadable };
  // AI가 확정한 답안이 하나라도 있으면 "AI 확인" — 강사가 다 확인했으면 "확인 완료"
  return { status: ts.some((t) => t.confirmed_by === "AI") ? "AI 확인" : "확인 완료", unreadable };
}

/** 이름 확인 전: 같은 반 동명이인, 또는 명부와 맞추지 못함 — 판독·채점은 하지만 점수표에는 확인 뒤에. */
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
        .then((js) => setJobs(js.filter((j) => j.params.exam === examId && ["scan_batch", "scan"].includes(j.kind) && ["queued", "running"].includes(j.status))))
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
              반 <Help>이 PDF의 반을 하나 고르면 그 반 명단에서만 표지 이름을 고르고 반은 그 반으로 합니다 (늦게 본 학생 한 명도). 여럿 고르면 그 안에서, 비우면 명부의 반 전체.</Help>
            </div>
            <Chips options={roster.data!.classes} value={classes} onChange={setClasses} />
          </div>
        )}
        {act.error && <ErrorNote message={act.error} />}
      </div>
    </Dialog>
  );
}

type Fix = { name?: string; klass?: string; off?: boolean; add?: boolean };

/** 서버(`students/roster.py`)와 같은 맞추기: 띄어쓰기·대소문자 무시, "2반" = "2". */
const normName = (n: string | null | undefined) => (n ?? "").replace(/\s+/g, "").toLowerCase();
function normClass(c: string | null | undefined): string {
  const x = normName(c);
  return x.length > 1 && x.endsWith("반") ? x.slice(0, -1) : x;
}

/** 지금 상태 한 줄: 무엇을 확인해야 하는지, 또는 명부와 어떻게 이어졌는지. */
function stateLine(s: ExamStudent, rosterEmpty: boolean): { text: string; cls: string } {
  const r = s.record;
  if (r?.needs_rename) return { text: "같은 반에 같은 이름이 또 있음 — 김다원A, 김다원B처럼 나누세요", cls: "text-bad" };
  if (r?.roster_note) return { text: r.roster_note, cls: "text-warn" };
  if (r?.roster_id) return { text: `명부와 이어짐 (${r.roster_id})`, cls: "text-text-3" };
  return { text: rosterEmpty ? "명부 없음 — 표지에서 읽은 이름" : "명부에 없는 학생", cls: "text-text-3" };
}

/** 이름 확인·고치기: 확인이 필요한 시험지 전부, 또는 표에서 누른 학생 한 명. 반·이름을 명부에서 고르거나
 * 고쳐 쓰고, 명부에 없으면 명부에 넣어 잇거나 명부 밖 학생으로 둔다. 고치면 판독문·채점이 새 이름으로
 * 옮겨지고 점수표를 다시 쓴다. */
function RenameDialog({ students, onClose }: { students: ExamStudent[] | null; onClose: () => void }) {
  const { id, base } = useExam();
  const open = students !== null;
  const roster = useApi<{ students: RosterStudent[]; classes: string[] }>(open ? "/students" : null, { refreshOnJobs: false });
  const [fixes, setFixes] = useState<Record<string, Fix>>({});
  const act = useAction();
  const everyone = roster.data?.students ?? [];
  const now = (s: ExamStudent) => {
    const f = fixes[s.student] ?? {};
    return { f, name: (f.name ?? s.record?.name ?? "").trim(), klass: (f.klass ?? s.record?.class_name ?? "").trim() };
  };
  const inClass = (klass: string) => everyone.filter((r) => normClass(r.class_name) === normClass(klass));
  const hitOf = (name: string, klass: string) => (name ? inClass(klass).find((r) => normName(r.name) === normName(name)) : undefined);
  const set = (sid: string, f: Fix) => setFixes({ ...fixes, [sid]: { ...fixes[sid], ...f } });
  const save = () =>
    act.run(async () => {
      for (const s of students ?? []) {
        const { f, name, klass } = now(s);
        const rec = s.record;
        if (!name) throw new Error(`${rec?.name ?? s.student}: 이름을 적어 주세요`);
        const classChanged = normClass(klass) !== normClass(rec?.class_name);
        const renamed = name !== (rec?.name ?? "") || classChanged;
        const found = hitOf(name, klass);
        if (!renamed && !(found && found.id !== rec?.roster_id) && !f.off && !f.add) continue;
        // 명부에 있는 반·이름이면 그 학생과 잇는다. 없으면 '명부에 넣기'로 넣고 잇거나, 이름만 고친다 (서버가 명부와 다시 맞춰 본다)
        const hit = found ?? (f.add ? await api.post<RosterStudent>("/students", { name, class_name: klass || null, school: rec?.school ?? null }) : undefined);
        const body = hit
          ? { roster_id: hit.id }
          : { name: renamed ? name : undefined, class_name: classChanged && klass ? klass : undefined, off_roster: Boolean(f.off) };
        await api.put(`${base}/students/${enc(s.student)}/identity`, body);
      }
      onClose();
    });
  return (
    <Dialog
      open={open}
      onOpenChange={(v) => !v && onClose()}
      title="이름 확인"
      desc="표지 그림을 보고 반·이름을 명부에서 고르거나 고쳐 주세요. 같은 반에 같은 이름이면 김다원A, 김다원B처럼 나눕니다."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            취소
          </Button>
          <Button variant="primary" disabled={act.busy} onClick={() => void save()}>
            {act.busy ? "저장하는 중…" : "확인"}
          </Button>
        </>
      }
    >
      <div className="flex max-h-[55vh] flex-col gap-4 overflow-auto">
        <datalist id="roster-classes">
          {(roster.data?.classes ?? []).map((c) => (
            <option key={c} value={c} />
          ))}
        </datalist>
        {(students ?? []).map((s) => {
          const { f, name, klass } = now(s);
          const hit = hitOf(name, klass);
          const state = stateLine(s, everyone.length === 0);
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
                  {s.record?.school ?? "학교 모름"}
                  {s.record?.cover_name && s.record.cover_name !== s.record.name && <span className="text-text-3"> · 표지: {s.record.cover_name}</span>}
                </div>
                <div className={state.cls}>{state.text}</div>
                <div className="mt-2 flex gap-2">
                  <Input
                    list="roster-classes"
                    value={f.klass ?? s.record?.class_name ?? ""}
                    onChange={(e) => set(s.student, { klass: e.target.value })}
                    placeholder="반"
                    className="w-24"
                    aria-label={`${s.record?.name ?? s.student} 반`}
                  />
                  <Input
                    list={list}
                    value={f.name ?? s.record?.name ?? ""}
                    onChange={(e) => set(s.student, { name: e.target.value })}
                    placeholder="이름"
                    className="w-32"
                    aria-label={`${s.record?.name ?? s.student} 이름`}
                  />
                  <datalist id={list}>
                    {inClass(klass).map((r) => (
                      <option key={r.id} value={r.name} />
                    ))}
                  </datalist>
                </div>
                {hit ? (
                  <div className="mt-1.5 text-text-2">
                    명부: {[hit.class_name, hit.name].filter(Boolean).join(" ")}
                    {hit.school ? ` (${hit.school})` : ""}
                  </div>
                ) : (
                  name && (
                    <div className="mt-1.5 flex flex-col gap-1 text-text-2">
                      <label className="flex items-center gap-1.5">
                        <input type="checkbox" checked={Boolean(f.add)} onChange={(e) => set(s.student, { add: e.target.checked, off: false })} />
                        명부에 넣고 잇기
                      </label>
                      {everyone.length > 0 && (
                        <label className="flex items-center gap-1.5">
                          <input type="checkbox" checked={Boolean(f.off)} onChange={(e) => set(s.student, { off: e.target.checked, add: false })} />
                          명부에 없는 학생으로 두기
                        </label>
                      )}
                    </div>
                  )
                )}
              </div>
            </div>
          );
        })}
        {act.error && <ErrorNote message={act.error} />}
      </div>
    </Dialog>
  );
}

/** 판독문 버튼 하나의 모양: 확인 완료 ✓, [판독불가] 빨강, 확인 전 노랑. */
function cellTone(c: Cell | undefined): string {
  const t = c?.transcript;
  if (!t) return "";
  if (t.unreadable) return "text-bad";
  return t.confirmed ? "text-ok" : "text-warn";
}

/** ③ 답안 판독: 스캔 넣기 → 표지·손글씨 판독 → 강사가 화면에서 원본과 나란히 보고 고쳐 확인 (2026-11-18 HWPX 대신). */
export function ScanStep() {
  const { id, base, detail, reload, focus } = useExam();
  const students = useApi<ExamStudent[]>(`${base}/students`);
  const inbox = useApi<InboxPdf[]>("/scanner/inbox");
  const jobs = useScanJobs(id);
  const [pdf, setPdf] = useState<InboxPdf | null>(null);
  const [renameFor, setRenameFor] = useState<string[] | null>(null); // 이름 확인 창에 보일 학생
  const [edit, setEdit] = useState<EditTarget | null>(focus.length >= 2 ? { student: focus[0]!, question: focus[1]! } : null);
  const act = useAction();

  const rows = (students.data ?? []).map((s) => ({ s, ...statusOf(s) })).sort((a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status));
  const waiting = rows.filter((r) => r.status === "입력 대기").length;
  const dup = rows.filter((r) => r.status === "이름 확인 대기").length;
  const twins = rows.filter((r) => r.s.record?.needs_rename).length;
  const offRoster = dup - twins;
  /** "차례로 확인": 표 순서대로 아직 확인하지 않은 답안. */
  const queue: EditTarget[] = rows.flatMap(({ s }) =>
    detail.exam.questions.filter((q) => s.cells[q.number]?.transcript && !s.cells[q.number]!.transcript!.confirmed).map((q) => ({ student: s.student, question: q.number })),
  );
  /** [판독불가]가 없는데 확인 전인 답안 (자동 확인 전에 판독한 시험) — 한 번에 AI 확인으로. */
  const acceptable = rows.reduce((n, { s }) => n + Object.values(s.cells).filter((c) => c?.transcript && !c.transcript.confirmed && !c.transcript.unreadable).length, 0);
  const acceptAll = () =>
    act.run(async () => {
      await api.post(`${base}/transcripts/accept`);
      students.reload();
      reload();
    });
  const [openQueue, setOpenQueue] = useState<EditTarget[]>([]);
  const open = (t: EditTarget, withQueue: boolean) => {
    setOpenQueue(withQueue ? queue : []);
    setEdit(t);
  };

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
          <Button size="sm" variant="outline" onClick={() => setRenameFor(rows.filter((r) => namePending(r.s)).map((r) => r.s.student))}>
            이름 확인
          </Button>
        </div>
      )}
      {act.error && <ErrorNote message={act.error} />}

      <Card className="overflow-hidden">
        <div className="flex items-center gap-3 border-b border-line px-5 py-3">
          <span className="font-medium">학생 {rows.length}명</span>
          {waiting > 0 && <Badge tone="bad">입력 대기 {waiting}</Badge>}
          <Help>
            문항 번호를 누르면 원본과 판독문을 나란히 보고 고칩니다. [판독불가]가 남은 답안은 "입력 대기" — 채우고 확인하면 그 학생·문항만 채점합니다.
            나머지는 AI가 바로 확정하고(AI 확인) 채점까지 갑니다 — 고쳐 확인하면 다시 채점합니다. 표지 이름을 잘못 읽었으면 이름을 눌러 고칩니다.
          </Help>
          <div className="flex-1" />
          {acceptable > 0 && (
            <Button variant="outline" size="sm" disabled={act.busy} onClick={() => void acceptAll()} title="[판독불가]가 없는 답안을 AI가 읽은 그대로 확정합니다 (AI 호출 없음, 빈 답안은 0점으로 끝)">
              <Check /> AI 판독 승인 ({acceptable})
            </Button>
          )}
          <Button variant="primary" size="sm" disabled={queue.length === 0} onClick={() => open(queue[0]!, true)}>
            {queue.length ? `차례로 확인 (${queue.length})` : "모두 확인함"}
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
                <th className="px-5 py-2 text-right font-normal">판독문</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ s, status, unreadable }) => (
                <tr key={s.student} className="group border-t border-line hover:bg-muted">
                  <td className="px-5 py-2.5 text-text-2">{s.record?.class_name ?? "—"}</td>
                  <td className="px-2 py-2.5">
                    <button type="button" onClick={() => setRenameFor([s.student])} className="cursor-pointer text-left hover:underline" title="이름·반 고치기">
                      {s.record?.name ?? s.student}
                      <Pencil className="ml-1 inline size-3.5 text-text-3 opacity-0 group-hover:opacity-100" />
                    </button>
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
                      const c = s.cells[q.number];
                      const t = c?.transcript;
                      if (!t) return null;
                      const state = t.unreadable ? `[판독불가] ${t.unreadable}곳` : t.confirmed ? (t.confirmed_by === "AI" ? "AI 확인" : "확인 완료") : "확인 전";
                      return (
                        <Button
                          key={q.number}
                          size="sm"
                          variant="ghost"
                          title={`${q.number}번 판독문 — ${state}`}
                          className={cellTone(c)}
                          onClick={() => open({ student: s.student, question: q.number }, false)}
                        >
                          {t.confirmed && !t.unreadable && <Check />}
                          {q.number}번
                        </Button>
                      );
                    })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <BatchDialog pdf={pdf} onClose={() => setPdf(null)} />
      <TranscriptEditor
        target={edit}
        queue={openQueue}
        onClose={() => setEdit(null)}
        onChanged={() => {
          students.reload();
          reload();
          refreshNotices(); // 단계 표시의 '입력 대기' 수
        }}
      />
      <RenameDialog
        key={renameFor?.join("|") ?? ""}
        students={renameFor && (students.data ?? []).filter((s) => renameFor.includes(s.student))}
        onClose={() => {
          setRenameFor(null);
          students.reload();
          reload();
          refreshNotices(); // 단계 표시의 '이름 확인' 수
        }}
      />
    </div>
  );
}
