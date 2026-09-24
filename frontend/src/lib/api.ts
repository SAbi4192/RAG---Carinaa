/**
 * API client.
 *
 * DESIGN NOTES
 * ------------
 * 1. EVERY error becomes an `ApiError` with a human-readable message. The server
 *    already returns a clean `{error: {code, message, detail}}` envelope and never
 *    a stack trace, so the client's job is to preserve that message rather than
 *    replace it with "Request failed". If the server says "That workspace has
 *    reached its limit of 12 documents.", that is exactly what the user should
 *    read - it is more useful than anything we could invent here.
 *
 * 2. The base URL is relative (`/api`). In development Vite proxies it to the
 *    backend, and in production the backend serves the built frontend, so the
 *    client never needs to know where the server lives. That also means there is
 *    no CORS surface and no API host baked into the bundle.
 *
 * 3. The auth token lives in localStorage under one key. It is a short-lived JWT
 *    and it carries no privileges by itself - the server derives the user from it
 *    and never trusts anything else the client sends.
 */

import type {
  ActivityAnalytics,
  AnalyticsOverview,
  AskRequest,
  AskResponse,
  AskStreamReady,
  AskStreamStage,
  Capabilities,
  ChunkPreview,
  Conversation,
  ConversationScope,
  ConversationDetail,
  ConversationList,
  DocumentList,
  DocumentProgress,
  DocumentRecord,
  ExplainPayload,
  Grounding,
  LabEmbedResponse,
  LabStagesResponse,
  Language,
  PreviewChunksResponse,
  ProviderReport,
  RagSettings,
  RetrieveCompareResponse,
  RetrieveResponse,
  RetrievalAnalytics,
  SecurityReport,
  ShortenLevelInfo,
  SpeechPayload,
  StageAnalytics,
  SupportedTypes,
  SystemStats,
  TokenResponse,
  Trace,
  UploadResponse,
  User,
  UserPreferences,
  Variant,
  Workspace,
  WorkspaceList,
} from "./types";

export const TOKEN_KEY = "carinaa.token";

/* ========================================================================== */
/* Errors                                                                      */
/* ========================================================================== */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: Record<string, unknown>;

  constructor(
    message: string,
    status: number,
    code = "error",
    detail: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }

  /** 401 means the session is gone - the caller should send the user to sign in. */
  get isAuthError(): boolean {
    return this.status === 401;
  }

  /** 404 is used for "not yours" as well as "not found", deliberately. */
  get isNotFound(): boolean {
    return this.status === 404;
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

/* ========================================================================== */
/* Token storage                                                               */
/* ========================================================================== */

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private browsing - the session simply will not persist */
  }
}

export function clearToken(): void {
  setToken("");
}

/* ========================================================================== */
/* Core request                                                                */
/* ========================================================================== */

interface RequestOptions {
  method?: string;
  body?: unknown;
  /** Raw body for uploads. */
  raw?: BodyInit;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** Skip the Authorization header (used by register/login). */
  anonymous?: boolean;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, raw, headers = {}, signal, anonymous } = options;

  const finalHeaders: Record<string, string> = { Accept: "application/json", ...headers };

  if (!anonymous) {
    const token = getToken();
    if (token) finalHeaders.Authorization = `Bearer ${token}`;
  }

  let payload: BodyInit | undefined = raw;
  if (body !== undefined && raw === undefined) {
    payload = JSON.stringify(body);
    finalHeaders["Content-Type"] = "application/json";
  }

  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers: finalHeaders,
      body: payload,
      signal,
    });
  } catch (cause) {
    // The request never reached the server. Distinguish "we cancelled it" from
    // "the server is unreachable", because only one of those is an error worth
    // showing the user.
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(
      "Could not reach the Carinaa server. Is the backend running?",
      0,
      "network_error",
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }

  if (!response.ok) {
    const envelope = parsed as { error?: { code?: string; message?: string; detail?: Record<string, unknown> } } | null;
    const info = envelope?.error;
    throw new ApiError(
      info?.message || `Request failed with status ${response.status}.`,
      response.status,
      info?.code || "error",
      info?.detail ?? {},
    );
  }

  return parsed as T;
}

/* ========================================================================== */
/* Server-Sent Events for real ingestion progress                              */
/* ========================================================================== */

export interface ProgressHandlers {
  onProgress: (progress: DocumentProgress) => void;
  onDone?: (progress: DocumentProgress) => void;
  onError?: (message: string) => void;
}

/**
 * Subscribe to a document's real ingestion progress.
 *
 * `EventSource` cannot send an Authorization header, which is why the backend
 * accepts the token as a query parameter on this one endpoint. The token is a
 * short-lived JWT and the endpoint still verifies ownership, so this does not
 * widen access - it just makes the stream possible.
 *
 * Returns an unsubscribe function. Callers MUST call it on unmount, otherwise a
 * navigating user leaves a connection open.
 */
export function streamDocumentProgress(
  documentId: number,
  handlers: ProgressHandlers,
): () => void {
  const token = getToken();
  const url = `/api/documents/${documentId}/progress/stream?token=${encodeURIComponent(token)}`;
  const source = new EventSource(url);
  let finished = false;

  const finish = (progress: DocumentProgress) => {
    if (finished) return;
    finished = true;
    handlers.onDone?.(progress);
    source.close();
  };

  source.onmessage = (event) => {
    let progress: DocumentProgress;
    try {
      progress = JSON.parse(event.data) as DocumentProgress;
    } catch {
      return;
    }
    handlers.onProgress(progress);
    if (progress.status === "ready" || progress.status === "failed") {
      finish(progress);
    }
  };

  source.onerror = () => {
    if (finished) return;
    finished = true;
    source.close();
    // A dropped stream is not fatal: the caller can fall back to polling. We
    // report it so the UI can say "live updates stopped" rather than silently
    // freezing at 40%.
    handlers.onError?.("Live progress updates stopped. Falling back to polling.");
  };

  return () => {
    finished = true;
    source.close();
  };
}

/* ========================================================================== */
/* Endpoints                                                                   */
/* ========================================================================== */

export const api = {
  /* ---- health -------------------------------------------------------- */
  health: () => request<{ status: string; [key: string]: unknown }>("/health"),
  healthDeep: () => request<Record<string, unknown>>("/health/deep"),

  /* ---- auth ---------------------------------------------------------- */
  auth: {
    register: (email: string, password: string, displayName = "") =>
      request<TokenResponse>("/auth/register", {
        method: "POST",
        body: { email, password, display_name: displayName },
        anonymous: true,
      }),

    login: (email: string, password: string) =>
      request<TokenResponse>("/auth/login", {
        method: "POST",
        body: { email, password },
        anonymous: true,
      }),

    me: () => request<User>("/auth/me"),

    updatePreferences: (preferences: Partial<UserPreferences>) =>
      request<User>("/auth/preferences", { method: "PATCH", body: preferences }),

    logout: () => request<void>("/auth/logout", { method: "POST" }),
  },

  /* ---- workspaces ---------------------------------------------------- */
  workspaces: {
    list: () => request<WorkspaceList>("/workspaces"),
    create: (name: string, description = "", color = "violet") =>
      request<Workspace>("/workspaces", { method: "POST", body: { name, description, color } }),
    get: (id: number) => request<Workspace>(`/workspaces/${id}`),
    update: (id: number, patch: Partial<Pick<Workspace, "name" | "description" | "color">>) =>
      request<Workspace>(`/workspaces/${id}`, { method: "PATCH", body: patch }),
    remove: (id: number) => request<void>(`/workspaces/${id}`, { method: "DELETE" }),
  },

  /* ---- documents ----------------------------------------------------- */
  documents: {
    supportedTypes: () => request<SupportedTypes>("/documents/supported-types"),

    list: (workspaceId: number) =>
      request<DocumentList>(`/workspaces/${workspaceId}/documents`),

    get: (documentId: number) => request<DocumentRecord>(`/documents/${documentId}`),

    upload: (workspaceId: number, file: File) => {
      const form = new FormData();
      form.append("file", file);
      return request<UploadResponse>(`/workspaces/${workspaceId}/documents`, {
        method: "POST",
        raw: form,
      });
    },

    remove: (documentId: number) =>
      request<void>(`/documents/${documentId}`, { method: "DELETE" }),

    progress: (documentId: number) =>
      request<DocumentProgress>(`/documents/${documentId}/progress`),

    chunks: (documentId: number, limit = 50, offset = 0) =>
      request<ChunkPreview[]>(
        `/documents/${documentId}/chunks?limit=${limit}&offset=${offset}`,
      ),

    /** Where a chunk sits in the document's ordered list (for deep-linking a
     *  citation to its exact position, across pagination). */
    chunkPosition: (documentId: number, chunkId: number) =>
      request<{ chunk_id: number; chunk_index: number; ordinal: number }>(
        `/documents/${documentId}/chunks/${chunkId}/position`,
      ),

    chunkStats: (documentId: number) =>
      request<Record<string, unknown>>(`/documents/${documentId}/chunk-stats`),

    reindex: (documentId: number) =>
      request<Record<string, unknown>>(`/documents/${documentId}/reindex`, {
        method: "POST",
      }),

    stale: (workspaceId: number) =>
      request<{ documents: DocumentRecord[]; total: number; note?: string }>(
        `/workspaces/${workspaceId}/stale-documents`,
      ),

    /**
     * Run the REAL chunker over pasted text. Nothing is stored and no model is
     * called, which is what makes it safe to call on every slider move.
     */
    previewChunks: (payload: { text: string; chunk_size: number; chunk_overlap: number }) =>
      request<PreviewChunksResponse>("/documents/preview-chunks", {
        method: "POST",
        body: payload,
      }),
  },

  /* ---- chat ---------------------------------------------------------- */
  chat: {
    conversations: (workspaceId?: number) =>
      request<ConversationList>(
        workspaceId ? `/conversations?workspace_id=${workspaceId}` : "/conversations",
      ),

    createConversation: (workspaceId: number, title = "New conversation", aiMode = "online") =>
      request<Conversation>("/conversations", {
        method: "POST",
        body: { workspace_id: workspaceId, title, ai_mode: aiMode },
      }),

    conversation: (id: number) => request<ConversationDetail>(`/conversations/${id}`),

    /* ---- chat-scoped documents ------------------------------------------
       Attaching is a reference, not a copy: the document stays in the workspace
       and can be attached to several conversations. Detaching removes it from
       the chat only - see the `removed_from_chat_only` flag in the response. */
    conversationDocuments: (conversationId: number) =>
      request<ConversationScope>(`/conversations/${conversationId}/documents`),

    attachDocument: (conversationId: number, documentId: number) =>
      request<ConversationScope>(`/conversations/${conversationId}/documents`, {
        method: "POST",
        body: { document_id: documentId },
      }),

    setDocumentActive: (conversationId: number, documentId: number, isActive: boolean) =>
      request<ConversationScope>(
        `/conversations/${conversationId}/documents/${documentId}`,
        { method: "PATCH", body: { is_active: isActive } },
      ),

    detachDocument: (conversationId: number, documentId: number) =>
      request<ConversationScope>(
        `/conversations/${conversationId}/documents/${documentId}`,
        { method: "DELETE" },
      ),

    updateConversation: (id: number, patch: { title?: string; ai_mode?: string }) =>
      request<Conversation>(`/conversations/${id}`, { method: "PATCH", body: patch }),

    deleteConversation: (id: number) =>
      request<void>(`/conversations/${id}`, { method: "DELETE" }),

    ask: (payload: AskRequest) =>
      request<AskResponse>("/chat/ask", { method: "POST", body: payload }),

    /** Ask a question, receiving REAL server-pushed events.
     *
     * `fetch` + a ReadableStream reader, not `EventSource`, because the request
     * must carry an Authorization header and a JSON body. That is the standard
     * way to consume POST-over-SSE, and it is why the backend and the UI can
     * share one event protocol.
     *
     * Callback contract (matches the backend exactly):
     *   onReady  - the user turn is persisted; caller learns its ids
     *   onStage  - a real trace event, fired the moment that stage finished
     *   onToken  - a real provider fragment, in order
     *   onDone   - the canonical, cited, grounded answer + trace summary
     *   onError  - a failure during streaming; may carry a partial trace
     *
     * IMPORTANT: the answer is only canonical when `onDone` fires. A mid-stream
     * `onError` means the streamed text was a draft that should be discarded -
     * never leave partial streamed text committed as if it were the answer.
     *
     * Pass an AbortSignal to cancel (e.g. the user stopped the generation).
     */
    askStream: async (
      payload: AskRequest,
      handlers: {
        onReady?: (info: AskStreamReady) => void;
        onStage?: (event: AskStreamStage) => void;
        onToken?: (text: string) => void;
        onDone?: (response: AskResponse) => void;
        onError?: (err: ApiError) => void;
      },
      signal?: AbortSignal,
    ): Promise<void> => {
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      };
      const token = getToken();
      if (token) headers.Authorization = `Bearer ${token}`;

      let response: Response;
      try {
        response = await fetch(`/api/chat/ask/stream`, {
          method: "POST",
          headers,
          body: JSON.stringify(payload),
          signal,
        });
      } catch (cause) {
        if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
        throw new ApiError(
          "Could not reach the Carinaa server. Is the backend running?",
          0,
          "network_error",
        );
      }

      // A non-2xx BEFORE the stream starts is a normal HTTP error (bad request,
      // ambiguous page, expired session). Surface it as `error` so the caller has
      // one code path, but keep the status for auth detection.
      if (!response.ok) {
        let envelope: { error?: { code?: string; message?: string; detail?: Record<string, unknown> } } | null = null;
        try {
          envelope = await response.json();
        } catch {
          /* an empty body still means "something failed"; we use the status. */
        }
        handlers.onError?.(
          new ApiError(
            envelope?.error?.message || `Request failed with status ${response.status}.`,
            response.status,
            envelope?.error?.code || "error",
            envelope?.error?.detail || {},
          ),
        );
        return;
      }

      if (!response.body) {
        handlers.onError?.(
          new ApiError("The server did not return a stream.", 0, "no_stream"),
        );
        return;
      }

      await consumeSse(response.body, handlers);
    },


    retrieve: (payload: {
      workspace_id: number;
      question: string;
      top_k?: number;
      candidate_k?: number;
      document_ids?: number[];
      use_rerank?: boolean;
      /** Structural filters, used by the Reference and Scope labs. */
      page_number?: number;
      section?: string;
      conversation_id?: number;
      /** Retriever mode for the lab: dense / bm25 / hybrid. */
      mode?: "dense" | "bm25" | "hybrid";
    }) => request<RetrieveResponse>("/chat/retrieve", { method: "POST", body: payload }),

    /** Compare the three retrievers over ONE question and scope (Retrieval Lab).
     *
     * The comparison runs server-side so all three modes see the identical
     * population and the three columns are genuinely comparable. See the backend
     * docstring: it deliberately runs them sequentially because they share a
     * request-scoped database session, which is not thread-safe.
     */
    retrieveCompare: (payload: {
      workspace_id: number;
      question: string;
      top_k?: number;
      candidate_k?: number;
      document_ids?: number[];
      conversation_id?: number;
    }) => request<RetrieveCompareResponse>("/chat/retrieve/compare", { method: "POST", body: payload }),

    trace: (messageId: number) => request<Trace>(`/messages/${messageId}/trace`),

    /** Download an answer's Evidence Pack (Markdown or print-ready HTML).
     *
     * The server returns the file as an attachment, but the request needs the
     * Authorization header, so a plain <a href> download cannot be used here.
     * We fetch the bytes, hand them to the browser as a Blob, and let the
     * Content-Disposition filename drive the save. A failure throws ApiError so
     * the caller can surface it rather than starting a download that yields an
     * empty file.
     */
    async downloadEvidencePack(
      messageId: number,
      format: "md" | "html",
    ): Promise<void> {
      const headers: Record<string, string> = {};
      const token = getToken();
      if (token) headers.Authorization = `Bearer ${token}`;

      const response = await fetch(
        `/api/messages/${messageId}/export?format=${format}`,
        { headers },
      );
      if (!response.ok) {
        let detail = "";
        try {
          detail = (await response.json())?.error?.message ?? "";
        } catch {
          /* an error body that is not JSON is still an error */
        }
        throw new ApiError(
          detail || `The export failed (${response.status}).`,
          response.status,
          "export_failed",
        );
      }
      const blob = await response.blob();
      const disposition = response.headers.get("Content-Disposition") || "";
      const match = /filename="?([^";]+)"?/i.exec(disposition);
      const filename = match?.[1] || `carinaa-evidence-${messageId}.${format}`;

      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      // Revoke on the next tick so the download has begun before the object URL
      // disappears; revoking synchronously can abort the save in some browsers.
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    },

    deleteMessage: (messageId: number) =>
      request<void>(`/messages/${messageId}`, { method: "DELETE" }),
  },

  /* ---- presentation features ----------------------------------------- */
  features: {
    languages: () => request<{ languages: Language[]; default: string }>("/features/languages"),

    shortenLevels: () =>
      request<{ levels: ShortenLevelInfo[] }>("/features/shorten-levels"),

    capabilities: () => request<Capabilities>("/features/capabilities"),

    translate: (messageId: number, language: string) =>
      request<Variant>("/features/translate", {
        method: "POST",
        body: { message_id: messageId, language },
      }),

    // Translate the Learning panel's explanation. Deliberately separate from
    // `translate`: teaching prose is not a grounded answer and must not be
    // stored as a variant of one, so this endpoint takes text and caches nothing.
    translateText: (text: string, language: string) =>
      request<{
        language: string;
        content: string;
        provider?: string;
        model?: string;
        warning?: string;
      }>("/features/translate-text", {
        method: "POST",
        body: { text, language },
      }),

    shorten: (messageId: number, level: string) =>
      request<Variant>("/features/shorten", {
        method: "POST",
        body: { message_id: messageId, level },
      }),

    speech: (messageId: number, language = "en") =>
      request<SpeechPayload>("/features/speech", {
        method: "POST",
        body: { message_id: messageId, language },
      }),

    explain: (messageId: number) =>
      request<ExplainPayload>("/features/explain", {
        method: "POST",
        body: { message_id: messageId },
      }),

    variants: (messageId: number) =>
      request<Variant[]>(`/messages/${messageId}/variants`),

    clearVariants: (messageId: number) =>
      request<void>(`/messages/${messageId}/variants`, { method: "DELETE" }),
  },

  /* ---- settings ------------------------------------------------------ */
  settings: {
    all: () => request<Record<string, unknown>>("/settings"),
    rag: () => request<RagSettings>("/settings/rag"),
    providers: () => request<ProviderReport>("/settings/providers"),
    providerModels: () => request<Record<string, unknown>>("/settings/providers/models"),
    loadLocalModel: () =>
      request<Record<string, unknown>>("/settings/local-model/load", { method: "POST" }),
    unloadLocalModel: () =>
      request<Record<string, unknown>>("/settings/local-model/unload", { method: "POST" }),
    system: () => request<SystemStats>("/settings/system"),
    languages: () => request<{ languages: Language[] }>("/settings/languages"),
  },

  /* ---- RAG Laboratory ------------------------------------------------ */
  labs: {
    /** Embed text with the real model and inspect the vectors. */
    embed: (payload: { texts: string[]; query?: string }) =>
      request<LabEmbedResponse>("/labs/embed", { method: "POST", body: payload }),
    /** The canonical pipeline stage list, served so the UI cannot disagree with it. */
    stages: () => request<LabStagesResponse>("/labs/stages"),
  },

  /* ---- analytics ----------------------------------------------------- */
  analytics: {
    overview: (workspaceId?: number) =>
      request<AnalyticsOverview>(
        workspaceId ? `/analytics/overview?workspace_id=${workspaceId}` : "/analytics/overview",
      ),
    retrieval: (workspaceId?: number) =>
      request<RetrievalAnalytics>(
        workspaceId ? `/analytics/retrieval?workspace_id=${workspaceId}` : "/analytics/retrieval",
      ),
    activity: (workspaceId?: number, days = 14) =>
      request<ActivityAnalytics>(
        `/analytics/activity?days=${days}${workspaceId ? `&workspace_id=${workspaceId}` : ""}`,
      ),
    /**
     * Per-stage timings aggregated from the recorded traces.
     *
     * This is the one call that makes the RAG architecture visible as data: how
     * long each real stage took, averaged over the questions actually asked.
     */
    stages: (workspaceId?: number, days = 30) =>
      request<StageAnalytics>(
        `/analytics/stages?days=${days}${workspaceId ? `&workspace_id=${workspaceId}` : ""}`,
      ),
  },

  /* ---- evaluation ---------------------------------------------------- */
  evaluation: {
    security: () => request<SecurityReport>("/evaluation/security"),
    runRetrieval: (payload: Record<string, unknown>) =>
      request<Record<string, unknown>>("/evaluation/retrieval", {
        method: "POST",
        body: payload,
      }),
    history: () => request<Record<string, unknown>>("/evaluation/history"),
    metricsReference: () => request<Record<string, unknown>>("/evaluation/metrics-reference"),
  },
};

/**
 * Parse an SSE stream from a fetch Response body and dispatch typed events.
 *
 * The SSE spec separates fields with `\n`, and events with `\n\n`; multi-line
 * payload fields concatenate their `data:` lines back into one string. The
 * browser `EventSource` does this for you, but we cannot use EventSource here
 * (POST + Authorization header). This is a small, spec-faithful parser - not a
 * regex splitter - because a naive split on `\n\n` would corrupt any token that
 * happens to end in a line break at a chunk boundary.
 *
 * Dispatch contract:
 *   ready  -> onReady  {conversation_id, user_message_id, trace_id, mode}
 *   stage  -> onStage  {seq, stage, label, status, duration_ms, data, created_at}
 *   token  -> onToken   string fragment, in order
 *   done   -> onDone    AskResponse
 *   error  -> onError   an ApiError constructed from the backend envelope
 */
async function consumeSse(
  body: ReadableStream<Uint8Array>,
  handlers: {
    onReady?: (info: AskStreamReady) => void;
    onStage?: (event: AskStreamStage) => void;
    onToken?: (text: string) => void;
    onDone?: (response: AskResponse) => void;
    onError?: (err: ApiError) => void;
  },
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");

  // `buffer` holds the decoded but not-yet-parsed tail of the byte stream.
  // It always represents a whole prefix of SSE text (so a `\n` boundary cannot
  // land inside a multi-byte character), and is the cross-call state.
  let buffer = "";

  // Once `done` or `error` has been dispatched the stream is finished. Without
  // this flag a clean close after `done` would look like a truncation.
  let terminal = false;

  const dispatch = (event: string, fields: Record<string, string>) => {
    const raw = (fields.data ?? "").trim();
    if (!raw) return;
    let payload: any;
    try {
      payload = JSON.parse(raw);
    } catch {
      // A malformed frame should never crash the client. The stream may still
      // contain real events; skip this one and keep reading.
      return;
    }
    switch (event) {
      case "ready": {
        try {
          handlers.onReady?.(payload as AskStreamReady);
        } catch {
          /* handler error, not a transport error */
        }
        break;
      }
      case "stage": {
        try {
          handlers.onStage?.(normalizeStageEvent(payload));
        } catch {
          /* keep streaming */
        }
        break;
      }
      case "token": {
        const text = typeof payload?.text === "string" ? payload.text : "";
        if (text) {
          try {
            handlers.onToken?.(text);
          } catch {
            /* keep streaming */
          }
        }
        break;
      }
      case "done": {
        terminal = true;
        try {
          handlers.onDone?.(payload as AskResponse);
        } catch {
          /* stream is over anyway */
        }
        break;
      }
      case "error": {
        terminal = true;
        const info = payload?.error ?? payload ?? {};
        const err = new ApiError(
          info.message || "The answer could not be completed.",
          Number(info.status ?? 500),
          info.code || "stream_error",
          info.detail || {},
        );
        // Do NOT swallow the handler here. The caller's `onError` re-throws (see
        // Chat.tsx) so that its `catch` can roll back the optimistic turn, restore
        // the question text and show the error card. Wrapping it in try/catch
        // previously turned every streamed failure into a silent no-op: the
        // promise resolved, the catch block never ran, and the user was left with
        // a dangling message and no indication anything went wrong.
        handlers.onError?.(err);
        // If the handler decided not to throw (it is optional), the failed stream
        // must still reject so the caller's promise semantics are consistent.
        throw err;
      }
    }
  };

  // Parse complete frames. The `event:` field is single-occurrence per frame;
  // `data:` lines concatenate with `\n` per the spec.
  const flushFrame = (frame: string) => {
    let eventName = "message";
    const fields: Record<string, string> = {};
    let dataLines: string[] = [];
    for (const line of frame.split("\n")) {
      if (line === "" || line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      if (colon < 0) {
        // field-name-only per spec ("event" line as a boolean marker, etc.).
        continue;
      }
      const key = line.slice(0, colon);
      let value = line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (key === "event") eventName = value;
      else if (key === "data") dataLines.push(value);
      else fields[key] = value;
    }
    if (dataLines.length > 0) {
      fields.data = dataLines.join("\n");
      dispatch(eventName, fields);
    }
  };

  // Main loop - we do not close on first EOF; an `error` frame may be the last.
  outer: while (true) {
    let chunk: ReadableStreamReadResult<Uint8Array>;
    try {
      chunk = await reader.read();
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      handlers.onError?.(
        new ApiError("The streamed connection was lost.", 0, "network_error"),
      );
      return;
    }
    const { done, value } = chunk;
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE frames are separated by a double newline. We split on the strict form
    // ("\n\n") rather than on any single "\n", which would emit half-frames
    // every time a payload token happened to contain a newline.
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, sep);
      const remainder = buffer.slice(sep + 2);
      buffer = remainder;
      flushFrame(frame);
    }
  }

  // Drain the decoder (rare - some encoders emit a trailing partial byte
  // sequence at the close).
  const tail = decoder.decode();
  if (tail) buffer += tail;
  if (buffer.trim()) flushFrame(buffer.trimEnd());

  // The stream closed without `done` or `error`. That is a truncated
  // generation - surface it. Don't leave the user staring at a half answer.
  // (An `AbortError` in `reader.read()` is a deliberate stop, not a failure,
  // and we have already returned from the loop; this code path is for an
  // unexpected TCP close.)
  if (!terminal) {
    handlers.onError?.(
      new ApiError("The answer ended before it finished.", 0, "stream_truncated"),
    );
  }
}

function normalizeStageEvent(payload: any): AskStreamStage {
  return {
    seq: Number(payload.seq ?? 0),
    stage: String(payload.stage ?? ""),
    label: String(payload.label ?? payload.stage ?? ""),
    status: String(payload.status ?? "ok"),
    duration_ms: Number(payload.duration_ms ?? 0),
    data: (payload.data ?? {}) as Record<string, unknown>,
    created_at: payload.created_at ?? null,
  };
}

export type { Grounding };
