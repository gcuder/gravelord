import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  DndContext,
  DragEndEvent,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { KanbanColumn } from "@/components/KanbanColumn";
import { api } from "@/lib/api";
import { EventStream } from "@/lib/ws";
import {
  bucketToMoveTarget,
  type BoardBucket,
  type BoardIssue,
  type BoardSnapshot,
  type RetryingIssue,
  type RunningIssue,
  type StatusSnapshot,
} from "@/types";

type Props = {
  repoFilter: string | null;
  status: StatusSnapshot | null;
  onSelectIssue: (identifier: string) => void;
};

const COLUMNS: { id: BoardBucket; label: string }[] = [
  { id: "backlog", label: "Backlog" },
  { id: "gravelord/todo", label: "Todo" },
  { id: "gravelord/in-progress", label: "In Progress" },
  { id: "gravelord/human-review", label: "Review" },
  { id: "gravelord/rework", label: "Rework" },
  { id: "gravelord/done", label: "Done" },
];

type CardEntry = { issue: BoardIssue; repoLabel?: string };

function emptyColumns(): Record<BoardBucket, CardEntry[]> {
  return COLUMNS.reduce(
    (acc, c) => {
      acc[c.id] = [];
      return acc;
    },
    {} as Record<BoardBucket, CardEntry[]>,
  );
}

function flatten(snap: BoardSnapshot | null): Record<BoardBucket, CardEntry[]> {
  const out = emptyColumns();
  if (!snap) return out;
  if (snap.buckets) {
    for (const c of COLUMNS) {
      out[c.id] = (snap.buckets[c.id] ?? []).map((issue) => ({ issue }));
    }
    return out;
  }
  if (snap.repos) {
    for (const [, entry] of Object.entries(snap.repos)) {
      if (entry.error || !entry.buckets) continue;
      const repoLabel = `${entry.owner}/${entry.name}`;
      for (const c of COLUMNS) {
        for (const issue of entry.buckets[c.id] ?? []) {
          out[c.id].push({ issue, repoLabel });
        }
      }
    }
  }
  return out;
}

// Optimistic local move: mutate a shallow copy of the snapshot.
function moveLocal(
  snap: BoardSnapshot | null,
  identifier: string,
  to: BoardBucket,
): BoardSnapshot | null {
  if (!snap) return snap;
  const removeFrom = (
    buckets: Record<BoardBucket, BoardIssue[]>,
  ): BoardIssue | null => {
    for (const c of COLUMNS) {
      const list = buckets[c.id] ?? [];
      const i = list.findIndex((iss) => iss.identifier === identifier);
      if (i >= 0) {
        const [item] = list.splice(i, 1);
        return item;
      }
    }
    return null;
  };
  if (snap.buckets) {
    const copy: BoardSnapshot = {
      ...snap,
      buckets: Object.fromEntries(
        COLUMNS.map((c) => [c.id, [...(snap.buckets![c.id] ?? [])]]),
      ) as Record<BoardBucket, BoardIssue[]>,
    };
    const item = removeFrom(copy.buckets!);
    if (item) {
      copy.buckets![to].unshift({ ...item, state: to });
    }
    return copy;
  }
  if (snap.repos) {
    const reposCopy: typeof snap.repos = {};
    for (const [repoId, entry] of Object.entries(snap.repos)) {
      reposCopy[repoId] = {
        ...entry,
        buckets: Object.fromEntries(
          COLUMNS.map((c) => [c.id, [...(entry.buckets?.[c.id] ?? [])]]),
        ) as Record<BoardBucket, BoardIssue[]>,
      };
    }
    for (const repoId of Object.keys(reposCopy)) {
      const item = removeFrom(reposCopy[repoId].buckets);
      if (item) {
        reposCopy[repoId].buckets[to].unshift({ ...item, state: to });
        break;
      }
    }
    return { ...snap, repos: reposCopy };
  }
  return snap;
}

export function Kanban({ repoFilter, status, onSelectIssue }: Props) {
  const [snapshot, setSnapshot] = useState<BoardSnapshot | null>(null);
  const [loading, setLoading] = useState(false);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
  );

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const s = await api.board(repoFilter ?? undefined);
      setSnapshot(s);
    } catch (err) {
      console.error("board fetch failed", err);
    } finally {
      setLoading(false);
    }
  }, [repoFilter]);

  useEffect(() => {
    void reload();
    const t = window.setInterval(reload, 15_000);
    return () => window.clearInterval(t);
  }, [reload]);

  // WS-driven refresh, debounced.
  const debounceRef = useRef<number | null>(null);
  useEffect(() => {
    const stream = new EventStream();
    stream.connect();
    const unsub = stream.subscribe((evt) => {
      const refreshing = new Set([
        "issue_moved",
        "label_changed",
        "worker_dispatched_resolved",
        "worker_finished",
        "worker_failed",
      ]);
      if (!refreshing.has(evt.event)) return;
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
      }
      debounceRef.current = window.setTimeout(() => {
        debounceRef.current = null;
        void reload();
      }, 500);
    });
    return () => {
      unsub();
      stream.close();
      if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
    };
  }, [reload]);

  const columns = useMemo(() => flatten(snapshot), [snapshot]);

  const runningByIdentifier = useMemo(() => {
    const m = new Map<string, RunningIssue>();
    if (status) for (const r of status.running) m.set(r.issue_identifier, r);
    return m;
  }, [status]);

  const retryingByIdentifier = useMemo(() => {
    const m = new Map<string, RetryingIssue>();
    if (status) for (const r of status.retrying) m.set(r.issue_identifier, r);
    return m;
  }, [status]);

  async function onDragEnd(evt: DragEndEvent) {
    const id = evt.active.id as string;
    const to = evt.over?.id as BoardBucket | undefined;
    if (!to) return;
    // Find current bucket; bail if same.
    let from: BoardBucket | null = null;
    for (const c of COLUMNS) {
      if (columns[c.id].some((e) => e.issue.identifier === id)) {
        from = c.id;
        break;
      }
    }
    if (from === to) return;

    // Optimistic
    setSnapshot((prev) => moveLocal(prev, id, to));

    const target = bucketToMoveTarget(to);
    try {
      await api.moveIssue(id, target);
    } catch (err) {
      const msg = String(err);
      if (msg.includes("confirm_required")) {
        if (
          window.confirm(
            "Move this issue out of Done? It will be re-opened in the new column.",
          )
        ) {
          try {
            await api.moveIssue(id, target, true);
            return;
          } catch {
            /* fall through to revert */
          }
        }
      }
      console.error("move failed", err);
      // Revert via re-fetch
      void reload();
    }
  }

  if (!snapshot && loading) {
    return (
      <p className="p-4 text-sm text-muted-foreground">Loading board…</p>
    );
  }

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragEnd={onDragEnd}
    >
      <div className="grid h-full grid-cols-6 gap-2 p-4">
        {COLUMNS.map((c) => (
          <KanbanColumn
            key={c.id}
            bucket={c.id}
            label={c.label}
            issues={columns[c.id]}
            runningByIdentifier={runningByIdentifier}
            retryingByIdentifier={retryingByIdentifier}
            onSelectIssue={onSelectIssue}
          />
        ))}
      </div>
    </DndContext>
  );
}
