import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  BookOpen,
  Cpu,
  FileSearch,
  Layers,
  MessagesSquare,
  Quote,
  ScanSearch,
  Shield,
  Sparkles,
  Waypoints,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { api } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { useAsync } from "@/hooks/useAsync";
import { useWorkspaces } from "@/state/workspace";
import type { Message } from "@/lib/types";
import { NoWorkspaceNotice, PageBody, PageHeader } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Select } from "@/components/ui/Field";
import { EmptyState, ErrorState, LoadingPanel } from "@/components/ui/Feedback";
import { TraceTimeline } from "@/components/trace/TraceTimeline";

/**
 * RAG Trace.
 *
 * A dedicated page for the pipeline trace, separate from the chat bubble. Two
 * audiences:
 *
 *   1. Someone debugging retrieval, who wants the raw stage data.
 *   2. Someone LEARNING how RAG works, who needs the stages explained.
 *
 * The stage reference section at the bottom serves the second group without
 * getting in the way of the first. Every explanation answers the same four
 * questions - what it does, why it exists, what you would lose without it, and
 * how to see it working - because that is the structure the project is documented
 * against.
 */

const STAGE_REFERENCE = [
  {
    key: "query_analysis",
    icon: <FileSearch className="h-3.5 w-3.5" />,
    what: "Normalises the question and pulls out keywords.",
    why: "Two questions that mean the same thing should retrieve the same evidence. Normalising first removes that variation.",
    without: "Trivial wording differences would change the results, making retrieval feel unpredictable.",
    observe: "Expand the stage and look at the extracted keywords.",
  },
  {
    key: "query_embedding",
    icon: <Cpu className="h-3.5 w-3.5" />,
    what: "Turns the question into a vector using the same model that embedded the chunks.",
    why: "Similarity only means something if both sides live in the same vector space.",
    without: "Comparing a question vector to chunk vectors from a different model produces meaningless scores.",
    observe: "The stage reports the model and the vector's dimensions.",
  },
  {
    key: "vector_search",
    icon: <ScanSearch className="h-3.5 w-3.5" />,
    what: "Cosine nearest-neighbour search, filtered to this workspace.",
    why: "This is the retrieval step. It is where recall is won or lost - nothing later can recover evidence that was not found here.",
    without: "No retrieval at all; the model would answer from memory, which is exactly what we are avoiding.",
    observe: "Compare candidates returned against the top score.",
  },
  {
    key: "candidate_retrieval",
    icon: <Layers className="h-3.5 w-3.5" />,
    what: "Joins the vectors back to the database, removes duplicates, applies the score threshold.",
    why: "Vectors carry text and metadata, but the authoritative record lives in SQLite. Joining keeps one source of truth.",
    without: "Stale or deleted documents could still be cited, because the vector store would be the only reference.",
    observe: "The stage reports how many candidates survived filtering.",
  },
  {
    key: "reranking",
    icon: <Layers className="h-3.5 w-3.5" />,
    what: "Optionally reorders the candidates with a cross-encoder.",
    why: "A second, slower model can judge relevance more precisely than vector distance alone.",
    without: "Nothing is lost - it is off by default. It can only reorder what retrieval found, never recover what it missed.",
    observe: "If it did not run, the stage is marked 'skipped', not shown as a zero-duration success.",
  },
  {
    key: "context_building",
    icon: <Quote className="h-3.5 w-3.5" />,
    what: "Numbers the excerpts, orders them by score, and fits them into a character budget.",
    why: "The [1] the model writes must be the [1] the user clicks. Numbers are assigned here, once, and carried through unchanged.",
    without: "Citations would point at the wrong passage, which is worse than having no citations.",
    observe: "The stage reports how many excerpts were used and how many were dropped.",
  },
  {
    key: "llm_generation",
    icon: <Sparkles className="h-3.5 w-3.5" />,
    what: "Calls the model with the system instruction plus the numbered context.",
    why: "Separating instructions from data is what stops a document from issuing commands.",
    without: "No natural-language answer - only raw retrieved passages.",
    observe: "The stage names the provider and model, and whether a fallback was used.",
  },
  {
    key: "citation_resolution",
    icon: <Waypoints className="h-3.5 w-3.5" />,
    what: "Maps each [n] in the answer back to the excerpt it refers to.",
    why: "A citation is only useful if it resolves to something real. This is where fabrication is caught.",
    without: "Markers would be decorative, and a hallucinated [7] would go unnoticed.",
    observe: "Look for invalid numbers - those are citations to excerpts that never existed.",
  },
  {
    key: "grounding",
    icon: <Shield className="h-3.5 w-3.5" />,
    what: "Checks whether the answer's claims are supported by the retrieved evidence.",
    why: "Retrieval finding evidence does not mean the model used it correctly. These are different questions.",
    without: "A confident, well-written answer with no evidence behind it would look identical to a grounded one.",
    observe: "The four checks and their outcomes are listed in the stage data.",
  },
];

export default function RagTrace() {
  const { messageId: routeMessageId } = useParams();
  const navigate = useNavigate();
  const { activeId, loading: workspacesLoading } = useWorkspaces();

  const initialMessageId = routeMessageId ? Number.parseInt(routeMessageId, 10) : null;
  const [selectedConversation, setSelectedConversation] = useState<number | null>(null);
  const [selectedMessage, setSelectedMessage] = useState<number | null>(initialMessageId);
  const [showReference, setShowReference] = useState(false);

  const conversations = useAsync(
    activeId ? () => api.chat.conversations(activeId) : null,
    [activeId],
  );

  const detail = useAsync(
    selectedConversation ? () => api.chat.conversation(selectedConversation) : null,
    [selectedConversation],
  );

  const trace = useAsync(
    selectedMessage ? () => api.chat.trace(selectedMessage) : null,
    [selectedMessage],
  );

  // Only assistant messages have traces, and only those that were actually asked
  // through the pipeline.
  const tracedMessages = useMemo(
    () => (detail.data?.messages ?? []).filter((message: Message) => message.role === "assistant"),
    [detail.data],
  );

  const questionFor = useMemo(() => {
    const map = new Map<number, string>();
    const messages = detail.data?.messages ?? [];
    messages.forEach((message, index) => {
      if (message.role !== "assistant") return;
      const previous = messages[index - 1];
      if (previous?.role === "user") map.set(message.id, previous.content);
    });
    return map;
  }, [detail.data]);

  if (!workspacesLoading && !activeId) {
    return (
      <>
        <PageHeader
          title="RAG Trace"
          icon={<Waypoints className="h-4.5 w-4.5" />}
          description="Every stage a question passed through, with real measurements."
        />
        <PageBody>
          <NoWorkspaceNotice />
        </PageBody>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="RAG Trace"
        description="Pick a conversation and inspect exactly how each answer was produced. These numbers are recorded while the query runs - they are never reconstructed afterwards."
        icon={<Waypoints className="h-4.5 w-4.5" />}
        actions={
          <Button
            variant="secondary"
            size="sm"
            icon={<BookOpen className="h-3.5 w-3.5" />}
            onClick={() => setShowReference((value) => !value)}
          >
            {showReference ? "Hide stage guide" : "What do these stages mean?"}
          </Button>
        }
      />

      <PageBody wide className="space-y-6">
        {/* ---- stage reference -------------------------------------- */}
        {showReference ? (
          <Card className="animate-slide-down">
            <CardHeader
              title="The nine stages"
              description="What each one does, why it exists, and what you would lose without it."
              icon={<BookOpen className="h-4 w-4" />}
            />
            <div className="grid gap-3 p-5 lg:grid-cols-2">
              {STAGE_REFERENCE.map((stage) => (
                <div
                  key={stage.key}
                  className="rounded-xl border border-line bg-sunken/50 p-3.5"
                >
                  <div className="flex items-center gap-2">
                    <span className="flex h-6 w-6 items-center justify-center rounded-md bg-brand/10 text-brand">
                      {stage.icon}
                    </span>
                    <span className="font-mono text-2xs font-semibold text-ink">{stage.key}</span>
                  </div>

                  <dl className="mt-2.5 space-y-1.5">
                    <ReferenceRow label="What" value={stage.what} />
                    <ReferenceRow label="Why" value={stage.why} />
                    <ReferenceRow label="If removed" value={stage.without} />
                    <ReferenceRow label="How to see it" value={stage.observe} />
                  </dl>
                </div>
              ))}
            </div>
          </Card>
        ) : null}

        {/* ---- picker ----------------------------------------------- */}
        <div className="grid gap-6 lg:grid-cols-[22rem_1fr]">
          <div className="space-y-4">
            <Card>
              <CardHeader
                title="Choose a question"
                icon={<MessagesSquare className="h-4 w-4" />}
              />
              <div className="space-y-3 p-5">
                {conversations.loading ? (
                  <LoadingPanel message="Loading conversations…" />
                ) : (conversations.data?.conversations.length ?? 0) === 0 ? (
                  <p className="text-2xs leading-relaxed text-muted">
                    No conversations yet. Ask a question in Chat and its trace will appear
                    here.
                  </p>
                ) : (
                  <Select
                    label="Conversation"
                    value={selectedConversation ? String(selectedConversation) : ""}
                    onChange={(event) => {
                      const id = event.target.value ? Number.parseInt(event.target.value, 10) : null;
                      setSelectedConversation(id);
                      setSelectedMessage(null);
                    }}
                    options={[
                      { value: "", label: "Select a conversation…" },
                      ...(conversations.data?.conversations ?? []).map((conversation) => ({
                        value: String(conversation.id),
                        label: `${conversation.title} (${conversation.message_count})`,
                      })),
                    ]}
                  />
                )}

                {detail.loading ? <LoadingPanel message="Loading messages…" /> : null}

                {tracedMessages.length > 0 ? (
                  <div className="space-y-1.5">
                    <p className="text-2xs font-medium uppercase tracking-wide text-faint">
                      Answered questions
                    </p>
                    {tracedMessages.map((message) => (
                      <button
                        key={message.id}
                        type="button"
                        onClick={() => setSelectedMessage(message.id)}
                        className={cn(
                          "w-full rounded-lg border px-3 py-2.5 text-left transition-all",
                          message.id === selectedMessage
                            ? "border-brand/40 bg-brand/8"
                            : "border-line bg-surface hover:border-line-strong",
                        )}
                      >
                        <p className="line-clamp-2 text-2xs font-medium text-ink">
                          {questionFor.get(message.id) ?? "Question"}
                        </p>
                        <p className="mt-1 flex flex-wrap items-center gap-1.5 text-2xs text-faint">
                          <span>{formatRelative(message.created_at)}</span>
                          <span aria-hidden>·</span>
                          <span className="font-mono">
                            {message.provider || "unknown"}
                            {message.used_fallback ? " (fallback)" : ""}
                          </span>
                          {message.grounding_status ? (
                            <>
                              <span aria-hidden>·</span>
                              <span className="font-mono">{message.grounding_status}</span>
                            </>
                          ) : null}
                        </p>
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
            </Card>
          </div>

          {/* ---- trace ---------------------------------------------- */}
          <div className="min-w-0">
            {selectedMessage === null ? (
              <EmptyState
                icon={<Waypoints className="h-5 w-5" />}
                title="No trace selected"
                description="Choose a conversation on the left, then pick a question. You will see every stage the query passed through, how long each took, and what it produced."
              />
            ) : trace.loading ? (
              <Card>
                <LoadingPanel message="Loading the trace…" />
              </Card>
            ) : trace.error ? (
              <ErrorState
                title="Could not load the trace"
                message={trace.error}
                onRetry={trace.reload}
              />
            ) : trace.data ? (
              <div className="space-y-4">
                <TraceTimeline trace={trace.data} />

                <div className="flex items-center justify-between gap-3">
                  <p className="font-mono text-2xs text-faint">
                    trace {trace.data.trace_id || "—"} · message {trace.data.message_id}
                  </p>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => navigate(`/app/chat`)}
                  >
                    Open in chat
                  </Button>
                </div>
              </div>
            ) : (
              <EmptyState
                icon={<Waypoints className="h-5 w-5" />}
                title="No trace recorded"
                description="This message does not have a stored trace."
              />
            )}
          </div>
        </div>
      </PageBody>
    </>
  );
}

function ReferenceRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2">
      <dt className="w-20 shrink-0 text-2xs font-medium uppercase tracking-wide text-faint">
        {label}
      </dt>
      <dd className="min-w-0 flex-1 text-2xs leading-relaxed text-muted">{value}</dd>
    </div>
  );
}

export { Badge };
