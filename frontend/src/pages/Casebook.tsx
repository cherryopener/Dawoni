import { BookMarked, Pencil, Trash2 } from "lucide-react";
import { useState } from "react";
import { RichText } from "@/components/Math";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Help } from "@/components/ui/help";
import { ErrorNote, Input } from "@/components/ui/misc";
import { Switch } from "@/components/ui/switch";
import { api, useAction, useApi } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { Case } from "@/lib/types";

const textarea = "w-full rounded-lg border border-line bg-card px-3 py-2 text-text outline-none placeholder:text-text-3 focus:border-accent";
const pts = (n: number | null) => (n === null ? "?" : Number.isInteger(n) ? String(n) : String(Number(n.toFixed(2))));

/** 채점 사례집 (2026-11-18): 채점 검토에서 강사가 바꾼 점수가 사례 한 건(md)으로 쌓인다. 다음 시험의 해설
 * 다듬기(0단계)·루브릭(1단계)에 그 문항과 열쇠말이 맞는 것만 들어간다 — 쌓여도 프롬프트는 커지지 않는다. */
export function CasebookCard() {
  const data = useApi<{ cases: Case[]; folder: string }>("/casebook");
  const [query, setQuery] = useState("");
  const [shown, setShown] = useState(10);
  const act = useAction();
  const cases = (data.data?.cases ?? []).filter((c) => {
    const q = query.trim();
    return !q || [c.title, c.body, c.exam, ...c.keywords].some((x) => x.includes(q));
  });
  const live = (data.data?.cases ?? []).filter((c) => c.enabled && c.reusable).length;
  const save = (c: Case, change: Partial<Case>) =>
    act.run(async () => {
      await api.patch(`/casebook/${c.id}`, change);
      data.reload();
    });
  const remove = (c: Case) =>
    act.run(async () => {
      await api.del(`/casebook/${c.id}`);
      data.reload();
    });

  return (
    <Card>
      <CardHeader
        icon={<BookMarked />}
        title={
          <>
            채점 사례집 <Badge tone="accent">{data.data?.cases.length ?? 0}</Badge>
            <Help>
              채점 검토에서 점수를 바꾸면 그 판단(학생 표현 · 기준 · AI 점수 → 강사 점수 · 이유)이 한 건씩 쌓입니다. 다음 시험에서 해설 다듬기와 루브릭을 만들 때 그 문항과 맞는 사례만
              참고로 들어갑니다. 승인할 필요는 없고, 끄거나 고치거나 지울 수 있습니다.
            </Help>
          </>
        }
        desc={`채점 검토에서 바꾼 점수가 쌓입니다 — 다음 시험의 해설 다듬기·루브릭에 그 문항과 맞는 사례만 들어갑니다 (쓰는 사례 ${live}건).`}
        right={
          data.data && (
            <Button variant="ghost" onClick={() => void act.run(() => api.post("/files/reveal", { path: data.data!.folder }))}>
              폴더 열기
            </Button>
          )
        }
      />
      <CardBody className="flex flex-col gap-2">
        {act.error && <ErrorNote message={act.error} />}
        {(data.data?.cases.length ?? 0) > 5 && <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="찾기 (예: 굴절, 2611 월례)" />}
        {cases.slice(0, shown).map((c) => (
          <CaseRow key={c.id} c={c} busy={act.busy} onSave={(ch) => void save(c, ch)} onDelete={() => void remove(c)} />
        ))}
        {cases.length > shown && (
          <Button variant="ghost" onClick={() => setShown(shown + 20)}>
            더 보기 ({cases.length - shown})
          </Button>
        )}
        {data.data && data.data.cases.length === 0 && <div className="text-text-3">아직 사례가 없습니다. 채점 검토에서 점수를 바꾸면 여기에 쌓입니다.</div>}
      </CardBody>
    </Card>
  );
}

function CaseRow({ c, busy, onSave, onDelete }: { c: Case; busy: boolean; onSave: (ch: Partial<Case>) => void; onDelete: () => void }) {
  const [edit, setEdit] = useState<{ title: string; body: string; keywords: string } | null>(null);
  const src = `${c.exam} ${c.question}번${c.part ? ` ${c.part}` : ""}`.trim();
  return (
    <div className={cn("rounded-lg border border-line p-3", !c.enabled && "opacity-60")}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={c.verdict === "인정" ? "ok" : "bad"}>{c.verdict}</Badge>
        <span className="min-w-0 flex-1 font-medium">
          <RichText text={c.title} />
        </span>
        {!c.reusable && <Badge>이 문항만</Badge>}
        <span className="text-[12px] text-text-3">
          {src} · AI {pts(c.ai_points)} → 강사 {pts(c.teacher_points)} / {pts(c.max_points)}점 · {c.created_at.slice(0, 10)}
        </span>
      </div>
      {edit === null ? (
        <>
          <div className="mt-2 text-[14px]">
            {c.body.split("\n").map((ln, i) => (
              <div key={i}>
                <RichText text={ln.replace(/\*\*/g, "")} />
              </div>
            ))}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {c.keywords.map((k) => (
              <span key={k} className="rounded-full bg-muted px-2 py-0.5 text-[12px] text-text-2">
                {k}
              </span>
            ))}
            <div className="flex-1" />
            <Button size="sm" variant="ghost" onClick={() => setEdit({ title: c.title, body: c.body, keywords: c.keywords.join(", ") })}>
              <Pencil /> 고치기
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={onDelete} aria-label="지우기">
              <Trash2 />
            </Button>
          </div>
          <div className="-mb-2">
            <Switch checked={c.enabled} onCheckedChange={(v) => onSave({ enabled: v })} label={<span className="text-[13px]">다음 시험에 쓰기</span>} />
          </div>
        </>
      ) : (
        <div className="mt-2 flex flex-col gap-2">
          <Input value={edit.title} onChange={(e) => setEdit({ ...edit, title: e.target.value })} placeholder="한 줄 요약" />
          <textarea rows={6} value={edit.body} onChange={(e) => setEdit({ ...edit, body: e.target.value })} className={textarea} />
          <Input value={edit.keywords} onChange={(e) => setEdit({ ...edit, keywords: e.target.value })} placeholder="열쇠말 (쉼표로 — 이 말이 문항 글·해설에 있으면 그 문항에 들어갑니다)" />
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => setEdit(null)}>
              취소
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={busy || !edit.title.trim() || !edit.body.trim()}
              onClick={() => {
                onSave({ title: edit.title.trim(), body: edit.body.trim(), keywords: edit.keywords.split(/[,，]/).map((k) => k.trim()).filter(Boolean) });
                setEdit(null);
              }}
            >
              저장
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
