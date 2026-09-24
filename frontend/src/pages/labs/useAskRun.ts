import { useCallback, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type { AskResponse } from "@/lib/types";

/**
 * Run one question through the real pipeline and expose everything it returned.
 *
 * The Context, Generation and Full Pipeline labs all need the same thing: a real
 * answer plus the context, citations, grounding and trace that produced it. They
 * differ only in which part they put on screen.
 *
 * Sharing this hook rather than re-implementing it three times is deliberate. If
 * the labs each called the API their own way, one of them would eventually drift
 * and start showing something the pipeline does not actually do - which is the
 * exact failure this project exists to avoid.
 *
 * The workspace id is passed in rather than read from storage, so the caller's
 * workspace context stays the single source of truth.
 */
/**
 * Optional knobs for one experiment run.
 *
 * Both are strictly opt-in: when omitted the request is a plain
 * document-grounded question, exactly as before, so every lab that shares this
 * hook keeps its current behaviour.
 */
export interface AskRunOptions {
  /** Add labelled web results alongside the documents (online mode only). */
  use_web_search?: boolean;
  /** Restrict retrieval to these documents; omit/empty = whole workspace. */
  document_ids?: number[];
}

export interface AskRun {
  question: string;
  answer: string;
  mode: "online" | "offline";
  result: AskResponse | null;
  running: boolean;
  error: string;
  run: (
    question: string,
    mode: "online" | "offline",
    options?: AskRunOptions,
  ) => Promise<void>;
  reset: () => void;
}

export function useAskRun(workspaceId: number | null): AskRun {
  const [result, setResult] = useState<AskResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<"online" | "offline">("online");

  const run = useCallback(
    async (
      nextQuestion: string,
      nextMode: "online" | "offline",
      options?: AskRunOptions,
    ) => {
      const trimmed = nextQuestion.trim();
      if (!trimmed) return;
      if (!workspaceId) {
        setError("Select a workspace first — retrieval is scoped to one workspace.");
        return;
      }

      setRunning(true);
      setError("");
      setQuestion(trimmed);
      setMode(nextMode);

      try {
        const response = await api.chat.ask({
          question: trimmed,
          workspace_id: workspaceId,
          mode: nextMode,
          language: "en",
          // Web search is request-scoped and never enabled implicitly: it is
          // only sent when the experiment explicitly asked for it, and never
          // in offline mode (the server would refuse it anyway).
          use_web_search:
            options?.use_web_search && nextMode === "online" ? true : false,
          document_ids: options?.document_ids?.length
            ? options.document_ids
            : undefined,
        });
        setResult(response);
      } catch (cause) {
        setError(
          cause instanceof ApiError
            ? cause.message
            : "The question could not be answered. Is the backend running?",
        );
        setResult(null);
      } finally {
        setRunning(false);
      }
    },
    [workspaceId],
  );

  const reset = useCallback(() => {
    setResult(null);
    setError("");
    setQuestion("");
  }, []);

  return {
    question,
    answer: result?.answer ?? "",
    mode,
    result,
    running,
    error,
    run,
    reset,
  };
}
