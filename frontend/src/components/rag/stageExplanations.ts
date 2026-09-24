import type { LucideIcon } from "lucide-react";
import {
  AlertTriangle,
  Braces,
  Database,
  FileSearch,
  Globe,
  Layers,
  ListChecks,
  MessageSquareText,
  ScanSearch,
  ShieldCheck,
  Sparkles,
} from "lucide-react";

/**
 * What each pipeline stage means, in the order the pipeline runs them.
 *
 * WHY THIS IS A DATA FILE AND NOT JSX
 * -----------------------------------
 * The same explanation is needed in three places - the chat pipeline view, the
 * RAG Trace page, and the Laboratory - and it must not drift between them. Keeping
 * it as plain data means one edit updates all three, and the stage ORDER here is
 * checked against the backend's `stage_definitions()` by a test, so the frontend
 * cannot quietly disagree with the pipeline about what runs.
 *
 * Each entry answers the six questions the project holds itself to:
 *   what / why / how / where / what-if-removed / how-to-demonstrate.
 */
export interface StageExplanation {
  stage: string;
  label: string;
  icon: LucideIcon;
  /** One line, shown inline. */
  what: string;
  /**
   * The beginner sentence, shown FIRST in Learning Mode's stage list.
   * `what` is technically accurate but terse ("The question becomes a vector"),
   * which reads like a debug log to a student new to RAG. `simple` says the same
   * thing in plain words, and the technical `what`/`why` stay available below it
   * when the stage is expanded. Nothing is removed - the order is what changes.
   */
  simple: string;
  /** A tiny concrete example next to `simple`, where one helps ("tell me about ..." → "..."). */
  example?: string;
  /** Why the stage exists at all. */
  why: string;
  /** The honest cost of deleting it. */
  without: string;
  /** The file that implements it. */
  where: string;
  /** What to look at in a real run to see it working. */
  lookFor: string;
  /**
   * True when the stage appears only in a special case rather than in the normal
   * flow. Conditional stages are still explained, because a trace can contain them
   * and an unlabelled stage is a gap the reader cannot see.
   */
  conditional?: boolean;
}

export const STAGE_EXPLANATIONS: StageExplanation[] = [
  {
    stage: "query_analysis",
    label: "Query analysis",
    icon: MessageSquareText,
    simple: "Carinaa first understands what you asked and tidies up the question before searching.",
    example: "“tell me about design thinking tools” → “design thinking tools”",
    what: "The question is read and normalised before anything is searched.",
    why: "A question typed by a human is not a good search key. Normalising it first — whitespace, trailing punctuation, an empty query — keeps a stray character from changing the result.",
    without: "A malformed or empty question would reach the retriever and return arbitrary chunks, and the answer would look confident and be wrong.",
    where: "app/rag/pipeline.py",
    lookFor: "the normalised query echoed back. If you asked with trailing spaces, they are gone.",
  },
  {
    stage: "query_embedding",
    label: "Query embedding",
    icon: Braces,
    simple: "Carinaa converts your question into numbers so it can compare your question with document content by meaning.",
    what: "The question becomes a vector, using the SAME model that embedded the documents.",
    why: "Similarity only means something if both sides live in the same space. A question embedded by a different model would produce distances that look like numbers but mean nothing.",
    without: "Retrieval would compare vectors from two different models — the scores would still be numbers between -1 and 1, and every one of them would be meaningless.",
    where: "app/rag/embeddings.py",
    lookFor: "the model name and the 384 dimensions. It is the same model the ingestion pipeline used.",
  },
  {
    stage: "vector_search",
    label: "Vector search",
    icon: Database,
    simple: "Carinaa looks through the stored document chunks and finds passages that are similar in meaning to your question.",
    what: "The query vector is compared against every stored chunk vector by cosine similarity.",
    why: "This is the step that finds things by MEANING rather than by matching words. It is what lets a question about 'resource utilisation' find a paragraph about 'how efficiently hardware is used'.",
    without: "You would be back to keyword search. Ask about 'resource utilisation' and you would miss the paragraph that says 'how efficiently the hardware is used' — the words do not overlap, the meaning does.",
    where: "app/rag/vectorstore.py (ChromaDB)",
    lookFor: "candidates retrieved, and the top score. Low scores (below ~0.4) mean the workspace may not cover the question.",
  },
  {
    stage: "candidate_retrieval",
    label: "Candidate retrieval",
    icon: ScanSearch,
    simple: "The best-matching chunks are kept as candidates for the next step.",
    what: "The top `candidate_k` results are kept as the working pool.",
    why: "Retrieval happens in two numbers. `candidate_k` is RECALL — cast a wide net. `top_k` is PRECISION — keep the best few. Separating them means you can widen the net without flooding the prompt.",
    without: "You could not tell whether a bad answer came from searching too narrowly or from keeping too few. The two failures need opposite fixes.",
    where: "app/rag/retriever.py",
    lookFor: "candidates in vs chunks out. Widening `candidate_k` and getting the same chunks back proves the problem is upstream, in chunking or embedding.",
  },
  {
    stage: "reranking",
    label: "Re-ranking",
    icon: ListChecks,
    simple: "A second model checks the candidates more carefully and puts the most useful passages first.",
    what: "A cross-encoder reads the question and each candidate TOGETHER and reorders them.",
    why: "Dense retrieval compares two vectors computed separately, so it never sees the question and the chunk side by side. A cross-encoder does, and ranks more accurately.",
    without: "Nothing breaks — re-ranking is an optimisation. You keep fewer relevant chunks near the top of the prompt.",
    where: "app/rag/reranker.py",
    lookFor: "whether it says 'skipped'. It is OFF by default (basic RAG before advanced RAG). When skipped, the trace says so rather than pretending it ran.",
  },
  {
    stage: "context_building",
    label: "Context building",
    icon: Layers,
    simple: "Carinaa picks the useful passages and prepares them as numbered evidence for the AI.",
    what: "The chosen chunks are numbered [1], [2], [3]... and assembled into one block under a character budget.",
    why: "This is where the [n] a reader clicks is created. Numbering here is what lets the model cite a source and the UI resolve that citation back to a real chunk.",
    without: "The model could still answer, but there would be no way to say WHICH passage supported which sentence. The citations would be decoration.",
    where: "app/rag/context.py",
    lookFor: "the character budget and what was dropped. Chunks that do not fit are reported, never silently discarded.",
  },
  {
    stage: "llm_generation",
    label: "Generation",
    icon: Sparkles,
    simple: "The AI receives your question, the conversation context and the retrieved evidence, then writes the answer.",
    what: "The question plus the numbered context is sent to the language model.",
    why: "The model turns retrieved evidence into readable prose. It is the only stage that writes sentences, and the only one that can invent something.",
    without: "You would get the raw chunks back — accurate, but unreadable as an answer.",
    where: "app/llm/ (Gemini, Groq, or the local GGUF)",
    lookFor: "which provider actually answered. If Gemini failed and Groq answered, the label says 'Groq · Fallback' — it never claims the primary served it.",
  },
  {
    stage: "citation_resolution",
    label: "Citation resolution",
    icon: FileSearch,
    simple: "Carinaa links citations like [1] and [2] back to the exact document passages they came from.",
    what: "Every [n] in the answer is matched to the chunk it points at.",
    why: "This is what makes an answer checkable. It also catches fabrication: a [7] when only five excerpts were sent is reported as an invented citation, not quietly dropped.",
    without: "The markers would look like provenance and provide none — worse than having no citations, because they imply verification that never happened.",
    where: "app/rag/citations.py",
    lookFor: "the resolved sources with their page or section. Any number outside the retrieved set appears under 'invalid'.",
  },
  {
    stage: "grounding",
    label: "Grounding check",
    icon: ShieldCheck,
    simple: "Carinaa double-checks whether the answer is actually supported by the evidence it found, not just confident-sounding.",
    what: "The answer is compared against the evidence and given one of four verdicts.",
    why: "Citations prove that a marker points somewhere. Grounding asks the harder question: does the evidence actually SUPPORT what was claimed? They are different checks and both are needed.",
    without: "An answer could cite real chunks and still say something they do not support, with nothing to flag it.",
    where: "app/rag/grounding.py",
    lookFor: "SUPPORTED / PARTIALLY_SUPPORTED / INSUFFICIENT_EVIDENCE / CITATION_ERROR, and the reason. A low verdict is the system being honest, not broken.",
  },

  /* ------------------------------------------------------------------ *
   * Conditional stages
   *
   * These appear in a trace without being part of the fixed order, so they
   * are declared separately by the backend (`conditional_stage_definitions`).
   * They are explained here for the same reason as the rest: a stage the
   * interface cannot label is a gap the reader cannot see.
   * ------------------------------------------------------------------ */
  {
    stage: "web_search",
    label: "Web search",
    icon: Globe,
    conditional: true,
    simple: "Because you switched on web search, Carinaa also looked at public web pages and added them as separate, clearly-labelled sources.",
    what: "Public web results are fetched and added to the context alongside your documents.",
    why: "Some questions are only partly answerable from a workspace. Web results let the answer say what the documents do not cover, clearly separated from them.",
    without: "The answer would be limited to the uploaded documents, and questions reaching beyond them would be refused rather than answered with a labelled source.",
    where: "app/rag/pipeline.py",
    lookFor: "it appears only when web search is switched on for a question, and web sources are cited as [W1], [W2] — a different marker from [1], so the two can never be confused.",
  },
  {
    stage: "failsafe",
    label: "Offline failsafe",
    icon: AlertTriangle,
    conditional: true,
    simple: "No AI model was available, so Carinaa answered by quoting the relevant passages directly instead of guessing — and it says so on the answer.",
    what: "The answer is assembled by quoting retrieved passages directly, with no language model involved.",
    why: "The evidence was retrieved successfully and is genuinely relevant. Quoting it is a real answer, and a better outcome than an error message when the local model is unavailable.",
    without: "Offline mode would fail with an error even though the evidence needed to answer was already in hand.",
    where: "app/rag/failsafe.py",
    lookFor: "the answer is labelled as extractive in the UI, and the provider reads \"Extractive (no model)\". It is never presented as generated prose, and no online provider is contacted to produce it.",
  },
];

const BY_STAGE = new Map(STAGE_EXPLANATIONS.map((item) => [item.stage, item]));

export function stageExplanation(stage: string): StageExplanation | undefined {
  return BY_STAGE.get(stage);
}

/** The stages that make up the fixed flow, in execution order. */
export const STAGE_ORDER = STAGE_EXPLANATIONS.filter((item) => !item.conditional).map(
  (item) => item.stage,
);

/** Stages that can appear in a trace without being part of the fixed flow. */
export const CONDITIONAL_STAGE_ORDER = STAGE_EXPLANATIONS.filter(
  (item) => item.conditional,
).map((item) => item.stage);
