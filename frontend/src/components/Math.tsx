import katex from "katex";
import "katex/contrib/mhchem";
import "katex/dist/katex.min.css";
import { useMemo } from "react";
import { looseSegments } from "../lib/loose";

// 변수 자리표시 \var{m}는 강조 상자로 보여 준다 (값은 출력할 때 채움).
const prep = (latex: string) => latex.replace(/\\var\{([^{}]*)\}/g, "\\boxed{$1}");

export function Tex({ latex, display }: { latex: string; display?: boolean }) {
  const html = useMemo(() => {
    try {
      return katex.renderToString(prep(latex), {
        displayMode: !!display,
        throwOnError: true,
        strict: "ignore",
        trust: false,
      });
    } catch {
      return null;
    }
  }, [latex, display]);
  if (html === null) {
    // 읽을 수 없는 식은 감추지 않고 원문 그대로 (학생 답안 판독문 등)
    return (
      <code title="수식으로 읽을 수 없어 원문 그대로 표시" style={{ color: "var(--bad)" }}>
        {latex}
      </code>
    );
  }
  return display ? (
    <div style={{ textAlign: "center", margin: "6px 0" }} dangerouslySetInnerHTML={{ __html: html }} />
  ) : (
    <span dangerouslySetInnerHTML={{ __html: html }} />
  );
}

/** AI·학생이 쓴 문장 (`\( \)` 수식, 구분자 없는 LaTeX 포함). */
export function Loose({ text }: { text: string | null | undefined }) {
  if (!text) return null;
  return (
    <>
      {looseSegments(text).map((seg, i) =>
        seg.kind === "math" ? <Tex key={i} latex={seg.v} /> : <span key={i}>{seg.v}</span>,
      )}
    </>
  );
}
