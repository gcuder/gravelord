import { useCallback, useEffect, useMemo, useState } from "react";
import { Send } from "lucide-react";
import { Sidebar } from "@/components/Sidebar";
import { Kanban } from "@/components/Kanban";
import { IssueDetail } from "@/components/IssueDetail";
import { DispatchModal } from "@/components/DispatchModal";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { EventStream } from "@/lib/ws";
import type { StatusSnapshot } from "@/types";

export default function App() {
  const [status, setStatus] = useState<StatusSnapshot | null>(null);
  const [selectedRepoId, setSelectedRepoId] = useState<string | null>(null);
  const [selectedIssue, setSelectedIssue] = useState<string | null>(null);
  const [dispatchOpen, setDispatchOpen] = useState(false);

  const reload = useCallback(async () => {
    try {
      const s = await api.status();
      setStatus(s);
    } catch (err) {
      console.error("status fetch failed", err);
    }
  }, []);

  useEffect(() => {
    void reload();
    const t = window.setInterval(reload, 5000);
    return () => window.clearInterval(t);
  }, [reload]);

  useEffect(() => {
    const stream = new EventStream();
    stream.connect();
    const unsub = stream.subscribe(() => {
      void reload();
    });
    return () => {
      unsub();
      stream.close();
    };
  }, [reload]);

  const repos = useMemo(() => status?.repos ?? [], [status]);

  return (
    <div className="flex h-screen w-screen bg-background text-foreground">
      <Sidebar
        repos={repos}
        selectedRepoId={selectedRepoId}
        onSelect={setSelectedRepoId}
        onChange={reload}
      />
      <div className="flex flex-1 flex-col">
        <div className="flex items-center justify-between border-b border-border px-4 py-2">
          <span className="text-sm text-muted-foreground">
            {status
              ? `${status.counts.running} running · ${status.counts.retrying} retrying · ${status.counts.completed} completed`
              : "loading…"}
          </span>
          <Button size="sm" onClick={() => setDispatchOpen(true)}>
            <Send className="mr-2 h-4 w-4" /> Dispatch
          </Button>
        </div>
        <div className="flex flex-1 overflow-hidden">
          <div className="flex-1 overflow-hidden">
            <Kanban
              repoFilter={selectedRepoId}
              status={status}
              onSelectIssue={setSelectedIssue}
            />
          </div>
          {selectedIssue && (
            <div className="w-[28rem]">
              <IssueDetail
                identifier={selectedIssue}
                onClose={() => setSelectedIssue(null)}
              />
            </div>
          )}
        </div>
      </div>
      <DispatchModal open={dispatchOpen} onOpenChange={setDispatchOpen} />
    </div>
  );
}
