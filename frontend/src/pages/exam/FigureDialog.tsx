import { Dialog } from "@/components/ui/dialog";
import { examFileUrl } from "@/lib/api";
import type { QuestionFigure } from "@/lib/types";

/** 정답·해설 속 그림 자리: `[그림 4]`, `[그림4]`, `[그림 F2]`, `(그림: …)`. */
const FIGURE_MARK = /[[(]\s*그림\s*(F?\d+)?/g;

export function hasFigureMark(text: string | null | undefined): boolean {
  return Boolean(text && new RegExp(FIGURE_MARK.source).test(text));
}

/** 글에 나온 그림 번호 ("4", "F2"). */
export function figureRefs(texts: (string | null | undefined)[]): string[] {
  const out = new Set<string>();
  for (const t of texts) for (const m of (t ?? "").matchAll(FIGURE_MARK)) if (m[1]) out.add(m[1]);
  return [...out];
}

/** 그림이 이 번호인가: ID(F4)이거나, 설명이 시험지 번호("[그림 4] …", "그림 4")로 시작. */
export function matches(f: QuestionFigure, ref: string): boolean {
  if (ref.startsWith("F")) return f.id === ref;
  if (f.caption && new RegExp(`^\\s*\\[?\\s*그림\\s*${ref}(?!\\d)`).test(f.caption)) return true;
  return !f.caption && f.id === `F${ref}`;
}

/** 정답의 `[그림 4]`가 무엇인지: 문항 그림과 해설지 쪽을 보여 준다 (AI 호출 없음). */
export function FigureDialog({
  open,
  onOpenChange,
  examId,
  number,
  refs,
  figures,
  keyPages,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  examId: string;
  number: string;
  refs: string[];
  figures: QuestionFigure[];
  keyPages: string[];
}) {
  const hit = (f: QuestionFigure) => refs.some((r) => matches(f, r));
  const sorted = [...figures].sort((a, b) => Number(hit(b)) - Number(hit(a)));
  const found = figures.some(hit);
  const asked = refs.map((r) => `[그림 ${r}]`).join(", ");
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      wide
      title={`${number}번 그림`}
      desc={
        refs.length === 0
          ? "문항 그림과 해설지 쪽입니다."
          : found
            ? `${asked}로 보이는 그림을 테두리로 표시했습니다. 해설지에만 있는 그림이면 아래 해설지 쪽에서 찾으세요.`
            : `${asked}와 이름이 맞는 문항 그림이 없습니다 — 해설지에만 있는 그림이면 아래 해설지 쪽에서 찾으세요.`
      }
    >
      <div className="flex flex-col gap-6">
        <section>
          <div className="mb-2 text-[13px] font-medium">문항 그림</div>
          {figures.length === 0 ? (
            <div className="text-[13px] text-text-3">이 문항에 저장된 그림이 없습니다.</div>
          ) : (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-3">
              {sorted.map((f) => (
                <figure
                  key={f.id}
                  className={"m-0 flex flex-col gap-1.5 rounded-lg border p-2 " + (hit(f) ? "border-accent ring-2 ring-accent/30" : "border-line")}
                >
                  <img src={examFileUrl(examId, f.path)} alt={f.caption ?? f.id} className="max-h-64 w-full rounded bg-white object-contain" />
                  <figcaption className="text-[12px] text-text-2">
                    <span className="text-text-3">{f.id}</span> {f.caption ?? ""}
                  </figcaption>
                </figure>
              ))}
            </div>
          )}
        </section>
        <section>
          <div className="mb-2 text-[13px] font-medium">해설지 쪽</div>
          {keyPages.length === 0 ? (
            <div className="text-[13px] text-text-3">저장된 해설지 쪽 그림이 없습니다 (해설지를 붙여 넣었거나 직접 입력한 해설).</div>
          ) : (
            <div className="flex flex-col gap-3">
              {keyPages.map((p) => (
                <img key={p} src={examFileUrl(examId, p)} alt={`해설지 ${p}`} className="w-full rounded border border-line bg-white" />
              ))}
            </div>
          )}
        </section>
      </div>
    </Dialog>
  );
}
