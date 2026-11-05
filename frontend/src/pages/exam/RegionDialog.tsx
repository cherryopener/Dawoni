import { ChevronLeft, ChevronRight, ImagePlus, X, ZoomIn, ZoomOut } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { ErrorNote } from "@/components/ui/misc";
import { Segmented } from "@/components/ui/segmented";
import { api, enc, useAction, useApi, waitJob } from "@/lib/api";
import type { ExamPart, ExamSource, Job } from "@/lib/types";

export type RegionWhat = "question" | "key";
type Box = { page: number; x0: number; y0: number; x1: number; y1: number };

/** 끈 상자가 이보다 작으면 실수로 누른 것으로 본다 (쪽 비율). */
const MIN_SIDE = 0.01;

function norm(b: Box): Box {
  return { page: b.page, x0: Math.min(b.x0, b.x1), y0: Math.min(b.y0, b.y1), x1: Math.max(b.x0, b.x1), y1: Math.max(b.y0, b.y1) };
}

/**
 * 원본에서 영역을 지정해 다시 읽기: 시험지 쪽 위에서 마우스로 상자를 끌거나(여러 개, 여러 쪽),
 * 캡처한 그림을 Ctrl+V로 붙여 넣는다. 문항 본문은 강사가 정해 둔 소문항을 그대로 두고 읽는다.
 */
export function RegionDialog({
  open,
  onOpenChange,
  base,
  number,
  parts,
  sourcePages,
  initial,
  onDone,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  base: string;
  number: string;
  parts: ExamPart[];
  sourcePages: number[];
  initial: RegionWhat;
  onDone: (notes: string[]) => void;
}) {
  const src = useApi<ExamSource>(open ? `${base}/source` : null, { refreshOnJobs: false });
  const [what, setWhat] = useState<RegionWhat>(initial);
  const [page, setPage] = useState(1);
  const [boxes, setBoxes] = useState<Box[]>([]);
  const [drag, setDrag] = useState<Box | null>(null);
  /** false: 쪽 전체가 보이게 (문항 하나를 한 번에 끌기 좋음), true: 폭에 맞춰 크게 */
  const [zoom, setZoom] = useState(false);
  const [pasted, setPasted] = useState<File[]>([]);
  const [msg, setMsg] = useState("");
  const act = useAction();
  const area = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const info = src.data;
  const total = info?.pages.length ?? 0;

  // 처음 보여 줄 쪽: 본문은 이 문항을 읽었던 쪽, 해설은 해설지 첫 쪽
  const firstSourcePage = sourcePages[0];
  useEffect(() => {
    if (!open || !info) return;
    const keyPage = info.key_pages[0];
    const firstQuestionPage = Array.from({ length: total }, (_, i) => i + 1).find((p) => !info.key_pages.includes(p)) ?? 1;
    setPage(what === "key" ? (keyPage ?? total) || 1 : (firstSourcePage ?? firstQuestionPage));
  }, [open, info, what, firstSourcePage, total]);

  useEffect(() => {
    if (open) setWhat(initial);
  }, [open, initial]);

  // 붙여 넣기 (Win+Shift+S로 캡처 → Ctrl+V)
  useEffect(() => {
    if (!open) return;
    const onPaste = (e: ClipboardEvent) => {
      const files = [...(e.clipboardData?.items ?? [])]
        .filter((i) => i.type.startsWith("image/"))
        .map((i) => i.getAsFile())
        .filter((f): f is File => f !== null);
      if (files.length) {
        e.preventDefault();
        setPasted((p) => [...p, ...files]);
      }
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [open]);

  const thumbs = useMemo(() => pasted.map((f) => URL.createObjectURL(f)), [pasted]);
  useEffect(() => () => thumbs.forEach((u) => URL.revokeObjectURL(u)), [thumbs]);

  const reset = () => {
    setBoxes([]);
    setPasted([]);
    setDrag(null);
    setMsg("");
    act.clear();
  };
  const close = (v: boolean) => {
    if (!v && act.busy) return;
    if (!v) reset();
    onOpenChange(v);
  };

  const at = (e: PointerEvent<HTMLDivElement>) => {
    const r = area.current!.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) };
  };
  const down = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || act.busy) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    const p = at(e);
    setDrag({ page, x0: p.x, y0: p.y, x1: p.x, y1: p.y });
  };
  const move = (e: PointerEvent<HTMLDivElement>) => {
    if (!drag) return;
    const p = at(e);
    setDrag({ ...drag, x1: p.x, y1: p.y });
  };
  const up = () => {
    if (!drag) return;
    const b = norm(drag);
    if (b.x1 - b.x0 >= MIN_SIDE && b.y1 - b.y0 >= MIN_SIDE) setBoxes((bs) => [...bs, b]);
    setDrag(null);
  };

  const run = () =>
    act.run(async () => {
      const form = new FormData();
      form.append("what", what);
      form.append("boxes", JSON.stringify(boxes));
      pasted.forEach((f, i) => form.append("files", f, f.name || `paste_${i + 1}.png`));
      const job = await api.upload<Job>(`${base}/questions/${enc(number)}/read-region`, form);
      const done = await waitJob(job, (j) => setMsg(j.progress.message));
      onDone((done.result?.notes as string[] | undefined) ?? []);
      reset();
      onOpenChange(false);
    });

  const labels = parts.map((p) => p.label).join(" ");
  const onPage = boxes.map((b, i) => ({ b, i })).filter(({ b }) => b.page === page);
  const ready = boxes.length + pasted.length > 0;
  const isKeyPage = info?.key_pages.includes(page);

  return (
    <Dialog
      wide
      open={open}
      onOpenChange={close}
      title={`${number}번 — 원본에서 영역 지정해서 읽기`}
      desc={
        what === "question"
          ? parts.length
            ? `문항 전체(그림 포함)를 끌어 지정하세요. 소문항은 지금 정해 둔 ${labels} 그대로 둡니다.`
            : "문항 전체(그림 포함)를 끌어 지정하세요. 소문항·배점도 그림에서 읽습니다."
          : `해설지에서 ${number}번 해설 부분을 끌어 지정하세요. 쪽을 넘어가면 다음 쪽에서도 끌면 됩니다.`
      }
      footer={
        <>
          <Button variant="ghost" disabled={act.busy} onClick={() => close(false)}>
            취소
          </Button>
          <Button variant="primary" disabled={!ready || act.busy} onClick={() => void run()}>
            {act.busy ? msg || "읽는 중…" : what === "question" ? "본문 읽기" : "해설 읽기"}
          </Button>
        </>
      }
    >
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <Segmented
          value={what}
          onChange={(v) => {
            setWhat(v);
            setBoxes([]);
            setPasted([]);
          }}
          options={[
            { value: "question", label: "문항 본문" },
            { value: "key", label: "해설" },
          ]}
        />
        {info?.pdf && (
          <div className="flex items-center gap-1">
            <Button size="icon" variant="ghost" aria-label="앞 쪽" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              <ChevronLeft />
            </Button>
            <span className="min-w-20 text-center tabular-nums text-text-2">
              {page} / {total}쪽{isKeyPage ? " · 해설" : ""}
            </span>
            <Button size="icon" variant="ghost" aria-label="다음 쪽" disabled={page >= total} onClick={() => setPage(page + 1)}>
              <ChevronRight />
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setZoom(!zoom)}>
              {zoom ? <ZoomOut /> : <ZoomIn />} {zoom ? "쪽 전체" : "크게"}
            </Button>
          </div>
        )}
        <div className="flex-1" />
        <span className="text-[13px] text-text-3">캡처한 그림은 Ctrl+V로 붙여 넣기</span>
        <Button size="sm" variant="ghost" onClick={() => fileInput.current?.click()}>
          <ImagePlus /> 그림 파일
        </Button>
        <input
          ref={fileInput}
          type="file"
          accept="image/*"
          multiple
          hidden
          onChange={(e) => {
            setPasted((p) => [...p, ...(e.target.files ? [...e.target.files] : [])]);
            e.target.value = "";
          }}
        />
      </div>

      {src.error && <ErrorNote message={src.error} />}
      {info && !info.pdf && (
        <div className="rounded-lg border border-dashed border-line px-4 py-8 text-center text-text-2">
          시험지 원본이 PDF가 아니어서 쪽을 보여 줄 수 없습니다. 캡처(Win+Shift+S)한 그림을 Ctrl+V로 붙여 넣어 주세요.
        </div>
      )}
      {info?.pdf && (
        <div className={"overflow-auto rounded-lg border border-line bg-muted " + (zoom ? "max-h-[66vh]" : "flex justify-center p-2")}>
          <div
            ref={area}
            className={"relative cursor-crosshair touch-none select-none " + (zoom ? "" : "inline-block")}
            onPointerDown={down}
            onPointerMove={move}
            onPointerUp={up}
            onPointerCancel={() => setDrag(null)}
          >
            <img
              src={`/api${base}/source/pages/${page}`}
              alt={`${page}쪽`}
              draggable={false}
              className={zoom ? "block w-full" : "block max-h-[64vh] w-auto"}
            />
            {onPage.map(({ b, i }) => (
              <div
                key={i}
                className="absolute border-2 border-accent bg-accent/15"
                style={{ left: `${b.x0 * 100}%`, top: `${b.y0 * 100}%`, width: `${(b.x1 - b.x0) * 100}%`, height: `${(b.y1 - b.y0) * 100}%` }}
              >
                <span className="absolute left-0 top-0 rounded-br bg-accent px-1.5 text-[12px] text-accent-fg">{i + 1}</span>
                <button
                  type="button"
                  aria-label={`영역 ${i + 1} 지우기`}
                  className="absolute right-0 top-0 cursor-pointer rounded-bl bg-card/90 p-0.5 text-text-2 hover:text-bad"
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={() => setBoxes((bs) => bs.filter((_, k) => k !== i))}
                >
                  <X className="size-3.5" />
                </button>
              </div>
            ))}
            {drag && drag.page === page && (
              <div
                className="pointer-events-none absolute border-2 border-dashed border-accent bg-accent/10"
                style={{
                  left: `${Math.min(drag.x0, drag.x1) * 100}%`,
                  top: `${Math.min(drag.y0, drag.y1) * 100}%`,
                  width: `${Math.abs(drag.x1 - drag.x0) * 100}%`,
                  height: `${Math.abs(drag.y1 - drag.y0) * 100}%`,
                }}
              />
            )}
          </div>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2 text-[13px] text-text-2">
        {boxes.length === 0 && pasted.length === 0 && <span className="text-text-3">아직 지정한 영역이 없습니다.</span>}
        {boxes.map((b, i) => (
          <button
            key={i}
            type="button"
            onClick={() => setPage(b.page)}
            className="cursor-pointer rounded-md border border-line px-2 py-0.5 hover:bg-muted"
          >
            영역 {i + 1} · {b.page}쪽
          </button>
        ))}
        {thumbs.map((u, i) => (
          <span key={u} className="relative inline-block">
            <img src={u} alt={`붙여 넣은 그림 ${i + 1}`} className="h-14 rounded border border-line" />
            <button
              type="button"
              aria-label={`붙여 넣은 그림 ${i + 1} 빼기`}
              className="absolute -right-1.5 -top-1.5 cursor-pointer rounded-full border border-line bg-card p-0.5 text-text-2 hover:text-bad"
              onClick={() => setPasted((p) => p.filter((_, k) => k !== i))}
            >
              <X className="size-3" />
            </button>
          </span>
        ))}
      </div>
      {act.error && (
        <div className="mt-3">
          <ErrorNote message={act.error} />
        </div>
      )}
    </Dialog>
  );
}
