import { describe, expect, it } from "vitest";
import { guessClass, guessRole } from "./roles";

describe("guessRole", () => {
  it("문서는 시험지, 엑셀·사진은 명단", () => {
    expect(guessRole("11월 월례평가 문제-물리_revision.hwpx", false)).toBe("exam");
    expect(guessRole("명단.xlsx", false)).toBe("roster");
    expect(guessRole("MSS1-J 명단.jpg", false)).toBe("roster");
    expect(guessRole("메모.txt", false)).toBe("skip");
  });
  it("PDF는 이름으로: 시험지 이름이면 시험지(아직 없을 때만), 아니면 답안지", () => {
    expect(guessRole("m1j.pdf", false)).toBe("answers");
    expect(guessRole("MSS1-J_물리_채점답안지.pdf", false)).toBe("answers");
    expect(guessRole("11월 시험지_해설.pdf", false)).toBe("exam");
    expect(guessRole("11월 시험지_해설.pdf", true)).toBe("answers");
  });
});

describe("guessClass", () => {
  const classes = ["MSS1-J", "MSS1-K", "MSS2-J", "1반"];
  it("파일 이름에 반 이름이 하나 들어 있을 때만", () => {
    expect(guessClass("MSS1-J 답안.pdf", classes)).toBe("MSS1-J");
    expect(guessClass("mss2j_채점.pdf", classes)).toBe("MSS2-J");
    expect(guessClass("m1j.pdf", classes)).toBe("");
    expect(guessClass("MSS1-J MSS1-K.pdf", classes)).toBe("");
  });
});
