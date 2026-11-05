import { FileSpreadsheet, FileText, FolderOpen, Info } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { ErrorNote } from "@/components/ui/misc";
import { Switch } from "@/components/ui/switch";
import { api, useAction, useApi, waitJob } from "@/lib/api";
import type { Job, Outputs } from "@/lib/types";
import { useExam } from "../Exam";

type FilesResult = { files: string[]; edited: string[]; file_warnings: string[] };

/** ⑤ 내보내기: HWPX와 점수표는 채점이 저장될 때마다 저절로 쓰이므로, 여기서는 열고 확인만 한다. */
export function ExportStep() {
  const { base, detail } = useExam();
  const out = useApi<Outputs>(`${base}/outputs`);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [askForce, setAskForce] = useState(false);
  const act = useAction();
  const o = out.data;
  const s = detail.summary;
  const left = s.students * s.questions - s.graded;

  const openScores = () => act.run(() => api.post("/files/open", { path: o!.scores }));
  const reveal = () => act.run(() => api.post("/files/reveal", { path: o!.folder }));
  const byClass = (v: boolean) =>
    act.run(async () => {
      const r = await api.post<FilesResult>(`${base}/scores`, { by_class: v });
      setWarnings(r.file_warnings);
      out.reload();
    });
  const rewrite = (force: boolean) =>
    act.run(async () => {
      const j = await waitJob(await api.post<Job>(`${base}/export`, { force }));
      setWarnings((j.result as FilesResult).file_warnings);
      setAskForce(false);
      out.reload();
    });

  if (!o) return <div className="p-8 text-text-3">불러오는 중…</div>;
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4 px-6 py-6">
      {left > 0 && (
        <div className="rounded-lg bg-muted px-4 py-2.5 text-[13px] text-text-2">
          아직 채점이 끝나지 않은 답안이 {left}건 있습니다. 점수표에는 빈 칸(회색)으로 들어갑니다.
        </div>
      )}
      <Card>
        <CardHeader
          icon={<FileSpreadsheet />}
          title="점수표 (엑셀)"
          desc={o.scores_exists ? `${o.scores.split("/").pop()} · 반 → 이름 가나다순` : "채점이 저장되면 저절로 만듭니다."}
          right={
            <Button variant="primary" disabled={!o.scores_exists || act.busy} onClick={() => void openScores()}>
              점수표 열기
            </Button>
          }
        />
        <CardBody>
          <Switch checked={o.scores_by_class} onCheckedChange={(v) => void byClass(v)} label="반마다 시트 나누기" desc="끄면 한 시트에 모든 반" />
          <div className="mt-1 flex flex-wrap gap-1.5 text-[12px]">
            <Badge tone="neutral">미채점 회색</Badge>
            <Badge tone="warn">검토 필요 노랑</Badge>
            <Badge tone="accent">판독문 확인 전 옅은 파랑</Badge>
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          icon={<FileText />}
          title="판독본 · 채점 HWPX"
          desc={o.folder_exists ? `학생 × 문항 ${o.students}개 · 반별 묶음 ${o.bundles}개 (인쇄용)` : "판독이 끝나면 저절로 만듭니다."}
          right={
            <Button variant="outline" disabled={!o.folder_exists || act.busy} onClick={() => void reveal()}>
              <FolderOpen /> 폴더 열기
            </Button>
          }
        />
        <CardBody className="flex flex-col gap-2">
          {warnings.map((w) => (
            <div key={w} className="flex items-center gap-2 rounded-lg bg-warn-soft px-3 py-2 text-[13px] text-warn">
              <Info className="size-4 shrink-0" />
              <span className="flex-1">{w}</span>
            </div>
          ))}
          {o.edited.length > 0 && (
            <div className="flex items-center gap-2 rounded-lg bg-muted px-3 py-2 text-[13px] text-text-2">
              <Info className="size-4 shrink-0" />
              <span className="flex-1">
                한글에서 고친 파일 {o.edited.length}개({o.edited.slice(0, 3).join(", ")})는 덮어쓰지 않았습니다. 판독문을 고친 것이면 답안 판독에서
                "한글에서 고친 것 반영"을 누르세요.
              </span>
              <Button size="sm" variant="ghost" onClick={() => setAskForce(true)}>
                덮어쓰기
              </Button>
            </div>
          )}
          <div className="flex justify-end">
            <Button size="sm" variant="ghost" disabled={act.busy} onClick={() => void rewrite(false)}>
              {act.busy ? "쓰는 중…" : "HWPX 다시 쓰기"}
            </Button>
          </div>
          {act.error && <ErrorNote message={act.error} />}
        </CardBody>
      </Card>

      <Dialog
        open={askForce}
        onOpenChange={setAskForce}
        title="고친 파일을 덮어쓸까요?"
        desc="한글에서 고친 내용은 사라집니다. 판독문을 고친 것이면 먼저 반영하세요."
        footer={
          <>
            <Button variant="ghost" onClick={() => setAskForce(false)}>
              취소
            </Button>
            <Button variant="danger" disabled={act.busy} onClick={() => void rewrite(true)}>
              덮어쓰기
            </Button>
          </>
        }
      />
    </div>
  );
}
