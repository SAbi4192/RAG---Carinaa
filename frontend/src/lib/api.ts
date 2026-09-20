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
  RetrieveResponse,
  RetrievalAnalytics,
  SecurityReport,
  ShortenLevelInfo,
  SpeechPayload,
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

    retrieve: (payload: {
      workspace_id: number;
      question: string;
      top_k?: number;
      candidate_k?: number;
      document_ids?: number[];
      use_rerank?: boolean;
    }) => request<RetrieveResponse>("/chat/retrieve", { method: "POST", body: payload }),

    trace: (messageId: number) => request<Trace>(`/messages/${messageId}/trace`),

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

export type { Grounding };
