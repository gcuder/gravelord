import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { agentBadgeColor } from "@/lib/agents";
import type { RunningIssue, RetryingIssue } from "@/types";

type Variant = "running" | "retrying";

type Props = {
  variant: Variant;
  identifier: string;
  agentKind?: RunningIssue["agent_kind"];
  model?: RunningIssue["model"];
  reasoningLevel?: RunningIssue["reasoning_level"];
  turnCount?: number;
  tokens?: RunningIssue["tokens"];
  retry?: { attempt: number; due_in_seconds: number; error: string | null };
  onClick?: () => void;
};

export function IssueCard({
  variant,
  identifier,
  agentKind,
  model,
  reasoningLevel,
  turnCount,
  tokens,
  retry,
  onClick,
}: Props) {
  return (
    <Card
      onClick={onClick}
      className="cursor-pointer transition hover:border-ring"
    >
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2">
          <span className="truncate font-mono text-sm">{identifier}</span>
        </CardTitle>
        <div className="mt-1 flex flex-wrap gap-1.5">
          {agentKind && (
            <Badge className={agentBadgeColor(agentKind)}>{agentKind}</Badge>
          )}
          {model && (
            <Badge className="bg-secondary text-secondary-foreground">
              {model}
            </Badge>
          )}
          {reasoningLevel && reasoningLevel !== "normal" && (
            <Badge className="bg-muted text-muted-foreground">
              {reasoningLevel}
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {variant === "running" && (
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>turn {turnCount ?? 0}</span>
            {tokens && (
              <span>
                {tokens.input_tokens.toLocaleString()}↑ /{" "}
                {tokens.output_tokens.toLocaleString()}↓
              </span>
            )}
          </div>
        )}
        {variant === "retrying" && retry && (
          <div className="text-xs text-muted-foreground">
            <div>attempt #{retry.attempt}</div>
            <div>retry in {Math.ceil(retry.due_in_seconds)}s</div>
            {retry.error && <div className="truncate">{retry.error}</div>}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
