import { AlertTriangle, ArrowRight, Check, ChevronRight, Download, FileText, FolderOpen, Pencil, Plus, RotateCcw, Save, Sparkles, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { RichText } from "@/components/Math";
import { Badge, type Tone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { ErrorNote, Input, Progress, textareaClass } from "@/components/ui/misc";
import { Select } from "@/components/ui/select";
import { api, enc, errorText, jobsChanged, onJobsChanged, useAction } from "@/lib/api";
import { cn } from "@/lib/cn";
import { handExam } from "@/lib/handoff";
import { go, href } from "@/lib/router";
import { useTeacher } from "@/lib/session";
import { fromDollars, toDollars } from "@/lib/transcript";
import type { Job, KeyPrep, LineKind, LineSource, PrepExport, PrepLine, PrepPart, PrepQuestion } from "@/lib/types";

const SOURCE: Record<LineSource, { label: string; tone: Tone }> = {
  key: { label: "해설지", tone: "neutral" },
  ai: { label: "AI 추천", tone: "accent" },
  ask: { label: "질문", tone: "warn" },
  fill: { label: "강사가 적기", tone: "bad" },
  teacher: { label: "강사", tone: "ok" },
};
const LEGEND: Record<LineSource, string> = {
  key: "원래 기준",
  ai: "바로 들어감",
  ask: "'넣기'를 골라야 들어감",
  fill: "AI가 정할 수 없는 값 — 빈칸(□)을 채워야 받을 수 있음",
  teacher: "직접 쓴 줄",
};
const BLANK = "□";

const KINDS: { value: LineKind; label: string }[] = [
  { value: "item", label: "채점 요소" },
  { value: "level", label: "낮은 단계" },
  { value: "accept", label: "인정" },
  { value: "deduct", label: "감점" },
  { value: "note", label: "안내" },
];
const KIND_LABEL = Object.fromEntries(KINDS.map((k) => [k.value, k.label])) as Record<LineKind, string>;
const POINTED = new Set<LineKind>(["item", "level"]);

const textarea = textareaClass;

const pts = (n: number) => (Number.isInteger(n) ? String(n) : String(Number(n.toFixed(2))));
const included = (l: PrepLine) => (l.decision !== null ? l.decision === "in" : l.source !== "ask");
const blank = (l: PrepLine) => included(l) && l.text.includes(BLANK);
const fileUrl = (id: string, path: string) => `/api/keyprep/${enc(id)}/file?path=${enc(path)}`;

/** 이 초안의 다듬기 작업 (돌고 있으면). */
function usePrepJob(prepId: string): Job | null {
  const [job, setJob] = useState<Job | null>(null);
  useEffect(() => {
    const load = () =>
      api
        .get<Job[]>("/jobs")
        .then((js) => setJob(js.find((j) => j.kind === "keyprep" && j.params.prep === prepId && ["queued", "running"].includes(j.status)) ?? null))
        .catch(() => undefined);
    load();
    const t = setInterval(load, 2000);
    const off = onJobsChanged(load);
    return () => {
      clearInterval(t);
      off();
    };
  }, [prepId]);
  return job;
}

/** ⓪ 해설 다듬기 (2026-11-18): AI가 다듬은 부분 점수 기준을 문항마다 보고, 질문에 답하고, 고친 뒤 HWPX로 받는다.
 * 고친 것은 바로 저장된다 (줄을 고칠 때는 [확인]을 누르면). */
export function KeyPrepPage({ prepId }: { prepId: string }) {
  const by = useTeacher();
  const [prep, setPrep] = useState<KeyPrep | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const [exported, setExported] = useState<PrepExport | null>(null);
  const [deleting, setDeleting] = useState(false);
  const job = usePrepJob(prepId);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const inFlight = useRef<Record<string, number>>({});
  const act = useAction();

  const load = useCallback(() => {
    api
      .get<KeyPrep>(`/keyprep/${enc(prepId)}`)
      .then((p) => {
        setPrep(p);
        setLoadErr(null);
      })
      .catch((e: unknown) => setLoadErr(errorText(e)));
  }, [prepId]);
  useEffect(load, [load]);
  const running = job !== null || prep?.state === "running";
  useEffect(() => {
    if (!running) return;
    const t = setInterval(load, 2500);
    return () => clearInterval(t);
  }, [running, load]);
  useEffect(() => onJobsChanged(load), [load]);

  /** 고친 문항: 화면에 바로, 서버에는 차례로 (HWPX 받기 전에 모두 저장되게). */
  const change = (q: PrepQuestion) => {
    setPrep((p) => (p ? { ...p, questions: p.questions.map((x) => (x.number === q.number ? q : x)) } : p));
    const body = { parts: q.parts, explanation: q.explanation, explanation_in: q.explanation_in, by };
    inFlight.current[q.number] = (inFlight.current[q.number] ?? 0) + 1;
    queue.current = queue.current
      .then(() => api.put<PrepQuestion>(`/keyprep/${enc(prepId)}/questions/${enc(q.number)}`, body))
      .then((saved) => {
        setSaveErr(null);
        // 더 늦게 고친 것이 아직 저장 중이면 이 응답(앞 상태)으로 화면을 되돌리지 않는다
        if (inFlight.current[saved.number] === 1) {
          setPrep((p) => (p ? { ...p, questions: p.questions.map((x) => (x.number === saved.number ? saved : x)) } : p));
        }
      })
      .catch((e: unknown) => setSaveErr(errorText(e)))
      .finally(() => {
        inFlight.current[q.number] = (inFlight.current[q.number] ?? 1) - 1;
      });
  };
  const redo = (numbers: string[] | null) =>
    act.run(async () => {
      await queue.current;
      await api.post<Job>(`/keyprep/${enc(prepId)}/refine`, { questions: numbers });
      jobsChanged();
      load();
    });
  const exportHwpx = () =>
    act.run(async () => {
      await queue.current;
      setExported(await api.post<PrepExport>(`/keyprep/${enc(prepId)}/export`));
      load();
    });
  const openFile = (path: string) => act.run(() => api.post("/files/open", { path }));
  const toStage1 = () =>
    act.run(async () => {
      const res = await fetch(`/api/keyprep/${enc(prepId)}/hwpx`, { credentials: "same-origin" });
      if (!res.ok) throw new Error("다듬은 HWPX를 읽지 못했습니다 — [HWPX로 받기]를 먼저 눌러 주세요.");
      const name = exported?.name ?? prep?.exported?.split("/").pop() ?? "다듬은 해설지.hwpx";
      handExam(new File([await res.blob()], name));
      go("exams");
    });
  const remove = () =>
    act.run(async () => {
      await api.del(`/keyprep/${enc(prepId)}`);
      go("exams");
    });

  if (loadErr) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-8">
        <ErrorNote message={loadErr} />
      </div>
    );
  }
  if (!prep) return <div className="mx-auto max-w-4xl px-6 py-8 text-text-3">불러오는 중…</div>;

  const qs = prep.questions;
  const done = qs.filter((q) => q.status === "done").length;
  const pending = qs.reduce((a, q) => a + q.pending, 0);
  const blanks = qs.reduce((a, q) => a + q.blanks, 0);
  const failed = qs.filter((q) => q.status === "failed");
  const lastFile = exported?.path ?? prep.exported;

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4 px-6 py-6">
      <div className="flex items-center gap-1 text-[13px] text-text-3">
        <a href={href("exams")} className="text-text-3 no-underline hover:text-text">
          시험
        </a>
        <ChevronRight className="size-3.5" />
        <span>0단계 · 해설 다듬기</span>
      </div>

      <Card className="p-5">
        <div className="flex flex-wrap items-start gap-3.5">
          <span className="grid size-11 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent-text">
            <Sparkles className="size-5" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-[18px] font-medium">{prep.title}</div>
            <div className="mt-0.5 text-[13px] text-text-3">
              {prep.source_name} · {prep.level} · 문항 {qs.length}개 · 다듬음 {done}
              {pending > 0 && ` · 답할 질문 ${pending}개`}
              {blanks > 0 && ` · 강사가 적을 곳 ${blanks}개`}
            </div>
          </div>
          <Button variant="ghost" size="icon" className="text-text-3 hover:bg-bad-soft hover:text-bad" onClick={() => setDeleting(true)} title="초안 지우기 (받은 HWPX는 남습니다)" aria-label="초안 지우기">
            <Trash2 />
          </Button>
          <Button
            variant={lastFile ? "outline" : "primary"}
            disabled={act.busy || running || done === 0 || blanks > 0}
            onClick={() => void exportHwpx()}
            title={blanks > 0 ? "강사가 적을 곳(□)을 먼저 채우거나 빼 주세요" : undefined}
          >
            <Download /> {lastFile ? "HWPX 다시 받기" : "HWPX로 받기"}
          </Button>
        </div>
        {running && (
          <div className="mt-4">
            <Progress value={job?.progress.total ? (job.progress.done / job.progress.total) * 100 : (done / Math.max(1, qs.length)) * 100 + 5} />
            <div className="mt-1.5 text-[13px] text-text-2">
              {job?.progress.message || "AI가 해설을 보며 풀고 부분 점수를 다듬는 중"} · 문항마다 몇 분 걸립니다. 끝난 문항부터 아래에서 볼 수 있습니다.
            </div>
          </div>
        )}
        <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1.5 rounded-xl bg-muted/60 px-3.5 py-2.5 text-[12px] text-text-2">
          {(Object.keys(SOURCE) as LineSource[]).map((s) => (
            <span key={s} className="flex items-center gap-1.5">
              <Badge tone={SOURCE[s].tone}>{SOURCE[s].label}</Badge>
              {LEGEND[s]}
            </span>
          ))}
        </div>
        {blanks > 0 && !running && (
          <div className="mt-3 rounded-xl bg-bad-soft px-3.5 py-2.5 text-[13px] text-bad">
            AI가 정답 그림에서도 풀이로도 정할 수 없던 값이 {blanks}곳 있습니다 — 아래 <b>강사가 적기</b> 줄의 빈칸({BLANK})을 채우거나 그 줄을 빼야 HWPX를
            받을 수 있습니다.
          </div>
        )}
        {prep.warnings.map((w) => (
          <div key={w} className="mt-3 rounded-xl bg-warn-soft px-3.5 py-2.5 text-[13px] text-warn">
            {w}
          </div>
        ))}
        {prep.state === "failed" && prep.error && (
          <div className="mt-3">
            <ErrorNote message={`다듬기가 멈췄습니다: ${prep.error}`} />
          </div>
        )}
        {failed.length > 0 && !running && (
          <div className="mt-3 flex flex-wrap items-center gap-3 rounded-xl bg-bad-soft px-3.5 py-2.5 text-[13px] text-bad">
            <span className="flex-1">{failed.map((q) => `${q.number}번`).join(", ")}을 다듬지 못했습니다 — HWPX에는 원래 해설 그대로 들어갑니다.</span>
            <Button size="sm" variant="outline" disabled={act.busy} onClick={() => void redo(failed.map((q) => q.number))}>
              <RotateCcw /> 다시 다듬기
            </Button>
          </div>
        )}
        {saveErr && (
          <div className="mt-3">
            <ErrorNote message={`저장하지 못했습니다: ${saveErr}`} />
          </div>
        )}
        {act.error && (
          <div className="mt-3">
            <ErrorNote message={act.error} />
          </div>
        )}
        {lastFile && (
          <div className="mt-4 rounded-xl border border-line p-3.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-accent-soft text-accent-text">
                <FileText className="size-4" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{lastFile.split("/").pop()}</span>
                <span className="block text-[12px] text-text-3">다듬은 해설지 HWPX</span>
              </span>
              <Button size="sm" variant="ghost" onClick={() => void openFile(lastFile)}>
                한글로 열기
              </Button>
              <Button size="sm" variant="ghost" onClick={() => void act.run(() => api.post("/files/reveal", { path: lastFile }))}>
                <FolderOpen /> 폴더에서 보기
              </Button>
              <Button size="sm" variant="ghost" onClick={() => void act.run(() => api.post("/files/save-as", { path: lastFile }))}>
                <Save /> 다른 이름으로 저장
              </Button>
              <Button size="sm" variant="primary" onClick={() => void toStage1()} title="첫 화면의 1단계(새 채점)에 시험지로 넣습니다">
                1단계로 — 이 해설지로 채점 <ArrowRight />
              </Button>
            </div>
            {exported && (exported.pending > 0 || exported.skipped.length > 0 || exported.warnings.length > 0) && (
              <ul className="m-0 mt-2 list-disc pl-5 text-[13px] text-text-2">
                {exported.pending > 0 && <li>답하지 않은 질문 {exported.pending}개는 넣지 않았습니다.</li>}
                {exported.skipped.length > 0 && <li>{exported.skipped.join(", ")}번은 다듬지 못해 원래 해설 그대로입니다.</li>}
                {exported.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Card>

      {qs.map((q) => (
        <QuestionCard key={q.number} prepId={prepId} q={q} busy={running || act.busy} onChange={change} onRedo={() => void redo([q.number])} />
      ))}

      <Dialog
        open={deleting}
        onOpenChange={setDeleting}
        title="이 초안을 지울까요?"
        desc="다듬은 기준과 고친 것이 지워집니다. 이미 받은 HWPX 파일은 남습니다."
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeleting(false)}>
              취소
            </Button>
            <Button variant="danger" disabled={act.busy} onClick={() => void remove()}>
              <Trash2 /> 지우기
            </Button>
          </>
        }
      />
    </div>
  );
}

// ---------------------------------------------------------------- 문항

function QuestionCard({ prepId, q, busy, onChange, onRedo }: { prepId: string; q: PrepQuestion; busy: boolean; onChange: (q: PrepQuestion) => void; onRedo: () => void }) {
  const [showQ, setShowQ] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [confirmRedo, setConfirmRedo] = useState(false);
  const setParts = (parts: PrepPart[]) => onChange({ ...q, parts });
  const setPart = (i: number, p: PrepPart) => setParts(q.parts.map((x, k) => (k === i ? p : x)));
  const differ = q.checks.filter((c) => c.agrees !== "same");
  const memos = q.checks.filter((c) => c.agrees === "same" && c.note);
  const replaced = new Set(q.replace);

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="grid h-8 min-w-8 shrink-0 place-items-center rounded-lg bg-primary px-2 text-[14px] font-medium text-primary-fg tabular-nums" title={`${q.number}번`}>
          {q.number}
        </span>
        {q.points !== null && (
          <span className="text-[13px] text-text-3" title={q.points_guessed ? "시험지에 배점 표시가 없어 1단계와 같은 규칙(⌊100 ÷ 문항 수⌋)으로 정했습니다" : undefined}>
            {pts(q.points)}점{q.points_guessed && " (배점 표시 없음 — ⌊100÷문항 수⌋)"}
          </span>
        )}
        {q.status === "waiting" && (busy ? <Badge tone="accent">다듬는 중</Badge> : <Badge>다듬지 않음</Badge>)}
        {q.status === "failed" && <Badge tone="bad">실패</Badge>}
        {q.pending > 0 && <Badge tone="warn">질문 {q.pending}</Badge>}
        {q.blanks > 0 && <Badge tone="bad">적을 곳 {q.blanks}</Badge>}
        {!q.has_key && <Badge tone="warn">해설 없음 — AI가 풀어 씀</Badge>}
        {q.edited_by && <span className="text-[12px] text-text-3">고침: {q.edited_by}</span>}
        <div className="flex-1" />
        <Button size="sm" variant="ghost" onClick={() => setShowQ(!showQ)}>
          {showQ ? "문항 접기" : "문항 보기"}
        </Button>
        {q.has_key && (
          <Button size="sm" variant="ghost" onClick={() => setShowKey(!showKey)}>
            {showKey ? "원래 해설 접기" : "원래 해설 보기"}
          </Button>
        )}
        {!(q.status === "waiting" && busy) && (
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => (q.status === "done" ? setConfirmRedo(true) : onRedo())} title="AI로 이 문항을 다듬기">
            <RotateCcw /> {q.status === "waiting" ? "다듬기" : "다시"}
          </Button>
        )}
      </div>

      {showQ && (
        <div className="mt-3 rounded-xl bg-muted px-4 py-3 text-[14px] leading-relaxed">
          {q.text.split("\n").map((ln, i) => (
            <div key={i}>
              <RichText text={ln} />
            </div>
          ))}
          {q.figures.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-2">
              {q.figures.map((f) => (
                <img key={f} src={fileUrl(prepId, f)} alt="" className="max-h-48 rounded border border-line bg-white" />
              ))}
            </div>
          )}
        </div>
      )}
      {showKey && (
        <div className="mt-3 rounded-xl border border-line px-4 py-3 text-[14px] leading-relaxed">
          <div className="mb-1.5 text-[12px] text-text-3">원래 해설 — 줄이 그어진 문단은 아래 새 기준으로 바뀝니다</div>
          {q.key_paragraphs.map((t, i) => (
            <div key={i} className={cn("flex gap-2", replaced.has(i + 1) && "text-text-3 line-through")}>
              <span className="w-7 shrink-0 text-right text-[12px] text-text-3 no-underline">¶{i + 1}</span>
              <RichText text={t} />
            </div>
          ))}
          {q.key_figures.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-2">
              {q.key_figures.map((f) => (
                <img key={f} src={fileUrl(prepId, f)} alt="" className="max-h-48 rounded border border-line bg-white" />
              ))}
            </div>
          )}
        </div>
      )}

      {q.status === "failed" && q.error && (
        <div className="mt-3">
          <ErrorNote message="이 문항을 다듬지 못했습니다 — [다시]를 눌러 보세요. HWPX에는 원래 해설 그대로 들어갑니다." detail={q.error} />
        </div>
      )}

      {q.status === "done" && (
        <>
          {differ.length > 0 && (
            <div className="mt-3 flex gap-2 rounded-xl bg-warn-soft px-3.5 py-2.5 text-[13px]">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" />
              <div>
                {q.has_key ? "AI가 해설지를 따라 풀어 본 답이 해설지와 다르거나 확실하지 않습니다 — 해설지를 확인하세요." : "AI가 직접 푼 답입니다 — 확인하세요."}
                {differ.map((c) => (
                  <div key={c.label} className="mt-0.5 text-text-2">
                    {c.label || "답"}: <RichText text={c.answer} />
                    {c.note && <> — {c.note}</>}
                  </div>
                ))}
              </div>
            </div>
          )}
          {q.cases.length > 0 && (
            <div className="mt-3 rounded-xl bg-accent-soft px-3.5 py-2.5 text-[13px] text-accent-text">
              지난 채점 사례 {q.cases.length}건을 참고했습니다 (배우기 → 채점 사례집): {q.cases.join(" · ")}
            </div>
          )}
          {memos.length > 0 && (
            <div className="mt-3 rounded-xl bg-muted px-3.5 py-2.5 text-[13px] text-text-2">
              <span className="text-text">AI 확인 메모</span> (답은 해설지와 같음)
              {memos.map((c) => (
                <div key={c.label} className="mt-0.5">
                  {c.label && `${c.label}: `}
                  <RichText text={c.note ?? ""} />
                </div>
              ))}
            </div>
          )}
          {q.explanation !== null && <Explanation q={q} onChange={onChange} />}
          {q.parts.map((p, i) => (
            <PartBlock key={`${p.label}-${i}`} part={p} single={q.parts.length === 1 && !p.label} onChange={(np) => setPart(i, np)} />
          ))}
          {q.problems.length > 0 && (
            <ul className="m-0 mt-3 list-disc rounded-xl bg-warn-soft py-2.5 pl-8 pr-3.5 text-[13px] text-warn">
              {q.problems.map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
          )}
          {q.agent && <div className="mt-3 text-[12px] text-text-3">다듬은 AI: {q.model ?? q.agent}</div>}
        </>
      )}

      <Dialog
        open={confirmRedo}
        onOpenChange={setConfirmRedo}
        title={`${q.number}번을 다시 다듬을까요?`}
        desc="AI가 처음부터 다시 다듬습니다. 이 문항에서 고치고 답한 것은 사라집니다."
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmRedo(false)}>
              취소
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                setConfirmRedo(false);
                onRedo();
              }}
            >
              <RotateCcw /> 다시 다듬기
            </Button>
          </>
        }
      />
    </Card>
  );
}

function Explanation({ q, onChange }: { q: PrepQuestion; onChange: (q: PrepQuestion) => void }) {
  const [edit, setEdit] = useState<string | null>(null);
  return (
    <div className={cn("mt-3 rounded-xl border border-line px-4 py-3", !q.explanation_in && "opacity-60")}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="accent">AI 해설</Badge>
        <span className="flex-1 text-[12px] text-text-3">{q.has_key ? "해설지에 풀이가 없어 AI가 썼습니다" : "해설지가 없어 AI가 풀어 썼습니다"}</span>
        {edit === null && (
          <>
            <Button size="sm" variant="ghost" onClick={() => setEdit(toDollars(q.explanation ?? ""))}>
              <Pencil /> 고치기
            </Button>
            <Button size="sm" variant="ghost" onClick={() => onChange({ ...q, explanation_in: !q.explanation_in })}>
              {q.explanation_in ? (
                <>
                  <X /> 넣지 않기
                </>
              ) : (
                <>
                  <Check /> 넣기
                </>
              )}
            </Button>
          </>
        )}
      </div>
      {edit === null ? (
        <div className="mt-2 text-[14px]">
          {(q.explanation ?? "").split("\n").map((ln, i) => (
            <div key={i}>
              <RichText text={ln} />
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-2 flex flex-col gap-2">
          <textarea rows={6} value={edit} onChange={(e) => setEdit(e.target.value)} className={textarea} />
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => setEdit(null)}>
              취소
            </Button>
            <Button
              size="sm"
              variant="primary"
              onClick={() => {
                onChange({ ...q, explanation: fromDollars(edit) });
                setEdit(null);
              }}
            >
              확인
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 소문항

let nextNew = 1;

function PartBlock({ part, single, onChange }: { part: PrepPart; single: boolean; onChange: (p: PrepPart) => void }) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<PrepLine | null>(null);
  const setLines = (lines: PrepLine[]) => onChange({ ...part, lines });
  const patch = (id: string, change: Partial<PrepLine>) => setLines(part.lines.map((l) => (l.id === id ? { ...l, ...change } : l)));
  const sum = part.lines.filter((l) => l.kind === "item" && included(l)).reduce((a, l) => a + (l.points ?? 0), 0);
  const add = () => {
    const kind: LineKind = part.no_partial ? "accept" : "item";
    setDraft({ id: `new-${nextNew++}`, kind, text: "", points: POINTED.has(kind) ? 1 : null, source: "teacher", why: null, question: null, decision: null });
  };

  return (
    <div className="mt-5 overflow-hidden rounded-xl border border-line">
      <div className="flex flex-wrap items-center gap-2 border-b border-line bg-muted/50 px-3.5 py-2">
        <span className="text-[14px] font-medium">{single ? "부분 점수 기준" : part.label}</span>
        <span className="text-[13px] text-text-3">{pts(part.points)}점</span>
        {part.answer && (
          <span className="min-w-0 truncate text-[13px] text-text-2">
            · 답 <RichText text={part.answer} />
          </span>
        )}
        <div className="flex-1" />
        {!part.no_partial && <span className={cn("text-[12px]", Math.abs(sum - part.points) < 1e-6 ? "text-text-3" : "text-bad")}>요소 합 {pts(sum)}점</span>}
        <button
          type="button"
          onClick={() => onChange({ ...part, no_partial: !part.no_partial })}
          title="해설지가 '부분점수 없음'이라고 한 소문항 — 맞으면 만점, 아니면 0점"
          className={cn(
            "h-7 cursor-pointer rounded-full border px-3 text-[12px]",
            part.no_partial ? "border-accent bg-accent-soft text-accent-text" : "border-line text-text-3 hover:bg-muted",
          )}
        >
          부분점수 없음
        </button>
      </div>
      {part.no_partial && <div className="px-3.5 pt-2 text-[12px] text-text-3">맞으면 만점, 아니면 0점 — 같은 답의 다른 표기·풀이 인정만 넣습니다.</div>}
      <div className="flex flex-col p-1.5">
        {part.lines.map((l) =>
          editing === l.id ? (
            <LineEditor
              key={l.id}
              line={l}
              noPartial={part.no_partial}
              onCancel={() => setEditing(null)}
              onSave={(nl) => {
                patch(l.id, nl);
                setEditing(null);
              }}
            />
          ) : (
            <LineRow
              key={l.id}
              line={l}
              onDecide={(d) => patch(l.id, { decision: d })}
              onEdit={() => setEditing(l.id)}
              onDelete={() => setLines(part.lines.filter((x) => x.id !== l.id))}
            />
          ),
        )}
        {draft && (
          <LineEditor
            line={draft}
            noPartial={part.no_partial}
            onCancel={() => setDraft(null)}
            onSave={(nl) => {
              setLines([...part.lines, { ...draft, ...nl }]);
              setDraft(null);
            }}
          />
        )}
      </div>
      {!draft && (
        <div className="border-t border-line/70 px-1.5 py-1">
          <Button size="sm" variant="ghost" className="text-text-3" onClick={add}>
            <Plus /> 줄 더하기
          </Button>
        </div>
      )}
    </div>
  );
}

function LineRow({ line, onDecide, onEdit, onDelete }: { line: PrepLine; onDecide: (d: "in" | "out" | null) => void; onEdit: () => void; onDelete: () => void }) {
  const on = included(line);
  const ask = line.source === "ask";
  const pending = ask && line.decision === null;
  const empty = blank(line);
  const filled = line.source === "fill" && !line.text.includes(BLANK);
  return (
    <div className={cn("group flex items-start gap-3 rounded-lg px-2 py-2", pending && "bg-warn-soft", empty && "bg-bad-soft", line.kind === "level" && "pl-8")}>
      <div className="flex w-[4.5rem] shrink-0 flex-col items-start gap-1 pt-0.5">
        {filled ? <Badge tone="ok">강사가 적음</Badge> : <Badge tone={SOURCE[line.source].tone}>{SOURCE[line.source].label}</Badge>}
        <span className="text-[11px] text-text-3">{KIND_LABEL[line.kind]}</span>
      </div>
      <div className="min-w-0 flex-1 text-[14px]">
        {/* 뺀 줄만 긋는다 — 답하지 않은 질문은 그대로 읽히게 */}
        <div className={cn(!on && !pending && "text-text-3 line-through")}>
          {line.points !== null && <span className="mr-1.5 font-medium">{pts(line.points)}점</span>}
          <RichText text={line.text} />
        </div>
        {ask && line.question && (
          <div className={cn("mt-1 text-[13px]", pending ? "font-medium text-text" : "text-text-2")}>
            질문: <RichText text={line.question} />
          </div>
        )}
        {line.source === "fill" && line.question && (
          <div className={cn("mt-1 text-[13px]", empty ? "font-medium text-text" : "text-text-2")}>
            적을 것: <RichText text={line.question} />
          </div>
        )}
        {line.why && <div className="mt-0.5 text-[12px] text-text-3">{line.why}</div>}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {empty && (
          <Button size="sm" variant="primary" onClick={onEdit}>
            <Pencil /> 적기
          </Button>
        )}
        {ask ? (
          <div className="inline-flex rounded-lg border border-line bg-card p-0.5">
            <button
              type="button"
              aria-pressed={line.decision === "in"}
              onClick={() => onDecide(line.decision === "in" ? null : "in")}
              className={cn("inline-flex h-7 cursor-pointer items-center gap-1 rounded-md px-2.5 text-[12px] font-medium [&_svg]:size-3.5", line.decision === "in" ? "bg-ok-soft text-ok" : "text-text-2 hover:bg-muted")}
            >
              <Check /> 넣기
            </button>
            <button
              type="button"
              aria-pressed={line.decision === "out"}
              onClick={() => onDecide(line.decision === "out" ? null : "out")}
              className={cn("inline-flex h-7 cursor-pointer items-center gap-1 rounded-md px-2.5 text-[12px] font-medium [&_svg]:size-3.5", line.decision === "out" ? "bg-bad-soft text-bad" : "text-text-2 hover:bg-muted")}
            >
              <X /> 빼기
            </button>
          </div>
        ) : (
          <Button size="sm" variant="ghost" onClick={() => onDecide(on ? "out" : null)} title={on ? "이 줄을 넣지 않기" : "다시 넣기"}>
            {on ? <X /> : <Check />}
            {on ? "빼기" : "넣기"}
          </Button>
        )}
        <Button size="icon" variant="ghost" aria-label="고치기" onClick={onEdit} className="size-8">
          <Pencil />
        </Button>
        <Button size="icon" variant="ghost" aria-label="지우기" onClick={onDelete} className="size-8 opacity-0 group-hover:opacity-100">
          <Trash2 />
        </Button>
      </div>
    </div>
  );
}

function LineEditor({ line, noPartial, onSave, onCancel }: { line: PrepLine; noPartial: boolean; onSave: (l: Partial<PrepLine>) => void; onCancel: () => void }) {
  const [kind, setKind] = useState<LineKind>(line.kind);
  const [text, setText] = useState(toDollars(line.text)); // 칸에서는 식이 $수식$ (저장은 \( \))
  const [points, setPoints] = useState(line.points !== null ? String(line.points) : "1");
  const kinds = noPartial ? KINDS.filter((k) => k.value === "accept" || k.value === "note" || k.value === "item") : KINDS;
  const pointed = POINTED.has(kind);
  const save = () => {
    const n = Number(points);
    // 고친 질문은 강사가 쓴 것이니 넣는다 (빼려면 [빼기])
    const decision = line.source === "ask" && line.decision === null ? "in" : line.decision;
    onSave({ kind, text: fromDollars(text.trim()), points: pointed && n > 0 ? n : pointed ? 1 : null, decision });
  };
  return (
    <div className="my-1 flex flex-col gap-2 rounded-xl border border-accent bg-card p-3 ring-3 ring-accent-soft">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={kind} onChange={(v) => setKind(v as LineKind)} options={kinds} className="w-36 min-w-0" />
        {pointed && (
          <label className="flex items-center gap-1.5 text-[13px] text-text-2">
            <Input type="number" min={0} step={0.5} value={points} onChange={(e) => setPoints(e.target.value)} className="w-20" />점
          </label>
        )}
        <span className="text-[12px] text-text-3">{kind === "level" ? "바로 위 채점 요소의 낮은 단계 (그중 하나만 받음)" : kind === "accept" ? "같은 점수로 인정하는 다른 답·표현·풀이" : ""}</span>
      </div>
      {line.source === "fill" && line.question && (
        <div className="text-[13px]">
          적을 것: <RichText text={line.question} /> — 빈칸({BLANK})을 지우고 값을 적어 주세요.
        </div>
      )}
      <textarea
        rows={2}
        autoFocus
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && text.trim()) save();
          if (e.key === "Escape") onCancel();
        }}
        placeholder="해설지에 들어갈 문장 (점수는 앞에 저절로 붙습니다, 식은 $수식$처럼 $로 감싸기 — 예: $\frac{1}{2}mv^2$)"
        className={textarea}
      />
      <div className="flex items-center justify-end gap-2">
        {text.includes(BLANK) && <span className="flex-1 text-[12px] text-bad">빈칸({BLANK})이 남아 있으면 HWPX를 받을 수 없습니다</span>}
        <Button size="sm" variant="ghost" onClick={onCancel}>
          취소
        </Button>
        <Button size="sm" variant="primary" disabled={!text.trim()} onClick={save}>
          확인
        </Button>
      </div>
    </div>
  );
}
