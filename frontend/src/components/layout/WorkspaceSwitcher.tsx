import { useEffect, useRef, useState } from "react";
import { Check, ChevronsUpDown, Database, Plus } from "lucide-react";

import { cn } from "@/lib/cn";
import { pluralize } from "@/lib/format";
import { useWorkspaces } from "@/state/workspace";
import { useToast } from "@/state/toast";
import { Button } from "@/components/ui/Button";
import { Input, Textarea } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { Spinner } from "@/components/ui/Feedback";

/**
 * Workspace switcher.
 *
 * Sits at the top of the sidebar because the active workspace is the most
 * consequential piece of context in the app: it decides which documents can be
 * retrieved at all. Making it visible at all times is a deliberate safety choice
 * - a user should never be unsure which knowledge base they are asking.
 */

const COLOR_SWATCHES: { name: string; className: string }[] = [
  { name: "violet", className: "bg-brand" },
  { name: "teal", className: "bg-accent" },
  { name: "blue", className: "bg-info" },
  { name: "green", className: "bg-positive" },
  { name: "amber", className: "bg-caution" },
  { name: "red", className: "bg-negative" },
];

function swatchClass(color: string): string {
  return COLOR_SWATCHES.find((swatch) => swatch.name === color)?.className ?? "bg-brand";
}

export function WorkspaceSwitcher({
  onNavigate,
  compact = false,
}: {
  onNavigate?: () => void;
  /**
   * Icon-only trigger for the collapsed sidebar.
   *
   * Collapsing must not silently remove the ability to change workspace - that is
   * a security-relevant control, because workspaces are the isolation boundary.
   * The dropdown is identical; only the trigger shrinks.
   */
  compact?: boolean;
}) {
  const { workspaces, activeId, active, select, create, loading } = useWorkspaces();
  const toast = useToast();

  const [open, setOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [color, setColor] = useState("violet");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const containerRef = useRef<HTMLDivElement>(null);

  // Close on outside click. Without this the menu stays open when the user
  // clicks anywhere else, which feels broken.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  async function handleCreate() {
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Give the workspace a name.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const created = await create(trimmed, description.trim(), color);
      toast.success(`Workspace "${created.name}" created`, "Upload a document to start indexing.");
      setCreateOpen(false);
      setName("");
      setDescription("");
      setColor("violet");
      onNavigate?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not create the workspace.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-haspopup="listbox"
        title={compact ? active?.name ?? "Select a workspace" : undefined}
        className={cn(
          "flex w-full items-center gap-2.5 rounded-lg border py-2 text-left transition-all duration-150",
          compact ? "justify-center px-0" : "px-2.5",
          open
            ? "border-brand/40 bg-brand/5"
            : "border-line bg-sunken hover:border-line-strong",
        )}
      >
        <span
          className={cn(
            "flex h-6 w-6 shrink-0 items-center justify-center rounded-md",
            active ? swatchClass(active.color) : "bg-line-strong",
          )}
        >
          <Database className="h-3.5 w-3.5 text-white" />
        </span>

        {compact ? (
          <span className="sr-only">{active?.name ?? "Select a workspace"}</span>
        ) : null}

        <span className={cn("min-w-0 flex-1", compact && "hidden")}>
          <span className="block truncate text-xs font-semibold text-ink">
            {active?.name ?? (loading ? "Loading…" : "No workspace")}
          </span>
          <span className="block truncate text-2xs text-faint">
            {active?.stats
              ? pluralize(active.stats.documents, "document")
              : "Select or create one"}
          </span>
        </span>

        {compact ? null : (
          <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 text-faint" />
        )}
      </button>

      {open ? (
        <div
          role="listbox"
          className={cn(
            "absolute left-0 top-full z-50 mt-1.5 animate-slide-down rounded-lg border border-line bg-raised p-1 shadow-pop",
            compact ? "w-60" : "right-0",
          )}
        >
          <div className="max-h-64 overflow-y-auto scrollbar-thin">
            {workspaces.length === 0 ? (
              <p className="px-2.5 py-3 text-2xs text-faint">
                You have no workspaces yet.
              </p>
            ) : (
              workspaces.map((workspace) => (
                <button
                  key={workspace.id}
                  type="button"
                  role="option"
                  aria-selected={workspace.id === activeId}
                  onClick={() => {
                    select(workspace.id);
                    setOpen(false);
                    onNavigate?.();
                  }}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left transition-colors",
                    workspace.id === activeId ? "bg-brand/10" : "hover:bg-sunken",
                  )}
                >
                  <span
                    className={cn(
                      "h-2 w-2 shrink-0 rounded-full",
                      swatchClass(workspace.color),
                    )}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-2xs font-medium text-ink">
                      {workspace.name}
                    </span>
                    <span className="block truncate text-2xs text-faint">
                      {workspace.stats
                        ? `${pluralize(workspace.stats.documents, "doc")} · ${pluralize(
                            workspace.stats.chunks,
                            "chunk",
                          )}`
                        : "—"}
                    </span>
                  </span>
                  {workspace.id === activeId ? (
                    <Check className="h-3.5 w-3.5 shrink-0 text-brand" />
                  ) : null}
                </button>
              ))
            )}
          </div>

          <div className="mt-1 border-t border-line pt-1">
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                setCreateOpen(true);
              }}
              className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-2xs font-medium text-brand transition-colors hover:bg-brand/10"
            >
              <Plus className="h-3.5 w-3.5" />
              New workspace
            </button>
          </div>
        </div>
      ) : null}

      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Create a workspace"
        description="A workspace is an isolated knowledge base. Documents and vectors never cross between workspaces."
        size="sm"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" size="sm" loading={saving} onClick={() => void handleCreate()}>
              Create workspace
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Input
            label="Name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Cloud Computing — Semester 5"
            error={error}
            autoFocus
            maxLength={200}
          />
          <Textarea
            label="Description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="Lecture notes, slides and reference PDFs for the cloud computing module."
            rows={3}
            maxLength={2000}
            hint="Optional, but it helps you tell workspaces apart later."
          />
          <div>
            <p className="mb-1.5 text-xs font-medium text-ink">Colour</p>
            <div className="flex flex-wrap gap-2">
              {COLOR_SWATCHES.map((swatch) => (
                <button
                  key={swatch.name}
                  type="button"
                  aria-label={swatch.name}
                  aria-pressed={color === swatch.name}
                  onClick={() => setColor(swatch.name)}
                  className={cn(
                    "h-6 w-6 rounded-full transition-transform",
                    swatch.className,
                    color === swatch.name
                      ? "ring-2 ring-ink ring-offset-2 ring-offset-raised"
                      : "hover:scale-110",
                  )}
                />
              ))}
            </div>
          </div>
          {saving ? (
            <p className="flex items-center gap-2 text-2xs text-muted">
              <Spinner size={12} /> Creating…
            </p>
          ) : null}
        </div>
      </Modal>
    </div>
  );
}
