import { useCallback, useState } from "react";
import { ArrowRight, Loader2, MessageSquare, Play, Sparkles } from "lucide-react";

import { ApiError, api } from "@/lib/api";
import { useWorkspaces } from "@/state/workspace";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/Feedback";
import { Concept, LabShell } from "./LabShell";

/**
 * Memory Lab: how a follow-up question is resolved from the conversation.
 *
 * THE HONEST SHAPE OF THIS BENCH
 * ------------------------------
 * The rewrite is a real language-model call, so this cannot be a pure input/output toy
 * — it has to run an actual conversation. It does so in a temporary conversation that
 * is created for the run, so nothing here touches the user's own chats.
 *
 * What it demonstrates is the split that makes conversational RAG work: the question
 * the USER typed is not the question RETRIEVAL searches for. "What is my name?" is
 * unsearchable; the resolved form is not. Showing both side by side is the point.
 */
export default function MemoryLab() {
  const { activeId, active, loading: workspacesLoading } = useWorkspaces();

  const [setup, setSetup] = useState("My name is Abishek and I am working on a RAG project.");
  const [followUp, setFollowUp] = useState("What is my name?");

  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<{
    original: string;
    resolved: string;
    isFollowUp: boolean;
    answer: string;
    turnsUsed: number;
    provider: string;
  } | null>(null);

  const run = useCallback(async () => {
    const first = setup.trim();
    const second = followUp.trim();
    if (!first || !second || !activeId) return;

    setRunning(true);
    setError("");
    setResult(null);

    try {
      // A throwaway conversation: the lab must never write into the user's chats.
      const conversation = await api.chat.createConversation(
        activeId,
        "Memory Lab (temporary)",
        "online",
      );

      // Seed one exchange, so the follow-up has something to resolve against.
      await api.chat.ask({
        workspace_id: activeId,
        conversation_id: conversation.id,
        question: first,
        mode: "online",
        language: "en",
      });

      const answer = await api.chat.ask({
        workspace_id: activeId,
        conversation_id: conversation.id,
        question: second,
        mode: "online",
        language: "en",
      });

      const analysis = answer.trace?.stages?.find((stage) => stage.stage === "query_analysis");
      const retrieval = answer.trace?.stages?.find(
        (stage) => stage.stage === "candidate_retrieval",
      );

      setResult({
        original: String(analysis?.data?.original_question ?? second),
        resolved: String(analysis?.data?.resolved_question ?? second),
        isFollowUp: Boolean(analysis?.data?.is_followup),
        answer: answer.answer,
        turnsUsed: Number(retrieval?.data?.conversation_turns_used ?? 0),
        provider: answer.provider_label,
      });

      // Leave nothing behind: the conversation existed only for this run.
      await api.chat.deleteConversation(conversation.id).catch(() => undefined);
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.message
          : "The run failed. Is the backend reachable and a provider configured?",
      );
    } finally {
      setRunning(false);
    }
  }, [activeId, setup, followUp]);

  if (workspacesLoading) {
    return (
      <LabShell title="Memory Lab" tagline="Loading…" concept={null}>
        <Card>
          <div className="px-5 py-10 text-center">
            <Loader2 className="mx-auto h-4 w-4 animate-spin text-faint" />
          </div>
        </Card>
      </LabShell>
    );
  }

  const resolvedDifferently = result && result.resolved !== result.original;

  return (
    <LabShell
      title="Memory Lab"
      tagline="Watch a follow-up question get resolved using the conversation."
      source="POST /api/chat/ask twice on a temporary conversation, then reads the trace"
      concept={
        <>
          <Concept label="What this shows">
            Two turns are sent: a statement, then a question that cannot be understood
            alone. The trace records both the question you typed and the standalone form
            retrieval actually searched for.
          </Concept>
          <Concept label="Why retrieval needs it">
            &ldquo;What is my name?&rdquo; is unsearchable — no document contains it. The
            resolved form carries the missing subject, so the search has something to work
            with.
          </Concept>
          <Concept label="Memory is not evidence">
            The conversation is sent to the model in its own block, labelled as
            conversation rather than document evidence. It is never cited, so a previous
            answer can never be mistaken for a source.
          </Concept>
          <div className="rounded-lg border border-line bg-sunken px-3 py-2">
            <p className="font-medium text-ink">Note</p>
            <p className="mt-1">
              This runs a real provider call and creates a temporary conversation, which
              is deleted afterwards. Nothing appears in your chat history.
            </p>
          </div>
        </>
      }
      controls={
        <>
          <CardHeader
            title="Set up a conversation"
            description="The first message establishes context; the second depends on it."
            icon={<MessageSquare className="h-4 w-4" />}
            actions={running ? <Loader2 className="h-3.5 w-3.5 animate-spin text-faint" /> : null}
          />
          <div className="space-y-3.5 p-5">
            <div>
              <label className="mb-1.5 block text-2xs font-medium text-ink">
                First message
              </label>
              <textarea
                value={setup}
                onChange={(event) => setSetup(event.target.value)}
                rows={2}
                className="w-full resize-none rounded-lg border border-line bg-surface px-3 py-2 text-xs text-ink outline-none transition focus:border-brand/50"
              />
            </div>
            <div>
              <label className="mb-1.5 block text-2xs font-medium text-ink">
                Follow-up (needs the first message to make sense)
              </label>
              <textarea
                value={followUp}
                onChange={(event) => setFollowUp(event.target.value)}
                rows={2}
                className="w-full resize-none rounded-lg border border-line bg-surface px-3 py-2 text-xs text-ink outline-none transition focus:border-brand/50"
              />
            </div>

            <Button
              className="w-full"
              onClick={() => void run()}
              loading={running}
              disabled={!activeId || !setup.trim() || !followUp.trim()}
            >
              <Play className="h-3.5 w-3.5" />
              Run the conversation
            </Button>

            {!active?.stats?.chunks ? (
              <p className="text-2xs text-faint">
                This workspace has no documents, which is fine here — the point is that the
                answer comes from the conversation, not from retrieval.
              </p>
            ) : null}
          </div>
        </>
      }
    >
      {error ? (
        <Card>
          <div className="px-5 py-4 text-xs text-negative">{error}</div>
        </Card>
      ) : null}

      {result ? (
        <>
          <Card padded={false}>
            <CardHeader
              title="How the follow-up was understood"
              description="Read from the query_analysis stage of the real run."
              icon={<Sparkles className="h-4 w-4" />}
              actions={
                <Badge tone={result.isFollowUp ? "accent" : "neutral"}>
                  {result.isFollowUp ? "resolved from the conversation" : "standalone"}
                </Badge>
              }
            />
            <div className="space-y-3 px-5 py-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-lg border border-line bg-sunken px-2.5 py-1.5 text-2xs text-muted">
                  {result.original}
                </span>
                <ArrowRight className="h-3.5 w-3.5 shrink-0 text-faint" />
                <span className="rounded-lg border border-brand/35 bg-brand/8 px-2.5 py-1.5 text-2xs font-medium text-brand">
                  {result.resolved}
                </span>
              </div>

              <p className="text-2xs leading-relaxed text-muted">
                {resolvedDifferently
                  ? "Retrieval searched the resolved form on the right. The answer was written from the question you actually typed, with the conversation supplied alongside the excerpts."
                  : "The question was already standalone, so it was left unchanged — a rewrite is not forced when it is not needed."}
              </p>

              <dl className="flex flex-wrap gap-x-5 gap-y-1 text-2xs">
                <div className="flex gap-1.5">
                  <dt className="text-faint">conversation turns sent</dt>
                  <dd className="font-mono text-ink">{result.turnsUsed}</dd>
                </div>
                <div className="flex gap-1.5">
                  <dt className="text-faint">provider</dt>
                  <dd className="font-mono text-ink">{result.provider}</dd>
                </div>
              </dl>
            </div>
          </Card>

          <Card padded={false}>
            <CardHeader title="The answer" description="Produced from the conversation." />
            <div className="px-5 py-4">
              <p className="whitespace-pre-wrap text-xs leading-relaxed text-ink">
                {result.answer}
              </p>
            </div>
          </Card>
        </>
      ) : !error && !running ? (
        <Card>
          <EmptyState
            icon={<MessageSquare className="h-5 w-5" />}
            title="Nothing run yet"
            description="Send the two messages and the resolution step will be shown."
          />
        </Card>
      ) : null}
    </LabShell>
  );
}
