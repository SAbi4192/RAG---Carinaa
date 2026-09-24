import { useState } from "react";
import { Cloud, Globe, HardDrive, Play } from "lucide-react";

import { cn } from "@/lib/cn";
import type { DocumentRecord } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Input, Select } from "@/components/ui/Field";

/**
 * Shared question controls for the labs that run the full pipeline.
 *
 * The mode toggle is a real choice, not decoration: it decides which provider
 * answers, and the response says which one actually did. Offline mode needs the
 * local GGUF model loaded; if it is not, the error explains that rather than
 * falling back online silently.
 *
 * The knowledge-source and web rows are OPTIONAL: they only render when their
 * props are passed, so the labs that share this control keep their current,
 * simpler form until they opt in.
 */
export function AskControls({
  onRun,
  running,
  defaultQuestion = "How does virtualization improve resource utilization?",
  compact = false,
  documents,
  selectedDocumentId,
  onSelectedDocumentChange,
  webEnabled,
  onWebChange,
}: {
  onRun: (question: string, mode: "online" | "offline") => void;
  running: boolean;
  defaultQuestion?: string;
  compact?: boolean;
  /** When provided, renders the "Knowledge source" selector. */
  documents?: DocumentRecord[];
  /** null = the whole workspace. */
  selectedDocumentId?: number | null;
  onSelectedDocumentChange?: (documentId: number | null) => void;
  /** Opt-in web search for this run (never on unless asked for). */
  webEnabled?: boolean;
  onWebChange?: (enabled: boolean) => void;
}) {
  const [question, setQuestion] = useState(defaultQuestion);
  const [mode, setMode] = useState<"online" | "offline">("online");

  const showKnowledge = Array.isArray(documents);
  const readyDocuments = (documents ?? []).filter((doc) => doc.status === "ready");

  return (
    <div className={cn("space-y-3", compact ? "" : "p-5")}>
      <Input
        label="Question"
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") onRun(question, mode);
        }}
        placeholder="Ask something…"
      />

      {/* Knowledge source: what this experiment is allowed to search.
          Whole workspace by default - selecting one document narrows the
          retrieval to it, enforced server-side, not prompt-level. */}
      {showKnowledge ? (
        <Select
          label="Knowledge source"
          value={selectedDocumentId ?? ""}
          onChange={(event) =>
            onSelectedDocumentChange?.(
              event.target.value === "" ? null : Number(event.target.value),
            )
          }
          hint={
            (selectedDocumentId ?? null) === null
              ? "Searching the whole workspace for this run."
              : "Searching only this document for this run."
          }
        >
          <option value="">
            All documents in this workspace
            {readyDocuments.length > 0 ? ` (${readyDocuments.length})` : ""}
          </option>
          {readyDocuments.map((document) => (
            <option key={document.id} value={document.id}>
              {document.original_filename}
            </option>
          ))}
        </Select>
      ) : null}

      {/* Web search: optional, off by default, never implicit. */}
      {onWebChange ? (
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => onWebChange(!webEnabled)}
            disabled={mode === "offline"}
            aria-pressed={Boolean(webEnabled)}
            title={
              mode === "offline"
                ? "Web search is unavailable in offline mode - it would require the network."
                : "Search current web information in addition to your selected knowledge. Off by default."
            }
            className={cn(
              "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-2xs font-medium transition",
              "disabled:cursor-not-allowed disabled:opacity-40",
              webEnabled
                ? "border-brand/40 bg-brand/10 text-brand"
                : "border-line text-muted hover:border-line-strong hover:text-ink",
            )}
          >
            <Globe className="h-3.5 w-3.5" />
            {webEnabled ? "Web search on" : "Web search (optional)"}
          </button>
          <p className="min-w-0 flex-1 text-2xs leading-relaxed text-faint">
            {webEnabled
              ? "Web results will be added alongside your documents and labelled 🌐."
              : "Off - this run uses only your documents."}
          </p>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex rounded-lg border border-line bg-sunken p-0.5">
          {(
            [
              { value: "online", label: "Online", icon: Cloud, hint: "Groq first, Gemini fallback" },
              { value: "offline", label: "Offline", icon: HardDrive, hint: "local GGUF model" },
            ] as const
          ).map((option) => {
            const Icon = option.icon;
            const active = mode === option.value;
            return (
              <button
                key={option.value}
                type="button"
                onClick={() => setMode(option.value)}
                title={option.hint}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-2xs font-medium transition",
                  active
                    ? "bg-surface text-ink shadow-card"
                    : "text-muted hover:text-ink",
                )}
              >
                <Icon className="h-3 w-3" />
                {option.label}
              </button>
            );
          })}
        </div>

        <Button onClick={() => onRun(question, mode)} loading={running} size="sm">
          <Play className="h-3.5 w-3.5" />
          Run the pipeline
        </Button>
      </div>

      {mode === "offline" ? (
        <p className="text-2xs leading-relaxed text-faint">
          Offline runs Qwen2.5-3B on the CPU. It is slower and less capable than the online
          providers, and it never contacts the network — not even as a fallback.
        </p>
      ) : null}
    </div>
  );
}

export default AskControls;
