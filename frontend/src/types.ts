export type AgentKind = "claude-code" | "codex" | "opencode";
export type ReasoningLevel = "low" | "normal" | "high" | "extended";

export type IssueState =
  | "gravelord/todo"
  | "gravelord/in-progress"
  | "gravelord/human-review"
  | "gravelord/rework"
  | "gravelord/done"
  | "unlabelled";

export interface Repo {
  id: string;
  owner: string;
  name: string;
  path: string;
  default_branch: string;
  agent: AgentKind | null;
  running: number;
  retrying: number;
}

export interface TokenCounts {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface RunningIssue {
  repo_id: string;
  issue_id: string;
  issue_identifier: string;
  state: IssueState;
  agent_kind: AgentKind | null;
  model: string | null;
  reasoning_level: ReasoningLevel | null;
  session_id: string | null;
  turn_count: number;
  last_event: string;
  last_event_at: string;
  started_at: string;
  tokens: TokenCounts;
}

export interface RetryingIssue {
  repo_id: string;
  issue_id: string;
  issue_identifier: string;
  attempt: number;
  due_in_seconds: number;
  error: string | null;
}

export interface StatusSnapshot {
  started_at: string;
  uptime_seconds: number;
  max_concurrent: number;
  concurrency_used: number;
  counts: { running: number; retrying: number; completed: number };
  repos: Repo[];
  running: RunningIssue[];
  retrying: RetryingIssue[];
  totals: TokenCounts & { seconds_running: number };
}

export interface StreamEvent {
  event: string;
  repo_id: string | null;
  issue_id: string | null;
  issue_identifier: string | null;
  timestamp: string;
  data: Record<string, unknown>;
}

export interface TriggerBody {
  agent_kind?: AgentKind;
  model?: string;
  reasoning_level?: ReasoningLevel;
}
