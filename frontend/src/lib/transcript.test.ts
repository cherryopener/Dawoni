import { describe, expect, it } from "vitest";
import { fixWon, fromDollars, insideMath, tidyMath, toDollars } from "./transcript";

describe("식 입력 ($수식$)", () => {
  it("₩(원화 기호)는 \\로", () => {
    expect(fixWon("₩frac{1}{2}")).toBe("\\frac{1}{2}");
    expect(fixWon("￦sqrt{2}")).toBe("\\sqrt{2}");
  });
  it("칸에서는 $수식$, 저장은 \\( \\)", () => {
    const saved = "속력은 \\(\\frac{1}{2}v\\) 이고 \\(F=ma\\)";
    expect(toDollars(saved)).toBe("속력은 $\\frac{1}{2}v$ 이고 $F=ma$");
    expect(fromDollars(toDollars(saved))).toBe(saved);
    expect(fromDollars("$$v^2$$ 그리고 $₩Delta t$")).toBe("\\(v^2\\) 그리고 \\(\\Delta t\\)");
    expect(fromDollars("식 없음")).toBe("식 없음");
  });
  it("식 줄은 감싼 구분자를 뗀다", () => {
    expect(tidyMath(" $mv^2$ ")).toBe("mv^2");
    expect(tidyMath("\\(F=ma\\)")).toBe("F=ma");
    expect(tidyMath("₩frac{a}{b}")).toBe("\\frac{a}{b}");
  });
  it("커서가 $ $ 안인지", () => {
    const s = "답 $x+1$ 끝";
    expect(insideMath(s, 4)).toBe(true);
    expect(insideMath(s, s.length)).toBe(false);
  });
});
