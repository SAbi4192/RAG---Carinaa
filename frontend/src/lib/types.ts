/**
 * API types.
 *
 * These mirror the Pydantic schemas in `backend/app/schemas/` one-for-one. They
 * are written by hand rather than generated, but they are written to MATCH - if
 * you change a schema on the server, change it here too.
 *
 * A note on `Record<string, unknown>`: the trace, retrieval and context payloads
 * are deliberately loosely typed. They are diagnostic data whose exact shape is
 * allowed to grow as the pipeline gains stages, and pretending otherwise would
 * mean a TypeScript error every time we add a metric. The fields the UI actually
 * reads are pulled out with narrow helpers in `lib/trace.ts`.
 */

/* ========================================================================== */
/* Auth                                                                        */
/* ========================================================================== */

export interface User {
  id: number;
  email: string;
  display_name: string;
  created_at: string;
  preferences: UserPreferences;
}

export interface UserPreferences {
  theme?: "light" | "dark" | "system";
  language?: string;
  default_ai_mode?: AiMode;
  default_top_k?: number;
  default_rerank?: boolean;
  learning_mode?: boolean;
  developer_mode?: boolean;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_at: string;
  user: User;
}

/* ========================================================================== */
/* Workspaces                                                                  */
/* ========================================================================== */

export interface WorkspaceStats {
  documents: number;
  documents_ready: number;
  documents_processing: number;
  documents_failed: number;
  chunks: number;
  vectors: number;
  conversations: number;
  messages: number;
  queries: number;
  pages: number;
  tokens_estimated: number;
  characters: number;
}

export interface Workspace {
  id: number;
  name: string;
  description: string;
  color: string;
  created_at: string;
  updated_at: string;
  stats?: WorkspaceStats | null;
}

export interface WorkspaceList {
  workspaces: Workspace[];
  total: number;
}

/* ========================================================================== */
/* Documents                                                                   */
/* ========================================================================== */

export type DocumentStatus = "pending" | "processing" | "ready" | "failed";

export interface DocumentRecord {
  id: number;
  workspace_id: number;
  original_filename: string;
  file_type: string;
  size_bytes: number;
  status: DocumentStatus | string;
  stage: string;
  progress: number;
  error_message: string | null;
  page_count: number;
  chunk_count: number;
  token_estimate: number;
  char_count: number;
  embedding_model: string;
  embedding_dim: number;
  doc_metadata: Record<string, unknown>;
  stage_timings: Record<string, unknown>;
  created_at: string;
  processed_at: string | null;
}

export interface DocumentList {
  documents: DocumentRecord[];
  total: number;
}

export interface StageDefinition {
  key: string;
  label: string;
  description?: string;
}

export interface DocumentProgress {
  document_id: number;
  status: string;
  stage: string;
  stage_label: string;
  progress: number;
  percent: number;
  error: string | null;
  chunk_count: number;
  stages: StageDefinition[];
}

export interface ChunkPreview {
  id: number;
  chunk_index: number;
  content: string;
  char_start: number;
  char_end: number;
  token_estimate: number;
  block_type: string;
  doc_metadata: Record<string, unknown>;
}

export interface UploadResponse {
  document: DocumentRecord;
  message: string;
  ingestion_started: boolean;
}

export interface SupportedTypes {
  extensions: string[];
  types?: { extension: string; label: string; parser: string }[];
  max_upload_mb?: number;
}

/* ========================================================================== */
/* Conversations & answers                                                     */
/* ========================================================================== */

export type AiMode = "online" | "offline";
export type ShortenLevel = "normal" | "short" | "very_short";

export interface Conversation {
  id: number;
  workspace_id: number;
  title: string;
  ai_mode: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ConversationList {
  conversations: Conversation[];
  total: number;
}

export interface Citation {
  number: number;
  marker: string;
  kind: string;
  location_label: string;
  document_id: number | null;
  document_name: string;
  file_type: string;
  chunk_id: number | null;
  chunk_index: number | null;
  page_number: number | null;
  page_end: number | null;
  slide_number: number | null;
  sheet_name: string | null;
  section: string | null;
  row_start: number | null;
  row_end: number | null;
  json_path: string | null;
  snippet: string;
  relevance: number;
  rank: number;
  url: string;
  title: string;
}

export type GroundingStatus =
  | "SUPPORTED"
  | "PARTIALLY_SUPPORTED"
  | "INSUFFICIENT_EVIDENCE"
  | "CITATION_ERROR";

export interface Grounding {
  status: GroundingStatus | string;
  label: string;
  tone: string;
  reason: string;
  refused: boolean;
  checks: Record<string, unknown>;
  counts: Record<string, number>;
  cited_numbers: number[];
  unused_numbers: number[];
  invalid_numbers: number[];
  citation_count: number;
  excerpt_count: number;
  top_score: number;
  disclaimer: string;
}

export interface Message {
  id: number;
  conversation_id: number;
  role: "user" | "assistant" | string;
  content: string;
  ai_mode: string;
  provider: string;
  model: string;
  used_fallback: boolean;
  fallback_reason: string;
  grounding_status: string;
  grounding_detail: Record<string, unknown>;
  latency_ms: number;
  token_usage: Record<string, unknown>;
  trace_id: string;
  retrieval: Record<string, unknown>;
  citations: Citation[];
  web_search_used: boolean;
  web_sources: unknown[];
  created_at: string;
  /**
   * NOTE: there is deliberately no `is_extractive_failsafe` here.
   *
   * `MessageOut` never carries that flag - the backend sets it only at the top
   * level of `AskResponse` (see below). An earlier version of this interface
   * declared it as optional on `Message`, which was a lie that TypeScript could
   * not catch: the field was always `undefined`, so a `message.is_extractive_failsafe`
   * check silently never fired and the fail-safe disclosure banner never rendered.
   *
   * Per-message, derive it from `model === "extractive"`. The pipeline sets exactly
   * that model name when the offline fail-safe produces the answer, and it is stored
   * on the row - so the derivation works both on `/chat/ask` and after a reload.
   * `components/chat/MessageBubble.tsx` does this once in `isFailsafe`; use that.
   */
}

/**
 * The trace returned inline with an answer.
 *
 * Typed rather than left as a loose record: Learning Mode builds its whole view from
 * this shape, and an `unknown` here meant every field access needed a cast - which is
 * exactly how a rename in the backend would slip through unnoticed.
 */
export interface TraceSummary {
  trace_id: string;
  event_count: number;
  total_ms: number;
  stages: TraceStage[];
  summary: Record<string, unknown>;
  question?: string;
  note?: string;
}

export interface AskResponse {
  message: Message;
  conversation_id: number;
  answer: string;
  provider_label: string;
  is_extractive_failsafe: boolean;
  grounding: Grounding | null;
  citations: Citation[];
  retrieval: Record<string, unknown>;
  context: Record<string, unknown>;
  trace: TraceSummary;
  web_sources: Record<string, unknown>[];
  available_variants: Variant[];
}

export interface AskRequest {
  question: string;
  workspace_id: number;
  conversation_id?: number | null;
  mode?: AiMode;
  top_k?: number | null;
  candidate_k?: number | null;
  document_ids?: number[] | null;
  use_rerank?: boolean | null;
  use_web_search?: boolean;
  language?: string;
  /** Detailed Answer presentation style (see ANSWER_STYLES on the server). */
  answer_style?: string;
}

export interface ConversationDetail {
  conversation: Conversation;
  messages: Message[];
}

/* ========================================================================== */
/* RAG Trace                                                                   */
/* ========================================================================== */

export interface TraceStage {
  seq: number;
  stage: string;
  label: string;
  status: "ok" | "skipped" | "error" | string;
  duration_ms: number;
  data: Record<string, unknown>;
  /**
   * ISO timestamp of when the stage finished. Present on events recorded after
   * this was added; absent on older ones, and the UI handles that by omitting the
   * time column rather than inventing one.
   */
  created_at?: string | null;
}

export interface Trace {
  trace_id: string;
  message_id: number;
  event_count: number;
  total_ms: number;
  stages: TraceStage[];
  summary: Record<string, unknown>;
  /**
   * The question THIS answer belongs to. The server resolves the paired user
   * turn, so the panel can label a run even when the conversation was loaded
   * from history and no ask-response is in memory.
   */
  question: string;
  note: string;
}

/* ========================================================================== */
/* Streaming ask (Server-Sent Events)                                          */
/* ========================================================================== */

export interface AskStreamReady {
  conversation_id: number;
  user_message_id: number;
  trace_id: string;
  mode: string;
}

/** A real trace event, forwarded the instant the backend recorded it. */
export interface AskStreamStage {
  seq: number;
  stage: string;
  label: string;
  status: string;
  duration_ms: number;
  data: Record<string, unknown>;
  created_at?: string | null;
}

/* ========================================================================== */
/* Presentation features                                                       */
/* ========================================================================== */

export interface Language {
  code: string;
  name: string;
  native_name: string;
  speech_code: string;
  speech_candidates?: string[];
  rtl?: boolean;
}

export interface ShortenLevelInfo {
  value: ShortenLevel | string;
  label: string;
  description: string;
}

export interface Variant {
  message_id: number;
  kind: "translated" | "shortened" | "explanation" | "detailed" | string;
  language: string;
  level: string;
  content: string;
  citations: Citation[];
  validation: Record<string, unknown>;
  provider: string;
  model: string;
  cached: boolean;
  warning: string;
  original_unchanged: boolean;
}

export interface SpeechPayload {
  message_id: number;
  language: string;
  speech_code: string;
  /** Ordered preferred BCP-47 tags. Try each before declaring no voice exists. */
  speech_candidates?: string[];
  text: string;
  characters: number;
  voice_hint: string;
  note: string;
}

export interface ExplainPayload {
  message_id: number;
  explanation: string;
  provider: string;
  model: string;
  used_fallback: boolean;
  cached: boolean;
}

export interface Capabilities {
  languages?: Language[];
  shorten_levels?: ShortenLevelInfo[];
  web_search?: { available: boolean; enabled: boolean; reason?: string };
  offline?: { available: boolean; model?: string };
  [key: string]: unknown;
}

/* ========================================================================== */
/* Playground                                                                  */
/* ========================================================================== */

export interface RetrievedChunk {
  chunk_id: number | null;
  vector_id: string;
  document_id: number;
  document_name: string;
  chunk_index: number | null;
  content: string;
  score: number;
  rank: number;
  metadata: Record<string, unknown>;
  original_score?: number | null;
  original_rank?: number | null;
}

export interface RetrievalOutcome {
  query: string;
  keywords?: string[];
  /**
   * How many neighbours were fetched before filtering. `RetrievalOutcome.as_dict()`
   * emits `candidates_retrieved`; some responses use `candidates`. Both are declared
   * because reading the wrong one silently yields 0 rather than failing.
   */
  candidates_retrieved?: number;
  candidates?: number;
  returned: number;
  top_score: number;
  /** Mean score across the returned chunks - a better "did this match well?" signal
   *  than the top score alone. */
  mean_score?: number;
  threshold?: number;
  reranked: boolean;
  rerank_reason?: string;
  /** How many near-identical candidates were collapsed, and how many fell below
   *  `threshold`. */
  duplicates_removed?: number;
  below_threshold?: number;
  chunks: RetrievedChunk[];
  timings?: Record<string, number>;
}

export interface RetrieveResponse {
  retrieval: RetrievalOutcome | Record<string, unknown>;
  context: Record<string, unknown>;
  trace: Record<string, unknown>;
  generated: boolean;
  note: string;
  error?: string;
}

/* ---- Retrieval Lab: dense vs BM25 vs hybrid ------------------------------ */

export type RetrievalMode = "dense" | "bm25" | "hybrid";

/** One chunk's result entry within a single mode's column. */
export interface CompareResult {
  vector_id: string | null;
  chunk_id: number | null;
  label: string | null;
  document_name: string | null;
  metadata: Record<string, unknown>;
  rank: number | null;
  score: number;
  rrf_score: number | null;
  bm25_score: number | null;
  preview: string | null;
}

/** One retriever's column of results. A mode can also have failed alone. */
export interface CompareColumn {
  mode?: RetrievalMode;
  score_scale?: string;
  search_ms?: number;
  results?: CompareResult[];
  error?: string;
}

/** One row of the comparison matrix: a chunk, and its rank in every mode. */
export interface CompareRow {
  vector_id: string;
  label: string | null;
  document_name: string | null;
  preview: string;
  ranks: Record<RetrievalMode, number | null>;
  agreement: number;
  found_by: RetrievalMode[];
  best_rank: number;
}

export interface RetrieveCompareResponse {
  question: string;
  columns: Record<RetrievalMode, CompareColumn>;
  comparison: CompareRow[];
  stats: {
    total_unique_chunks: number;
    found_by_all_three: number;
    top_disagreements: Array<{
      label: string | null;
      document_name: string | null;
      found_by: RetrievalMode[];
    }>;
  };
}

/* ========================================================================== */
/* Settings                                                                    */
/* ========================================================================== */

/** One answer engine as the SERVER reports it: a ROLE, never a vendor.

    The backend sanitises its provider chain down to these three roles
    (app/core/sanitize.public_engines). A cloud implementation's name never
    appears on this object - if one did, the type would have to say so, and it
    does not, because the contract is that it must not.
*/
export interface EngineInfo {
  /** "primary" | "fallback" | "offline" */
  role: string;
  label: string;
  /** Present only for the local model, which the user configured themselves. */
  name?: string | null;
  configured: boolean;
  available: boolean;
  reason?: string;
  guarantee?: string;
}

export interface EngineModeStatus {
  available: boolean;
  reason?: string;
}

export interface EngineReport {
  engines: EngineInfo[];
  modes: {
    online: EngineModeStatus;
    offline: EngineModeStatus;
    default_mode: string;
  };
  note?: string;
}

export interface RagSettings {
  chunk_size: number;
  chunk_overlap: number;
  top_k: number;
  candidate_k: number;
  score_threshold: number;
  max_context_chars: number;
  rerank_enabled: boolean;
  embedding_model: string;
  embedding_dim: number;
  [key: string]: unknown;
}

export interface SystemStats {
  documents: number;
  chunks: number;
  messages: number;
  queries: number;
  vector_store: Record<string, unknown>;
  embedding_model: string;
  database: string;
  storage: Record<string, unknown>;
}

/* ========================================================================== */
/* Analytics & evaluation                                                      */
/* ========================================================================== */

export interface AnalyticsOverview {
  workspaces: number;
  documents: number;
  chunks: number;
  conversations: number;
  queries: number;
  /** Count of recorded queries per grounding verdict. */
  grounding?: Record<string, number>;
  grounding_labels?: Record<string, string>;
  /** Queries that used web augmentation (opt-in, never implicit). */
  web_searches?: number;
  /** Mean wall-clock time per query, in milliseconds. */
  avg_total_ms?: number;
  note?: string;
}

export interface RetrievalRecentRow {
  provider: string;
  model: string;
  ai_mode: string;
  grounding_status: string;
  top_score: number;
  retrieved: number;
  used: number;
  citations: number;
  total_ms: number;
}

export interface RetrievalAnalytics {
  total: number;
  note?: string;
  /** Share of answers whose grounding verdict was SUPPORTED. */
  faithfulness?: number;
  citation_correctness?: number;
  citation_coverage?: number;
  refusal_rate?: number;
  refusal_accuracy?: number;
  evidence_yield?: number;
  mean_top_similarity?: number;
  mean_latency_ms?: number;
  /** Lexical-overlap proxy, NOT a human quality judgement. */
  answer_relevance_proxy?: number;
  counts?: {
    supported: number;
    partially_supported: number;
    insufficient_evidence: number;
    citation_error: number;
  };
  /** The backend states its own limitations here. Shown verbatim. */
  caveats?: string[];
  latency?: {
    p50_ms: number;
    p95_ms: number;
    max_ms: number;
    retrieval_p50_ms: number;
    retrieval_p95_ms: number;
  };
  providers?: Record<string, number>;
  modes?: Record<string, number>;
  /**
   * Which documents the answers actually cited, most-cited first.
   *
   * Counted from the stored citations of recent answers - a document with no
   * citations is simply absent rather than shown as zero, because "never cited"
   * and "not counted yet" are different facts.
   */
  documents?: DocumentUsage[];
  /** Queries in this window that used web augmentation. */
  web_searches?: number;
  recent?: RetrievalRecentRow[];
}

/** One document's share of the citations across recent answers. */
export interface DocumentUsage {
  name: string;
  citations: number;
  queries: number;
}

/**
 * How long one pipeline stage took, aggregated over recorded traces.
 *
 * Computed from the persisted `trace_events` rows, so it is the real measured
 * duration of that stage - not an estimate derived from the total.
 */
export interface StageTiming {
  stage: string;
  label: string;
  count: number;
  avg_ms: number;
  p50_ms: number;
  p95_ms: number;
  total_ms: number;
  skipped: number;
  errors: number;
}

export interface StageAnalytics {
  total_queries: number;
  stages: StageTiming[];
  /** Average end-to-end pipeline time across the same traces. */
  avg_total_ms?: number;
  note?: string;
}

export interface ActivityPoint {
  date: string;
  queries: number;
  refusals: number;
  citation_errors: number;
  /** Mean wall-clock time for that day's queries, in milliseconds. */
  avg_ms?: number;
  /** Queries that used web augmentation that day. */
  web_searches?: number;
}

export interface ActivityAnalytics {
  days: number;
  series: ActivityPoint[];
}

export interface SecurityCheckResult {
  name: string;
  title: string;
  passed: boolean;
  skipped?: boolean;
  detail: string;
  evidence?: Record<string, unknown>;
}

export interface SecurityReport {
  checks: SecurityCheckResult[];
  passed: number;
  failed: number;
  skipped: number;
  note: string;
  [key: string]: unknown;
}

/* ========================================================================== */
/* Errors                                                                      */
/* ========================================================================== */

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    detail?: Record<string, unknown>;
  };
}

/* ------------------------------------------------------------------ *
 * RAG Laboratory
 * ------------------------------------------------------------------ */

/**
 * One vector as returned by the Embedding Lab.
 *
 * TRUNCATED for display: the model uses 384 dimensions and the API sends only
 * the first `preview_dimensions` of them. The response says how many, so the UI
 * never implies the user is seeing the whole vector.
 */
export interface LabEmbedResponse {
  model: string;
  dimensions: number;
  preview_dimensions: number;
  count: number;
  vectors: number[][];
  norms: number[];
  /** Pairwise cosine similarity, `count` x `count`. */
  similarity: number[][];
  projection: LabProjectionPoint[];
  /**
   * Fraction of variance each projected axis explains. Reported so the user can
   * judge how much the 2-D picture is hiding.
   */
  projection_explained_variance: number[];
  projection_note: string;
  note: string;
  query?: LabEmbedQuery;
}

export interface LabProjectionPoint {
  x: number;
  y: number;
}

export interface LabEmbedQuery {
  text: string;
  vector: number[];
  ranking: { index: number; score: number }[];
  note: string;
}

export interface LabStage {
  stage: string;
  label: string;
}

export interface LabStagesResponse {
  stages: LabStage[];
  note: string;
}

/** A chunk produced by the real chunker, as returned by /documents/preview-chunks. */
export interface PreviewChunk {
  chunk_index: number;
  content: string;
  characters: number;
  tokens_estimated: number;
  char_start: number;
  char_end: number;
  block_type: string;
  /** Whole blocks repeated from the previous chunk. Whole-block overlap is what
   *  makes every character map back to exactly one source block. */
  overlap_blocks: number;
}

export interface PreviewChunksResponse {
  chunk_size: number;
  chunk_overlap: number;
  chunks: PreviewChunk[];
  count: number;
  note: string;
}

/**
 * One numbered piece of evidence handed to the model.
 *
 * The `number` here IS the `[n]` the answer cites. It is assigned per question,
 * not per chunk, which is why the same chunk can be [2] in one answer and [5] in
 * another.
 */
export interface ContextExcerpt {
  number: number;
  chunk_id: number | null;
  vector_id: string;
  document_id: number;
  document_name: string;
  file_type: string;
  content: string;
  metadata: Record<string, unknown>;
  score: number;
  rank: number;
  chunk_index: number | null;
  original_score: number | null;
  original_rank: number | null;
  /** Human-readable location, e.g. "Cloud.pdf · p.32". */
  label: string;
}

export interface ContextBundleOut {
  excerpt_count: number;
  characters: number;
  budget: number;
  dropped: number;
  truncated: boolean;
  notes: string[];
  excerpts: ContextExcerpt[];
}

/**
 * One document attached to a conversation.
 *
 * `is_active` is the selection WITHIN the chat: a conversation can have five
 * documents attached while only two are searched. Deactivating narrows retrieval;
 * it deletes nothing.
 */
export interface ConversationDocumentLink {
  document_id: number;
  original_filename: string;
  file_type: string;
  status: string;
  chunk_count: number;
  is_active: boolean;
  added_at: string | null;
}

/**
 * The knowledge scope for one conversation.
 *
 * `scope` is "chat" when at least one document is active, and "workspace"
 * otherwise. "workspace" is the fallback, not an error - a chat with no
 * attachments searches the whole workspace so Carinaa still works as a general
 * knowledge-base assistant.
 */
export interface ConversationScope {
  conversation_id: number;
  documents: ConversationDocumentLink[];
  active_document_ids: number[];
  scope: "chat" | "workspace" | string;
  workspace_document_count: number;
  note: string;
  removed_from_chat_only?: boolean;
}
