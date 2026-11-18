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

/** ⑤ 내보내기: 점수표는 판독·채점이 저장될 때마다 저절로 쓰이므로, 여기서는 열고 확인만 한다.
 * 채점 답안지 PDF(학생 답안지 위에 빨간 글씨로 점수·이유)는 강사가 눌러 만든다 — 채점 HWPX는 2026-11-18에 없앴다. */
export function ExportStep() {
  const { base, detail } = useExam();
  const out = useApi<Outputs>(`${base}/outputs`);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [askForce, setAskForce] = useState(false);
  const [pdfNotes, setPdfNotes] = useState<string[]>([]);
  const act = useAction();
  const pdf = useAction();
  const o = out.data;
  const s = detail.summary;
  const left = s.students * s.questions - s.graded;

  const openScores = () => act.run(() => api.post("/files/open", { path: o!.scores }));
  const reveal = () => act.run(() => api.post("/files/reveal", { path: o!.folder }));
  const write = (body: { by_class?: boolean; force?: boolean }) =>
    act.run(async () => {
      const r = await api.post<FilesResult>(`${base}/scores`, body);
      setWarnings(r.file_warnings);
      setAskForce(false);
      out.reload();
    });

  const makePdf = () =>
    pdf.run(async () => {
      const j = await waitJob(await api.post<Job>(`${base}/marked`, {}));
      const r = j.result as { files: string[]; file_warnings: string[]; students: number };
      setPdfNotes([`${r.files.length}개 반 · 학생 ${r.students}명`, ...r.file_warnings]);
      out.reload();
    });
  const openPdf = (name: string) => pdf.run(() => api.post("/files/open", { path: `${o!.marked_folder}/${name}` }));

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
            <div className="flex gap-1">
              <Button variant="ghost" disabled={!o.folder_exists || act.busy} onClick={() => void reveal()}>
                <FolderOpen /> 폴더
              </Button>
              <Button variant="primary" disabled={!o.scores_exists || act.busy} onClick={() => void openScores()}>
                점수표 열기
              </Button>
            </div>
          }
        />
        <CardBody className="flex flex-col gap-2">
          <Switch checked={o.scores_by_class} onCheckedChange={(v) => void write({ by_class: v })} label="반마다 시트 나누기" desc="끄면 한 시트에 모든 반" />
          <div className="flex flex-wrap gap-1.5 text-[12px]">
            <Badge tone="neutral">미채점 회색</Badge>
            <Badge tone="warn">검토 필요 노랑</Badge>
            <Badge tone="accent">판독문 확인 전 옅은 파랑</Badge>
          </div>
          {warnings.map((w) => (
            <div key={w} className="flex items-center gap-2 rounded-lg bg-warn-soft px-3 py-2 text-[13px] text-warn">
              <Info className="size-4 shrink-0" />
              <span className="flex-1">{w}</span>
            </div>
          ))}
          {o.scores_edited && (
            <div className="flex items-center gap-2 rounded-lg bg-muted px-3 py-2 text-[13px] text-text-2">
              <Info className="size-4 shrink-0" />
              <span className="flex-1">엑셀에서 고친 점수표라 다시 쓰지 않았습니다. 앱은 점수표를 읽지 않으니 점수는 채점 검토에서 고치세요.</span>
              <Button size="sm" variant="ghost" onClick={() => setAskForce(true)}>
                덮어쓰기
              </Button>
            </div>
          )}
          {act.error && <ErrorNote message={act.error} />}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          icon={<FileText />}
          title="채점 답안지 (PDF)"
          desc="학생 답안지 위에 빨간 글씨로 총점·문항 점수, 소문항 점수, 감점 이유 — 반마다 한 파일"
          right={
            <div className="flex gap-1">
              <Button variant="ghost" disabled={o.marked.length === 0 || pdf.busy} onClick={() => void pdf.run(() => api.post("/files/reveal", { path: o.marked_folder }))}>
                <FolderOpen /> 폴더
              </Button>
              <Button variant={o.marked.length ? "outline" : "primary"} disabled={pdf.busy || s.graded === 0} onClick={() => void makePdf()}>
                {pdf.busy ? "만드는 중…" : o.marked.length ? "다시 만들기" : "만들기"}
              </Button>
            </div>
          }
        />
        <CardBody className="flex flex-col gap-1.5">
          {o.marked.length === 0 && <div className="text-[13px] text-text-3">채점이 끝난 뒤 만듭니다. 검토 필요·채점 전 답안은 알려 드립니다.</div>}
          {o.marked.map((name) => (
            <button key={name} type="button" onClick={() => void openPdf(name)} className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-muted">
              <FileText className="size-4 text-text-3" />
              <span className="flex-1">{name}</span>
            </button>
          ))}
          {pdfNotes.length > 0 && (
            <div className="mt-1 max-h-40 overflow-auto rounded-lg bg-muted px-3 py-2 text-[12px] text-text-2">
              {pdfNotes.map((n) => (
                <div key={n}>{n}</div>
              ))}
            </div>
          )}
          {pdf.error && <ErrorNote message={pdf.error} />}
        </CardBody>
      </Card>

      <Dialog
        open={askForce}
        onOpenChange={setAskForce}
        title="엑셀에서 고친 점수표를 덮어쓸까요?"
        desc="엑셀에서 고친 내용은 사라지고, 앱의 점수로 다시 만듭니다."
        footer={
          <>
            <Button variant="ghost" onClick={() => setAskForce(false)}>
              취소
            </Button>
            <Button variant="danger" disabled={act.busy} onClick={() => void write({ force: true })}>
              덮어쓰기
            </Button>
          </>
        }
      />
    </div>
  );
}
