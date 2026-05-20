import type {
  BoardSnapshot,
  IssueSettings,
  IssueSettingsPatch,
  MoveTarget,
  Repo,
  StatusSnapshot,
  TriggerBody,
} from "@/types";

async function json<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const res = await fetch(input, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    let detail: unknown;
    try {
      detail = await res.json();
    } catch {
      detail = await res.text();
    }
    throw new Error(`HTTP ${res.status}: ${JSON.stringify(detail)}`);
  }
  return (await res.json()) as T;
}

export const api = {
  status: () => json<StatusSnapshot>("/api/status"),
  repos: () => json<{ repos: Repo[] }>("/api/repos"),
  addRepo: (body: { path: string; id?: string; agent?: string }) =>
    json<{ repo: Repo }>("/api/repos", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  removeRepo: (repoId: string) =>
    json<{ removed: boolean; repo_id: string }>(
      `/api/repos/${encodeURIComponent(repoId)}`,
      { method: "DELETE" },
    ),
  issueDetail: (identifier: string) => {
    const [head, num] = identifier.split("#");
    const [owner, repo] = head.split("/");
    return json<unknown>(`/api/issues/${owner}/${repo}/${num}`);
  },
  issueLogs: (identifier: string, n = 100) => {
    const [head, num] = identifier.split("#");
    const [owner, repo] = head.split("/");
    return json<{ identifier: string; entries: unknown[] }>(
      `/api/issues/${owner}/${repo}/${num}/logs?n=${n}`,
    );
  },
  triggerIssue: (identifier: string, body?: TriggerBody) => {
    const [head, num] = identifier.split("#");
    const [owner, repo] = head.split("/");
    return json<{ queued: boolean; identifier: string }>(
      `/api/issues/${owner}/${repo}/${num}/trigger`,
      { method: "POST", body: JSON.stringify(body ?? {}) },
    );
  },
  killIssue: (identifier: string) => {
    const [head, num] = identifier.split("#");
    const [owner, repo] = head.split("/");
    return json<{ killed: boolean; identifier: string }>(
      `/api/issues/${owner}/${repo}/${num}/kill`,
      { method: "POST" },
    );
  },
  board: (repoId?: string) =>
    json<BoardSnapshot>(repoId ? `/api/board/${repoId}` : `/api/board`),
  moveIssue: (identifier: string, to: MoveTarget, confirm = false) => {
    const [head, num] = identifier.split("#");
    const [owner, repo] = head.split("/");
    return json<{ moved: boolean; identifier: string; to: MoveTarget }>(
      `/api/issues/${owner}/${repo}/${num}/move`,
      { method: "POST", body: JSON.stringify({ to, confirm }) },
    );
  },
  issueSettings: (identifier: string) => {
    const [head, num] = identifier.split("#");
    const [owner, repo] = head.split("/");
    return json<IssueSettings>(
      `/api/issues/${owner}/${repo}/${num}/settings`,
    );
  },
  patchIssueSettings: (identifier: string, patch: IssueSettingsPatch) => {
    const [head, num] = identifier.split("#");
    const [owner, repo] = head.split("/");
    return json<IssueSettings>(
      `/api/issues/${owner}/${repo}/${num}/settings`,
      { method: "PATCH", body: JSON.stringify(patch) },
    );
  },
};
