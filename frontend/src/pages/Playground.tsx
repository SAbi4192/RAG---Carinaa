import { Link, Navigate, useParams } from "react-router-dom";
import {
  ArrowRight,
  Braces,
  Database,
  FlaskConical,
  GitCompareArrows,
  Layers,
  MessageSquareText,
  Scissors,
  ScanSearch,
  BookMarked,
  MessagesSquare,
  Sparkles,
  Workflow,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/cn";
import { Card, CardHeader } from "@/components/ui/Card";
import ChunkingLab from "./labs/ChunkingLab";
import ContextLab from "./labs/ContextLab";
import EmbeddingLab from "./labs/EmbeddingLab";
import GenerationLab from "./labs/GenerationLab";
import HybridLab from "./labs/HybridLab";
import MemoryLab from "./labs/MemoryLab";
import PipelineLab from "./labs/PipelineLab";
import ReferenceLab from "./labs/ReferenceLab";
import ScopeLab from "./labs/ScopeLab";
import RetrievalLab from "./labs/RetrievalLab";
import VectorStoreLab from "./labs/VectorStoreLab";

/**
 * The RAG Laboratory.
 *
 * Replaces the old Playground, which did one thing well (retrieval) and left the
 * other stages invisible. Each stage of the pipeline now has its own bench, and
 * every bench runs the same code the real pipeline runs.
 *
 * The design rule that matters: a lab that reimplemented a stage would teach the
 * wrong thing, because what is being taught is what THIS system does. So the
 * chunking bench calls the real chunker, the retrieval bench calls the real
 * retriever, and the embedding bench calls the real model.
 *
 * Routes:
 *   /app/playground            the dashboard
 *   /app/playground/:lab       one bench
 */

interface LabDefinition {
  slug: string;
  title: string;
  blurb: string;
  icon: LucideIcon;
  /** What the lab proves you can see. */
  shows: string;
  /** Whether it needs an indexed workspace to be useful. */
  needsWorkspace?: boolean;
}

const LABS: LabDefinition[] = [
  {
    slug: "chunking",
    title: "Chunking",
    blurb: "Split text into passages and watch chunk size change the result.",
    icon: Scissors,
    shows: "how one document becomes many searchable units",
  },
  {
    slug: "embedding",
    title: "Embeddings",
    blurb: "Turn text into vectors and check that meaning survived.",
    icon: Braces,
    shows: "why similar sentences end up with similar numbers",
  },
  {
    slug: "vectors",
    title: "Vector store",
    blurb: "See what is actually indexed, per document.",
    icon: Database,
    shows: "whether your document really made it into the index",
    needsWorkspace: true,
  },
  {
    slug: "retrieval",
    title: "Retrieval",
    blurb: "Search with generation switched off and judge the evidence.",
    icon: ScanSearch,
    shows: "which chunks were found, and how close they were",
    needsWorkspace: true,
  },
  {
    slug: "hybrid",
    title: "Hybrid Retrieval",
    blurb: "One question through dense, BM25 and their fusion — watch the ranking reorganise.",
    icon: GitCompareArrows,
    shows: "what meaning-only search would have missed, and how RRF recovers it",
    needsWorkspace: true,
  },
  {
    slug: "context",
    title: "Context",
    blurb: "See the numbered evidence block the model was given.",
    icon: Layers,
    shows: "where the [n] citation numbering is created",
    needsWorkspace: true,
  },
  {
    slug: "generation",
    title: "Generation",
    blurb: "Read the answer with every qualifier attached.",
    icon: Sparkles,
    shows: "which provider answered and whether the evidence supported it",
    needsWorkspace: true,
  },
  {
    slug: "pipeline",
    title: "Full pipeline",
    blurb: "Run a real question and step through every stage.",
    icon: Workflow,
    shows: "the whole system, one measured stage at a time",
    needsWorkspace: true,
  },
  /* ---- conversational benches ------------------------------------------
     The three above are stages of ONE question. These three are about what a
     conversation adds on top: what the question refers to, what it is allowed to
     search, and how it was understood from what came before. */
  {
    slug: "reference",
    title: "Reference Lab",
    blurb: "Ask about a page or a section and see the search change.",
    icon: BookMarked,
    shows: "why \"page 2\" has to be a filter rather than a search term",
    needsWorkspace: true,
  },
  {
    slug: "scope",
    title: "Scope Lab",
    blurb: "Limit retrieval to chosen documents and compare.",
    icon: Layers,
    shows: "how \"answer only from this document\" is enforced, not requested",
    needsWorkspace: true,
  },
  {
    slug: "memory",
    title: "Memory Lab",
    blurb: "Watch a follow-up question get resolved from the conversation.",
    icon: MessagesSquare,
    shows: "why the question you type is not the question that gets searched",
    needsWorkspace: true,
  },
];

export default function Playground() {
  const { lab } = useParams<{ lab?: string }>();

  if (lab) {
    const known = LABS.find((item) => item.slug === lab);
    if (!known) return <Navigate to="/app/playground" replace />;

    switch (lab) {
      case "chunking":
        return <ChunkingLab />;
      case "embedding":
        return <EmbeddingLab />;
      case "vectors":
        return <VectorStoreLab />;
      case "retrieval":
        return <RetrievalLab />;
      case "hybrid":
        return <HybridLab />;
      case "context":
        return <ContextLab />;
      case "generation":
        return <GenerationLab />;
      case "pipeline":
        return <PipelineLab />;
      case "reference":
        return <ReferenceLab />;
      case "scope":
        return <ScopeLab />;
      case "memory":
        return <MemoryLab />;
      default:
        return <Navigate to="/app/playground" replace />;
    }
  }

  return <LaboratoryDashboard />;
}

function LaboratoryDashboard() {
  return (
    <div className="space-y-5">
      <div>
        <h1 className="flex items-center gap-2 text-base font-semibold text-ink">
          <FlaskConical className="h-4 w-4 text-brand" />
          RAG Laboratory
        </h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">
          {LABS.length} benches: the stages of the pipeline, the retrievers behind them, and the parts a
          conversation adds. Each one runs the{" "}
          <strong className="text-ink">same code the real pipeline runs</strong> — nothing here is
          a simulation, so what you measure is what the system does. Start with Chunking if you are
          new; the stages are listed in the order the pipeline runs them.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {LABS.map((definition, index) => {
          const Icon = definition.icon;
          return (
            <Link
              key={definition.slug}
              to={`/app/playground/${definition.slug}`}
              className="group"
            >
              <Card
                interactive
                className={cn(
                  "h-full transition-all duration-200",
                  "group-hover:-translate-y-0.5 group-hover:border-line-strong",
                )}
              >
                <div className="flex h-full flex-col p-5">
                  <div className="flex items-start justify-between gap-3">
                    <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-brand/25 bg-brand/8 text-brand">
                      <Icon className="h-4 w-4" />
                    </span>
                    <span className="font-mono text-2xs text-faint">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                  </div>

                  <h2 className="mt-3.5 text-sm font-semibold text-ink">{definition.title}</h2>
                  <p className="mt-1 text-2xs leading-relaxed text-muted">{definition.blurb}</p>

                  <p className="mt-3 flex-1 text-2xs leading-relaxed text-faint">
                    You will see {definition.shows}.
                  </p>

                  <span className="mt-4 inline-flex items-center gap-1.5 text-2xs font-medium text-brand">
                    Open bench
                    <ArrowRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5" />
                  </span>
                </div>
              </Card>
            </Link>
          );
        })}
      </div>

      <Card padded={false}>
        <CardHeader
          title="How to use the laboratory"
          description="A suggested order, and what to look for at each step."
          icon={<MessageSquareText className="h-4 w-4" />}
        />
        <ol className="space-y-2.5 px-5 py-4 text-2xs leading-relaxed text-muted">
          <li>
            <span className="font-medium text-ink">1. Chunking.</span> Paste a paragraph, then drag
            the chunk size down. Watch the count climb. This is the first place an answer can go
            wrong — too small and a passage loses its context, too large and it dilutes.
          </li>
          <li>
            <span className="font-medium text-ink">2. Embeddings.</span> Type two sentences about
            the same subject and two about different ones, then read the similarity matrix. Above
            roughly 0.5 means "about the same thing".
          </li>
          <li>
            <span className="font-medium text-ink">3. Vector store.</span> Confirm your document is
            really indexed, and see how many vectors it became.
          </li>
          <li>
            <span className="font-medium text-ink">4. Retrieval.</span> Ask a question with
            generation off. Then raise <span className="font-mono">candidate_k</span> a lot. If the
            same chunks come back, the ranking is fine and the problem is upstream.
          </li>
          <li>
            <span className="font-medium text-ink">5. Context.</span> See those same chunks become
            numbered evidence. This is where <span className="font-mono">[n]</span> comes from.
          </li>
          <li>
            <span className="font-medium text-ink">6. Generation.</span> Read the answer next to its
            grounding verdict. A low verdict is the system being honest, not broken.
          </li>
          <li>
            <span className="font-medium text-ink">7. Full pipeline.</span> Run one question and
            step through all of it at your own pace — the best bench to demonstrate from.
          </li>
        </ol>
      </Card>

      <div className="rounded-xl border border-line bg-sunken px-5 py-4">
        <p className="text-2xs leading-relaxed text-muted">
          <strong className="text-ink">Nothing here is faked.</strong> Where a stage is not enabled
          in the current configuration — re-ranking, for example — the laboratory says so and shows
          nothing in its place. A bench that animated plausible-looking activity would be worse than
          no bench, because you could not tell the animation from the system.
        </p>
      </div>
    </div>
  );
}
