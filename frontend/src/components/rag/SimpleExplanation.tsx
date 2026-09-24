import { useMemo } from "react";
import type { TraceStage } from "@/lib/types";

/**
 * The beginner layer: one plain-language run-through of what happened for THIS
 * answer, assembled from the real trace measurements.
 *
 * The stage cards below already carry the technical detail. This block exists
 * because a student reads this first and understands the whole run in one
 * paragraph before deciding which stage to open. It is generated from the
 * inspected message's numbers - question, document count, candidate count,
 * excerpt count, provider, verdict - never a fixed paragraph.
 */

type StageBits = {
  question?: string;
  keywords?: string[];
  candidates?: number;
  excerpts?: number;
  turns?: number;
  provider?: string;
  model?: string;
  verdict?: string;
  citations?: number;
  topScore?: number;
  generationMs?: number;
  totalMs?: number;
  failedStage?: string;
  scopeKind?: string;
  scopeSearched?: number;
  scopeAvailable?: number;
};

function verdictWords(status: string | undefined): string {
  switch (status) {
    case "SUPPORTED":
      return "every claim in the answer was found in those passages";
    case "PARTIALLY_SUPPORTED":
      return "most claims were found in the passages, but a few were only loosely supported";
    case "INSUFFICIENT_EVIDENCE":
      return "the passages did not actually answer the question, so Carinaa refused to guess";
    case "CITATION_ERROR":
      return "the answer pointed at a source that was not in the retrieved set, which the check caught";
    default:
      return "the check ran; open the Grounding stage for the verdict";
  }
}

export function SimpleExplanation({
  stages,
  question,
  scope,
  context,
}: {
  stages: TraceStage[];
  question: string;
  scope: { kind: string; searched: number; available: number } | null;
  context: { turns: number; excerpts: number } | null;
}) {
  const bits = useMemo<StageBits>(() => {
    const find = (name: string) => stages.find((s) => s.stage === name);
    const generation = find("llm_generation");
    const grounding = find("grounding");
    const retrieval = find("candidate_retrieval");
    const analysis = find("query_analysis");
    const failed = stages.find((s) => s.status === "error");
    return {
      keywords: Array.isArray(analysis?.data?.keywords)
        ? (analysis.data.keywords as unknown[]).map(String).slice(0, 5)
        : undefined,
      candidates: retrieval ? Number(retrieval.data?.candidates_retrieved ?? retrieval.data?.candidates ?? 0) : undefined,
      excerpts: context?.excerpts,
      turns: context?.turns,
      provider: generation?.data?.provider ? String(generation.data.provider) : undefined,
      model: generation?.data?.model ? String(generation.data.model) : undefined,
      verdict: grounding?.data?.status ? String(grounding.data.status) : undefined,
      citations: find("citation_resolution")
        ? Number(find("citation_resolution")?.data?.citations_found ?? 0)
        : undefined,
      generationMs: generation?.duration_ms,
      failedStage: failed ? failed.label || failed.stage : undefined,
      scopeKind: scope?.kind,
      scopeSearched: scope?.searched,
      scopeAvailable: scope?.available,
    };
  }, [stages, scope, context]);

  if (stages.length === 0) {
    return (
      <p className="text-2xs leading-relaxed text-muted">
        Nothing has run yet. Ask a question (or click an answer), and Carinaa will show you
        the exact steps it took to produce that one answer - each step with the real numbers
        it measured while it ran.
      </p>
    );
  }

  const searchedWhat =
    bits.scopeKind === "chat"
      ? `the ${bits.scopeSearched} document(s) you selected for this chat`
      : `all ${bits.scopeAvailable ?? "?"} document(s) in your workspace`;

  return (
    <div className="space-y-1.5 text-2xs leading-relaxed text-muted">
      <p>
        You asked: <span className="font-medium text-ink">“{question || "…"}”</span>
        {bits.keywords && bits.keywords.length
          ? ` — Carinaa pulled the key ideas (${bits.keywords.join(", ")}) and turned the question into numbers so it could compare meaning, not words.`
          : "."}
      </p>
      <p>
        It searched {searchedWhat}
        {bits.candidates ? `, found ${bits.candidates} candidate passage(s),` : ","} and kept
        the best {bits.excerpts ?? 0} as evidence for the AI.
        {bits.turns ? ` Your ${bits.turns} earlier message turn(s) were included as conversation context, clearly separated so they can never be cited as document sources.` : ""}
      </p>
      <p>
        {bits.provider
          ? `${bits.provider.charAt(0).toUpperCase() + bits.provider.slice(1)}${bits.model ? ` (${bits.model})` : ""} wrote the answer from that evidence`
          : "The AI wrote the answer from that evidence"}
        {bits.citations ? `, and ${bits.citations} citation(s) were linked back to the exact passages.` : "."}
        {bits.verdict ? ` A final check found ${verdictWords(bits.verdict)}.` : ""}
      </p>
      {bits.failedStage ? (
        <p className="rounded-lg border border-negative/30 bg-negative/8 px-2 py-1.5 text-negative">
          One step ({bits.failedStage}) failed during this run — its stage card below shows
          the error.
        </p>
      ) : null}
    </div>
  );
}

export default SimpleExplanation;
