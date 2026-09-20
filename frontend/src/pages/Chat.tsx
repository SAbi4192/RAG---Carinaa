import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowUp,
  Bot,
  Database,
  Globe,
  GraduationCap,
  MessageSquarePlus,
  Menu,
  MessagesSquare,
  Minimize2,
  Paperclip,
  Plus,
  Settings2,
  Sparkles,
  Trash2,
  Wifi,
  WifiOff,
  X,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { ApiError, api } from "@/lib/api";
import { formatRelative, pluralize } from "@/lib/format";
import { useAsync } from "@/hooks/useAsync";
import { useWorkspaces } from "@/state/workspace";
import { useToast } from "@/state/toast";
import type {
  AskResponse,
  Conversation,
  Grounding,
  ConversationScope,
  Message,
  TraceSummary,
  Variant,
} from "@/lib/types";
import { NoWorkspaceNotice, PageHeader } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ConfirmDialog, Modal } from "@/components/ui/Modal";
import { Segmented } from "@/components/ui/Tabs";
import { Input, Select, Textarea, Toggle } from "@/components/ui/Field";
import { EmptyState, ErrorState, LoadingPanel, Spinner } from "@/components/ui/Feedback";
import { ChatDocuments } from "@/components/chat/ChatDocuments";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { UploadDialog } from "@/components/documents/UploadDialog";
import { LearningPanel } from "@/components/rag/LearningPanel";
import { PanelMenu, type PipelineMode } from "@/components/layout/PanelMenu";
import { TraceTimeline } from "@/components/trace/TraceTimeline";

/**
 * Chat.
 *
 * The main surface of the product, and the place where the "one canonical answer"
 * rule is enforced in the UI. A message's `content` is never mutated: choosing a
 * translation or a condensed version stores a VARIANT against the message and
 * changes only what is displayed. Switching back is always one click.
 *
 * Offline mode is a first-class choice, not a fallback: the toggle sits in the
 * header, and when it is on the composer says plainly that no network will be
 * used. If the local model cannot answer, the result is a clearly-labelled
 * extractive answer - never a silent jump to an online provider.
 */

const EXAMPLE_QUESTIONS = [
  "Summarise the key ideas in these documents.",
  "What are the main differences between the concepts covered?",
  "What limitations or warnings do the documents mention?",
  "List the definitions given for the important terms.",
];

/**
 * Chat.
 *
 * Rendered by two routes:
 *
 *   /app/chat            the everyday chatbot (default)
 *   /app/learning        the same conversation, with the RAG pipeline beside it
 *
 * They are one component on purpose. Learning Mode must show the SAME conversation
 * and the SAME answers - a separate page with its own state would drift, and a
 * learner comparing the two would eventually be looking at two different systems.
 * The only difference is whether the pipeline column is rendered.
 *
 * `learning` is a prop rather than a query parameter because a query parameter
 * cannot be relied on: React Router keeps this component mounted across a search
 * change, so a `?learning=1` flag read in a `useState` initializer would never be
 * re-read when the user clicks the nav item while already on Chat. That was the
 * cause of "Learning Mode sometimes goes back to Chat".
 */
export default function Chat({ learning = false }: { learning?: boolean }) {
  const { conversationId: routeId } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const { activeId, active, loading: workspacesLoading } = useWorkspaces();

  const [conversationId, setConversationId] = useState<number | null>(
    routeId ? Number.parseInt(routeId, 10) : null,
  );
  const [messages, setMessages] = useState<Message[]>([]);
  const [groundings, setGroundings] = useState<Record<number, Grounding | null>>({});
  const [variants, setVariants] = useState<Record<number, Variant | null>>({});
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  /**
   * The last failure, kept as a structure rather than a string.
   *
   * `code` lets the UI respond to WHAT went wrong - an ambiguous document gets a
   * chooser, a missing local model gets a pointer to Settings - instead of matching
   * on message text, which breaks as soon as the wording is edited.
   */
  const [askError, setAskError] = useState<{
    message: string;
    code: string;
    detail: Record<string, unknown>;
  } | null>(null);

  // Retrieval overrides
  const [mode, setMode] = useState<"online" | "offline">("online");
  const [showOptions, setShowOptions] = useState(false);
  const [topK, setTopK] = useState(5);
  const [candidateK, setCandidateK] = useState(20);
  const [useRerank, setUseRerank] = useState(false);
  const [useWeb, setUseWeb] = useState(false);

  const [traceFor, setTraceFor] = useState<number | null>(null);

  /**
   * The trace for each answer, captured from the ask response.
   *
   * Stored rather than re-fetched so Learning Mode shows the run that produced the
   * answer on screen. When a conversation is loaded from history the trace is
   * fetched lazily instead (see `ensureTrace`), which is why this is a map and not a
   * single value.
   */
  const [traces, setTraces] = useState<Record<number, TraceSummary>>({});

  const ensureTrace = useCallback(
    async (messageId: number) => {
      if (messageId <= 0 || traces[messageId]) return;
      try {
        const trace = await api.chat.trace(messageId);
        setTraces((current) => ({
          ...current,
          [messageId]: {
            trace_id: trace.trace_id,
            event_count: trace.event_count,
            total_ms: trace.total_ms,
            stages: trace.stages,
            summary: trace.summary ?? {},
          },
        }));
      } catch {
        // A missing trace must not break the conversation. Learning Mode shows its
        // own "no trace available" state; the answer is unaffected either way.
      }
    },
    [traces],
  );

  const lastAssistantId = useMemo(() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (message.role === "assistant" && message.id > 0) return message.id;
    }
    return null;
  }, [messages]);

  const latestTrace = lastAssistantId ? traces[lastAssistantId] ?? null : null;

  // Learning Mode needs the trace even for a conversation loaded from history,
  // where no ask response is in memory. Fetched once, on demand.
  useEffect(() => {
    if (learning && lastAssistantId) void ensureTrace(lastAssistantId);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- ensureTrace changes identity
    // whenever `traces` does; including it would re-run on every trace write.
  }, [learning, lastAssistantId]);

  const [pendingDelete, setPendingDelete] = useState<Conversation | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [mobileListOpen, setMobileListOpen] = useState(false);
  /** Below lg the pipeline column is hidden, so Learning Mode opens it as a drawer. */
  const [showPipelineDrawer, setShowPipelineDrawer] = useState(false);

  /**
   * Side-panel layout, controlled by the ☰ menu.
   *
   * Persisted, because someone who hides the history rail wants it to stay hidden,
   * and someone presenting from the full-screen pipeline does not want to
   * re-maximise it after every reload.
   */
  const [railVisible, setRailVisible] = useState(() => {
    const stored = window.localStorage.getItem("carinaa.railVisible");
    return stored !== "0"; // visible unless explicitly hidden
  });

  const [pipelineMode, setPipelineMode] = useState<PipelineMode>(() => {
    const stored = window.localStorage.getItem("carinaa.pipelineMode");
    return stored === "full" || stored === "hidden" || stored === "side" ? stored : "side";
  });

  useEffect(() => {
    window.localStorage.setItem("carinaa.railVisible", railVisible ? "1" : "0");
  }, [railVisible]);

  useEffect(() => {
    window.localStorage.setItem("carinaa.pipelineMode", pipelineMode);
  }, [pipelineMode]);

  // "Full screen" is a Learning Mode concept. Leaving Learning Mode with the
  // pipeline maximised would otherwise strand the user with no conversation.
  useEffect(() => {
    if (!learning && pipelineMode === "full") setPipelineMode("side");
  }, [learning, pipelineMode]);

  /**
   * Learning Mode opens its own panel when a question runs.
   *
   * The panel IS the feature - asking someone to turn it on after they have already
   * chosen Learning Mode is a step with no purpose. Only "hidden" is overridden: a
   * user who has maximised the pipeline has made an explicit choice, and collapsing
   * it back would undo their decision.
   */
  useEffect(() => {
    if (!learning) return;
    if (asking && pipelineMode === "hidden") setPipelineMode("side");
  }, [learning, asking, pipelineMode]);

  /**
   * Whether the Learning walkthrough is still animating.
   *
   * In Learning Mode the newest answer is withheld until the pipeline finishes, so the
   * user watches it being built rather than reading it while stages are still
   * appearing.
   *
   * THE SAFETY TIMEOUT IS NOT OPTIONAL. The animation is a presentation layer; the
   * answer is the product. If the panel never reports completion - a bug, a crash, a
   * stuck timer - the answer must still appear. So a hard ceiling reveals it
   * regardless, and a Skip button in the panel ends the wait immediately.
   */
  const [pipelinePlaying, setPipelinePlaying] = useState(false);
  const [revealDeadlinePassed, setRevealDeadlinePassed] = useState(false);

  useEffect(() => {
    if (!pipelinePlaying) {
      setRevealDeadlinePassed(false);
      return;
    }
    const timer = window.setTimeout(() => setRevealDeadlinePassed(true), 7000);
    return () => window.clearTimeout(timer);
  }, [pipelinePlaying]);

  const holdNewestAnswer = learning && pipelinePlaying && !revealDeadlinePassed;

  /**
   * Uploading from chat.
   *
   * The brief is explicit that an upload must never navigate away, reload, reset the
   * conversation or clear messages. That is why this opens a dialog layered over the
   * chat rather than routing to the Knowledge Base: the conversation behind it is
   * never unmounted, so nothing can be lost.
   */
  const [uploadOpen, setUploadOpen] = useState(false);
  const [droppedFile, setDroppedFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);

  /**
   * Chat-scoped documents.
   *
   * The scope is resolved SERVER-side at retrieval time, so this state exists to
   * show and edit the scope - not to enforce it. Even if this component never
   * loaded, retrieval would still honour the stored scope.
   */
  const [scope, setScope] = useState<ConversationScope | null>(null);
  const [scopeBusy, setScopeBusy] = useState(false);

  const loadScope = useCallback(async (id: number) => {
    try {
      setScope(await api.chat.conversationDocuments(id));
    } catch {
      // A scope that cannot be loaded must not break the conversation. The
      // server still applies whatever scope is stored.
      setScope(null);
    }
  }, []);

  useEffect(() => {
    if (conversationId) void loadScope(conversationId);
    else setScope(null);
  }, [conversationId, loadScope]);

  /** Attaching needs a conversation. Create one on demand rather than making the
   *  user send a message first - "attach a file, then ask" is a normal order. */
  const ensureConversation = useCallback(async (): Promise<number | null> => {
    if (conversationId) return conversationId;
    if (!activeId) return null;
    try {
      const created = await api.chat.createConversation(activeId, "New conversation", mode);
      setConversationId(created.id);
      navigate(
        learning ? `/app/learning/${created.id}` : `/app/chat/${created.id}`,
        { replace: true },
      );
      return created.id;
    } catch {
      toast.error("Could not start a conversation", "Try again in a moment.");
      return null;
    }
  }, [conversationId, activeId, mode, learning, navigate, toast]);

  const handleAttach = useCallback(
    async (documentId: number) => {
      const id = await ensureConversation();
      if (!id) return;
      setScopeBusy(true);
      try {
        setScope(await api.chat.attachDocument(id, documentId));
      } catch {
        toast.error("Could not attach that document", "It may not be indexed yet.");
      } finally {
        setScopeBusy(false);
      }
    },
    [ensureConversation, toast],
  );

  const handleToggleDocument = useCallback(
    async (documentId: number, isActive: boolean) => {
      if (!conversationId) return;
      setScopeBusy(true);
      // Optimistic: the checkbox must feel instant, and the server response
      // replaces this state either way.
      setScope((current) =>
        current
          ? {
              ...current,
              documents: current.documents.map((item) =>
                item.document_id === documentId ? { ...item, is_active: isActive } : item,
              ),
              active_document_ids: isActive
                ? [...current.active_document_ids, documentId]
                : current.active_document_ids.filter((value) => value !== documentId),
              scope: isActive ? "chat" : current.active_document_ids.length > 1 ? "chat" : "workspace",
            }
          : current,
      );
      try {
        setScope(await api.chat.setDocumentActive(conversationId, documentId, isActive));
      } catch {
        toast.error("Could not change the scope", "Reloading the current scope.");
        void loadScope(conversationId);
      } finally {
        setScopeBusy(false);
      }
    },
    [conversationId, toast, loadScope],
  );

  const handleDetachDocument = useCallback(
    async (documentId: number) => {
      if (!conversationId) return;
      setScopeBusy(true);
      try {
        const next = await api.chat.detachDocument(conversationId, documentId);
        setScope(next);
        toast.success(
          "Removed from this chat",
          "The document is still in your workspace.",
        );
      } catch {
        toast.error("Could not remove that document", "Try again in a moment.");
      } finally {
        setScopeBusy(false);
      }
    },
    [conversationId, toast],
  );

  const openUpload = useCallback((file: File | null = null) => {
    setDroppedFile(file);
    setUploadOpen(true);
    setDragging(false);
    dragDepth.current = 0;
  }, []);

  const onDragEnter = useCallback((event: React.DragEvent) => {
    if (!event.dataTransfer?.types?.includes("Files")) return;
    dragDepth.current += 1;
    setDragging(true);
  }, []);

  const onDragLeave = useCallback((event: React.DragEvent) => {
    if (!event.dataTransfer?.types?.includes("Files")) return;
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragging(false);
  }, []);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      if (!event.dataTransfer?.files?.length) return;
      event.preventDefault();
      const file = event.dataTransfer.files[0];
      dragDepth.current = 0;
      setDragging(false);
      openUpload(file);
    },
    [openUpload],
  );

  const threadRef = useRef<HTMLDivElement>(null);

  const workspaceDocuments = useAsync(
    activeId ? () => api.documents.list(activeId) : null,
    [activeId],
  );

  const conversations = useAsync(
    activeId ? () => api.chat.conversations(activeId) : null,
    [activeId],
  );

  const languages = useAsync(() => api.features.languages(), []);

  const current = useMemo(
    () => conversations.data?.conversations.find((item) => item.id === conversationId) ?? null,
    [conversations.data, conversationId],
  );

  /* ---- load a conversation ------------------------------------------- */
  useEffect(() => {
    if (!conversationId) {
      setMessages([]);
      setGroundings({});
      setVariants({});
      return;
    }

    let cancelled = false;
    void api.chat
      .conversation(conversationId)
      .then((detail) => {
        if (cancelled) return;
        setMessages(detail.messages);
        setMode(detail.conversation.ai_mode === "offline" ? "offline" : "online");

        // Rebuild the grounding verdicts from what was stored on each message.
        const restored: Record<number, Grounding | null> = {};
        detail.messages.forEach((message) => {
          const detailPayload = message.grounding_detail as Record<string, unknown>;
          if (detailPayload && Object.keys(detailPayload).length && message.grounding_status) {
            restored[message.id] = {
              status: message.grounding_status,
              label: String(detailPayload.label ?? message.grounding_status),
              tone: String(detailPayload.tone ?? "neutral"),
              reason: String(detailPayload.reason ?? ""),
              refused: Boolean(detailPayload.refused),
              checks: (detailPayload.checks as Record<string, unknown>) ?? {},
              counts: (detailPayload.counts as Record<string, number>) ?? {},
              cited_numbers: (detailPayload.cited_numbers as number[]) ?? [],
              unused_numbers: (detailPayload.unused_numbers as number[]) ?? [],
              invalid_numbers: (detailPayload.invalid_numbers as number[]) ?? [],
              citation_count: Number(detailPayload.citation_count ?? 0),
              excerpt_count: Number(detailPayload.excerpt_count ?? 0),
              top_score: Number(detailPayload.top_score ?? 0),
              disclaimer: String(detailPayload.disclaimer ?? ""),
            };
          }
        });
        setGroundings(restored);
      })
      .catch((cause) => {
        if (cancelled) return;
        if (cause instanceof ApiError && cause.isNotFound) {
          toast.warning("Conversation not found", "Starting a new one instead.");
          setConversationId(null);
          navigate("/app/chat", { replace: true });
        }
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  /* ---- auto-scroll on new content ------------------------------------ */
  useEffect(() => {
    if (!messages.length) return;
    const element = threadRef.current;
    if (!element) return;
    element.scrollTo({ top: element.scrollHeight, behavior: "smooth" });
  }, [messages.length, asking]);

  /* ---- ask ----------------------------------------------------------- */
  const ask = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || !activeId || asking) return;

      setAsking(true);
      setAskError(null);
      setQuestion("");

      // Show the user's turn immediately. This is an optimistic render of text
      // the user definitely typed, not a fabricated answer - the assistant's
      // reply only appears once the server has actually produced one.
      const optimistic: Message = {
        id: -Date.now(),
        conversation_id: conversationId ?? 0,
        role: "user",
        content: trimmed,
        ai_mode: mode,
        provider: "",
        model: "",
        used_fallback: false,
        fallback_reason: "",
        grounding_status: "",
        grounding_detail: {},
        latency_ms: 0,
        token_usage: {},
        trace_id: "",
        retrieval: {},
        citations: [],
        web_search_used: false,
        web_sources: [],
        created_at: new Date().toISOString(),
      };
      setMessages((current) => [...current, optimistic]);

      try {
        const result: AskResponse = await api.chat.ask({
          question: trimmed,
          workspace_id: activeId,
          conversation_id: conversationId,
          mode,
          top_k: topK,
          candidate_k: candidateK,
          use_rerank: useRerank,
          use_web_search: useWeb && mode === "online",
          language: "en",
        });

        setMessages((current) => [
          ...current.filter((message) => message.id !== optimistic.id),
          { ...result.message, role: "user", content: trimmed } as Message,
          result.message,
        ]);

        setGroundings((current) => ({ ...current, [result.message.id]: result.grounding }));
        setTraces((current) => ({ ...current, [result.message.id]: result.trace }));

        if (!conversationId) {
          setConversationId(result.conversation_id);
          // Stay inside whichever surface the user is on: asking from Learning Mode
          // must not bounce them back to plain Chat.
          navigate(
            learning
              ? `/app/learning/${result.conversation_id}`
              : `/app/chat/${result.conversation_id}`,
            { replace: true },
          );
        }

        conversations.reload();

        if (result.message.used_fallback) {
          // Transient by design: the fact is also on the provider badge, which
          // never disappears, so nothing is lost when this clears. The wording
          // names the provider that actually answered rather than a hard-coded
          // one - "so Groq answered instead" was wrong whenever Gemini fell back
          // to something else.
          // `provider_label` is already human-formatted by the backend
          // (e.g. "Groq · Fallback"), so there is nothing to capitalise here.
          const actual = result.provider_label || "the fallback provider";
          toast.warning(
            `Answered by ${actual}`,
            "The primary provider was unavailable. This is labelled on the answer.",
            2500,
          );
        }
      } catch (cause) {
        // Roll the optimistic turn back so the transcript matches reality.
        setMessages((current) => current.filter((message) => message.id !== optimistic.id));
        setQuestion(trimmed);

        const message =
          cause instanceof ApiError ? cause.message : "The question could not be answered.";

        if (cause instanceof ApiError) {
          setAskError({
            message:
              cause.code === "local_model_unavailable"
                ? "The local model is not loaded, so offline mode cannot answer. Open Settings and load it, or switch to online mode."
                : cause.message,
            code: cause.code,
            detail: cause.detail ?? {},
          });
        } else {
          setAskError({ message, code: "error", detail: {} });
        }

        // A clarification is not a failure, so it does not get an error toast.
        if (!(cause instanceof ApiError && cause.code === "ambiguous_document")) {
          toast.error("Could not answer that", message);
        }
      } finally {
        setAsking(false);
      }
    },
    [
      activeId,
      asking,
      conversationId,
      mode,
      topK,
      candidateK,
      useRerank,
      useWeb,
      navigate,
      conversations,
      toast,
    ],
  );

  /* ---- new conversation --------------------------------------------- */
  const startNew = useCallback(() => {
    setConversationId(null);
    setMessages([]);
    setGroundings({});
    setVariants({});
    setAskError(null);
    setQuestion("");
    navigate("/app/chat");
    setMobileListOpen(false);
  }, [navigate]);

  const handleDelete = useCallback(async () => {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      await api.chat.deleteConversation(pendingDelete.id);
      toast.success("Conversation deleted");
      if (pendingDelete.id === conversationId) startNew();
      setPendingDelete(null);
      conversations.reload();
    } catch (cause) {
      toast.error(
        "Could not delete the conversation",
        cause instanceof ApiError ? cause.message : "Please try again.",
      );
    } finally {
      setDeleting(false);
    }
  }, [pendingDelete, conversationId, startNew, conversations, toast]);

  const languageList = languages.data?.languages ?? [];

  if (!workspacesLoading && !activeId) {
    return (
      <>
        <PageHeader
          title="Chat"
          icon={<MessagesSquare className="h-4.5 w-4.5" />}
          description="Ask questions grounded in your documents."
        />
        <div className="p-5 sm:p-7">
          <NoWorkspaceNotice />
        </div>
      </>
    );
  }

  /**
   * The conversation list.
   *
   * `collapsible` renders the ☰ that closes the desktop rail. It is off in the
   * mobile drawer, which is already an overlay with its own way out - showing a
   * collapse control there would imply the drawer is a persistent panel.
   */
  const renderConversationList = ({
    collapsible = false,
  }: { collapsible?: boolean } = {}) => (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1.5 border-b border-line px-2 py-3">
        {collapsible ? (
          /* ☰ collapses this rail. It sits on the rail it controls rather than
             only in the header menu, so the thing you click is next to the
             thing that moves. */
          <button
            type="button"
            onClick={() => setRailVisible(false)}
            aria-label="Collapse the conversation list"
            aria-expanded="true"
            title="Collapse the conversation list"
            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-muted transition hover:bg-sunken hover:text-ink"
          >
            <Menu className="h-4 w-4" />
          </button>
        ) : null}

        <p className="min-w-0 flex-1 truncate text-2xs font-semibold uppercase tracking-wider text-faint">
          Conversations
        </p>

        <Button
          variant="ghost"
          size="icon"
          onClick={startNew}
          aria-label="New conversation"
          title="New conversation"
        >
          <MessageSquarePlus className="h-3.5 w-3.5" />
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto scrollbar-thin p-2">
        {conversations.loading ? (
          <div className="flex justify-center py-8">
            <Spinner />
          </div>
        ) : (conversations.data?.conversations.length ?? 0) === 0 ? (
          <p className="px-2.5 py-6 text-center text-2xs leading-relaxed text-faint">
            No conversations yet. Ask something to start one.
          </p>
        ) : (
          <ul className="space-y-0.5">
            {conversations.data?.conversations.map((conversation) => (
              <li key={conversation.id} className="group relative">
                <button
                  type="button"
                  onClick={() => {
                    setConversationId(conversation.id);
                    navigate(`/app/chat/${conversation.id}`);
                    setMobileListOpen(false);
                  }}
                  className={cn(
                    "w-full rounded-lg px-2.5 py-2 pr-8 text-left transition-colors",
                    conversation.id === conversationId
                      ? "bg-brand/10"
                      : "hover:bg-sunken",
                  )}
                >
                  <span
                    className={cn(
                      "block truncate text-2xs font-medium",
                      conversation.id === conversationId ? "text-brand" : "text-ink",
                    )}
                  >
                    {conversation.title}
                  </span>
                  <span className="mt-0.5 flex items-center gap-1.5 text-2xs text-faint">
                    <span>{pluralize(conversation.message_count, "msg")}</span>
                    <span aria-hidden>·</span>
                    <span>{formatRelative(conversation.updated_at)}</span>
                    {conversation.ai_mode === "offline" ? (
                      <WifiOff className="h-2.5 w-2.5 text-accent" />
                    ) : null}
                  </span>
                </button>

                <button
                  type="button"
                  onClick={() => setPendingDelete(conversation)}
                  aria-label={`Delete ${conversation.title}`}
                  className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-faint opacity-0 transition-all hover:text-negative group-hover:opacity-100"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );

  return (
    <>
      <div className="flex h-[calc(100vh-0px)] flex-col lg:h-screen">
        {/* ---- header ---------------------------------------------- */}
        <PageHeader
          title={current?.title ?? "New conversation"}
          description={
            active
              ? `Answers are grounded in "${active.name}" only.`
              : "Select a workspace to begin."
          }
          icon={<MessagesSquare className="h-4.5 w-4.5" />}
          badge={
            current?.ai_mode === "offline" ? (
              <Badge tone="accent" icon={<WifiOff className="h-2.5 w-2.5" />}>
                offline
              </Badge>
            ) : undefined
          }
          actions={
            <>
              <Button
                variant="ghost"
                size="sm"
                className="lg:hidden"
                onClick={() => setMobileListOpen(true)}
              >
                History
              </Button>

              {/* ☰ — expand or minimise the side panels. In Learning Mode this also
                  controls the pipeline column. */}
              <PanelMenu
                railVisible={railVisible}
                onRailVisibleChange={setRailVisible}
                {...(learning
                  ? { pipelineMode, onPipelineModeChange: setPipelineMode }
                  : {})}
              />

              {/* Learning Mode is a real route, not a toggle. A toggle read from the
                  URL cannot work here: this component stays mounted across a search
                  change, so the state would never be re-read. */}
              {learning ? (
                <Link
                  to={conversationId ? `/app/chat/${conversationId}` : "/app/chat"}
                  title="Leave Learning Mode and return to the plain chat"
                  className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-brand/40 bg-brand/10 px-2.5 text-2xs font-medium text-brand"
                >
                  <GraduationCap className="h-3.5 w-3.5" />
                  <span className="hidden sm:inline">Exit Learning Mode</span>
                </Link>
              ) : (
                <Link
                  to={conversationId ? `/app/learning/${conversationId}` : "/app/learning"}
                  title="Open Learning Mode: chat with the RAG pipeline beside it"
                  className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 text-2xs font-medium text-muted transition hover:border-line-strong hover:text-ink"
                >
                  <GraduationCap className="h-3.5 w-3.5" />
                  <span className="hidden sm:inline">Learning Mode</span>
                </Link>
              )}

              {/* Below lg the pipeline column is hidden, so Learning Mode needs an
                  explicit way to open it as a drawer. */}
              {learning ? (
                <button
                  type="button"
                  onClick={() => setShowPipelineDrawer(true)}
                  className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 text-2xs font-medium text-muted transition hover:border-line-strong hover:text-ink lg:hidden"
                >
                  <GraduationCap className="h-3.5 w-3.5" />
                  Show RAG Process
                </button>
              ) : null}

              <Segmented
                value={mode}
                onChange={(next) => setMode(next)}
                options={[
                  {
                    value: "online",
                    label: "Online",
                    icon: <Wifi className="h-3 w-3" />,
                    title: "Gemini first, Groq as a disclosed fallback",
                  },
                  {
                    value: "offline",
                    label: "Offline",
                    icon: <WifiOff className="h-3 w-3" />,
                    title: "Local model only. No network calls at all.",
                  },
                ]}
              />
            </>
          }
        />

        <div
          className="relative flex min-h-0 flex-1"
          onDragEnter={onDragEnter}
          onDragOver={(event) => {
            if (event.dataTransfer?.types?.includes("Files")) {
              event.preventDefault();
            }
          }}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
        >
          {/* ---- drag-and-drop overlay (§11). The file is never uploaded here;
              it is handed to the same dialog the + button opens, so there is one
              upload path and one set of real progress stages. ---- */}
          {dragging ? (
            <div className="pointer-events-none absolute inset-0 z-40 flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-brand/50 bg-brand/8 px-6 text-center backdrop-blur-sm">
              <Paperclip className="h-7 w-7 text-brand" />
              <p className="text-sm font-medium text-ink">Drop your document here</p>
              <p className="max-w-xs text-2xs leading-relaxed text-muted">
                Carinaa will add it to your knowledge base. Your conversation stays exactly
                where it is.
              </p>
            </div>
          ) : null}

          {/* ---- conversation rail --------------------------------
                When collapsed it keeps a narrow strip instead of vanishing: a
                control that disappears along with its panel is undiscoverable,
                and the only way back would be the header menu. */}
          <aside
            className={cn(
              "hidden shrink-0 border-r border-line bg-surface transition-[width] duration-200 lg:block",
              railVisible ? "w-60" : "w-11",
            )}
          >
            {railVisible ? (
              renderConversationList({ collapsible: true })
            ) : (
              <div className="flex h-full flex-col items-center gap-2 py-3">
                <button
                  type="button"
                  onClick={() => setRailVisible(true)}
                  aria-label="Expand the conversation list"
                  aria-expanded="false"
                  title="Expand the conversation list"
                  className="inline-flex h-7 w-7 items-center justify-center rounded-lg text-muted transition hover:bg-sunken hover:text-ink"
                >
                  <Menu className="h-4 w-4" />
                </button>

                {/* Rotated rather than wrapped, so it never forces the rail wide. */}
                <span
                  aria-hidden
                  className="mt-1 select-none whitespace-nowrap text-2xs font-semibold uppercase tracking-wider text-faint"
                  style={{ writingMode: "vertical-rl" }}
                >
                  Conversations
                </span>

                <button
                  type="button"
                  onClick={startNew}
                  aria-label="New conversation"
                  title="New conversation"
                  className="inline-flex h-7 w-7 items-center justify-center rounded-lg text-muted transition hover:bg-sunken hover:text-ink"
                >
                  <MessageSquarePlus className="h-3.5 w-3.5" />
                </button>
              </div>
            )}
          </aside>

            {/* ---- thread ------------------------------------------- */}
            <div
              className={cn(
                "flex min-w-0 flex-1 flex-col",
                learning && pipelineMode === "full" && "hidden",
              )}
            >
            <div ref={threadRef} className="flex-1 overflow-y-auto scrollbar-thin px-4 py-5 sm:px-7">
              <div className="mx-auto max-w-3xl space-y-5">
                {messages.length === 0 ? (
                  <div className="pt-6">
                    <EmptyState
                      icon={<Sparkles className="h-5 w-5" />}
                      title="Ask something about your documents"
                      description={
                        active?.stats?.documents
                          ? `${pluralize(active.stats.documents, "document")} and ${pluralize(
                              active.stats.chunks,
                              "chunk",
                            )} are searchable in this workspace. Answers will cite the passages they came from.`
                          : "This workspace has no documents yet, so there is nothing to retrieve. Add a file first."
                      }
                    />

                    {active?.stats?.documents ? (
                      <div className="mt-5 grid gap-2 sm:grid-cols-2">
                        {EXAMPLE_QUESTIONS.map((example) => (
                          <button
                            key={example}
                            type="button"
                            onClick={() => void ask(example)}
                            className="rounded-lg border border-line bg-surface px-3.5 py-2.5 text-left text-2xs text-muted transition-all hover:border-brand/40 hover:bg-brand/5 hover:text-ink"
                          >
                            {example}
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ) : (
                  messages.map((message) => (
                    /* In Learning Mode the newest answer waits for the walkthrough.
                       It is replaced by a placeholder rather than left blank, so the
                       gap reads as "still being built" instead of "nothing happened". */
                    holdNewestAnswer && message.id === lastAssistantId ? (
                      <div
                        key={message.id}
                        className="flex items-center gap-3 rounded-2xl border border-brand/25 bg-brand/6 px-4 py-3.5"
                      >
                        <Spinner size={14} />
                        <span className="text-xs text-muted">
                          Building the answer — watch the pipeline on the right.
                        </span>
                      </div>
                    ) : (
                    <MessageBubble
                      key={message.id}
                      message={message}
                      grounding={groundings[message.id] ?? null}
                      citations={message.citations ?? []}
                      languages={languageList}
                      activeVariant={variants[message.id] ?? null}
                      onVariantChange={(variant) =>
                        setVariants((current) => ({ ...current, [message.id]: variant }))
                      }
                      onOpenTrace={() => setTraceFor(message.id)}
                    />
                    )
                  ))
                )}

                {asking ? (
                  <div className="flex items-center gap-3 text-xs text-muted">
                    <Spinner size={14} />
                    <span>
                      Retrieving and generating…
                      {mode === "offline"
                        ? " The local model can take 30 seconds or more on a laptop."
                        : ""}
                    </span>
                  </div>
                ) : null}

                {askError ? (
                  askError.code === "ambiguous_document" ? (
                    /* A clarification, not an error. The backend refused because a
                       page number means nothing across several documents, and it
                       told us the candidates - so offer them rather than making the
                       user retype their question with a filename in it. */
                    <div className="rounded-xl border border-caution/30 bg-caution/8 p-4">
                      <p className="text-xs font-medium text-ink">
                        Which document did you mean?
                      </p>
                      <p className="mt-1 text-2xs leading-relaxed text-muted">
                        This chat has more than one document in scope, and a page number
                        alone does not say which one to look in.
                      </p>
                      <div className="mt-2.5 flex flex-wrap gap-1.5">
                        {((askError.detail?.candidates as string[]) ?? []).map((name) => (
                          <button
                            key={name}
                            type="button"
                            onClick={() =>
                              void ask(`${question} (in ${name})`)
                            }
                            className="rounded-lg border border-line bg-surface px-2.5 py-1.5 text-2xs font-medium text-ink transition hover:border-brand/40 hover:text-brand"
                          >
                            {name}
                          </button>
                        ))}
                      </div>
                      <button
                        type="button"
                        onClick={() => setAskError(null)}
                        className="mt-2 text-2xs text-faint transition hover:text-ink"
                      >
                        dismiss
                      </button>
                    </div>
                  ) : (
                    <ErrorState
                      title="That question could not be answered"
                      message={askError.message}
                      onRetry={() => void ask(question || "")}
                    />
                  )
                ) : null}
              </div>
            </div>

            {/* ---- composer ----------------------------------------- */}
            <div className="border-t border-line bg-surface/70 px-4 py-3.5 backdrop-blur sm:px-7">
              <div className="mx-auto max-w-3xl space-y-2.5">
                {/* Knowledge scope sits immediately above the input, because it
                    describes what the next question will search. Placing it at
                    the top of the page would separate it from the action it
                    modifies. */}
                {activeId ? (
                  <ChatDocuments
                    scope={scope}
                    workspaceDocuments={workspaceDocuments.data?.documents ?? []}
                    onAttach={handleAttach}
                    onToggle={handleToggleDocument}
                    onDetach={handleDetachDocument}
                    busy={scopeBusy}
                  />
                ) : null}
                {/* options */}
                {showOptions ? (
                  <div className="animate-slide-down rounded-xl border border-line bg-surface p-3.5">
                    <div className="grid gap-3 sm:grid-cols-2">
                      <Input
                        label="Excerpts to use (top_k)"
                        type="number"
                        min={1}
                        max={20}
                        value={topK}
                        onChange={(event) => setTopK(Number(event.target.value) || 5)}
                        hint="How many passages go into the context."
                      />
                      <Input
                        label="Candidates to retrieve"
                        type="number"
                        min={1}
                        max={100}
                        value={candidateK}
                        onChange={(event) => setCandidateK(Number(event.target.value) || 20)}
                        hint="Retrieved before filtering down to top_k."
                      />
                    </div>

                    <div className="mt-3 space-y-3 border-t border-line pt-3">
                      <Toggle
                        checked={useRerank}
                        onChange={setUseRerank}
                        label="Re-rank candidates"
                        hint="Off by default. Re-ranking can only reorder what retrieval already found - it cannot recover evidence retrieval missed."
                      />
                      <Toggle
                        checked={useWeb}
                        onChange={setUseWeb}
                        disabled={mode === "offline"}
                        label="Augment with web search"
                        hint={
                          mode === "offline"
                            ? "Unavailable in offline mode - web search would require the network."
                            : "Off by default. Results are labelled separately from your documents."
                        }
                      />
                    </div>
                  </div>
                ) : null}

                {/* offline notice */}
                {mode === "offline" ? (
                  <div className="flex items-start gap-2 rounded-lg border border-accent/25 bg-accent/6 px-3 py-2">
                    <WifiOff className="mt-px h-3.5 w-3.5 shrink-0 text-accent" />
                    <p className="text-2xs leading-relaxed text-muted">
                      Offline mode. The local model answers using retrieved passages, and no
                      request leaves this machine. If it cannot answer, you get a labelled
                      extractive result - never a silent switch to an online provider.
                    </p>
                  </div>
                ) : null}

                <div className="flex items-end gap-2">
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => openUpload()}
                    aria-label="Add a document"
                    title="Add a document to this workspace"
                    disabled={!activeId}
                  >
                    <Plus className="h-4 w-4" />
                  </Button>

                  <div className="relative flex-1">
                    <Textarea
                      value={question}
                      onChange={(event) => setQuestion(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" && !event.shiftKey) {
                          event.preventDefault();
                          void ask(question);
                        }
                      }}
                      placeholder={
                        active?.stats?.documents
                          ? "Ask a question about your documents…"
                          : "Add a document first, then ask about it…"
                      }
                      rows={1}
                      disabled={!active?.stats?.documents}
                      className="min-h-[2.5rem] resize-none pr-2"
                    />
                  </div>

                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => setShowOptions((value) => !value)}
                    aria-label="Retrieval options"
                    aria-expanded={showOptions}
                    className={cn(showOptions && "bg-brand/10 text-brand")}
                    title="Retrieval options"
                  >
                    <Settings2 className="h-4 w-4" />
                  </Button>

                  <Button
                    variant="primary"
                    size="icon"
                    onClick={() => void ask(question)}
                    disabled={!question.trim() || asking || !active?.stats?.documents}
                    aria-label="Send question"
                    title="Send (Enter)"
                  >
                    {asking ? <Spinner size={14} className="text-brand-ink" /> : <ArrowUp className="h-4 w-4" />}
                  </Button>
                </div>

                <div className="flex items-center justify-between gap-3">
                  <p className="flex items-center gap-1.5 text-2xs text-faint">
                    <Database className="h-2.5 w-2.5" />
                    Searching only{" "}
                    <span className="font-medium text-muted">{active?.name ?? "no workspace"}</span>
                  </p>
                  <p className="text-2xs text-faint">
                    Enter to send · Shift+Enter for a new line
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* ---- Learning Mode: the pipeline beside the conversation.
                Hidden below lg, where it becomes a drawer instead (see below), so
                it can never cover the chat on a narrow screen. ---- */}
          {learning && pipelineMode === "side" ? (
            <aside className="hidden w-[40%] max-w-[520px] shrink-0 border-l border-line lg:block">
              <LearningPanel
                stages={latestTrace?.stages ?? []}
                totalMs={latestTrace?.total_ms}
                running={asking}
                onClose={() => setPipelineMode("hidden")}
                onPlayingChange={setPipelinePlaying}
              />
            </aside>
          ) : null}

          {/* Full screen: the pipeline replaces the conversation entirely. Used
              when presenting - the diagram is the subject, not a sidebar. A banner
              gives the way back, so this can never strand anyone. */}
          {learning && pipelineMode === "full" ? (
            <div className="flex min-w-0 flex-1 flex-col">
              <div className="flex items-center justify-between gap-3 border-b border-line bg-surface px-4 py-2">
                <p className="text-2xs text-muted">
                  The pipeline is full screen. The conversation is still there.
                </p>
                <button
                  type="button"
                  onClick={() => setPipelineMode("side")}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1 text-2xs font-medium text-muted transition hover:border-line-strong hover:text-ink"
                >
                  <Minimize2 className="h-3 w-3" />
                  Show chat beside it
                </button>
              </div>
              <div className="min-h-0 flex-1">
                <LearningPanel
                  stages={latestTrace?.stages ?? []}
                  totalMs={latestTrace?.total_ms}
                  running={asking}
                  onClose={() => setPipelineMode("side")}
                  onPlayingChange={setPipelinePlaying}
                />
              </div>
            </div>
          ) : null}
        </div>
      </div>

      {/* ---- mobile conversation drawer ---------------------------- */}
      {mobileListOpen ? (
        <div className="fixed inset-0 z-[80] lg:hidden">
          <div
            className="absolute inset-0 animate-fade-in bg-black/45"
            onClick={() => setMobileListOpen(false)}
            aria-hidden
          />
          <aside className="absolute right-0 top-0 h-full w-72 animate-fade-in border-l border-line bg-surface shadow-pop">
            <div className="flex items-center justify-between border-b border-line px-3 py-2.5">
              <p className="text-xs font-semibold text-ink">History</p>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setMobileListOpen(false)}
                aria-label="Close history"
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            </div>
            <div className="h-[calc(100%-2.75rem)]">{renderConversationList()}</div>
          </aside>
        </div>
      ) : null}

      {/* ---- trace modal ------------------------------------------- */}
      <TraceModal
        messageId={traceFor}
        onClose={() => setTraceFor(null)}
      />

      {/*
        Uploading from chat. Layered over the conversation rather than routed away,
        so the workspace, the conversation and the messages all survive it (§12).
        The `key` forces a fresh dialog per file, which is what makes a dropped file
        start uploading immediately instead of reusing a previous selection.
      */}
      <UploadDialog
        key={droppedFile ? `drop-${droppedFile.name}-${droppedFile.size}` : "picker"}
        open={uploadOpen}
        onClose={() => {
          setUploadOpen(false);
          setDroppedFile(null);
        }}
        workspaceId={activeId}
        initialFile={droppedFile}
        onUploaded={() => {
          // Refresh workspace stats so the composer and the document count update.
          void conversations.reload();
        }}
      />

      {/* ---- Learning Mode pipeline drawer (below lg) -------------- */}
      {learning && showPipelineDrawer ? (
        <div className="fixed inset-0 z-[85] lg:hidden">
          <div
            className="absolute inset-0 animate-fade-in bg-black/45"
            onClick={() => setShowPipelineDrawer(false)}
            aria-hidden
          />
          <div className="absolute inset-y-0 right-0 flex w-full max-w-[420px] animate-fade-in flex-col border-l border-line bg-surface shadow-pop">
            <LearningPanel
              stages={latestTrace?.stages ?? []}
              totalMs={latestTrace?.total_ms}
              running={asking}
              onClose={() => setShowPipelineDrawer(false)}
            />
          </div>
        </div>
      ) : null}

      <ConfirmDialog
        open={Boolean(pendingDelete)}
        onClose={() => setPendingDelete(null)}
        onConfirm={() => void handleDelete()}
        busy={deleting}
        title="Delete this conversation?"
        confirmLabel="Delete"
        message={
          pendingDelete ? (
            <p>
              <span className="font-medium text-ink">{pendingDelete.title}</span> and its{" "}
              {pluralize(pendingDelete.message_count, "message")} will be removed. The documents
              themselves are not affected. This cannot be undone.
            </p>
          ) : null
        }
      />
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Trace modal                                                                 */
/* -------------------------------------------------------------------------- */

function TraceModal({
  messageId,
  onClose,
}: {
  messageId: number | null;
  onClose: () => void;
}) {
  const trace = useAsync(
    messageId ? () => api.chat.trace(messageId) : null,
    [messageId],
  );

  return (
    <Modal
      open={messageId !== null}
      onClose={onClose}
      title="RAG trace"
      description="Every stage this question passed through, measured while it ran."
      size="xl"
    >
      {trace.loading ? (
        <LoadingPanel message="Loading the trace…" />
      ) : trace.error ? (
        <ErrorState message={trace.error} onRetry={trace.reload} />
      ) : trace.data ? (
        <TraceTimeline trace={trace.data} />
      ) : (
        <EmptyState icon={<Bot className="h-5 w-5" />} title="No trace recorded" />
      )}
    </Modal>
  );
}

export { Card, Globe, Select };
