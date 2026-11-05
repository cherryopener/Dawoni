import { describe, expect, it } from "vitest";
import { figureRefs, hasFigureMark, matches } from "./FigureDialog";

describe("figure marks", () => {
  it("finds figure numbers in answers", () => {
    expect(figureRefs(["[그림4]처럼 그린다", "[그림 F2], [그림 4]", null])).toEqual(["4", "F2"]);
    expect(hasFigureMark("(그림: 힘 화살표)")).toBe(true);
    expect(hasFigureMark("\\(t = 2\\)")).toBe(false);
    expect(figureRefs(["(그림: 힘 화살표)"])).toEqual([]);
  });

  it("matches the exam's own figure numbers by caption, else by ID", () => {
    const f = (id: string, caption: string | null) => ({ id, caption, path: `figures/1/${id}.png` });
    expect(matches(f("F1", "[그림 4] 빗면 위의 물체"), "4")).toBe(true);
    expect(matches(f("F4", "[그림 1] 도르래"), "4")).toBe(false);
    expect(matches(f("F1", "그림 41 …"), "4")).toBe(false);
    expect(matches(f("F4", null), "4")).toBe(true);
    expect(matches(f("F2", "[그림 4]"), "F2")).toBe(true);
  });
});
