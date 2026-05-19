import type { AgentKind, ReasoningLevel } from "@/types";

export type AgentOption = {
  models: readonly string[] | "freetext";
  reasoning: readonly ReasoningLevel[];
};

export const AGENT_OPTIONS: Record<AgentKind, AgentOption> = {
  "claude-code": {
    models: ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"],
    reasoning: ["normal", "extended"],
  },
  codex: {
    models: ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex"],
    reasoning: ["low", "normal", "high"],
  },
  opencode: {
    // opencode uses Models.dev — 75+ providers, so any provider/model string works.
    models: "freetext",
    reasoning: ["low", "normal", "high", "extended"],
  },
};

export const AGENT_KINDS: AgentKind[] = ["claude-code", "codex", "opencode"];

export const DEFAULT_REASONING: ReasoningLevel = "normal";

export function agentBadgeColor(kind: AgentKind | null): string {
  switch (kind) {
    case "claude-code":
      return "bg-[#0075ca] text-white";
    case "codex":
      return "bg-[#e4e669] text-black";
    case "opencode":
      return "bg-[#f9d0c4] text-black";
    default:
      return "bg-muted text-muted-foreground";
  }
}
