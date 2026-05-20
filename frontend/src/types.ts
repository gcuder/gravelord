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

// ---------- Kanban board ----------

export type BoardBucket =
  | "backlog"
  | "gravelord/todo"
  | "gravelord/in-progress"
  | "gravelord/human-review"
  | "gravelord/rework"
  | "gravelord/done";

// MoveTarget mirrors the backend's BoardTarget literal — the trailing
// "gravelord/" prefix is stripped because the move endpoint takes the
// short form. `gravelord/todo` → `"todo"`.
export type MoveTarget =
  | "backlog"
  | "todo"
  | "in-progress"
  | "human-review"
  | "rework"
  | "done";

export function bucketToMoveTarget(b: BoardBucket): MoveTarget {
  if (b === "backlog") return "backlog";
  return b.replace("gravelord/", "") as MoveTarget;
}

export interface BoardIssue {
  id: string;
  identifier: string; // owner/repo#number
  title: string;
  url: string | null;
  state: string;
  labels: string[];
  agent_kind: AgentKind | null;
  model: string | null;
  reasoning_level: ReasoningLevel | null;
}

export interface IssueSettings {
  agent_kind: AgentKind | null;
  model: string | null;
  reasoning_level: ReasoningLevel | null;
}

// PATCH /api/issues/.../settings: omitted field = unchanged, null = clear.
export type IssueSettingsPatch = Partial<IssueSettings>;

export interface BoardRepoEntry {
  owner: string;
  name: string;
  buckets: Record<BoardBucket, BoardIssue[]>;
  error?: string;
}

export interface BoardSnapshot {
  // Global view: keyed by repo_id.
  repos?: Record<string, BoardRepoEntry>;
  // Per-repo view: flat buckets.
  repo_id?: string;
  owner?: string;
  name?: string;
  buckets?: Record<BoardBucket, BoardIssue[]>;
}
