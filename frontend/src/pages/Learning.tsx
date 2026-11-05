import { BookText, Brain, Check, FileCode2, X } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Help } from "@/components/ui/help";
import { ErrorNote, Input } from "@/components/ui/misc";
import { Switch } from "@/components/ui/switch";
import { api, jobsChanged, useAction, useApi, waitJob } from "@/lib/api";
import { useTeacher } from "@/lib/session";
import type { GlobalRule, Job, MemoryNote, Scope, Skill } from "@/lib/types";

const TARGET: Record<string, string> = {
  all: "전체",
  solve: "해설",
  rubric: "루브릭",
  transcribe: "손글씨 판독",
  grade: "채점",
  feedback: "첨삭",
  ingest: "시험지 읽기",
};
const scopes = (s: Scope[]) => s.map((x) => TARGET[x] ?? x).join(" · ");

type Draft = { title: string; text: string; applies_to: Scope[]; clarification_needed: string | null; conflicts: { rule_id: string; reason: string }[] };

/** 배우기 (PLAN §10): AI가 제안한 메모리·스킬을 강사가 승인, 전역 규칙 켜고 끄기. 모두 작업공간의 md 파일. */
export function LearningPage() {
  const by = useTeacher();
  const memory = useApi<{ notes: MemoryNote[]; usage: Record<string, number>; limit: number }>("/memory");
  const skills = useApi<Skill[]>("/skills");
  const rules = useApi<GlobalRule[]>("/rules");
  const [request, setRequest] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const act = useAction();

  const proposals = [
    ...(memory.data?.notes ?? []).filter((n) => n.proposed).map((n) => ({ kind: "메모리" as const, id: n.id, text: n.text, source: n.request, scope: n.applies_to })),
    ...(skills.data ?? []).filter((s) => s.proposed).map((s) => ({ kind: "스킬" as const, id: s.id, text: `${s.name} — ${s.description}`, source: s.request, scope: s.applies_to })),
  ];
  const reloadAll = () => {
    memory.reload();
    skills.reload();
    rules.reload();
    jobsChanged(); // 알림 숫자도
  };
  const decide = (kind: "메모리" | "스킬", id: string, ok: boolean) =>
    act.run(async () => {
      const path = kind === "메모리" ? `/memory/${id}` : `/skills/${id}`;
      await (ok ? api.post(`${path}/approve`) : api.del(path));
      reloadAll();
    });
  const toggle = (r: GlobalRule, v: boolean) =>
    act.run(async () => {
      await api.patch(`/rules/${r.id}`, { enabled: v });
      rules.reload();
    });
  const refine = () =>
    act.run(async () => {
      if (!request.trim()) return;
      const j = await waitJob(await api.post<Job>("/rules/refine", { request }));
      setDraft((j.result as { draft: Draft }).draft);
    });
  const saveRule = () =>
    act.run(async () => {
      await api.post("/rules", { request, draft, by });
      setDraft(null);
      setRequest("");
      rules.reload();
    });
  const reveal = () => act.run(() => api.post("/files/reveal", { path: "memory" }));
  const used = Math.max(0, ...Object.values(memory.data?.usage ?? {}));

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4 px-6 py-8">
      <h1 className="m-0 text-[20px] font-medium">배우기</h1>
      {act.error && <ErrorNote message={act.error} />}

      <Card>
        <CardHeader
          icon={<Brain />}
          title={
            <>
              승인 기다리는 제안 <Badge tone="accent">{proposals.length}</Badge>
            </>
          }
          desc="강사가 고친 것·반복된 오류에서 AI가 제안했습니다. 승인해야 다음 호출부터 들어갑니다."
        />
        <CardBody className="flex flex-col gap-2">
          {proposals.map((p) => (
            <div key={p.id} className="rounded-lg border border-line p-3">
              <div className="flex items-center gap-2">
                <Badge tone={p.kind === "스킬" ? "accent" : "neutral"}>{p.kind}</Badge>
                <span className="text-[12px] text-text-3">{scopes(p.scope)}</span>
              </div>
              <div className="mt-2">{p.text}</div>
              {p.source && <div className="mt-1 text-[12px] text-text-3">{p.source}</div>}
              <div className="mt-2 flex gap-1">
                <Button size="sm" variant="outline" disabled={act.busy} onClick={() => void decide(p.kind, p.id, true)}>
                  <Check /> 승인
                </Button>
                <Button size="sm" variant="ghost" disabled={act.busy} onClick={() => void decide(p.kind, p.id, false)}>
                  <X /> 버리기
                </Button>
              </div>
            </div>
          ))}
          {proposals.length === 0 && <div className="text-text-3">기다리는 제안이 없습니다.</div>}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          icon={<BookText />}
          title={
            <>
              전역 규칙 <Help>규칙은 강사가 정합니다. 켜 둔 규칙은 해당 작업의 모든 호출에 들어갑니다.</Help>
            </>
          }
        />
        <CardBody className="flex flex-col">
          {(rules.data ?? []).map((r) => (
            <Switch key={r.id} checked={r.enabled} onCheckedChange={(v) => void toggle(r, v)} label={r.text} desc={`${r.title} · ${scopes(r.applies_to)}`} />
          ))}
          {rules.data?.length === 0 && <div className="py-2 text-text-3">규칙이 없습니다.</div>}
          <form
            className="mt-2 flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void refine();
            }}
          >
            <Input value={request} onChange={(e) => setRequest(e.target.value)} placeholder="앞으로는 이렇게… (예: 그래프 축 이름은 채점하지 않는다)" className="flex-1" />
            <Button variant="outline" disabled={act.busy || !request.trim()}>
              {act.busy && !draft ? "다듬는 중…" : "규칙 만들기"}
            </Button>
          </form>
          {draft && (
            <div className="mt-3 rounded-lg bg-muted p-3 text-[13px]">
              <div className="font-medium">{draft.title}</div>
              <div className="mt-1">{draft.text}</div>
              <div className="mt-1 text-text-3">{scopes(draft.applies_to)}</div>
              {draft.clarification_needed && <div className="mt-1 text-warn">확인: {draft.clarification_needed}</div>}
              {draft.conflicts.map((c) => (
                <div key={c.rule_id} className="mt-1 text-warn">
                  다른 규칙과 충돌: {c.reason}
                </div>
              ))}
              <div className="mt-2 flex gap-2">
                <Button size="sm" variant="outline" disabled={act.busy} onClick={() => void saveRule()}>
                  이대로 저장
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setDraft(null)}>
                  버리기
                </Button>
              </div>
            </div>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          icon={<FileCode2 />}
          title="메모리 · 스킬 파일"
          desc="작업공간 memory/ · skills/ · rules/ 폴더의 md 파일입니다. 메모장으로 고쳐도 됩니다."
          right={
            <Button variant="ghost" onClick={() => void reveal()}>
              폴더 열기
            </Button>
          }
        />
        <CardBody className="text-[13px] text-text-2">
          메모리 {memory.data?.notes.filter((n) => !n.proposed).length ?? 0}개 (작업별 최대 {used.toLocaleString()} / {memory.data?.limit.toLocaleString() ?? "—"}자) · 스킬{" "}
          {skills.data?.filter((s) => !s.proposed).length ?? 0}개 · 규칙 {rules.data?.length ?? 0}개
        </CardBody>
      </Card>
    </div>
  );
}
