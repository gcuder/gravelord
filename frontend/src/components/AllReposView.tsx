import { IssueCard } from "@/components/IssueCard";
import type { StatusSnapshot } from "@/types";

type Props = {
  status: StatusSnapshot;
  onSelect: (identifier: string) => void;
};

export function AllReposView({ status, onSelect }: Props) {
  const repoById = new Map(status.repos.map((r) => [r.id, r]));
  const all = [
    ...status.running.map((r) => ({ kind: "running" as const, item: r })),
    ...status.retrying.map((r) => ({ kind: "retrying" as const, item: r })),
  ];

  return (
    <div className="flex h-full flex-col gap-2 overflow-y-auto p-4">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        All active runs ({all.length})
      </h3>
      {all.length === 0 && (
        <p className="text-xs text-muted-foreground">No active runs anywhere.</p>
      )}
      {all.map((row) => {
        const repo = repoById.get(row.item.repo_id);
        return (
          <div key={`${row.kind}:${row.item.issue_id}`} className="flex flex-col gap-1">
            {repo && (
              <span className="text-xs text-muted-foreground">
                {repo.owner}/{repo.name}
              </span>
            )}
            {row.kind === "running" ? (
              <IssueCard
                variant="running"
                identifier={row.item.issue_identifier}
                agentKind={row.item.agent_kind}
                model={row.item.model}
                reasoningLevel={row.item.reasoning_level}
                turnCount={row.item.turn_count}
                tokens={row.item.tokens}
                onClick={() => onSelect(row.item.issue_identifier)}
              />
            ) : (
              <IssueCard
                variant="retrying"
                identifier={row.item.issue_identifier}
                retry={{
                  attempt: row.item.attempt,
                  due_in_seconds: row.item.due_in_seconds,
                  error: row.item.error,
                }}
                onClick={() => onSelect(row.item.issue_identifier)}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}
