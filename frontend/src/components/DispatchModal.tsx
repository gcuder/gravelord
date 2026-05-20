import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogClose,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { AGENT_OPTIONS, AGENT_KINDS, DEFAULT_REASONING } from "@/lib/agents";
import { api } from "@/lib/api";
import type { AgentKind, IssueSettingsPatch, ReasoningLevel } from "@/types";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  defaultIdentifier?: string;
};

export function DispatchModal({
  open,
  onOpenChange,
  defaultIdentifier = "",
}: Props) {
  const [identifier, setIdentifier] = useState(defaultIdentifier);
  const [agentKind, setAgentKind] = useState<AgentKind>("claude-code");
  const [model, setModel] = useState<string>("");
  const [reasoning, setReasoning] = useState<ReasoningLevel>(DEFAULT_REASONING);
  const [error, setError] = useState<string | null>(null);
  const [loadingPrefill, setLoadingPrefill] = useState(false);

  const options = AGENT_OPTIONS[agentKind];
  const modelOptions = options.models;
  const reasoningOptions = useMemo(() => options.reasoning, [options]);

  // Pre-fill from saved card settings whenever the modal opens (or the
  // identifier changes while open). Falls back to defaults on 404 / empty.
  useEffect(() => {
    if (!open) return;
    if (!identifier.includes("#")) return;
    let cancelled = false;
    setLoadingPrefill(true);
    api
      .issueSettings(identifier)
      .then((s) => {
        if (cancelled) return;
        if (s.agent_kind) setAgentKind(s.agent_kind);
        setModel(s.model ?? "");
        setReasoning(s.reasoning_level ?? DEFAULT_REASONING);
      })
      .catch(() => {
        /* leave defaults */
      })
      .finally(() => {
        if (!cancelled) setLoadingPrefill(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, identifier]);

  async function submit() {
    setError(null);
    try {
      // 1. Persist the choices to the card. Send explicit nulls for empty
      // values so previously-saved settings get cleared as expected.
      const patch: IssueSettingsPatch = {
        agent_kind: agentKind,
        model: model.trim() || null,
        reasoning_level: reasoning,
      };
      await api.patchIssueSettings(identifier, patch);
      // 2. Trigger — no payload override, orchestrator picks up the just-saved
      // settings via IssueSettingsStore.
      await api.triggerIssue(identifier);
      onOpenChange(false);
    } catch (err) {
      setError(String(err));
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Dispatch issue</DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-3">
          <label className="text-sm">
            <span className="mb-1 block text-muted-foreground">Identifier</span>
            <Input
              placeholder="owner/repo#42"
              value={identifier}
              onChange={(e) => setIdentifier(e.target.value)}
            />
          </label>

          <label className="text-sm">
            <span className="mb-1 block text-muted-foreground">Agent</span>
            <Select
              value={agentKind}
              onValueChange={(v) => {
                setAgentKind(v as AgentKind);
                setModel("");
                setReasoning(DEFAULT_REASONING);
              }}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {AGENT_KINDS.map((k) => (
                  <SelectItem key={k} value={k}>
                    {k}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>

          <label className="text-sm">
            <span className="mb-1 block text-muted-foreground">Model</span>
            {modelOptions === "freetext" ? (
              <Input
                placeholder="(optional) e.g. gpt-4o, gemma3:27b"
                value={model}
                onChange={(e) => setModel(e.target.value)}
              />
            ) : (
              <Select value={model} onValueChange={setModel}>
                <SelectTrigger>
                  <SelectValue placeholder="(default)" />
                </SelectTrigger>
                <SelectContent>
                  {modelOptions.map((m) => (
                    <SelectItem key={m} value={m}>
                      {m}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </label>

          <label className="text-sm">
            <span className="mb-1 block text-muted-foreground">Reasoning</span>
            <Select
              value={reasoning}
              onValueChange={(v) => setReasoning(v as ReasoningLevel)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {reasoningOptions.map((r) => (
                  <SelectItem key={r} value={r}>
                    {r}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>

          {error && (
            <p className="text-sm text-destructive-foreground">{error}</p>
          )}
          <p className="text-[11px] text-muted-foreground">
            Saved to the card · retries reuse these settings
          </p>
        </div>

        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost">Cancel</Button>
          </DialogClose>
          <Button
            onClick={submit}
            disabled={!identifier.includes("#") || loadingPrefill}
          >
            Save &amp; Run
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
