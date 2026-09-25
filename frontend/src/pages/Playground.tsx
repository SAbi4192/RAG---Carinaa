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

import { Card, CardHeader } from "@/components/ui/Card";import ChunkingLab from "./labs/ChunkingLab";
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
  /** Which group the bench belongs to on the dashboard. */
  group: "build" | "find" | "answer" | "conversation";
}

/**
 * Dashboard groups. The numbering is CONTINUOUS across groups (01..11): a gap
 * like 01, 02, 04 used to imply a hidden or removed stage, when the truth was
 * just that a bench had been taken out of the old list. One flat counter over
 * one ordered list cannot drift, because the label is the array index.
 */
const LAB_GROUPS: { id: LabDefinition["group"]; label: string; note: string }[] = [
  {
    id: "build",
    label: "Build the knowledge base",
    note: "What your documents become before anything is asked.",
  },
  {
    id: "find",
    label: "Find the evidence",
    note: "The searches. Re-ranking runs inside Retrieval/Context when enabled.",
  },
  {
    id: "answer",
    label: "Answer honestly",
    note: "Evidence to prompt to answer, and how the answer proves itself.",
  },
  {
    id: "conversation",
    label: "Conversation layer",
    note: "What a multi-turn chat adds on top of one question.",
  },
];

const LABS: LabDefinition[] = [
  {
    slug: "chunking",
    title: "Chunking",
    blurb: "Split text into passages and watch chunk size change the result.",
    icon: Scissors,
    shows: "how one document becomes many searchable units",
    group: "build",
  },
  {
    slug: "embedding",
    title: "Embeddings",
    blurb: "Turn text into vectors and check that meaning survived.",
    icon: Braces,
    shows: "why similar sentences end up with similar numbers",
    group: "build",
  },
  {
    slug: "vectors",
    title: "Vector store",
    blurb: "See what is actually indexed, per document.",
    icon: Database,
    shows: "whether your document really made it into the index",
    needsWorkspace: true,
    group: "build",
  },
  {
    slug: "retrieval",
    title: "Retrieval",
    blurb: "Search with generation switched off and judge the evidence.",
    icon: ScanSearch,
    shows: "which chunks were found, and how close they were",
    needsWorkspace: true,
    group: "find",
  },
  {
    slug: "hybrid",
    title: "Hybrid Retrieval",
    blurb: "One question through dense, BM25 and their fusion — watch the ranking reorganise.",
    icon: GitCompareArrows,
    shows: "what meaning-only search would have missed, and how RRF recovers it",
    needsWorkspace: true,
    group: "find",
  },
  {
    slug: "context",
    title: "Context",
    blurb: "See the numbered evidence block the model was given.",
    icon: Layers,
    shows: "where the [n] citation numbering is created",
    needsWorkspace: true,
    group: "answer",
  },
  {
    slug: "generation",
    title: "Generation",
    blurb: "Read the answer with every qualifier attached.",
    icon: Sparkles,
    shows: "which engine answered and whether the evidence supported it",
    needsWorkspace: true,
    group: "answer",
  },
  {
    slug: "pipeline",
    title: "Full pipeline",
    blurb: "Run a real question and step through every stage.",
    icon: Workflow,
    shows: "the whole system, one measured stage at a time",
    needsWorkspace: true,
    group: "answer",
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
    group: "conversation",
  },
  {
    slug: "scope",
    title: "Scope Lab",
    blurb: "Limit retrieval to chosen documents and compare.",
    icon: Layers,
    shows: "how \"answer only from this document\" is enforced, not requested",
    needsWorkspace: true,
    group: "conversation",
  },
  {
    slug: "memory",
    title: "Memory Lab",
    blurb: "Watch a follow-up question get resolved from the conversation.",
    icon: MessagesSquare,
    shows: "why the question you type is not the question that gets searched",
    needsWorkspace: true,
    group: "conversation",
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
  /** Continuous index across groups, so the numbers never gap (see LAB_GROUPS). */
  let counter = 0;

  return (
    <div className="mx-auto max-w-[1400px] space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-lg font-semibold text-ink">
          <FlaskConical className="h-4.5 w-4.5 text-brand" />
          RAG Laboratory
        </h1>
        <p className="mt-1.5 max-w-3xl text-xs leading-relaxed text-muted">
          {LABS.length} benches grouped in the order the pipeline runs them. Each one
          executes the{" "}
          <strong className="font-semibold text-ink">same production code the real
          pipeline uses</strong> — nothing here is a simulation, so what you measure is
          what the system actually does.
        </p>
      </div>

      {LAB_GROUPS.map((group) => {
        const benches = LABS.filter((definition) => definition.group === group.id);
        if (benches.length === 0) return null;
        return (
          <section key={group.id}>
            <div className="mb-2.5 flex items-baseline gap-3 border-b border-line pb-1.5">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-ink">
                {group.label}
              </h2>
              <p className="text-2xs text-faint">{group.note}</p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {benches.map((definition) => {
                counter += 1;
                const number = String(counter).padStart(2, "0");
                const Icon = definition.icon;
                return (
                  <Link
                    key={definition.slug}
                    to={`/app/playground/${definition.slug}`}
                    className="group"
                    aria-label={`Open bench ${number}: ${definition.title}`}
                  >
                    <Card
                      interactive
                      className="h-full transition-all duration-200 group-hover:-translate-y-0.5 group-hover:border-brand/40 group-hover:shadow-card"
                    >
                      <div className="flex h-full flex-col p-4">
                        <div className="flex items-center justify-between gap-2">
                          <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-brand/25 bg-brand/8 text-brand transition group-hover:bg-brand/12">
                            <Icon className="h-4 w-4" />
                          </span>
                          <span className="font-mono text-2xs font-medium text-faint">
                            {number}
                          </span>
                        </div>

                        <h3 className="mt-3 text-sm font-semibold leading-tight text-ink">
                          {definition.title}
                        </h3>
                        <p className="mt-1 text-2xs leading-relaxed text-muted line-clamp-2">
                          {definition.blurb}
                        </p>

                        <p className="mt-2 flex-1 text-2xs leading-relaxed text-faint line-clamp-3">
                          You will see {definition.shows}.
                        </p>

                        <span className="mt-3 inline-flex items-center gap-1 text-2xs font-medium text-brand">
                          Open bench
                          <ArrowRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5" />
                        </span>
                      </div>
                    </Card>
                  </Link>
                );
              })}
            </div>
          </section>
        );
      })}

      <Card padded={false}>
        <CardHeader
          title="Suggested path"
          description="A beginner-friendly order that builds understanding step by step."
          icon={<MessageSquareText className="h-4 w-4" />}
        />
        <ol className="grid gap-2.5 px-5 py-4 text-2xs leading-relaxed text-muted sm:grid-cols-2 lg:grid-cols-4">
          <li>
            <span className="font-semibold text-ink">1 · Chunking.</span> Paste a
            paragraph and drag the chunk size. Watch the count climb. This is where an
            answer first goes wrong.
          </li>
          <li>
            <span className="font-semibold text-ink">2 · Embeddings.</span> Type two
            related and two unrelated sentences; read the similarity matrix.
          </li>
          <li>
            <span className="font-semibold text-ink">3 · Retrieval.</span> Ask with
            generation off, then raise candidate_k. Judge the evidence before any model
            speaks.
          </li>
          <li>
            <span className="font-semibold text-ink">4 · Full pipeline.</span> One
            question, every stage, measured — the bench to demonstrate from.
          </li>
        </ol>
      </Card>

      <div className="rounded-xl border border-line bg-sunken px-5 py-4">
        <p className="text-2xs leading-relaxed text-muted">
          <strong className="font-semibold text-ink">Nothing here is faked.</strong>{" "}
          Where a stage is not enabled — re-ranking, for example — the laboratory says
          so and shows nothing in its place. A bench that animated plausible-looking
          activity would be worse than no bench, because you could not tell the
          animation from the system.
        </p>
      </div>
    </div>
  );
}
