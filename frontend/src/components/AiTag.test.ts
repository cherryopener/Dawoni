import { describe, expect, it } from "vitest";
import { aiFamily, shortModel } from "./AiTag";

describe("AiTag", () => {
  it("모델 이름으로 먼저, 모르면 CLI로 계열을 정한다", () => {
    expect(aiFamily("agy", "gemini-3.8-flash-medium")).toBe("gemini");
    expect(aiFamily("agy", "claude-sonnet-5-5")).toBe("claude");
    expect(aiFamily("codex", null)).toBe("openai");
    expect(aiFamily("claude", null)).toBe("claude");
    expect(aiFamily(null, "gpt-5.5")).toBe("openai");
    expect(aiFamily("other", null)).toBe("other");
  });
  it("모델 이름을 짧게", () => {
    expect(shortModel("gemini-3.8-flash-medium", "gemini")).toBe("3.8 Flash Medium");
    expect(shortModel("claude-opus-5-5", "claude")).toBe("Opus 5.5");
    expect(shortModel("gpt-5.5", "openai")).toBe("5.5");
  });
});
