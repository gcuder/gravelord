import { useDroppable } from "@dnd-kit/core";
import { KanbanCard } from "@/components/KanbanCard";
import { cn } from "@/lib/utils";
import type {
  BoardBucket,
  BoardIssue,
  RetryingIssue,
  RunningIssue,
} from "@/types";

type Props = {
  bucket: BoardBucket;
  label: string;
  issues: { issue: BoardIssue; repoLabel?: string }[];
  runningByIdentifier: Map<string, RunningIssue>;
  retryingByIdentifier: Map<string, RetryingIssue>;
  onSelectIssue: (identifier: string) => void;
};

const BUCKET_TINT: Record<BoardBucket, string> = {
  backlog: "border-t-zinc-500",
  "gravelord/todo": "border-t-emerald-500",
  "gravelord/in-progress": "border-t-blue-500",
  "gravelord/human-review": "border-t-amber-500",
  "gravelord/rework": "border-t-orange-500",
  "gravelord/done": "border-t-violet-500",
};

export function KanbanColumn({
  bucket,
  label,
  issues,
  runningByIdentifier,
  retryingByIdentifier,
  onSelectIssue,
}: Props) {
  const { setNodeRef, isOver } = useDroppable({ id: bucket });

  return (
    <div
      ref={setNodeRef}
      className={cn(
        "flex h-full flex-col rounded-md border border-border border-t-4 bg-card/40 transition",
        BUCKET_TINT[bucket],
        isOver && "ring-2 ring-ring",
      )}
    >
      <div className="flex items-center justify-between border-b border-border px-2 py-1.5">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
        <span className="text-xs text-muted-foreground">{issues.length}</span>
      </div>
      <div className="flex-1 space-y-1.5 overflow-y-auto p-2">
        {issues.map(({ issue, repoLabel }) => (
          <KanbanCard
            key={issue.id}
            issue={issue}
            repoLabel={repoLabel}
            running={runningByIdentifier.get(issue.identifier)}
            retrying={retryingByIdentifier.get(issue.identifier)}
            onClick={() => onSelectIssue(issue.identifier)}
          />
        ))}
        {issues.length === 0 && (
          <p className="px-1 py-2 text-[10px] text-muted-foreground">
            (empty)
          </p>
        )}
      </div>
    </div>
  );
}
