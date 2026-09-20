import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Layers, Loader2, Scissors, Sparkles } from "lucide-react";

import { cn } from "@/lib/cn";
import { ApiError, api } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import type { PreviewChunk, PreviewChunksResponse } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Textarea } from "@/components/ui/Field";
import { EmptyState } from "@/components/ui/Feedback";
import { Concept, LabShell } from "./LabShell";

/**
 * Chunking Laboratory.
 *
 * Chunking is the stage beginners underestimate most, because it is invisible: the
 * document goes in, an answer comes out, and nothing on screen tells you the text
 * was cut into pieces at all. This lab makes it the most visible thing instead.
 *
 * THE HONESTY NOTE
 * ----------------
 * The brief for this lab asked for a strategy selector (Recursive / Fixed /
 * Semantic). The chunker implements ONE strategy - structure-aware recursive
 * splitting - so a dropdown would be a control that changes nothing, which is
 * worse than no control: it teaches that strategy is a free choice when here it
 * is not. The strategy is therefore shown as fixed, with the reason.
 *
 * Everything else is live. `chunk_size` and `chunk_overlap` are real inputs to the
 * same function the ingestion pipeline calls, and moving either re-runs it.
 */

const SAMPLE = `Cloud computing delivers computing services over the internet.

Virtualization is the foundation of cloud computing. A hypervisor creates and runs virtual machines, each with its own operating system, on a single physical server.

Resource utilisation improves dramatically. A physical server that would otherwise run at 10 to 15 percent utilisation can host many virtual machines and reach 70 to 80 percent utilisation.

There are two types of hypervisor. Type 1 runs directly on the hardware, for example VMware ESXi or KVM. Type 2 runs on top of a host operating system, for example VirtualBox.

Containers take a different approach. Instead of virtualising hardware they share the host kernel, which makes them lighter and faster to start than virtual machines.`;

const PRESETS = [
  { label: "Tiny (200)", size: 200, overlap: 0 },
  { label: "Small (400)", size: 400, overlap: 60 },
  { label: "Balanced (800)", size: 800, overlap: 120 },
  { label: "Large (1600)", size: 1600, overlap: 200 },
];

/** Colour a chunk by position so the proportional bar and the cards agree. */
const CHUNK_TONES = [
  "bg-brand/70",
  "bg-accent/70",
  "bg-positive/70",
  "bg-caution/70",
  "bg-info/70",
  "bg-brand/40",
  "bg-accent/40",
  "bg-positive/40",
];

function toneFor(index: number) {
  return CHUNK_TONES[index % CHUNK_TONES.length];
}

export default function ChunkingLab() {
  const [text, setText] = useState(SAMPLE);
  const [size, setSize] = useState(400);
  const [overlap, setOverlap] = useState(60);
  const [result, setResult] = useState<PreviewChunksResponse | null>(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);

  // The request sequence guard: sliders fire quickly, and an older response
  // arriving late must not overwrite a newer one.
  const sequence = useRef(0);

  const run = useCallback(async (nextText: string, nextSize: number, nextOverlap: number) => {
    if (!nextText.trim()) {
      setResult(null);
      return;
    }
    const ticket = ++sequence.current;
    setRunning(true);
    setError("");
    try {
      const response = await api.documents.previewChunks({
        text: nextText,
        chunk_size: nextSize,
        chunk_overlap: nextOverlap,
      });
      if (ticket !== sequence.current) return;
      setResult(response);
      setSelected(null);
    } catch (cause) {
      if (ticket !== sequence.current) return;
      setError(cause instanceof ApiError ? cause.message : "The chunker could not run.");
      setResult(null);
    } finally {
      if (ticket === sequence.current) setRunning(false);
    }
  }, []);

  // Auto-run, debounced. The brief asks for the document to change as the user
  // moves the slider, and waiting for a button click makes the relationship
  // between the number and the result much harder to see.
  useEffect(() => {
    const timer = window.setTimeout(() => void run(text, size, overlap), 320);
    return () => window.clearTimeout(timer);
  }, [text, size, overlap, run]);

  const chunks = result?.chunks ?? [];

  const stats = useMemo(() => {
    if (chunks.length === 0) return null;
    const lengths = chunks.map((c) => c.characters);
    const total = lengths.reduce((a, b) => a + b, 0);
    const overlapped = chunks.filter((c) => (c.overlap_blocks ?? 0) > 0).length;
    return {
      count: chunks.length,
      total,
      min: Math.min(...lengths),
      max: Math.max(...lengths),
      mean: Math.round(total / lengths.length),
      overlapped,
    };
  }, [chunks]);

  // A proportional bar of the source text, one segment per chunk. This is the
  // "document splitting into chunks" picture - drawn from real character offsets,
  // so the widths are the actual proportions, not decoration.
  const bar = useMemo(() => {
    if (chunks.length === 0) return [];
    const span = Math.max(...chunks.map((c) => c.char_end)) || 1;
    return chunks.map((chunk) => ({
      index: chunk.chunk_index,
      left: (chunk.char_start / span) * 100,
      width: Math.max(1, ((chunk.char_end - chunk.char_start) / span) * 100),
    }));
  }, [chunks]);

  return (
    <LabShell
      title="Chunking Laboratory"
      tagline="Paste text, move the sliders, and watch the real chunker respond. Nothing is stored and no model is called."
      source="POST /api/documents/preview-chunks → app/ingestion/chunker.py :: preview()"
      concept={
        <>
          <Concept label="What happens">
            A document is split into passages small enough to embed and specific enough to be
            useful. Each chunk becomes one vector, and one vector is one searchable unit.
          </Concept>
          <Concept label="Why it exists">
            Embedding a whole document produces one vector for everything it says, which
            matches nothing in particular. Chunking is what gives retrieval something precise
            to aim at.
          </Concept>
          <Concept label="If it were removed">
            Retrieval could only return whole documents. A 40-page PDF would arrive as context
            for a question about one sentence.
          </Concept>
          <Concept label="The overlap trade-off">
            Overlap repeats a little text so a sentence split across a boundary survives in at
            least one chunk. More overlap means better continuity and more storage — and more
            near-duplicate results.
          </Concept>
          <div className="rounded-lg border border-caution/25 bg-caution/8 px-3 py-2">
            <p className="flex items-start gap-1.5 font-medium text-caution">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
              Strategy is fixed
            </p>
            <p className="mt-1 text-caution/90">
              This chunker implements one strategy — structure-aware recursive splitting — so
              there is no strategy selector here. A dropdown that changed nothing would suggest
              strategy is a free choice; it is not, in this implementation.
            </p>
          </div>
        </>
      }
      controls={
        <>
          <CardHeader
            title="Experiment"
            description="The same function the ingestion pipeline calls."
            icon={<Scissors className="h-4 w-4" />}
            actions={running ? <Loader2 className="h-3.5 w-3.5 animate-spin text-faint" /> : null}
          />

          <div className="space-y-4 px-5 py-4">
            <div className="flex flex-wrap gap-2">
              {PRESETS.map((preset) => {
                const active = preset.size === size && preset.overlap === overlap;
                return (
                  <button
                    key={preset.label}
                    type="button"
                    onClick={() => {
                      setSize(preset.size);
                      setOverlap(preset.overlap);
                    }}
                    className={cn(
                      "rounded-lg border px-2.5 py-1.5 text-2xs font-medium transition",
                      active
                        ? "border-brand/40 bg-brand/10 text-brand"
                        : "border-line bg-surface text-muted hover:border-line-strong hover:text-ink",
                    )}
                  >
                    {preset.label}
                  </button>
                );
              })}
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <div className="mb-1.5 flex items-baseline justify-between">
                  <label
                    htmlFor="chunk-size"
                    className="text-2xs font-medium text-ink"
                  >
                    Chunk size
                  </label>
                  <span className="font-mono text-2xs text-faint">
                    {size} chars ≈ {Math.round(size / 4)} tokens
                  </span>
                </div>
                <input
                  id="chunk-size"
                  type="range"
                  min={120}
                  max={2000}
                  step={20}
                  value={size}
                  onChange={(event) => setSize(Number(event.target.value))}
                  className="w-full accent-brand"
                />
              </div>

              <div>
                <div className="mb-1.5 flex items-baseline justify-between">
                  <label
                    htmlFor="chunk-overlap"
                    className="text-2xs font-medium text-ink"
                  >
                    Overlap
                  </label>
                  <span className="font-mono text-2xs text-faint">
                    {overlap} chars ({Math.round((overlap / Math.max(1, size)) * 100)}% of a chunk)
                  </span>
                </div>
                <input
                  id="chunk-overlap"
                  type="range"
                  min={0}
                  max={Math.max(0, Math.floor(size / 2))}
                  step={10}
                  value={overlap}
                  onChange={(event) => setOverlap(Number(event.target.value))}
                  className="w-full accent-brand"
                />
                {overlap === 0 ? (
                  <p className="mt-1 text-2xs text-caution">
                    No overlap — a sentence split across a boundary is lost from both chunks.
                  </p>
                ) : null}
              </div>
            </div>

            <Textarea
              label="Source text"
              hint={`${formatNumber(text.length)} characters`}
              value={text}
              onChange={(event) => setText(event.target.value)}
              rows={7}
              className="font-mono text-2xs"
              placeholder="Paste any text here…"
            />

            <div className="flex items-center justify-between gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setText(SAMPLE);
                  setSize(400);
                  setOverlap(60);
                }}
              >
                Reset to sample
              </Button>
              <Button size="sm" onClick={() => void run(text, size, overlap)} loading={running}>
                <Sparkles className="h-3.5 w-3.5" />
                Run chunking
              </Button>
            </div>
          </div>
        </>
      }
    >
      {error ? (
        <Card>
          <div className="px-5 py-4 text-xs text-negative">{error}</div>
        </Card>
      ) : null}

      {stats ? (
        <Card padded={false}>
          <CardHeader
            title={`${stats.count} chunks`}
            description={`${formatNumber(stats.total)} characters total · mean ${formatNumber(stats.mean)} · range ${formatNumber(stats.min)}–${formatNumber(stats.max)}`}
            icon={<Layers className="h-4 w-4" />}
            actions={
              stats.overlapped > 0 ? (
                <Badge tone="accent">{stats.overlapped} overlapped</Badge>
              ) : (
                <Badge tone="caution">no overlap</Badge>
              )
            }
          />

          {/* The document, split. Widths are real character proportions. */}
          <div className="border-b border-line px-5 py-4">
            <p className="mb-2 text-2xs font-medium text-ink">
              The source text, split into chunks
            </p>
            <div className="relative h-7 w-full overflow-hidden rounded-lg border border-line bg-sunken">
              {bar.map((segment) => (
                <button
                  key={segment.index}
                  type="button"
                  title={`Chunk ${segment.index}`}
                  onClick={() => setSelected(segment.index)}
                  className={cn(
                    "absolute top-0 h-full border-r border-canvas/60 transition-all duration-300",
                    toneFor(segment.index),
                    selected === segment.index
                      ? "ring-2 ring-inset ring-ink/40"
                      : "hover:opacity-80",
                  )}
                  style={{ left: `${segment.left}%`, width: `${segment.width}%` }}
                />
              ))}
            </div>
            <p className="mt-2 text-2xs leading-relaxed text-faint">
              Segment widths are the real character ranges. Because chunks overlap, the segments
              are slightly wider than they would be without overlap — that repeated text is
              exactly what the overlap setting buys.
            </p>
          </div>

          <div className="divide-y divide-line">
            {chunks.map((chunk) => (
              <ChunkRow
                key={chunk.chunk_index}
                chunk={chunk}
                total={chunks.length}
                open={selected === chunk.chunk_index}
                onToggle={() =>
                  setSelected(selected === chunk.chunk_index ? null : chunk.chunk_index)
                }
              />
            ))}
          </div>

          {result && result.count >= 12 ? (
            <div className="border-t border-line bg-sunken px-5 py-3">
              <p className="text-2xs text-muted">
                The preview is capped at 12 chunks. Smaller chunk sizes produce more than this;
                the real ingestion pipeline has no such cap.
              </p>
            </div>
          ) : null}
        </Card>
      ) : !error && !running ? (
        <Card>
          <EmptyState
            icon={<Scissors className="h-5 w-5" />}
            title="Nothing to chunk yet"
            description="Paste some text above and the chunker will run automatically."
          />
        </Card>
      ) : null}
    </LabShell>
  );
}

function ChunkRow({
  chunk,
  total,
  open,
  onToggle,
}: {
  chunk: PreviewChunk;
  total: number;
  open: boolean;
  onToggle: () => void;
}) {
  const previous = chunk.chunk_index > 0 ? chunk.chunk_index - 1 : null;
  const next = chunk.chunk_index < total - 1 ? chunk.chunk_index + 1 : null;

  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-5 py-3 text-left transition hover:bg-raised"
      >
        <span
          className={cn("h-6 w-1.5 shrink-0 rounded-full", toneFor(chunk.chunk_index))}
          aria-hidden
        />
        <span className="w-14 shrink-0 font-mono text-2xs text-faint">
          #{chunk.chunk_index}
        </span>
        <span className="min-w-0 flex-1 truncate text-xs text-ink">
          {chunk.content.slice(0, 110).replace(/\s+/g, " ")}
        </span>
        <span className="shrink-0 font-mono text-2xs text-faint">
          {formatNumber(chunk.characters)} ch
        </span>
        {chunk.overlap_blocks > 0 ? (
          <Badge tone="accent">+{chunk.overlap_blocks} overlap</Badge>
        ) : null}
      </button>

      {open ? (
        <div className="animate-fade-up space-y-3 border-t border-line bg-sunken px-5 py-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <MiniStat label="Characters" value={formatNumber(chunk.characters)} />
            <MiniStat label="~Tokens" value={formatNumber(chunk.tokens_estimated)} />
            <MiniStat
              label="Source range"
              value={`${formatNumber(chunk.char_start)}–${formatNumber(chunk.char_end)}`}
            />
            <MiniStat label="Block type" value={chunk.block_type || "—"} />
          </div>

          <div className="flex flex-wrap gap-1.5 text-2xs">
            <span className="rounded-md border border-line bg-surface px-2 py-0.5 text-muted">
              previous: {previous === null ? "none — first chunk" : `#${previous}`}
            </span>
            <span className="rounded-md border border-line bg-surface px-2 py-0.5 text-muted">
              next: {next === null ? "none — last chunk" : `#${next}`}
            </span>
            <span className="rounded-md border border-line bg-surface px-2 py-0.5 text-muted">
              whole blocks repeated from previous: {chunk.overlap_blocks}
            </span>
          </div>

          <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-surface p-3 font-mono text-2xs leading-relaxed text-ink">
            {chunk.content}
          </pre>
        </div>
      ) : null}
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-line bg-surface px-2.5 py-2">
      <p className="text-2xs text-faint">{label}</p>
      <p className="mt-0.5 font-mono text-xs text-ink">{value}</p>
    </div>
  );
}
