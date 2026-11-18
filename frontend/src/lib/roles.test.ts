import { describe, expect, it } from "vitest";
import { guessRole } from "./roles";

describe("guessRole", () => {
  it("문서는 시험지, 엑셀은 명단", () => {
    expect(guessRole("11월 월례평가 문제-물리_revision.hwpx", false)).toBe("exam");
    expect(guessRole("명단.xlsx", false)).toBe("roster");
    expect(guessRole("사진.png", false)).toBe("skip");
  });
  it("PDF는 이름으로: 시험지 이름이면 시험지(아직 없을 때만), 아니면 답안지", () => {
    expect(guessRole("m1j.pdf", false)).toBe("answers");
    expect(guessRole("MSS1-J_물리_채점답안지.pdf", false)).toBe("answers");
    expect(guessRole("11월 시험지_해설.pdf", false)).toBe("exam");
    expect(guessRole("11월 시험지_해설.pdf", true)).toBe("answers");
  });
});
