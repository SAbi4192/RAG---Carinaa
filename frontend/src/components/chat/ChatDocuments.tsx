import { useEffect, useState } from "react";
import { BookOpen, Check, ChevronDown, FileText, Plus, X } from "lucide-react";

import { cn } from "@/lib/cn";
import type { ConversationScope, DocumentRecord } from "@/lib/types";
import { fileBadge, pluralize } from "@/lib/format";

/**
 * "This chat" — the documents scoped to the current conversation.
 *
 * COLLAPSED BY DEFAULT
 * --------------------
 * The full panel is informative, but as a permanent block above the composer it costs
 * a lot of vertical space for something that rarely changes. The default is now a
 * single compact line stating the scope, with the detail one click away.
 *
 * The collapsed form is not a summary of a summary: it carries the one fact that
 * matters - what the next answer will be based on - so a user who never expands it
 * still knows whether their question will search one document or the whole workspace.
 *
 * THE DISTINCTION THIS UI MUST NEVER BLUR
 * ---------------------------------------
 *   unchecking a document  ->  it stops being searched HERE (still in the workspace)
 *   the X on a chip        ->  it leaves THIS CHAT      (still in the workspace)
 *   deleting in Knowledge  ->  it is gone everywhere
 *
 * Three different things, and conflating any two loses data or silently changes what
 * an answer was based on.
 *
 * The scope is enforced server-side at retrieval time, so nothing here is a
 * prompt-level hint that a model could ignore.
 */

const STORAGE_KEY = "carinaa.scopeExpanded";

export function ChatDocuments({
  scope,
  workspaceDocuments,
  onAttach,
  onToggle,
  onDetach,
  busy,
}: {
  scope: ConversationScope | null;
  workspaceDocuments: DocumentRecord[];
  onAttach: (documentId: number) => void;
  onToggle: (documentId: number, isActive: boolean) => void;
  onDetach: (documentId: number) => void;
  busy?: boolean;
}) {
  // Collapsed unless the user has chosen otherwise, so the chat stays compact.
  const [expanded, setExpanded] = useState(
    () => window.localStorage.getItem(STORAGE_KEY) === "1",
  );
  const [pickerOpen, setPickerOpen] = useState(false);

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, expanded ? "1" : "0");
  }, [expanded]);

  const attached = scope?.documents ?? [];
  const active = attached.filter((item) => item.is_active);
  const attachedIds = new Set(attached.map((item) => item.document_id));
  const attachable = workspaceDocuments.filter(
    (document) => !attachedIds.has(document.id) && document.status === "ready",
  );

  /** The one-line state: which document, how many, or the whole workspace. */
  const summary = (() => {
    if (!scope) return "No documents";
    if (active.length === 0) {
      return scope.workspace_document_count > 0 ? "Workspace" : "No documents";
    }
    if (active.length === 1) return active[0].original_filename;
    return `${active.length} documents`;
  })();

  return (
    <div className="rounded-xl border border-line bg-surface">
      {/* ---- compact header, always visible --------------------------- */}
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        title="Documents this chat is using"
        className="flex w-full items-center gap-2 px-2.5 py-2 text-left"
      >
        <BookOpen className="h-3.5 w-3.5 shrink-0 text-faint" />

        <span className="shrink-0 text-2xs font-medium text-ink">This chat</span>

        <span
          className={cn(
            "min-w-0 flex-1 truncate text-2xs",
            active.length > 0 ? "text-brand" : "text-faint",
          )}
          title={summary}
        >
          {summary}
          {scope && active.length > 0 ? (
            <span className="text-faint">
              {" "}
              · {active.length}/{scope.workspace_document_count} searched
            </span>
          ) : null}
        </span>

        <ChevronDown
          className={cn(
            "h-3 w-3 shrink-0 text-faint transition-transform",
            expanded && "rotate-180",
          )}
        />
      </button>

      {/* ---- expanded detail ----------------------------------------- */}
      {expanded ? (
        <div className="animate-slide-down border-t border-line">
          <p className="px-2.5 pt-2 text-2xs leading-relaxed text-faint">
            {active.length > 0
              ? `Searching ${pluralize(active.length, "document")} of ${scope?.workspace_document_count ?? 0} in this workspace.`
              : "No scope set — searching the whole workspace."}
          </p>

          {attached.length > 0 ? (
            <div className="flex flex-wrap gap-1.5 px-2.5 py-2">
              {attached.map((document) => (
                <span
                  key={document.document_id}
                  className={cn(
                    "inline-flex max-w-[15rem] items-center gap-1.5 rounded-lg border px-2 py-1 text-2xs transition",
                    document.is_active
                      ? "border-brand/35 bg-brand/8 text-ink"
                      : "border-line bg-sunken text-faint",
                  )}
                >
                  {/* The checkbox IS the retrieval switch, so it carries the
                      accessible role rather than being a decorative dot. */}
                  <button
                    type="button"
                    role="checkbox"
                    aria-checked={document.is_active}
                    aria-label={`${document.is_active ? "Exclude" : "Include"} ${document.original_filename} from this chat`}
                    title={
                      document.is_active
                        ? "Included in retrieval — click to exclude"
                        : "Excluded from retrieval — click to include"
                    }
                    disabled={busy}
                    onClick={() => onToggle(document.document_id, !document.is_active)}
                    className={cn(
                      "flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border transition",
                      document.is_active
                        ? "border-brand bg-brand text-brand-ink"
                        : "border-line-strong bg-surface",
                    )}
                  >
                    {document.is_active ? <Check className="h-2.5 w-2.5" /> : null}
                  </button>

                  <span className="min-w-0 flex-1 truncate" title={document.original_filename}>
                    {document.original_filename}
                  </span>

                  <span className="shrink-0 font-mono text-[0.625rem] text-faint">
                    {fileBadge(document.original_filename)}
                  </span>

                  <button
                    type="button"
                    onClick={() => onDetach(document.document_id)}
                    disabled={busy}
                    aria-label={`Remove ${document.original_filename} from this chat`}
                    title="Remove from this chat (stays in your workspace)"
                    className="shrink-0 rounded p-0.5 text-faint transition hover:text-negative"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </span>
              ))}
            </div>
          ) : null}

          {/* ---- attach ---------------------------------------------- */}
          <div className="flex items-center gap-2 px-2.5 pb-2">
            <button
              type="button"
              onClick={() => setPickerOpen((value) => !value)}
              aria-expanded={pickerOpen}
              className={cn(
                "inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-2xs font-medium transition",
                pickerOpen
                  ? "border-brand/40 bg-brand/10 text-brand"
                  : "border-line text-muted hover:border-line-strong hover:text-ink",
              )}
            >
              <Plus className="h-3 w-3" />
              Attach
            </button>
            <span className="truncate text-2xs text-faint">
              {attachable.length > 0
                ? `${pluralize(attachable.length, "document")} available`
                : "all indexed documents attached"}
            </span>
          </div>

          {pickerOpen ? (
            <div className="border-t border-line px-2.5 py-2">
              <p className="pb-1.5 text-2xs text-faint">
                Attaching scopes this chat to that document. It stays in your workspace.
              </p>
              {attachable.length === 0 ? (
                <p className="py-1.5 text-2xs text-muted">
                  Every indexed document is already attached to this chat.
                </p>
              ) : (
                <ul className="max-h-44 space-y-0.5 overflow-y-auto scrollbar-thin">
                  {attachable.map((document) => (
                    <li key={document.id}>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => {
                          onAttach(document.id);
                          setPickerOpen(false);
                        }}
                        className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition hover:bg-sunken"
                      >
                        <FileText className="h-3 w-3 shrink-0 text-faint" />
                        <span className="min-w-0 flex-1 truncate text-2xs text-ink">
                          {document.original_filename}
                        </span>
                        <span className="shrink-0 text-2xs text-faint">
                          {pluralize(document.chunk_count, "chunk")}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : null}

          {/* ---- the promise that this is not deletion ---------------- */}
          {attached.length > 0 ? (
            <p className="border-t border-line px-2.5 py-1.5 text-[0.625rem] leading-relaxed text-faint">
              Unchecking excludes a document from this chat&apos;s answers. Removing it
              here never deletes it from your workspace.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export default ChatDocuments;
