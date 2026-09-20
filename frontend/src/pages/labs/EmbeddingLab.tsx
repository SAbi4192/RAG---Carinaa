import { useCallback, useMemo, useState } from "react";
import { AlertTriangle, Braces, Loader2, Plus, Sparkles, Trash2 } from "lucide-react";

import { cn } from "@/lib/cn";
import { ApiError, api } from "@/lib/api";
import { formatScore } from "@/lib/format";
import type { LabEmbedResponse } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Input } from "@/components/ui/Field";
import { EmptyState } from "@/components/ui/Feedback";
import { Concept, LabShell } from "./LabShell";

/**
 * Embedding Laboratory.
 *
 * The hardest idea in RAG to picture is that a sentence becomes a list of numbers
 * and that "meaning" survives the conversion. This lab lets a learner watch that
 * happen and then check it: type two sentences about the same thing and two about
 * different things, and read the similarity matrix.
 *
 * WHAT THE PICTURE IS, AND IS NOT
 * -------------------------------
 * The scatter plot is a PCA projection of the real 384-dimensional vectors onto
 * two axes. It is NOT the true embedding space. The response reports how much
 * variance the two axes preserve, and the UI shows that number, because a
 * projection that hides its own distortion teaches something false.
 *
 * The similarity matrix, by contrast, is exact: cosine similarity on the real
 * vectors, computed the same way the vector store computes it.
 */

const DEFAULT_TEXTS = [
  "Virtualization lets one physical server host many virtual machines.",
  "A hypervisor creates and runs virtual machines on a single host.",
  "Photosynthesis converts light energy into chemical energy in plants.",
  "Plants use sunlight to make sugar from carbon dioxide and water.",
];

const PLOT = { width: 460, height: 300, padding: 34 };

export default function EmbeddingLab() {
  const [texts, setTexts] = useState<string[]>(DEFAULT_TEXTS);
  const [query, setQuery] = useState("How do hypervisors work?");
  const [result, setResult] = useState<LabEmbedResponse | null>(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [hovered, setHovered] = useState<number | null>(null);

  const run = useCallback(async (nextTexts: string[], nextQuery: string) => {
    const cleaned = nextTexts.map((t) => t.trim()).filter(Boolean);
    if (cleaned.length === 0) {
      setResult(null);
      return;
    }
    setRunning(true);
    setError("");
    try {
      const response = await api.labs.embed({ texts: cleaned, query: nextQuery.trim() });
      setResult(response);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The model could not embed that text.");
      setResult(null);
    } finally {
      setRunning(false);
    }
  }, []);

  /** Normalise the projection into SVG coordinates with a little headroom. */
  const plot = useMemo(() => {
    const points = result?.projection ?? [];
    if (points.length === 0) return [];

    const xs = points.map((p) => p.x);
    const ys = points.map((p) => p.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    // A single point, or a perfectly flat one, would divide by zero.
    const spanX = maxX - minX || 1;
    const spanY = maxY - minY || 1;

    const inner = {
      width: PLOT.width - PLOT.padding * 2,
      height: PLOT.height - PLOT.padding * 2,
    };

    return points.map((point, index) => ({
      index,
      cx: PLOT.padding + ((point.x - minX) / spanX) * inner.width,
      // SVG y grows downward; a plot should grow upward.
      cy: PLOT.padding + inner.height - ((point.y - minY) / spanY) * inner.height,
    }));
  }, [result]);

  const explained = result?.projection_explained_variance ?? [0, 0];

  return (
    <LabShell
      title="Embedding Laboratory"
      tagline="Turn text into vectors with the real model, then check that meaning survived the conversion."
      source="POST /api/labs/embed → app/rag/embeddings.py :: EmbeddingService"
      concept={
        <>
          <Concept label="What happens">
            Each text becomes a list of 384 numbers. Texts that mean similar things end up with
            vectors that point in similar directions.
          </Concept>
          <Concept label="Why it exists">
            This is what lets retrieval search by MEANING instead of by matching words. A question
            about "resource utilisation" can find a paragraph about "how efficiently hardware is
            used" — the words do not overlap, the meaning does.
          </Concept>
          <Concept label="If it were removed">
            You would be back to keyword search, and every paraphrase would miss.
          </Concept>
          <Concept label="Reading the matrix">
            Cosine similarity runs from -1 to 1. Above roughly 0.5 usually means the two texts are
            about the same thing. Near 0 means unrelated.
          </Concept>
          <div className="rounded-lg border border-caution/25 bg-caution/8 px-3 py-2">
            <p className="flex items-start gap-1.5 font-medium text-caution">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
              The scatter plot is a projection
            </p>
            <p className="mt-1 text-caution/90">
              Two dimensions cannot hold 384. The plot shows the two axes carrying the most
              variance, and reports how much that is — so you can judge how much it is hiding.
              Trust the matrix for distances; use the plot only for "who is near whom".
            </p>
          </div>
        </>
      }
      controls={
        <>
          <CardHeader
            title="Texts to embed"
            description="Add up to 12. Try two pairs about different subjects."
            icon={<Braces className="h-4 w-4" />}
            actions={running ? <Loader2 className="h-3.5 w-3.5 animate-spin text-faint" /> : null}
          />

          <div className="space-y-3 px-5 py-4">
            {texts.map((value, index) => (
              <div key={index} className="flex items-start gap-2">
                <span className="mt-2.5 w-5 shrink-0 font-mono text-2xs text-faint">{index + 1}</span>
                <Input
                  value={value}
                  onChange={(event) => {
                    const next = [...texts];
                    next[index] = event.target.value;
                    setTexts(next);
                  }}
                  placeholder="Type a sentence…"
                  wrapperClassName="flex-1"
                />
                <button
                  type="button"
                  onClick={() => setTexts(texts.filter((_, i) => i !== index))}
                  disabled={texts.length <= 1}
                  className="mt-1 inline-flex h-8 w-8 items-center justify-center rounded-lg border border-line bg-surface text-faint transition hover:border-negative/40 hover:text-negative disabled:opacity-40"
                  aria-label={`Remove text ${index + 1}`}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}

            <div className="flex items-center justify-between gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setTexts([...texts, ""])}
                disabled={texts.length >= 12}
              >
                <Plus className="h-3.5 w-3.5" />
                Add text
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setTexts(DEFAULT_TEXTS);
                  setQuery("How do hypervisors work?");
                }}
              >
                Reset to sample
              </Button>
            </div>

            <div className="border-t border-line pt-3">
              <Input
                label="Optional question"
                hint="Embedded through the model's query path and ranked against every text."
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Ask something…"
              />
            </div>

            <Button
              className="w-full"
              onClick={() => void run(texts, query)}
              loading={running}
              disabled={texts.every((t) => !t.trim())}
            >
              <Sparkles className="h-3.5 w-3.5" />
              Embed and compare
            </Button>
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
          {/* ---- concept space ---- */}
          <Card padded={false}>
            <CardHeader
              title="Concept space"
              description={result.projection_note}
              icon={<Sparkles className="h-4 w-4" />}
              actions={
                <Badge tone="neutral">
                  {explained[0] !== undefined
                    ? `${Math.round(explained[0] * 100)}% + ${Math.round((explained[1] ?? 0) * 100)}% variance`
                    : "—"}
                </Badge>
              }
            />
            <div className="px-5 py-4">
              <svg
                viewBox={`0 0 ${PLOT.width} ${PLOT.height}`}
                className="w-full max-w-[520px]"
                role="img"
                aria-label="Concept space projection of the embedded texts"
              >
                <rect
                  x={PLOT.padding}
                  y={PLOT.padding}
                  width={PLOT.width - PLOT.padding * 2}
                  height={PLOT.height - PLOT.padding * 2}
                  rx={10}
                  className="fill-sunken stroke-line"
                  strokeWidth={1}
                />
                {/* axes */}
                <line
                  x1={PLOT.padding}
                  y1={PLOT.padding + (PLOT.height - PLOT.padding * 2) / 2}
                  x2={PLOT.width - PLOT.padding}
                  y2={PLOT.padding + (PLOT.height - PLOT.padding * 2) / 2}
                  className="stroke-line"
                  strokeWidth={0.5}
                  strokeDasharray="3 4"
                />
                <line
                  x1={PLOT.padding + (PLOT.width - PLOT.padding * 2) / 2}
                  y1={PLOT.padding}
                  x2={PLOT.padding + (PLOT.width - PLOT.padding * 2) / 2}
                  y2={PLOT.height - PLOT.padding}
                  className="stroke-line"
                  strokeWidth={0.5}
                  strokeDasharray="3 4"
                />

                {plot.map((point) => {
                  const active = hovered === point.index;
                  return (
                    <g
                      key={point.index}
                      onMouseEnter={() => setHovered(point.index)}
                      onMouseLeave={() => setHovered(null)}
                      className="cursor-pointer"
                    >
                      {active ? (
                        <circle
                          cx={point.cx}
                          cy={point.cy}
                          r={13}
                          className="fill-brand/20"
                        />
                      ) : null}
                      <circle
                        cx={point.cx}
                        cy={point.cy}
                        r={active ? 7 : 5.5}
                        className={active ? "fill-brand" : "fill-brand/75"}
                      />
                      <text
                        x={point.cx}
                        y={point.cy - 12}
                        textAnchor="middle"
                        className="fill-ink font-mono"
                        fontSize={11}
                      >
                        {point.index + 1}
                      </text>
                    </g>
                  );
                })}
              </svg>

              <p className="mt-2 text-2xs leading-relaxed text-faint">
                Each dot is one of your texts, numbered to match the list. Texts about the same
                subject should sit near each other.
              </p>
            </div>
          </Card>

          {/* ---- vectors ---- */}
          <Card padded={false}>
            <CardHeader
              title="The vectors themselves"
              description={`${result.dimensions} dimensions per text. Showing the first ${result.preview_dimensions} — the model uses ${result.dimensions}.`}
              icon={<Braces className="h-4 w-4" />}
              actions={<Badge tone="brand">{result.model.split("/").pop()}</Badge>}
            />
            <div className="divide-y divide-line">
              {result.vectors.map((vector, index) => (
                <div key={index} className="px-5 py-3">
                  <div className="flex items-baseline justify-between gap-3">
                    <p className="min-w-0 flex-1 truncate text-2xs text-muted">
                      <span className="mr-1.5 font-mono text-faint">{index + 1}</span>
                      {texts[index]}
                    </p>
                    <span className="shrink-0 font-mono text-2xs text-faint">
                      norm {result.norms[index]?.toFixed(3)}
                    </span>
                  </div>
                  <p className="mt-1.5 overflow-hidden font-mono text-2xs text-ink">
                    [{vector.map((v) => v.toFixed(3)).join(", ")}
                    {result.dimensions > result.preview_dimensions ? ", …" : ""}]
                  </p>
                </div>
              ))}
            </div>
            <div className="border-t border-line bg-sunken px-5 py-3">
              <p className="text-2xs leading-relaxed text-muted">
                Every norm is 1.000. The vectors are L2-normalised on the way out of the model, so
                cosine similarity is just a dot product — which is exactly what the vector store
                computes.
              </p>
            </div>
          </Card>

          {/* ---- similarity ---- */}
          <Card padded={false}>
            <CardHeader
              title="Cosine similarity"
              description="Exact values on the real vectors, not the projection."
              icon={<Braces className="h-4 w-4" />}
            />
            <div className="overflow-x-auto px-5 py-4">
              <table className="border-separate border-spacing-1">
                <thead>
                  <tr>
                    <th className="w-6" />
                    {result.vectors.map((_, index) => (
                      <th
                        key={index}
                        className="w-12 font-mono text-2xs font-normal text-faint"
                      >
                        {index + 1}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.similarity.map((row, i) => (
                    <tr key={i}>
                      <th className="pr-1 text-right font-mono text-2xs font-normal text-faint">
                        {i + 1}
                      </th>
                      {row.map((value, j) => {
                        const self = i === j;
                        // Map -1..1 onto a brand tint. Unrelated pairs stay near
                        // the surface colour, related ones light up.
                        const intensity = Math.max(0, Math.min(1, value));
                        return (
                          <td
                            key={j}
                            title={`${i + 1} vs ${j + 1}: ${formatScore(value)}`}
                            className={cn(
                              "h-9 w-12 rounded-md text-center font-mono text-2xs",
                              self ? "text-faint" : "text-ink",
                            )}
                            style={{
                              backgroundColor: self
                                ? "rgb(var(--sunken))"
                                : `rgb(var(--brand) / ${0.06 + intensity * 0.42})`,
                            }}
                          >
                            {value.toFixed(2)}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          {/* ---- query ranking ---- */}
          {result.query ? (
            <Card padded={false}>
              <CardHeader
                title="Ranking against your question"
                description={result.query.note}
                icon={<Sparkles className="h-4 w-4" />}
                actions={<Badge tone="accent">{result.query.text}</Badge>}
              />
              <ol className="divide-y divide-line">
                {result.query.ranking.map((entry, position) => (
                  <li key={entry.index} className="flex items-center gap-3 px-5 py-3">
                    <span className="w-6 shrink-0 font-mono text-2xs text-faint">
                      {position + 1}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-2xs text-ink">
                      <span className="mr-1.5 font-mono text-faint">{entry.index + 1}</span>
                      {texts[entry.index]}
                    </span>
                    <span className="shrink-0 font-mono text-2xs text-ink">
                      {formatScore(entry.score)}
                    </span>
                  </li>
                ))}
              </ol>
            </Card>
          ) : null}
        </>
      ) : !error && !running ? (
        <Card>
          <EmptyState
            icon={<Braces className="h-5 w-5" />}
            title="Nothing embedded yet"
            description="Press Embed and compare to see the vectors, the similarity matrix and the concept space."
          />
        </Card>
      ) : null}
    </LabShell>
  );
}
