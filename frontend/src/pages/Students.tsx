import { Pencil, Plus, Search, Trash2, Upload } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Chips, ErrorNote, Input, PageTitle } from "@/components/ui/misc";
import { Select } from "@/components/ui/select";
import { api, enc, useAction, useApi } from "@/lib/api";
import { cn } from "@/lib/cn";
import { href } from "@/lib/router";
import { pts } from "@/lib/steps";
import type { HistoryExam, RosterStudent, StudentHistory } from "@/lib/types";

const DAYS: Record<string, number> = { "1m": 31, "3m": 92, "1y": 366 };

/** 한 학생의 한 시험: 문항을 누르면 그 학생의 채점 검토(원본·판독문·점수·이유)를 연다. */
function ExamRow({ e }: { e: HistoryExam }) {
  return (
    <div className="flex items-center gap-4 px-5 py-3">
      <a href={href("exams", e.exam_id)} className="min-w-0 flex-1 text-text no-underline hover:text-accent-text">
        <div className="truncate font-medium">{e.title}</div>
        <div className="text-[12px] text-text-3 tabular-nums">{e.date}</div>
      </a>
      <div className="flex flex-wrap justify-end gap-1">
        {(e.numbers ?? []).map((q) => (
          <a
            key={q}
            href={href("exams", e.exam_id, "grade", q, e.student_id)}
            title={`${q}번 채점 검토에서 보기`}
            className="inline-flex h-7 items-center rounded-full border border-line px-2.5 text-[12px] text-text-2 no-underline hover:border-line-strong hover:bg-muted hover:text-text"
          >
            {q}번
          </a>
        ))}
      </div>
      <span className="w-24 shrink-0 text-right tabular-nums">
        {e.total === null ? (
          <span className="text-[13px] text-text-3">
            채점 {e.graded}/{e.questions}
          </span>
        ) : (
          <>
            <span className="text-[16px] font-medium">{pts(e.total)}</span>
            <span className="text-[13px] text-text-3"> / {pts(e.max)}</span>
          </>
        )}
      </span>
    </div>
  );
}

/** 이름 첫 글자 동그라미. */
function Avatar({ name, big = false }: { name: string; big?: boolean }) {
  return (
    <span className={cn("grid shrink-0 place-items-center rounded-full bg-accent-soft font-medium text-accent-text", big ? "size-11 text-[16px]" : "size-8 text-[13px]")}>
      {name.trim().slice(0, 1) || "?"}
    </span>
  );
}

type Draft = { id: string | null; name: string; class_name: string; school: string };

/** 명부 학생 한 명 넣기·고치기·빼기. 고쳐도 지난 시험의 이름·파일은 그대로다 (시험마다 그때 이름으로 남는다).
 * 열 때마다 새로 만든다 (`key`). */
function RosterDialog({ draft, classes, onClose }: { draft: Draft | null; classes: string[]; onClose: (changed: boolean) => void }) {
  const [d, setD] = useState<Draft>(draft ?? { id: null, name: "", class_name: "", school: "" });
  const [sure, setSure] = useState(false);
  const act = useAction();
  const body = { name: d.name.trim(), class_name: d.class_name.trim() || null, school: d.school.trim() || null };
  const save = () =>
    act.run(async () => {
      if (d.id) await api.put<RosterStudent>(`/students/${enc(d.id)}`, body);
      else await api.post<RosterStudent>("/students", body);
      onClose(true);
    });
  const remove = () =>
    act.run(async () => {
      if (!d.id) return;
      await api.del(`/students/${enc(d.id)}`);
      onClose(true);
    });
  const field = (k: "name" | "class_name" | "school", label: string, extra: object = {}) => (
    <label className="flex flex-col gap-1.5">
      <span className="text-[13px] text-text-2">{label}</span>
      <Input value={d[k]} onChange={(e) => setD({ ...d, [k]: e.target.value })} className="w-full" {...extra} />
    </label>
  );
  return (
    <Dialog
      open={draft !== null}
      onOpenChange={(v) => !v && !act.busy && onClose(false)}
      title={d.id ? "명부 학생 고치기" : "학생 추가"}
      desc={d.id ? `${d.id} · 지난 시험의 이름과 파일은 그대로 두고, 학생 검색에서 새 이름으로 묶습니다.` : "같은 반에 같은 이름이 있으면 김다원A, 김다원B처럼 알파벳을 붙입니다."}
      footer={
        <>
          {d.id && (
            <Button variant="danger" disabled={act.busy} onClick={() => (sure ? void remove() : setSure(true))} className="mr-auto">
              <Trash2 /> {sure ? "한 번 더 누르면 뺍니다" : "명부에서 빼기"}
            </Button>
          )}
          <Button variant="ghost" disabled={act.busy} onClick={() => onClose(false)}>
            취소
          </Button>
          <Button variant="primary" disabled={act.busy || !body.name} onClick={() => void save()}>
            {d.id ? "저장" : "넣기"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {field("name", "이름", { autoFocus: true })}
        {field("class_name", "반", { list: "roster-dialog-classes" })}
        <datalist id="roster-dialog-classes">
          {classes.map((c) => (
            <option key={c} value={c} />
          ))}
        </datalist>
        {field("school", "학교")}
        {sure && <div className="rounded-lg bg-warn-soft px-3 py-2 text-[13px]">명부에서만 뺍니다. 이 학생의 시험 기록은 남고, 학생 검색에서 '명부 밖'이 됩니다.</div>}
        {act.error && <ErrorNote message={act.error} />}
      </div>
    </Dialog>
  );
}

/** 학생 검색 (PLAN §5-4): 이름·반·학교·시험·기간으로 찾아 그 학생의 채점 검토를 연다. 명부는 엑셀·CSV로 한 번에 넣거나 한 명씩 넣고 고친다. */
export function StudentsPage() {
  const { data, error, reload } = useApi<StudentHistory[]>("/students/history");
  const [q, setQ] = useState("");
  const [classes, setClasses] = useState<string[]>([]);
  const [period, setPeriod] = useState("all");
  const [sel, setSel] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
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
      <PageTitle title="학생" desc="이름 · 반 · 학교 · 시험으로 찾아, 문항을 누르면 그 학생의 채점 검토를 엽니다. 명부는 엑셀 · CSV · 사진으로 한 번에 넣습니다.">
        <input ref={fileRef} type="file" accept=".xlsx,.csv,.jpg,.jpeg,.png" className="hidden" onChange={(e) => void importRoster(e.target.files?.[0])} />
        <Button variant="outline" disabled={act.busy} onClick={() => setDraft({ id: null, name: "", class_name: classes.length === 1 ? classes[0]! : "", school: "" })}>
          <Plus /> 학생 추가
        </Button>
        <Button variant="primary" disabled={act.busy} onClick={() => fileRef.current?.click()}>
          <Upload /> 명부 넣기
        </Button>
      </PageTitle>
      {(error || act.error) && (
        <div className="mt-4">
          <ErrorNote message={(act.error ?? error)!} />
        </div>
      )}
      {note && <div className="mt-4 rounded-xl border border-line bg-card px-4 py-2.5 text-[13px] text-text-2">{note}</div>}
      <div className="mt-6 flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-text-3" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="이름 · 학교 · 시험" className="h-10 w-full pl-9" />
        </div>
        <Select
          value={period}
          onChange={setPeriod}
          className="h-10 min-w-32"
          options={[
            { value: "1m", label: "최근 1달" },
            { value: "3m", label: "최근 3달" },
            { value: "1y", label: "최근 1년" },
            { value: "all", label: "전체 기간" },
          ]}
        />
      </div>
      {allClasses.length > 0 && (
        <div className="mt-3">
          <Chips options={allClasses} value={classes} onChange={setClasses} />
        </div>
      )}
      <div className="mt-5 grid grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)] items-start gap-4">
        <Card className="flex max-h-[64vh] flex-col overflow-hidden">
          <div className="shrink-0 border-b border-line px-5 py-2.5 text-[12px] text-text-3">
            학생 <span className="tabular-nums">{hits.length}</span>
          </div>
          <div className="min-h-0 overflow-auto p-1.5">
            {hits.map((s) => {
              const here = st?.key === s.key;
              return (
                <button
                  key={s.key}
                  type="button"
                  onClick={() => setSel(s.key)}
                  className={cn(
                    "relative flex w-full cursor-pointer items-center gap-3 rounded-lg px-3 py-2 text-left hover:bg-muted",
                    here && "bg-muted before:absolute before:inset-y-2 before:left-0 before:w-[3px] before:rounded-full before:bg-primary",
                  )}
                >
                  <Avatar name={s.name} />
                  <span className="min-w-0 flex-1">
                    <span className={cn("block truncate", here && "font-medium")}>{s.name}</span>
                    <span className="block truncate text-[12px] text-text-3">{[s.class_name, s.school].filter(Boolean).join(" · ") || "반 모름"}</span>
                  </span>
                  <span className="shrink-0 text-[12px] text-text-3 tabular-nums">{s.exams.length}회</span>
                </button>
              );
            })}
            {data && hits.length === 0 && <div className="px-4 py-8 text-center text-[13px] text-text-3">{data.length === 0 ? "명부를 넣거나 답안을 판독하면 학생이 생깁니다." : "찾는 학생이 없습니다."}</div>}
          </div>
        </Card>
        {st && (
          <Card className="overflow-hidden">
            <div className="flex items-center gap-3.5 px-5 py-4">
              <Avatar name={st.name} big />
              <div className="min-w-0 flex-1">
                <div className="text-[17px] font-medium">{st.name}</div>
                <div className="text-[13px] text-text-2">{[st.class_name, st.school, st.roster_id ?? "명부 밖"].filter(Boolean).join(" · ")}</div>
              </div>
              {st.roster_id && (
                <Button size="sm" variant="outline" onClick={() => setDraft({ id: st.roster_id, name: st.name, class_name: st.class_name ?? "", school: st.school ?? "" })}>
                  <Pencil /> 고치기
                </Button>
              )}
            </div>
            <div className="border-t border-line bg-muted/50 px-5 py-2 text-[12px] text-text-3">
              본 시험 <span className="tabular-nums">{st.exams.length}</span>
            </div>
            <div className="divide-y divide-line">
              {st.exams.length === 0 && <div className="px-5 py-6 text-center text-[13px] text-text-3">본 시험이 없습니다.</div>}
              {st.exams.map((e) => (
                <ExamRow key={e.exam_id + e.student_id} e={e} />
              ))}
            </div>
          </Card>
        )}
      </div>
      <RosterDialog
        key={draft ? (draft.id ?? "new") : "closed"}
        draft={draft}
        classes={allClasses}
        onClose={(changed) => {
          setDraft(null);
          if (changed) reload();
        }}
      />
    </div>
  );
}
