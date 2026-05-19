import { useMemo, useState } from "react";
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
import type { AgentKind, ReasoningLevel } from "@/types";

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

  const options = AGENT_OPTIONS[agentKind];
  const modelOptions = options.models;
  const reasoningOptions = useMemo(() => options.reasoning, [options]);

  async function submit() {
    setError(null);
    try {
      await api.triggerIssue(identifier, {
        agent_kind: agentKind,
        ...(model.trim() ? { model: model.trim() } : {}),
        ...(reasoning !== DEFAULT_REASONING ? { reasoning_level: reasoning } : {}),
      });
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
        </div>

        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost">Cancel</Button>
          </DialogClose>
          <Button onClick={submit} disabled={!identifier.includes("#")}>
            Dispatch
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
