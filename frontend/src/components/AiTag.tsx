import s from "./AiTag.module.css";

type Family = "gemini" | "claude" | "openai" | "other";

const BRAND: Record<Family, string> = { gemini: "Gemini", claude: "Claude", openai: "ChatGPT", other: "AI" };

/** 모델 이름으로 먼저, 모르면 CLI 이름으로 (agy → Gemini, claude → Claude, codex → ChatGPT). */
export function aiFamily(agent?: string | null, model?: string | null): Family {
  const m = (model ?? "").toLowerCase();
  if (m.includes("gemini")) return "gemini";
  if (/claude|opus|sonnet|haiku|fable/.test(m)) return "claude";
  if (/gpt|codex|^o\d/.test(m)) return "openai";
  if (agent === "agy") return "gemini";
  if (agent === "claude") return "claude";
  if (agent === "codex") return "openai";
  return "other";
}

/** "gemini-3.8-flash-medium" → "3.8 flash medium" (브랜드는 앞에 따로 붙인다). */
export function shortModel(model: string, family: Family): string {
  const prefix = { gemini: /^gemini-?/i, claude: /^claude-?/i, openai: /^(gpt-?|openai-?)/i, other: /^$/ }[family];
  return model.replace(prefix, "").replace(/-/g, " ").trim();
}

/** 만든 AI·모델 태그 — 색으로 한눈에 구분 (Gemini 파랑, Claude 주황, ChatGPT 청록). */
export function AiTag({ agent, model, what }: { agent?: string | null; model?: string | null; what?: string }) {
  if (!agent && !model) return null;
  const f = aiFamily(agent, model);
  const name = model ? `${BRAND[f]} ${shortModel(model, f)}`.trim() : BRAND[f];
  const title = [what && `${what}을(를) 만든 AI`, agent && `CLI: ${agent}`, model && `모델: ${model}`].filter(Boolean).join(" · ");
  return (
    <span className={`${s.tag} ${s[f]}`} title={title}>
      <span className={s.dot} />
      {name}
    </span>
  );
}
