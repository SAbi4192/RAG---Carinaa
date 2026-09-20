import { useState } from "react";
import { Check, FileText, Paperclip, Plus, X } from "lucide-react";

import { cn } from "@/lib/cn";
import type { ConversationScope, DocumentRecord } from "@/lib/types";
import { fileBadge, pluralize } from "@/lib/format";

/**
 * "This chat" — the documents scoped to the current conversation.
 *
 * THE DISTINCTION THIS UI MUST NEVER BLUR
 * ---------------------------------------
 *   unchecking a document  ->  it stops being searched HERE (still in the workspace)
 *   the X on a chip        ->  it leaves THIS CHAT      (still in the workspace)
 *   deleting in Knowledge  ->  it is gone everywhere
 *
 * Three different things, and conflating any two of them loses data or silently
 * changes what an answer was based on. So the copy is explicit: the empty state
 * says "searches the whole workspace", and removing says "still in your workspace".
 *
 * The scope is enforced server-side at retrieval time, so nothing here is a
 * prompt-level hint that a model could ignore.
 */
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
  const [pickerOpen, setPickerOpen] = useState(false);

  const attached = scope?.documents ?? [];
  const attachedIds = new Set(attached.map((item) => item.document_id));
  const attachable = workspaceDocuments.filter(
    (document) => !attachedIds.has(document.id) && document.status === "ready",
  );
  const isChatScoped = scope?.scope === "chat";

  return (
    <div className="rounded-xl border border-line bg-surface">
      <div className="flex items-center gap-2 px-2.5 py-2">
        <Paperclip className="h-3 w-3 shrink-0 text-faint" />

        <p className="shrink-0 text-2xs font-medium text-ink">This chat</p>

        {/* The scope statement. This is the line that tells the user what the
            next answer will actually be based on. */}
        <p className="min-w-0 flex-1 truncate text-2xs text-faint">
          {isChatScoped
            ? `searching ${pluralize(scope!.active_document_ids.length, "document")} of ${scope!.workspace_document_count}`
            : "no scope set — searching the whole workspace"}
        </p>

        {attachable.length > 0 || pickerOpen ? (
          <button
            type="button"
            onClick={() => setPickerOpen((value) => !value)}
            aria-expanded={pickerOpen}
            className={cn(
              "inline-flex shrink-0 items-center gap-1 rounded-lg border px-2 py-1 text-2xs font-medium transition",
              pickerOpen
                ? "border-brand/40 bg-brand/10 text-brand"
                : "border-line text-muted hover:border-line-strong hover:text-ink",
            )}
          >
            <Plus className="h-3 w-3" />
            Attach
          </button>
        ) : null}
      </div>

      {/* ---- attached chips ------------------------------------------- */}
      {attached.length > 0 ? (
        <div className="flex flex-wrap gap-1.5 border-t border-line px-2.5 py-2">
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
              {/* The checkbox is the retrieval switch, so it gets the accessible
                  role rather than being a decorative dot. */}
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

      {/* ---- picker --------------------------------------------------- */}
      {pickerOpen ? (
        <div className="animate-slide-down border-t border-line px-2.5 py-2">
          <p className="pb-1.5 text-2xs text-faint">
            Attaching a document scopes this chat to it. The document stays in your
            workspace.
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

      {/* ---- the promise that detaching is safe ----------------------- */}
      {attached.length > 0 ? (
        <p className="border-t border-line px-2.5 py-1.5 text-[0.625rem] leading-relaxed text-faint">
          Unchecking excludes a document from this chat&apos;s answers. Removing it here
          never deletes it from your workspace.
        </p>
      ) : null}
    </div>
  );
}

export default ChatDocuments;
