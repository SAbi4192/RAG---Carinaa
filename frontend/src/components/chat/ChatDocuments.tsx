import { useEffect, useState } from "react";
import { BookOpen, Check, ChevronDown, Loader2 } from "lucide-react";

import { cn } from "@/lib/cn";
import type { ConversationScope, DocumentRecord } from "@/lib/types";
import { fileBadge, fileAccent, pluralize } from "@/lib/format";

/**
 * "This chat" — the documents scoped to the current conversation.
 *
 * MULTI-SELECT IS THE WHOLE POINT
 * -------------------------------
 * The previous picker showed each unattached document as a "+" row that vanished
 * on attach, which read as single-select. This is a CHECKLIST: every document in
 * the workspace is listed with a checkbox, checking several is the normal case,
 * and the header always says how many are selected.
 *
 *   ☑ ADT_Notes.pdf      ← searched in this chat
 *   ☑ Design_Thinking.pdf
 *   ☐ Management.pdf     ← still in the workspace, just not searched here
 *
 * THE DISTINCTION THIS UI MUST NEVER BLUR
 * ---------------------------------------
 *   unchecking a document  ->  it stops being searched HERE (still in the workspace)
 *   "Clear selection"      ->  the chat goes back to searching the whole workspace
 *   deleting in Knowledge  ->  it is gone everywhere
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
  onDetach: _onDetach,
  onClearScope,
  busy,
}: {
  scope: ConversationScope | null;
  workspaceDocuments: DocumentRecord[];
  onAttach: (documentId: number) => void;
  onToggle: (documentId: number, isActive: boolean) => void;
  onDetach: (documentId: number) => void;
  /** Drop the chat's scope entirely and go back to searching the whole workspace. */
  onClearScope?: () => void;
  busy?: boolean;
}) {
  // Collapsed unless the user has chosen otherwise, so the chat stays compact.
  const [expanded, setExpanded] = useState(
    () => window.localStorage.getItem(STORAGE_KEY) === "1",
  );

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, expanded ? "1" : "0");
  }, [expanded]);

  const attached = scope?.documents ?? [];
  const activeIds = new Set(
    attached.filter((item) => item.is_active).map((item) => item.document_id),
  );
  const workspaceCount = scope?.workspace_document_count ?? workspaceDocuments.length;

  /** The one-line state: how many documents the next question will search. */
  const summary = (() => {
    if (workspaceCount === 0) return "No documents in this workspace yet";
    if (activeIds.size === 0) return `Searching all ${pluralize(workspaceCount, "document")}`;
    return `Searching ${activeIds.size} of ${pluralize(workspaceCount, "document")}`;
  })();

  /**
   * Checking a box means "search this document in this chat":
   *   - not attached yet  -> attach it (the server marks it active)
   *   - attached but off  -> switch it back on
   * Unchecking switches it off for this chat. The document itself is never deleted.
   */
  const handleCheck = (document: DocumentRecord, checked: boolean) => {
    const link = attached.find((item) => item.document_id === document.id);
    if (link) onToggle(document.id, checked);
    else if (checked) onAttach(document.id);
  };

  return (
    <div className="rounded-xl border border-line bg-surface">
      {/* ---- compact header, always visible ------------------------------
          Deliberately NOT styled like an input: this is a scope indicator,
          and looking like a second search bar was a real source of confusion. */}
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        title="Choose which documents this chat searches"
        className="flex w-full items-center gap-2 px-2.5 py-2 text-left"
      >
        <BookOpen className="h-3.5 w-3.5 shrink-0 text-muted" />
        <span className="min-w-0 flex-1 truncate text-2xs text-muted">{summary}</span>
        {activeIds.size > 0 ? (
          <span className="shrink-0 rounded-full border border-brand/30 bg-brand/10 px-1.5 py-0.5 text-[0.625rem] font-medium text-brand">
            {activeIds.size} selected
          </span>
        ) : null}
        <ChevronDown
          className={cn(
            "h-3 w-3 shrink-0 text-faint transition-transform",
            expanded && "rotate-180",
          )}
        />
      </button>

      {expanded ? (
        <div className="animate-fade-up border-t border-line">
          <div className="flex items-center gap-2 px-2.5 pt-2">
            <p className="min-w-0 flex-1 text-2xs text-faint">
              Tick the documents this chat should search. Tick as many as you like.
            </p>
            {attached.length > 0 && onClearScope ? (
              <button
                type="button"
                onClick={onClearScope}
                disabled={busy}
                className="shrink-0 text-2xs text-faint underline decoration-dotted underline-offset-2 transition hover:text-negative"
                title="Search the whole workspace again"
              >
                Clear selection
              </button>
            ) : null}
          </div>

          {workspaceDocuments.length === 0 ? (
            <p className="px-2.5 py-2.5 text-2xs text-muted">
              No documents yet. Add one with the 📎 button below and it will be
              searchable here.
            </p>
          ) : (
            <ul className="max-h-48 space-y-0.5 overflow-y-auto scrollbar-thin px-1.5 py-1.5">
              {workspaceDocuments.map((document) => {
                const ready = document.status === "ready";
                const checked = ready && activeIds.has(document.id);
                return (
                  <li key={document.id}>
                    <button
                      type="button"
                      role="checkbox"
                      aria-checked={checked}
                      disabled={busy || !ready}
                      onClick={() => handleCheck(document, !checked)}
                      title={
                        ready
                          ? checked
                            ? "Stop searching this document in this chat"
                            : "Search this document in this chat"
                          : `This document is ${document.status} and cannot be searched yet`
                      }
                      className={cn(
                        "flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition",
                        ready ? "hover:bg-sunken" : "cursor-not-allowed opacity-50",
                      )}
                    >
                      <span
                        className={cn(
                          "flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border transition",
                          checked
                            ? "border-brand bg-brand text-brand-ink"
                            : "border-line-strong bg-surface text-transparent",
                        )}
                      >
                        {busy ? (
                          <Loader2 className="h-2.5 w-2.5 animate-spin" />
                        ) : (
                          <Check className="h-2.5 w-2.5" strokeWidth={3} />
                        )}
                      </span>
                      <span
                        className={cn(
                          "shrink-0 rounded border px-1 py-px text-[0.5625rem] font-semibold tracking-wide",
                          fileAccent(document.original_filename),
                        )}
                      >
                        {fileBadge(document.original_filename)}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-2xs text-ink">
                        {document.original_filename}
                      </span>
                      <span className="shrink-0 text-2xs text-faint">
                        {ready
                          ? pluralize(document.chunk_count, "chunk")
                          : document.status}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          {/* ---- the promise that this is not deletion ------------------- */}
          <p className="border-t border-line px-2.5 py-1.5 text-[0.625rem] leading-relaxed text-faint">
            {activeIds.size === 0
              ? "No selection — the next question searches the whole workspace."
              : `Only the ${pluralize(activeIds.size, "document")} ticked above will be searched.`}{" "}
            Unticking never deletes anything from your workspace.
          </p>
        </div>
      ) : null}
    </div>
  );
}

export default ChatDocuments;

