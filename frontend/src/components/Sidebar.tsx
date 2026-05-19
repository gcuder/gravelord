import { useState } from "react";
import { Plus, FolderGit2, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  DialogClose,
} from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Repo } from "@/types";

type Props = {
  repos: Repo[];
  selectedRepoId: string | null;
  onSelect: (repoId: string | null) => void;
  onChange: () => void;
};

export function Sidebar({ repos, selectedRepoId, onSelect, onChange }: Props) {
  const [adding, setAdding] = useState(false);
  const [path, setPath] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    try {
      await api.addRepo({ path });
      setPath("");
      setAdding(false);
      onChange();
    } catch (err) {
      setError(String(err));
    }
  }

  async function remove(repoId: string) {
    if (!confirm(`Unregister ${repoId}? Running issues will be cancelled.`)) {
      return;
    }
    try {
      await api.removeRepo(repoId);
      if (selectedRepoId === repoId) onSelect(null);
      onChange();
    } catch (err) {
      alert(String(err));
    }
  }

  return (
    <aside className="flex h-full w-64 flex-col border-r border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <span className="text-sm font-semibold tracking-wide">Gravelord</span>
        <Dialog open={adding} onOpenChange={setAdding}>
          <DialogTrigger asChild>
            <Button size="icon" variant="ghost" aria-label="Add repo">
              <Plus className="h-4 w-4" />
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Add a repository</DialogTitle>
              <DialogDescription>
                Path to a local git checkout. Owner, name, and default branch
                are read from git.
              </DialogDescription>
            </DialogHeader>
            <Input
              placeholder="/Users/you/workspace/repo"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              autoFocus
            />
            {error && (
              <p className="text-sm text-destructive-foreground">{error}</p>
            )}
            <DialogFooter>
              <DialogClose asChild>
                <Button variant="ghost">Cancel</Button>
              </DialogClose>
              <Button disabled={!path.trim()} onClick={submit}>
                Add
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      <button
        onClick={() => onSelect(null)}
        className={cn(
          "border-b border-border px-4 py-2 text-left text-sm transition hover:bg-accent",
          selectedRepoId === null && "bg-accent",
        )}
      >
        All repos
      </button>

      <div className="flex-1 overflow-y-auto">
        {repos.map((repo) => (
          <div
            key={repo.id}
            className={cn(
              "group flex items-center justify-between border-b border-border px-3 py-2 text-sm transition hover:bg-accent",
              selectedRepoId === repo.id && "bg-accent",
            )}
          >
            <button
              onClick={() => onSelect(repo.id)}
              className="flex flex-1 items-center gap-2 truncate text-left"
              title={`${repo.owner}/${repo.name}`}
            >
              <FolderGit2 className="h-4 w-4 shrink-0 opacity-70" />
              <span className="truncate">
                {repo.owner}/{repo.name}
              </span>
              {repo.running > 0 && (
                <span className="ml-auto rounded bg-primary/20 px-1.5 text-xs">
                  {repo.running}
                </span>
              )}
            </button>
            <button
              onClick={() => remove(repo.id)}
              className="ml-2 opacity-0 transition group-hover:opacity-100"
              aria-label={`Remove ${repo.id}`}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
        {repos.length === 0 && (
          <p className="px-4 py-3 text-xs text-muted-foreground">
            No repos registered. Click + to add one.
          </p>
        )}
      </div>
    </aside>
  );
}
