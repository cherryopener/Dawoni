import { describe, expect, it } from "vitest";
import { draftProblems, insertAt, lines, partsPerFigure, removeFigure, usedFigures } from "./manual";
import type { ManualDraft } from "./types";

const base: ManualDraft = {
  number: "5", item_type: "short_answer", points: 4, text: "", choices: [], answer: "", parts: [],
};

describe("직접 입력 글", () => {
  it("그림마다 소문항: 회로 그림 7개 + 한 줄 문제 (실측: 기초물리학 51쪽)", () => {
    const text = ["A, B 사이 등가저항을 구하라.", ...[2, 3, 5, 7, 1, 4, 6].map((n) => `[그림 F${n}]`)].join("\n");
    const d = partsPerFigure({ ...base, text });
    expect(d.parts.map((p) => p.label)).toEqual(["(1)", "(2)", "(3)", "(4)", "(5)", "(6)", "(7)"]);
    expect(d.points).toBe(7);
    expect(d.parts.every((p) => p.points === 1)).toBe(true);
    expect(d.text.split("\n").slice(0, 5)).toEqual(["A, B 사이 등가저항을 구하라.", "(1)", "[그림 F2]", "(2)", "[그림 F3]"]);
    expect(draftProblems(d, ["F1", "F2", "F3", "F4", "F5", "F6", "F7"])).toEqual([]);
    // 다시 눌러도 같은 모양 (소문항 줄이 쌓이지 않음)
    expect(partsPerFigure(d).text).toBe(d.text);
  });

  it("나눠떨어지는 배점은 그대로 나눈다", () => {
    const d = partsPerFigure({ ...base, points: 6, text: "구하라\n[그림 F1]\n[그림 F2]" });
    expect(d.points).toBe(6);
    expect(d.parts.map((p) => p.points)).toEqual([3, 3]);
  });

  it("그림 줄 넣기·빼기, 쓴 그림", () => {
    const r = insertAt("첫 줄두 줄", 3, "[그림 F1]", true);
    expect(r.text).toBe("첫 줄\n[그림 F1]\n두 줄");
    expect(usedFigures({ text: r.text, choices: ["[그림 F3]", "1"] })).toEqual(["F1", "F3"]);
    expect(removeFigure(r.text, "F1")).toBe("첫 줄\n두 줄");
  });

  it("미리보기 줄 종류", () => {
    expect(lines("글 \\(x^2\\)\n[그림 F1]\n[블록 2]\n\\[ F=ma \\]").map((l) => l.kind))
      .toEqual(["text", "figure", "block", "display"]);
  });

  it("저장 전 문제", () => {
    const mc: ManualDraft = { ...base, item_type: "multiple_choice", text: "[그림 F9]", choices: ["1", ""] };
    expect(draftProblems(mc, ["F1"])).toEqual(["없는 그림: F9", "2번 선지가 비어 있습니다"]);
  });
});
