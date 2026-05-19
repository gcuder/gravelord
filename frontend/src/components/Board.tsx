import { IssueCard } from "@/components/IssueCard";
import type { StatusSnapshot } from "@/types";

type Props = {
  status: StatusSnapshot;
  repoFilter: string | null;
  onSelect: (identifier: string) => void;
};

const COLUMNS = [
  { id: "running", label: "Running" },
  { id: "retrying", label: "Retrying" },
] as const;

export function Board({ status, repoFilter, onSelect }: Props) {
  const running = status.running.filter(
    (r) => !repoFilter || r.repo_id === repoFilter,
  );
  const retrying = status.retrying.filter(
    (r) => !repoFilter || r.repo_id === repoFilter,
  );

  return (
    <div className="grid h-full grid-cols-2 gap-4 p-4">
      {COLUMNS.map((col) => (
        <div key={col.id} className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {col.label} ({col.id === "running" ? running.length : retrying.length})
          </h3>
          <div className="flex flex-col gap-2 overflow-y-auto pr-1">
            {col.id === "running" &&
              running.map((iss) => (
                <IssueCard
                  key={iss.issue_id}
                  variant="running"
                  identifier={iss.issue_identifier}
                  agentKind={iss.agent_kind}
                  model={iss.model}
                  reasoningLevel={iss.reasoning_level}
                  turnCount={iss.turn_count}
                  tokens={iss.tokens}
                  onClick={() => onSelect(iss.issue_identifier)}
                />
              ))}
            {col.id === "retrying" &&
              retrying.map((iss) => (
                <IssueCard
                  key={iss.issue_id}
                  variant="retrying"
                  identifier={iss.issue_identifier}
                  retry={{
                    attempt: iss.attempt,
                    due_in_seconds: iss.due_in_seconds,
                    error: iss.error,
                  }}
                  onClick={() => onSelect(iss.issue_identifier)}
                />
              ))}
            {col.id === "running" && running.length === 0 && (
              <p className="text-xs text-muted-foreground">No active runs.</p>
            )}
            {col.id === "retrying" && retrying.length === 0 && (
              <p className="text-xs text-muted-foreground">
                Nothing in retry backoff.
              </p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
