import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { EventStream } from "@/lib/ws";
import { agentBadgeColor } from "@/lib/agents";
import type { StreamEvent } from "@/types";

type Props = {
  identifier: string;
  onClose: () => void;
};

export function IssueDetail({ identifier, onClose }: Props) {
  const [entries, setEntries] = useState<StreamEvent[]>([]);
  const logRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .issueLogs(identifier, 200)
      .then((data) => {
        if (!cancelled) setEntries(data.entries as StreamEvent[]);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [identifier]);

  useEffect(() => {
    const stream = new EventStream();
    stream.connect();
    const unsub = stream.subscribe((evt) => {
      if (evt.issue_identifier !== identifier) return;
      setEntries((prev) => [...prev.slice(-499), evt]);
    });
    return () => {
      unsub();
      stream.close();
    };
  }, [identifier]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [entries.length]);

  async function killNow() {
    if (!confirm(`Kill ${identifier}?`)) return;
    try {
      await api.killIssue(identifier);
    } catch (err) {
      alert(String(err));
    }
  }

  const headerAgent = entries
    .map((e) => e.data?.agent_kind)
    .filter(Boolean)
    .pop() as string | undefined;

  return (
    <div className="flex h-full flex-col border-l border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm">{identifier}</span>
          {headerAgent && (
            <Badge className={agentBadgeColor(headerAgent as any)}>
              {headerAgent}
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="destructive" onClick={killNow}>
            Kill
          </Button>
          <Button size="icon" variant="ghost" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>
      <div
        ref={logRef}
        className="flex-1 overflow-y-auto p-3 font-mono text-xs leading-relaxed"
      >
        {entries.map((e, i) => (
          <div key={i} className="border-b border-border/40 py-1">
            <span className="text-muted-foreground">
              {new Date(e.timestamp).toLocaleTimeString()}
            </span>{" "}
            <span className="font-semibold">{e.event}</span>{" "}
            <span className="text-muted-foreground">
              {JSON.stringify(e.data)}
            </span>
          </div>
        ))}
        {entries.length === 0 && (
          <p className="text-muted-foreground">No events yet.</p>
        )}
      </div>
    </div>
  );
}
