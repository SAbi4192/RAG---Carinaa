import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  GraduationCap,
  Loader2,
  MessageSquareText,
  Pause,
  Play,
  RotateCcw,
  Sparkles,
  Terminal,
  Workflow,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { api } from "@/lib/api";
import { useAsync } from "@/hooks/useAsync";
import { formatDuration } from "@/lib/format";
import type { TraceStage } from "@/lib/types";
import { useWorkspaces } from "@/state/workspace";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/Feedback";
import { GroundingBadge } from "@/components/chat/Citations";
import { LiveTrace } from "@/components/rag/LiveTrace";
import { stageExplanation } from "@/components/rag/stageExplanations";
import { AskControls } from "./AskControls";
import { Concept, LabShell } from "./LabShell";
import { useAskRun } from "./useAskRun";

/**
 * Full Pipeline Laboratory, with pause and step-through.
 *
 * Built for demonstration: run a real question, then walk the stages one at a time
 * at whatever pace the room needs. Every step shows the values the pipeline
 * actually recorded, so pausing on "Context building" shows the real budget and
 * the real drop count rather than a scripted caption.
 *
 * A stage that did not run is shown as skipped WITH its reason. There is no
 * "simulate" mode, because a simulation would be indistinguishable from the real
 * thing on screen - which is precisely the confusion this project exists to
 * prevent.
 */
export default function PipelineLab() {
  const { activeId } = useWorkspaces();
  const ask = useAskRun(activeId);

  /* ---- experiment setup -------------------------------------------------
     Everything the run is allowed to do, chosen up front: which knowledge to
     search, and whether to add the web (opt-in, never implicit). */
  const [knowledgeDoc, setKnowledgeDoc] = useState<number | null>(null);
  const [webOn, setWebOn] = useState(false);
  const [showTech, setShowTech] = useState(false);
  const documents = useAsync(
    activeId ? () => api.documents.list(activeId) : null,
    [activeId],
  );

  const selectedDocName = useMemo(
    () =>
      (documents.data?.documents ?? []).find((doc) => doc.id === knowledgeDoc)
        ?.original_filename ?? null,
    [documents.data, knowledgeDoc],
  );

  const stages: TraceStage[] = useMemo(
    () => ((ask.result?.trace as { stages?: TraceStage[] } | undefined)?.stages ?? []),
    [ask.result],
  );

  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);

  // Reset the walkthrough whenever a new run lands.
  useEffect(() => {
    setStep(0);
    setPlaying(stages.length > 0);
  }, [stages]);

  useEffect(() => {
    if (!playing || stages.length === 0) return;
    if (step >= stages.length - 1) {
      setPlaying(false);
      return;
    }
    const timer = window.setTimeout(() => setStep((current) => current + 1), 1400);
    return () => window.clearTimeout(timer);
  }, [playing, step, stages.length]);

  const current = stages[step];
  const explanation = current ? stageExplanation(current.stage) : undefined;
  const totalMs = (ask.result?.trace as { total_ms?: number } | undefined)?.total_ms ?? 0;
  const finished = stages.length > 0 && step >= stages.length - 1;

  return (
    <LabShell
      title="Full Pipeline Laboratory"
      tagline="Run a real question, then step through every stage at your own pace. Built for demonstrations."
      source="POST /api/chat/ask → response.trace (app/rag/trace.py, measured live)"
      concept={
        <>
          <Concept label="What happens">
            One question runs the whole pipeline end to end: analysis, embedding, search, context,
            generation, citation resolution and grounding.
          </Concept>
          <Concept label="Why step through it">
            In real time it finishes in a few seconds and the interesting parts blur. Pausing on a
            stage lets you read the measured values and ask what would change if they were
            different.
          </Concept>
          <Concept label="Every number is real">
            Durations and counts come from the trace the pipeline recorded while answering. Nothing
            is scripted, and a stage that did not run says so.
          </Concept>
          <div className="rounded-lg border border-line bg-sunken px-3 py-2">
            <p className="font-medium text-ink">Presenting this</p>
            <p className="mt-1">
              Ask a question, pause on "Retrieval" to show the scores, then step to "Context" to
              show the same chunks becoming numbered evidence. That transition is the part people
              find hardest to picture.
            </p>
          </div>
        </>
      }
      controls={
        <>
          <CardHeader
            title="Run a question"
            description="The walkthrough starts automatically once the trace arrives."
            icon={<Workflow className="h-4 w-4" />}
            actions={ask.running ? <Loader2 className="h-3.5 w-3.5 animate-spin text-faint" /> : null}
          />
          <AskControls
            onRun={(question, mode) =>
              ask.run(question, mode, {
                use_web_search: webOn,
                document_ids: knowledgeDoc ? [knowledgeDoc] : undefined,
              })
            }
            running={ask.running}
            documents={documents.data?.documents ?? []}
            selectedDocumentId={knowledgeDoc}
            onSelectedDocumentChange={setKnowledgeDoc}
            webEnabled={webOn}
            onWebChange={setWebOn}
          />
        </>
      }
    >
      {ask.error ? (
        <Card>
          <div className="px-5 py-4 text-xs text-negative">{ask.error}</div>
        </Card>
      ) : null}

      {stages.length > 0 ? (
        <>
          <Card padded={false}>
            <CardHeader
              title="Step-through controls"
              description={`Stage ${step + 1} of ${stages.length}${totalMs ? ` · ${formatDuration(totalMs)} total` : ""}`}
              icon={<Workflow className="h-4 w-4" />}
              actions={
                <Badge tone={finished ? "positive" : playing ? "accent" : "neutral"}>
                  {finished ? "complete" : playing ? "playing" : "paused"}
                </Badge>
              }
            />

            {/* the stage rail */}
            <div className="border-b border-line px-5 py-4">
              <div className="flex flex-wrap gap-1.5">
                {stages.map((stage, index) => {
                  const done = index <= step;
                  const isCurrent = index === step;
                  return (
                    <button
                      key={`${stage.stage}-${stage.seq}`}
                      type="button"
                      onClick={() => {
                        setStep(index);
                        setPlaying(false);
                      }}
                      className={cn(
                        "rounded-lg border px-2.5 py-1.5 text-2xs font-medium transition",
                        isCurrent
                          ? "border-brand/50 bg-brand/12 text-brand"
                          : done
                            ? "border-positive/30 bg-positive/8 text-positive"
                            : "border-line bg-surface text-faint hover:border-line-strong",
                        stage.status === "skipped" && "opacity-60",
                      )}
                    >
                      {index + 1}. {stage.label}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2 px-5 py-3">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setStep((s) => Math.max(0, s - 1))}
                disabled={step === 0}
              >
                <ChevronLeft className="h-3.5 w-3.5" />
                Previous
              </Button>

              <Button
                variant={playing ? "secondary" : "primary"}
                size="sm"
                onClick={() => {
                  if (finished) {
                    setStep(0);
                    setPlaying(true);
                  } else {
                    setPlaying((p) => !p);
                  }
                }}
              >
                {playing ? (
                  <>
                    <Pause className="h-3.5 w-3.5" />
                    Pause
                  </>
                ) : finished ? (
                  <>
                    <RotateCcw className="h-3.5 w-3.5" />
                    Replay
                  </>
                ) : (
                  <>
                    <Play className="h-3.5 w-3.5" />
                    Play
                  </>
                )}
              </Button>

              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  setStep((s) => Math.min(stages.length - 1, s + 1));
                  setPlaying(false);
                }}
                disabled={finished}
              >
                Next
                <ChevronRight className="h-3.5 w-3.5" />
              </Button>

              <span className="ml-auto text-2xs text-faint">
                {finished ? "Walkthrough complete" : "Auto-advances while playing"}
              </span>
            </div>
          </Card>

          {current ? (
            <Card padded={false} className="animate-fade-up">
              <CardHeader
                title={`${step + 1}. ${current.label || current.stage}`}
                description={
                  current.duration_ms
                    ? `${formatDuration(current.duration_ms)} measured for this stage`
                    : "No measurable duration — this stage did no work"
                }
                actions={
                  <Badge
                    tone={
                      current.status === "ok"
                        ? "positive"
                        : current.status === "error"
                          ? "caution"
                          : "neutral"
                    }
                  >
                    {current.status}
                  </Badge>
                }
              />

              {current.status === "skipped" ? (
                <div className="border-b border-line bg-caution/8 px-5 py-3">
                  <p className="text-2xs leading-relaxed text-caution">
                    <strong>Not enabled in this configuration.</strong> This stage did not run
                    {current.data && "reason" in current.data
                      ? ` — ${String(current.data.reason)}`
                      : "."}{" "}
                    Nothing is shown in its place.
                  </p>
                </div>
              ) : null}

              <div className="grid gap-5 px-5 py-4 lg:grid-cols-2">
                {explanation ? (
                  <dl className="space-y-2.5 text-2xs leading-relaxed">
                    <div>
                      <dt className="font-medium text-ink">What happens</dt>
                      <dd className="text-muted">{explanation.what}</dd>
                    </div>
                    <div>
                      <dt className="font-medium text-ink">Why it exists</dt>
                      <dd className="text-muted">{explanation.why}</dd>
                    </div>
                    <div>
                      <dt className="font-medium text-ink">If it were removed</dt>
                      <dd className="text-muted">{explanation.without}</dd>
                    </div>
                    <div>
                      <dt className="font-medium text-ink">Look for</dt>
                      <dd className="text-muted">{explanation.lookFor}</dd>
                    </div>
                    <div>
                      <dt className="font-medium text-ink">Implemented in</dt>
                      <dd className="font-mono text-faint">{explanation.where}</dd>
                    </div>
                  </dl>
                ) : null}

                <div>
                  <p className="mb-2 text-2xs font-medium text-ink">
                    Measured values for this run
                  </p>
                  {Object.keys(current.data ?? {}).length === 0 ? (
                    <p className="text-2xs text-faint">This stage recorded no extra values.</p>
                  ) : (
                    <div className="flex flex-wrap gap-1.5">
                      {Object.entries(current.data ?? {})
                        .filter(([key]) => !["label", "stage", "status", "seq"].includes(key))
                        .map(([key, value]) => (
                          <span
                            key={key}
                            className="inline-flex items-center gap-1 rounded-md border border-line bg-sunken px-2 py-0.5 text-2xs"
                          >
                            <span className="text-faint">{key.replace(/_/g, " ")}</span>
                            <span className="font-mono text-ink">
                              {typeof value === "object" && value !== null
                                ? Array.isArray(value)
                                  ? `${value.length} items`
                                  : `${Object.keys(value).length} fields`
                                : String(value)}
                            </span>
                          </span>
                        ))}
                    </div>
                  )}
                </div>
              </div>
            </Card>
          ) : null}

          {finished && ask.answer ? (
            <>
              {/* ---- 1. what was asked, on what ---------------------- */}
              <Card padded={false} className="animate-fade-up">
                <CardHeader
                  title="Your question"
                  description="The experiment, exactly as it was run."
                  icon={<MessageSquareText className="h-4 w-4" />}
                  actions={
                    ask.result?.web_sources?.length ? (
                      <Badge tone="accent">📚 + 🌐 combined</Badge>
                    ) : (
                      <Badge tone="neutral">📚 documents only</Badge>
                    )
                  }
                />
                <div className="space-y-1.5 px-5 py-4">
                  <p className="text-sm leading-relaxed text-ink">{ask.question}</p>
                  <p className="text-2xs leading-relaxed text-faint">
                    Knowledge source:{" "}
                    <span className="text-muted">
                      {selectedDocName ?? "all documents in this workspace"}
                    </span>{" "}
                    · Web search:{" "}
                    <span className="text-muted">
                      {ask.result?.web_sources?.length
                        ? `${ask.result.web_sources.length} web results added 🌐`
                        : "off"}
                    </span>
                  </p>
                </div>
              </Card>

              {/* ---- 2. what the AI produced, and the check ------------ */}
              <Card padded={false} className="animate-fade-up">
                <CardHeader
                  title="AI response"
                  description={`From ${ask.result?.provider_label ?? "unknown provider"}`}
                  icon={<Sparkles className="h-4 w-4" />}
                  actions={
                    ask.result?.grounding ? (
                      <GroundingBadge grounding={ask.result.grounding} />
                    ) : undefined
                  }
                />
                <div className="space-y-3 px-5 py-4">
                  <p className="whitespace-pre-wrap text-xs leading-relaxed text-ink">
                    {ask.answer}
                  </p>
                  {ask.result?.grounding ? (
                    <p className="border-t border-line pt-2.5 text-2xs leading-relaxed text-muted">
                      <span className="font-medium text-ink">Verification: </span>
                      {ask.result.grounding.reason ||
                        "The answer was checked against the retrieved evidence."}
                    </p>
                  ) : null}
                </div>
              </Card>

              {/* ---- 3. the measurements, collapsed by default --------- */}
              <Card padded={false} className="animate-fade-up">
                <button
                  type="button"
                  onClick={() => setShowTech((value) => !value)}
                  aria-expanded={showTech}
                  className="flex w-full items-center gap-2 px-5 py-3 text-left"
                >
                  <Terminal className="h-4 w-4 text-muted" />
                  <span className="flex-1 text-xs font-semibold text-ink">
                    Technical details
                  </span>
                  <span className="text-2xs text-faint">
                    {showTech ? "hide" : "timings, scores & raw trace"}
                  </span>
                  <ChevronDown
                    className={cn(
                      "h-3.5 w-3.5 text-faint transition-transform",
                      showTech && "rotate-180",
                    )}
                  />
                </button>
                {showTech ? (
                  <div className="animate-slide-down border-t border-line px-5 py-4">
                    <p className="mb-2 text-2xs leading-relaxed text-faint">
                      The same run as a log. Times are when each stage finished.
                    </p>
                    <LiveTrace stages={stages} />
                  </div>
                ) : null}
              </Card>

              {/* ---- 4. the bridge to Learning Mode -------------------- */}
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-brand/25 bg-brand/6 px-5 py-3.5">
                <p className="min-w-0 text-xs leading-relaxed text-muted">
                  Want to understand what happened? Learning Mode walks this run
                  through the six simple steps, with the technical detail underneath.
                </p>
                {ask.result?.conversation_id ? (
                  <Link
                    to={`/app/learning/${ask.result.conversation_id}`}
                    className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-brand/40 bg-brand/10 px-3 py-1.5 text-2xs font-medium text-brand transition hover:bg-brand/15"
                  >
                    <GraduationCap className="h-3.5 w-3.5" />
                    Open this run in Learning Mode
                  </Link>
                ) : null}
              </div>
            </>
          ) : null}
        </>
      ) : !ask.error && !ask.running ? (
        <Card>
          <EmptyState
            icon={<Workflow className="h-5 w-5" />}
            title="Nothing to step through yet"
            description="Ask a question and the walkthrough will start automatically."
          />
        </Card>
      ) : null}
    </LabShell>
  );
}
