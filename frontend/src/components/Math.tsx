import katex from "katex";
import "katex/dist/katex.min.css";
import { useMemo } from "react";
import { prosePieces } from "@/lib/prose";

function render(latex: string): string {
  try {
    return katex.renderToString(latex, { throwOnError: false, output: "html", strict: "ignore" });
  } catch {
    return latex;
  }
}

/** LaTeX 식 하나 (`$` 없이). */
export function TeX({ latex, className }: { latex: string; className?: string }) {
  const html = useMemo(() => render(latex), [latex]);
  return <span className={className} dangerouslySetInnerHTML={{ __html: html }} />;
}

/** 해설·루브릭 기준·채점 이유: `\( \)`로 감싼 식과, AI가 감싸지 않고 글에 섞어 쓴 식을 그 자리에 그린다. */
export function RichText({ text, className }: { text: string; className?: string }) {
  const parts = useMemo(() => prosePieces(text), [text]);
  return (
    <span className={className}>
      {parts.map(([p, math], i) => (math ? <TeX key={i} latex={p} /> : <span key={i}>{p}</span>))}
    </span>
  );
}
