import { useEffect, useState } from "react";
import { useDraggable } from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import { Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { CardSettingsPopover } from "@/components/CardSettingsPopover";
import { agentBadgeColor } from "@/lib/agents";
import { cn } from "@/lib/utils";
import type {
  BoardIssue,
  IssueSettings,
  RetryingIssue,
  RunningIssue,
} from "@/types";

type Props = {
  issue: BoardIssue;
  repoLabel?: string; // shown only in global view
  running?: RunningIssue;
  retrying?: RetryingIssue;
  onClick?: () => void;
};

export function KanbanCard({
  issue,
  repoLabel,
  running,
  retrying,
  onClick,
}: Props) {
  const { attributes, listeners, setNodeRef, transform, isDragging } =
    useDraggable({ id: issue.identifier });

  const style: React.CSSProperties = {
    transform: CSS.Translate.toString(transform),
    opacity: isDragging ? 0.4 : 1,
    cursor: isDragging ? "grabbing" : "grab",
  };

  // Mirror of saved settings so the popover can update the card optimistically
  // before the next board refresh. Sync from props whenever the board snapshot
  // delivers new values.
  const [savedSettings, setSavedSettings] = useState<IssueSettings>({
    agent_kind: issue.agent_kind,
    model: issue.model,
    reasoning_level: issue.reasoning_level,
  });
  useEffect(() => {
    setSavedSettings({
      agent_kind: issue.agent_kind,
      model: issue.model,
      reasoning_level: issue.reasoning_level,
    });
  }, [issue.agent_kind, issue.model, issue.reasoning_level]);

  // Prefer live overlay agent_kind (running) over the saved/label one.
  const agentKind = running?.agent_kind ?? savedSettings.agent_kind;
  const configuredModel = running?.model ?? savedSettings.model;
  const configuredReasoning =
    running?.reasoning_level ?? savedSettings.reasoning_level;
  const number = issue.identifier.split("#")[1];

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...listeners}
      {...attributes}
      className={cn(
        "select-none rounded-md border border-border bg-card p-2 text-xs shadow-sm transition",
        "hover:border-ring",
        running && "ring-1 ring-emerald-500/40",
        retrying && "ring-1 ring-amber-500/40",
      )}
      onClick={onClick}
    >
      <div className="mb-1 flex items-center gap-1.5">
        {running && (
          <Loader2 className="h-3 w-3 animate-spin text-emerald-500" />
        )}
        <span className="font-mono text-[10px] text-muted-foreground">
          #{number}
        </span>
        {repoLabel && (
          <Badge className="bg-muted text-[10px] text-muted-foreground">
            {repoLabel}
          </Badge>
        )}
        <div className="ml-auto">
          <CardSettingsPopover
            identifier={issue.identifier}
            initial={savedSettings}
            onChange={setSavedSettings}
          />
        </div>
      </div>
      <div className="line-clamp-2 font-medium text-foreground">
        {issue.title}
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-1">
        {agentKind && (
          <Badge className={cn("text-[10px]", agentBadgeColor(agentKind))}>
            {agentKind}
          </Badge>
        )}
        {configuredModel && (
          <Badge className="bg-secondary text-[10px] text-secondary-foreground">
            {configuredModel}
          </Badge>
        )}
        {configuredReasoning && configuredReasoning !== "normal" && (
          <Badge className="bg-muted text-[10px] text-muted-foreground">
            {configuredReasoning}
          </Badge>
        )}
      </div>
      {running && (
        <div className="mt-1 flex justify-between text-[10px] text-muted-foreground">
          <span>turn {running.turn_count}</span>
          <span>
            {running.tokens.input_tokens.toLocaleString()}↑ /{" "}
            {running.tokens.output_tokens.toLocaleString()}↓
          </span>
        </div>
      )}
      {retrying && (
        <div className="mt-1 text-[10px] text-amber-500/80">
          retry #{retrying.attempt} in {Math.ceil(retrying.due_in_seconds)}s
        </div>
      )}
    </div>
  );
}
