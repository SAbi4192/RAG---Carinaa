import { useEffect, useMemo, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Loader2,
  Pause,
  Play,
  RotateCcw,
  Terminal,
  Workflow,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { formatDuration } from "@/lib/format";
import type { TraceStage } from "@/lib/types";
import { useWorkspaces } from "@/state/workspace";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/Feedback";
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
          <AskControls onRun={ask.run} running={ask.running} />
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
              <Card padded={false} className="animate-fade-up">
                <CardHeader
                  title="Live trace"
                  description="The same run as a log. Times are when each stage finished."
                  icon={<Terminal className="h-4 w-4" />}
                />
                <div className="px-5 py-4">
                  <LiveTrace stages={stages} />
                </div>
              </Card>

              <Card padded={false} className="animate-fade-up">
                <CardHeader
                  title="The answer this pipeline produced"
                  description={`From ${ask.result?.provider_label ?? "unknown provider"}`}
                />
                <div className="px-5 py-4">
                  <p className="whitespace-pre-wrap text-xs leading-relaxed text-ink">
                    {ask.answer}
                  </p>
                </div>
              </Card>
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
