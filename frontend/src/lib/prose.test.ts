import { describe, expect, it } from "vitest";
import { prosePieces } from "./prose";

// tests/test_export.py `test_prose_pieces`와 같은 사례 (파이썬 prose_pieces와 같게 나와야 한다)
describe("prosePieces", () => {
  it.each<[string, [string, boolean][]]>([
    [
      String.raw`정답은 2\Omega이나 4\Omega으로 작성함`,
      [["정답은 ", false], [String.raw`2\Omega`, true], ["이나 ", false], [String.raw`4\Omega`, true], ["으로 작성함", false]],
    ],
    [
      String.raw`공식으로 T_1 = 2\sqrt{\frac{r}{g}}를 구함.`,
      [["공식으로 ", false], [String.raw`T_1 = 2\sqrt{\frac{r}{g}}`, true], ["를 구함.", false]],
    ],
    [String.raw`각도(\theta)가 같다`, [["각도(", false], [String.raw`\theta`, true], [")가 같다", false]]],
    [
      String.raw`전력 \(P=VI\)와 $V^2/R$`,
      [["전력 ", false], ["P=VI", true], ["와 ", false], ["V^2/R", true]],
    ],
    ["식 없이 (1)과 1/2, V=IR", [["식 없이 (1)과 1/2, V=IR", false]]],
  ])("%s", (text, want) => {
    expect(prosePieces(text)).toEqual(want);
  });
});
