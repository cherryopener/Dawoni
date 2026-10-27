import { describe, expect, it } from "vitest";
import { looseSegments, splitDelimited } from "./loose";

const maths = (s: string) => looseSegments(s).filter((x) => x.kind === "math").map((x) => x.v);

describe("looseSegments (파이썬 loose_segments와 같은 결과)", () => {
  it.each([
    ["x좌표는 x=-\\frac{b}{2a}=2이다.", ["x=-\\frac{b}{2a}=2"]],
    ["(단, \\frac{1}{2}) 이고 \\(y=3\\)", ["\\frac{1}{2}", "y=3"]],
    ["식을 y=(x-2)^2+3으로 변형", ["y=(x-2)^2+3"]],
    ["그냥 문장 x=2와 (0,1)", []],
  ])("%s", (s, expected) => {
    expect(maths(s)).toEqual(expected);
  });

  it("글자를 잃지 않는다", () => {
    const s = "꼭짓점 (단, \\frac{1}{2}) 이다";
    expect(looseSegments(s).map((x) => x.v).join("")).toBe(s);
  });

  it("금액 $5와 $3은 수식이 아님", () => {
    expect(splitDelimited("가격 $5와 $3")).toEqual([{ kind: "text", v: "가격 $5와 $3" }]);
  });
});
