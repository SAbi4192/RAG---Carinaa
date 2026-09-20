import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  FileUp,
  Loader2,
  Upload as UploadIcon,
  X,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { ApiError, api, streamDocumentProgress } from "@/lib/api";
import { fileBadge, formatBytes } from "@/lib/format";
import type { DocumentProgress, DocumentRecord } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { ProgressBar } from "@/components/ui/Feedback";
import { Badge } from "@/components/ui/Badge";

/**
 * Upload a document and watch it being ingested.
 *
 * THE RULE THIS COMPONENT EXISTS TO ENFORCE
 * -----------------------------------------
 * Progress is never faked. Every percentage on screen comes from the server's
 * Document row, which the ingestion pipeline updates as each stage actually
 * starts and finishes. If parsing takes eight seconds, the bar sits at the
 * parsing stage for eight seconds. There is no timer, no easing animation that
 * creeps toward 90%, and no "finishing up…" stage that does not exist.
 *
 * The transport is Server-Sent Events. If the stream drops, we fall back to
 * polling the same endpoint - and the UI says so, rather than silently freezing
 * at whatever percentage it last received.
 */

type Phase = "idle" | "uploading" | "processing" | "done" | "error";

export function UploadDialog({
  open,
  onClose,
  workspaceId,
  onUploaded,
  initialFile,
}: {
  open: boolean;
  onClose: () => void;
  workspaceId: number | null;
  onUploaded?: (document: DocumentRecord) => void;
  /**
   * Start uploading this file immediately, without the picker step.
   *
   * Added for drag-and-drop from the Chat screen: the user has already chosen the
   * file by dropping it, so showing them a picker again would be a second decision
   * for something they already decided.
   */
  initialFile?: File | null;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [file, setFile] = useState<File | null>(null);
  const [document, setDocument] = useState<DocumentRecord | null>(null);
  const [progress, setProgress] = useState<DocumentProgress | null>(null);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [liveStopped, setLiveStopped] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  const pollTimerRef = useRef<number | undefined>(undefined);

  /* ---- teardown ------------------------------------------------------ */
  const cleanup = useCallback(() => {
    unsubscribeRef.current?.();
    unsubscribeRef.current = null;
    if (pollTimerRef.current) {
      window.clearTimeout(pollTimerRef.current);
      pollTimerRef.current = undefined;
    }
  }, []);

  useEffect(() => cleanup, [cleanup]);

  // Reset whenever the dialog is reopened, so a previous document's progress
  // does not flash up before the new upload starts.
  useEffect(() => {
    if (open) return;
    cleanup();
    setPhase("idle");
    setFile(null);
    setDocument(null);
    setProgress(null);
    setError("");
    setDragging(false);
    setLiveStopped(false);
  }, [open, cleanup]);

  // A dropped file skips the picker and starts uploading straight away, so the
  // user sees the real backend stages rather than another choice to make.
  const initialFileRef = useRef<File | null>(null);
  const uploadRef = useRef<((file: File) => Promise<void>) | null>(null);
  useEffect(() => {
    if (!open || !initialFile) return;
    // Only once per file, and only when the dialog has just opened.
    if (initialFileRef.current === initialFile) return;
    initialFileRef.current = initialFile;
    void uploadRef.current?.(initialFile);
  }, [open, initialFile]);

  /* ---- terminal state ------------------------------------------------- */
  const finishWith = useCallback(
    (final: DocumentProgress) => {
      cleanup();
      if (final.status === "failed") {
        setPhase("error");
        setError(final.error || "Ingestion failed.");
        return;
      }
      setPhase("done");
      // The row we already have is stale by now (it predates chunking), so fetch
      // the real record rather than showing "0 chunks" on a document that has six.
      void api.documents
        .get(final.document_id)
        .then((fresh) => {
          setDocument(fresh);
          onUploaded?.(fresh);
        })
        .catch(() => {
          /* the list will refresh anyway */
        });
    },
    [cleanup, onUploaded],
  );

  /* ---- polling fallback ---------------------------------------------- */
  // Only used when the SSE stream drops. It reads the same endpoint, so the
  // numbers stay real even on the fallback path.
  const startPolling = useCallback(
    (documentId: number) => {
      const tick = async () => {
        try {
          const result = await api.documents.progress(documentId);
          setProgress(result);
          if (result.status === "ready" || result.status === "failed") {
            finishWith(result);
            return;
          }
        } catch {
          /* transient; keep polling */
        }
        pollTimerRef.current = window.setTimeout(tick, 1200);
      };
      void tick();
    },
    [finishWith],
  );

  /* ---- upload -------------------------------------------------------- */
  const upload = useCallback(
    async (selected: File) => {
      if (!workspaceId) {
        setError("Select a workspace first.");
        return;
      }

      cleanup();
      setFile(selected);
      setError("");
      setProgress(null);
      setLiveStopped(false);
      setPhase("uploading");

      let uploaded: DocumentRecord;
      try {
        const result = await api.documents.upload(workspaceId, selected);
        uploaded = result.document;
        setDocument(uploaded);
      } catch (cause) {
        setPhase("error");
        setError(
          cause instanceof ApiError
            ? cause.message
            : "The upload could not be completed. Please try again.",
        );
        return;
      }

      setPhase("processing");

      // Subscribe to the real stream. If it fails, fall back to polling the same
      // state so the user is never left staring at a frozen bar.
      unsubscribeRef.current = streamDocumentProgress(uploaded.id, {
        onProgress: (next) => setProgress(next),
        onDone: (final) => finishWith(final),
        onError: () => {
          setLiveStopped(true);
          startPolling(uploaded.id);
        },
      });
    },
    [workspaceId, cleanup, finishWith, startPolling],
  );

  // Keep the dropped-file effect pointing at the current upload implementation.
  useEffect(() => {
    uploadRef.current = upload;
  }, [upload]);

  const handleFiles = useCallback(
    (files: FileList | null) => {
      const selected = files?.[0];
      if (selected) void upload(selected);
    },
    [upload],
  );

  const busy = phase === "uploading" || phase === "processing";
  const percent = progress?.percent ?? 0;

  return (
    <Modal
      open={open}
      onClose={busy ? () => undefined : onClose}
      closeOnBackdrop={!busy}
      title="Add a document"
      description="The file is parsed, normalised, chunked, embedded and indexed. Every stage below is reported by the server as it actually runs."
      size="lg"
      footer={
        phase === "done" ? (
          <>
            <Button variant="ghost" size="sm" onClick={onClose}>
              Close
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => {
                cleanup();
                setPhase("idle");
                setFile(null);
                setDocument(null);
                setProgress(null);
                inputRef.current?.click();
              }}
            >
              Add another
            </Button>
          </>
        ) : phase === "error" ? (
          <>
            <Button variant="ghost" size="sm" onClick={onClose}>
              Close
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => {
                if (file) void upload(file);
                else inputRef.current?.click();
              }}
            >
              Try again
            </Button>
          </>
        ) : busy ? (
          <p className="text-2xs text-faint">
            You can leave this open - the document keeps processing either way.
          </p>
        ) : null
      }
    >
      <input
        ref={inputRef}
        type="file"
        className="hidden"
        onChange={(event) => handleFiles(event.target.files)}
        accept=".pdf,.docx,.pptx,.xlsx,.csv,.md,.markdown,.txt,.json,.jsonl"
      />

      {/* ---- idle: drop zone ---------------------------------------- */}
      {phase === "idle" ? (
        <div
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            handleFiles(event.dataTransfer.files);
          }}
          onClick={() => inputRef.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              inputRef.current?.click();
            }
          }}
          className={cn(
            "flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-12 text-center transition-all duration-200",
            dragging
              ? "border-brand bg-brand/8"
              : "border-line-strong bg-sunken/50 hover:border-brand/50 hover:bg-brand/5",
          )}
        >
          <div
            className={cn(
              "mb-3.5 flex h-12 w-12 items-center justify-center rounded-xl border transition-colors",
              dragging ? "border-brand/40 bg-brand/15 text-brand" : "border-line bg-surface text-faint",
            )}
          >
            <UploadIcon className="h-5 w-5" />
          </div>
          <p className="text-sm font-medium text-ink">
            {dragging ? "Drop to upload" : "Drop a file here, or click to browse"}
          </p>
          <p className="mt-1.5 max-w-sm text-2xs leading-relaxed text-muted">
            PDF, DOCX, PPTX, XLSX, CSV, Markdown, TXT and JSON. Each format keeps the
            provenance that makes its citations meaningful - pages, slides, sheets, rows.
          </p>
        </div>
      ) : null}

      {/* ---- busy / done / error ------------------------------------ */}
      {phase !== "idle" ? (
        <div className="space-y-4">
          {/* file row */}
          <div className="flex items-center gap-3 rounded-xl border border-line bg-sunken px-3.5 py-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-line bg-surface font-mono text-2xs font-semibold text-muted">
              {file ? fileBadge(file.name) : "—"}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-xs font-medium text-ink">
                {file?.name ?? document?.original_filename ?? "document"}
              </p>
              <p className="text-2xs text-faint">
                {file ? formatBytes(file.size) : ""}
                {document ? ` · ${document.file_type.toUpperCase()}` : ""}
              </p>
            </div>

            {phase === "done" ? (
              <CheckCircle2 className="h-4.5 w-4.5 shrink-0 text-positive" />
            ) : phase === "error" ? (
              <AlertCircle className="h-4.5 w-4.5 shrink-0 text-negative" />
            ) : (
              <Loader2 className="h-4 w-4 shrink-0 animate-spin text-brand" />
            )}
          </div>

          {/* progress */}
          {phase === "uploading" ? (
            <div className="space-y-2">
              <p className="text-xs font-medium text-ink">Uploading…</p>
              <p className="text-2xs text-muted">
                Streaming the file to the server. The size limit is enforced while
                writing, so an oversized file is rejected without filling the disk.
              </p>
            </div>
          ) : null}

          {phase === "processing" || phase === "done" ? (
            <div className="space-y-3">
              <div className="flex items-baseline justify-between gap-3">
                <p className="text-xs font-medium text-ink">
                  {progress?.stage_label ?? "Starting…"}
                </p>
                <span className="font-mono text-2xs tabular-nums text-muted">
                  {percent.toFixed(0)}%
                </span>
              </div>

              <ProgressBar
                value={percent}
                tone={phase === "done" ? "positive" : "brand"}
              />

              {liveStopped ? (
                <p className="flex items-center gap-1.5 text-2xs text-caution">
                  <AlertCircle className="h-3 w-3" />
                  Live updates stopped - polling instead. The numbers are still real.
                </p>
              ) : null}

              {/* stage list */}
              {progress?.stages?.length ? (
                <ol className="grid gap-1.5 sm:grid-cols-2">
                  {progress.stages.map((stage) => {
                    const stageIndex = progress.stages.findIndex((s) => s.key === stage.key);
                    const currentIndex = progress.stages.findIndex(
                      (s) => s.key === progress.stage,
                    );
                    const complete =
                      progress.status === "ready" || stageIndex < currentIndex;
                    const active = stage.key === progress.stage && progress.status !== "ready";

                    return (
                      <li
                        key={stage.key}
                        className={cn(
                          "flex items-center gap-2 rounded-lg border px-2.5 py-1.5 transition-colors",
                          active
                            ? "border-brand/30 bg-brand/8"
                            : complete
                              ? "border-line bg-surface"
                              : "border-line bg-sunken/40 opacity-60",
                        )}
                      >
                        {complete ? (
                          <CheckCircle2 className="h-3 w-3 shrink-0 text-positive" />
                        ) : active ? (
                          <Loader2 className="h-3 w-3 shrink-0 animate-spin text-brand" />
                        ) : (
                          <span className="h-3 w-3 shrink-0 rounded-full border border-line-strong" />
                        )}
                        <span
                          className={cn(
                            "truncate text-2xs",
                            active ? "font-medium text-brand" : "text-muted",
                          )}
                        >
                          {stage.label}
                        </span>
                      </li>
                    );
                  })}
                </ol>
              ) : null}
            </div>
          ) : null}

          {/* completion summary - real numbers, read back from the record */}
          {phase === "done" && document ? (
            <div className="animate-slide-down rounded-xl border border-positive/25 bg-positive/8 p-3.5">
              <p className="flex items-center gap-2 text-xs font-medium text-positive">
                <CheckCircle2 className="h-3.5 w-3.5" />
                Indexed and ready to search
              </p>
              <div className="mt-2.5 flex flex-wrap gap-1.5">
                <Badge tone="neutral" mono>
                  {document.chunk_count} chunks
                </Badge>
                <Badge tone="neutral" mono>
                  {document.char_count.toLocaleString()} chars
                </Badge>
                {document.page_count > 0 ? (
                  <Badge tone="neutral" mono>
                    {document.page_count} pages
                  </Badge>
                ) : null}
                <Badge tone="accent" mono>
                  {document.embedding_dim}-d vectors
                </Badge>
              </div>
              <p className="mt-2 text-2xs leading-relaxed text-muted">
                These vectors are now searchable from this workspace - and only this
                workspace.
              </p>
            </div>
          ) : null}

          {/* error */}
          {phase === "error" ? (
            <div className="rounded-xl border border-negative/30 bg-negative/8 p-3.5">
              <p className="flex items-center gap-2 text-xs font-medium text-negative">
                <AlertCircle className="h-3.5 w-3.5" />
                Could not process this document
              </p>
              <p className="mt-1.5 text-2xs leading-relaxed text-muted">{error}</p>
              <p className="mt-2 text-2xs text-faint">
                Nothing was left behind - the partial row and any vectors were removed.
              </p>
            </div>
          ) : null}
        </div>
      ) : null}
    </Modal>
  );
}

/** Compact inline trigger used in empty states and page headers. */
export function UploadButton({
  onClick,
  disabled,
  label = "Add document",
}: {
  onClick: () => void;
  disabled?: boolean;
  label?: string;
}) {
  return (
    <Button
      variant="primary"
      size="sm"
      disabled={disabled}
      onClick={onClick}
      icon={<FileUp className="h-3.5 w-3.5" />}
    >
      {label}
    </Button>
  );
}

export { X };
