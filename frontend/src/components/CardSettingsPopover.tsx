import { useEffect, useMemo, useState } from "react";
import { Settings2, X } from "lucide-react";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { AGENT_KINDS, AGENT_OPTIONS } from "@/lib/agents";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  AgentKind,
  IssueSettings,
  IssueSettingsPatch,
  ReasoningLevel,
} from "@/types";

type Props = {
  identifier: string;
  initial: IssueSettings;
  onChange: (next: IssueSettings) => void;
};

function reasoningAllowed(
  agent: AgentKind | null,
  level: ReasoningLevel | null,
): boolean {
  if (!level) return true;
  if (!agent) return true;
  return (AGENT_OPTIONS[agent].reasoning as readonly string[]).includes(level);
}

function modelAllowed(
  agent: AgentKind | null,
  model: string | null,
): boolean {
  if (!model) return true;
  if (!agent) return true;
  const opts = AGENT_OPTIONS[agent].models;
  if (opts === "freetext") return true;
  return (opts as readonly string[]).includes(model);
}

export function CardSettingsPopover({ identifier, initial, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [settings, setSettings] = useState<IssueSettings>(initial);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Keep local state in sync if the card receives updated settings via
  // board refresh (e.g. WS-driven reload after DispatchModal saves).
  useEffect(() => {
    setSettings(initial);
  }, [initial.agent_kind, initial.model, initial.reasoning_level]);

  const agent = settings.agent_kind;
  const opts = agent ? AGENT_OPTIONS[agent] : null;

  const modelOptions = useMemo(
    () => (opts ? opts.models : null),
    [opts],
  );
  const reasoningOptions = useMemo(
    () => (opts ? opts.reasoning : null),
    [opts],
  );

  async function apply(patch: IssueSettingsPatch, optimistic: IssueSettings) {
    setError(null);
    setSaving(true);
    const previous = settings;
    setSettings(optimistic);
    try {
      const saved = await api.patchIssueSettings(identifier, patch);
      setSettings(saved);
      onChange(saved);
    } catch (err) {
      setSettings(previous);
      setError(String(err));
    } finally {
      setSaving(false);
    }
  }

  async function setAgent(next: AgentKind) {
    // Reconcile: if model / reasoning no longer fit the new agent, clear them
    // in the same PATCH so the user never sees a stale invalid combo.
    const clearedModel = !modelAllowed(next, settings.model);
    const clearedReasoning = !reasoningAllowed(next, settings.reasoning_level);
    const patch: IssueSettingsPatch = { agent_kind: next };
    const optimistic: IssueSettings = { ...settings, agent_kind: next };
    if (clearedModel) {
      patch.model = null;
      optimistic.model = null;
    }
    if (clearedReasoning) {
      patch.reasoning_level = null;
      optimistic.reasoning_level = null;
    }
    await apply(patch, optimistic);
  }

  async function setModel(next: string | null) {
    await apply({ model: next }, { ...settings, model: next });
  }

  async function setReasoning(next: ReasoningLevel | null) {
    await apply(
      { reasoning_level: next },
      { ...settings, reasoning_level: next },
    );
  }

  const hasAny =
    settings.agent_kind || settings.model || settings.reasoning_level;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label="Issue settings"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => e.stopPropagation()}
          className={cn(
            "rounded p-0.5 text-muted-foreground transition hover:bg-muted hover:text-foreground",
            hasAny && "text-foreground",
          )}
        >
          <Settings2 className="h-3.5 w-3.5" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        onPointerDown={(e) => e.stopPropagation()}
        onClick={(e) => e.stopPropagation()}
        className="space-y-3"
      >
        <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Per-issue settings
        </div>

        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Agent</label>
          <Select
            value={agent ?? ""}
            onValueChange={(v) => setAgent(v as AgentKind)}
          >
            <SelectTrigger>
              <SelectValue placeholder="(use default)" />
            </SelectTrigger>
            <SelectContent>
              {AGENT_KINDS.map((k) => (
                <SelectItem key={k} value={k}>
                  {k}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <label className="text-xs text-muted-foreground">Model</label>
            {settings.model && (
              <button
                type="button"
                onClick={() => setModel(null)}
                className="text-[10px] text-muted-foreground hover:text-foreground"
                aria-label="Clear model"
              >
                <X className="inline h-3 w-3" /> clear
              </button>
            )}
          </div>
          {modelOptions === null ? (
            <p className="text-[11px] italic text-muted-foreground">
              pick an agent first
            </p>
          ) : modelOptions === "freetext" ? (
            <Input
              placeholder="e.g. anthropic/claude-sonnet-4-6"
              value={settings.model ?? ""}
              onChange={(e) => setSettings({ ...settings, model: e.target.value || null })}
              onBlur={() => {
                if (settings.model !== initial.model) {
                  void setModel(settings.model);
                }
              }}
            />
          ) : (
            <Select
              value={settings.model ?? ""}
              onValueChange={(v) => setModel(v)}
            >
              <SelectTrigger>
                <SelectValue placeholder="(default)" />
              </SelectTrigger>
              <SelectContent>
                {(modelOptions as readonly string[]).map((m) => (
                  <SelectItem key={m} value={m}>
                    {m}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>

        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <label className="text-xs text-muted-foreground">Reasoning</label>
            {settings.reasoning_level && (
              <button
                type="button"
                onClick={() => setReasoning(null)}
                className="text-[10px] text-muted-foreground hover:text-foreground"
                aria-label="Clear reasoning"
              >
                <X className="inline h-3 w-3" /> clear
              </button>
            )}
          </div>
          {reasoningOptions === null ? (
            <p className="text-[11px] italic text-muted-foreground">
              pick an agent first
            </p>
          ) : (
            <Select
              value={settings.reasoning_level ?? ""}
              onValueChange={(v) => setReasoning(v as ReasoningLevel)}
            >
              <SelectTrigger>
                <SelectValue placeholder="(default)" />
              </SelectTrigger>
              <SelectContent>
                {reasoningOptions.map((r) => (
                  <SelectItem key={r} value={r}>
                    {r}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>

        {error && <p className="text-[11px] text-destructive">{error}</p>}
        {saving && (
          <p className="text-[10px] text-muted-foreground">saving…</p>
        )}
      </PopoverContent>
    </Popover>
  );
}
