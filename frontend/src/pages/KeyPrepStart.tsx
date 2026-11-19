import { ChevronRight, FileText, Sparkles, X } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Help } from "@/components/ui/help";
import { Dropzone, ErrorNote } from "@/components/ui/misc";
import { Select } from "@/components/ui/select";
import { api, jobsChanged, useAction, useApi } from "@/lib/api";
import { go, href } from "@/lib/router";
import type { Job, KeyPrepSummary, Level } from "@/lib/types";

/** 0단계 해설 다듬기 (2026-11-18): 시험지·해설 HWP/HWPX를 놓으면 AI가 해설을 보며 풀고 부분 점수 기준을
 * 촘촘하게 세운다 → 강사가 고르고 고친다(`#/prep/<ID>`) → 다듬은 HWPX → 1단계. 건너뛰어도 된다 — 다른 AI
 * 채팅으로 다듬은 해설지는 바로 1단계에 넣는다. */
export function KeyPrepStart() {
  const levels = useApi<Level[]>("/levels", { refreshOnJobs: false });
  const preps = useApi<KeyPrepSummary[]>("/keyprep");
  const [file, setFile] = useState<File | null>(null);
  const [level, setLevel] = useState("물리");
  const [fileErr, setFileErr] = useState<string | null>(null);
  const act = useAction();

  const pick = (fs: File[]) => {
    const f = fs[0];
    if (!f) return;
    const ok = /\.(hwp|hwpx)$/i.test(f.name);
    setFileErr(ok ? null : `${f.name}: 한글 파일(HWP·HWPX)만 다듬을 수 있습니다 — 다듬은 해설지를 HWPX로 돌려 드리기 때문입니다.`);
    setFile(ok ? f : null);
  };
  const start = () =>
    act.run(async () => {
      if (!file) return;
      const form = new FormData();
      form.append("file", file);
      form.append("level", level);
      form.append("title", file.name.replace(/\.[^.]+$/, ""));
      const job = await api.upload<Job>("/keyprep", form);
      jobsChanged();
      setFile(null);
      go("prep", String(job.params.prep));
    });

  const recent = (preps.data ?? []).slice(0, 4);
  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
        <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent-text">
          <Sparkles className="size-5" />
        </span>
        <div className="min-w-[260px] flex-1">
          <div className="flex items-center gap-2">
            <h2 className="m-0 text-[15px] font-medium">0단계 · 해설·부분 점수 다듬기</h2>
            <Badge>선택</Badge>
            <Help>
              AI가 해설을 보며 문제를 풀고 해설지의 부분 점수 기준을 더 촘촘하게 세웁니다 (해설이 없으면 직접 풀어 씁니다). 추천은 바로 넣고, 넣어도 될지
              애매한 것은 묻습니다. 고친 뒤 HWPX로 받아 아래 1단계에 넣으세요. 다른 AI 채팅으로 이미 다듬은 해설지라면 건너뛰고 바로 1단계로.
            </Help>
          </div>
          <p className="m-0 mt-1 text-[13px] text-text-2">부분 점수 기준이 필요하면 여기에 시험지와 해설지를 넣어 주세요. 다듬은 해설지(HWPX)를 1단계에 넣습니다.</p>
        </div>
        {!file && <Dropzone compact title="HWP · HWPX 놓기" hint="시험지(해설 포함)" accept=".hwp,.hwpx" onFiles={pick} className="w-[260px]" />}
      </div>
      {file && (
        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-4">
          <div className="flex min-w-0 flex-1 items-center gap-2 rounded-lg bg-muted px-3 py-2">
            <FileText className="size-4 shrink-0 text-text-3" />
            <span className="flex-1 truncate">{file.name}</span>
            {!act.busy && (
              <button type="button" aria-label="빼기" onClick={() => setFile(null)} className="cursor-pointer text-text-3 hover:text-text">
                <X className="size-4" />
              </button>
            )}
          </div>
          <Select value={level} onChange={setLevel} className="w-36" options={(levels.data ?? [{ id: "물리", label: "물리", scope_id: "" }]).map((l) => ({ value: l.label, label: l.label }))} />
          <Button variant="primary" disabled={act.busy} onClick={() => void start()}>
            <Sparkles /> {act.busy ? "나누는 중…" : "다듬기 시작"}
          </Button>
        </div>
      )}
      {fileErr && (
        <div className="mt-3">
          <ErrorNote message={fileErr} />
        </div>
      )}
      {act.error && (
        <div className="mt-3">
          <ErrorNote message={act.error} />
        </div>
      )}
      {recent.length > 0 && (
        <div className="mt-4 flex flex-col border-t border-line pt-2">
          {recent.map((p) => (
            <a key={p.id} href={href("prep", p.id)} className="-mx-2 flex items-center gap-2 rounded-lg px-2 py-1.5 text-[13px] text-text no-underline hover:bg-muted">
              <FileText className="size-4 text-text-3" />
              <span className="min-w-0 flex-1 truncate">{p.title}</span>
              <span className="text-text-3 tabular-nums">{p.created_at.slice(0, 10)}</span>
              {p.state === "running" ? (
                <Badge tone="accent">
                  다듬는 중 {p.done}/{p.questions}
                </Badge>
              ) : p.blanks > 0 ? (
                <Badge tone="bad">적을 곳 {p.blanks}</Badge>
              ) : p.pending > 0 ? (
                <Badge tone="warn">질문 {p.pending}</Badge>
              ) : p.exported ? (
                <Badge tone="ok">HWPX 받음</Badge>
              ) : (
                <Badge>다듬음 {p.done}/{p.questions}</Badge>
              )}
              {p.failed > 0 && <Badge tone="bad">실패 {p.failed}</Badge>}
              <ChevronRight className="size-4 text-text-3" />
            </a>
          ))}
        </div>
      )}
    </Card>
  );
}
