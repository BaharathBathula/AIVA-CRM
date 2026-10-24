/**
 * Where AI text came from. The API answers with a `source` on drafts, summaries,
 * answers, agent suggestions and call notes:
 * the workspace's own model (`openai`, `azure_openai`), AIVA's default (`claude`),
 * or built-in rules (`rules`).
 */

export type AiSource = "openai" | "azure_openai" | "claude" | "rules";

export const AI_SOURCE_LABELS: Record<string, string> = {
  openai: "OpenAI",
  azure_openai: "Azure OpenAI",
  claude: "Claude",
  rules: "Built-in rules",
};

/** True when a model wrote it (any provider), false for built-in rules. */
export function isAiSource(source: string | null | undefined): boolean {
  return Boolean(source) && source !== "rules";
}

/** "OpenAI", or "OpenAI (gpt-5-mini)" when the model is known. */
export function aiSourceLabel(source: string | null | undefined, model?: string | null): string {
  const label = AI_SOURCE_LABELS[source ?? ""] ?? source ?? "";
  return model && isAiSource(source) ? `${label} (${model})` : label;
}

/** Which model each AI feature uses right now (GET /ai-models/status). */
export type AiModelState = { source: string; label: string; model: string | null; reason: string };
