import { Link } from "react-router-dom";
import {
  ArrowRight,
  BadgeCheck,
  Boxes,
  Braces,
  FileSpreadsheet,
  FileText,
  FileType2,
  GitBranch,
  Layers,
  Lock,
  Moon,
  Presentation,
  Quote,
  Search,
  Shield,
  Sparkles,
  Sun,
  Waypoints,
  WifiOff,
  FileJson,
} from "lucide-react";

import { useTheme } from "@/state/theme";
import { useAuth } from "@/state/auth";
import { Logo } from "@/components/brand/Logo";
import { KnowledgeFlow } from "@/components/brand/KnowledgeFlow";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";

/**
 * Landing page.
 *
 * This page has a job beyond looking good: it has to explain what makes this
 * project different from "a chatbot over your PDFs", because that difference is
 * the entire point. So the copy is specific and technical - two pipelines, real
 * citations, a measured trace, no fabricated confidence - rather than the usual
 * vague promises.
 */

const PIPELINE_INGEST = [
  { step: "Upload", detail: "size-capped, type-checked" },
  { step: "Parse", detail: "8 formats → common shape" },
  { step: "Normalise", detail: "drop noise, keep structure" },
  { step: "Chunk", detail: "structure-aware, small overlap" },
  { step: "Embed", detail: "local model, 384-d" },
  { step: "Index", detail: "workspace-scoped vectors" },
];

const PIPELINE_QUERY = [
  { step: "Question", detail: "normalised, keywords extracted" },
  { step: "Embed", detail: "same model as the chunks" },
  { step: "Retrieve", detail: "cosine search, filtered" },
  { step: "Re-rank", detail: "optional, off by default" },
  { step: "Build context", detail: "numbered, budgeted" },
  { step: "Generate", detail: "Groq → Gemini, or local" },
  { step: "Ground", detail: "is it actually supported?" },
  { step: "Cite", detail: "resolve [n] → real passage" },
];

const FORMATS = [
  { icon: <FileText className="h-4 w-4" />, label: "PDF", note: "page-accurate" },
  { icon: <FileType2 className="h-4 w-4" />, label: "DOCX", note: "headings + tables" },
  { icon: <Presentation className="h-4 w-4" />, label: "PPTX", note: "per-slide" },
  { icon: <FileSpreadsheet className="h-4 w-4" />, label: "XLSX", note: "sheet + row range" },
  { icon: <FileSpreadsheet className="h-4 w-4" />, label: "CSV", note: "dialect sniffed" },
  { icon: <FileText className="h-4 w-4" />, label: "Markdown", note: "section-aware" },
  { icon: <FileText className="h-4 w-4" />, label: "TXT", note: "plain text" },
  { icon: <FileJson className="h-4 w-4" />, label: "JSON", note: "json_path per value" },
];

const CAPABILITIES = [
  {
    icon: <Quote className="h-4.5 w-4.5" />,
    title: "Citations that resolve",
    body: "Every [1] maps back to a real chunk, with the page, slide, sheet or JSON path it came from. If the model cites a source that was never retrieved, that is detected and reported - not quietly dropped.",
  },
  {
    icon: <BadgeCheck className="h-4.5 w-4.5" />,
    title: "Grounding, not vibes",
    body: "Before you see an answer, it is checked against the retrieved evidence and labelled Supported, Partially supported, Insufficient evidence, or Citation error.",
  },
  {
    icon: <Waypoints className="h-4.5 w-4.5" />,
    title: "A trace you can inspect",
    body: "Every stage - query analysis, embedding, search, context building, generation, grounding - with its real duration and real counts. Measured while answering, never reconstructed afterwards.",
  },
  {
    icon: <Shield className="h-4.5 w-4.5" />,
    title: "Documents are data",
    body: "Uploaded files are treated as untrusted reference material, never as instructions. The prompt contains no API keys, so a successful injection has nothing to steal.",
  },
  {
    icon: <Lock className="h-4.5 w-4.5" />,
    title: "Workspaces that don't leak",
    body: "Retrieval is filtered by workspace inside the index and re-verified after it, then cross-checked again against the database. Isolation is tested, not assumed.",
  },
  {
    icon: <WifiOff className="h-4.5 w-4.5" />,
    title: "Genuinely offline",
    body: "A local GGUF model answers with no network at all. Offline mode has no code path to an online provider, so it cannot silently fall back to one - and a test proves it.",
  },
];

export default function Landing() {
  const { resolved, toggle } = useTheme();
  const { isAuthenticated } = useAuth();

  return (
    <div className="min-h-screen bg-canvas">
      {/* ================= nav ================= */}
      <header className="sticky top-0 z-40 border-b border-line bg-canvas/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-5 py-3 sm:px-7">
          <Logo />

          <nav className="hidden items-center gap-6 md:flex">
            <a href="#how" className="text-xs font-medium text-muted transition-colors hover:text-ink">
              How it works
            </a>
            <a href="#capabilities" className="text-xs font-medium text-muted transition-colors hover:text-ink">
              Capabilities
            </a>
            <a href="#formats" className="text-xs font-medium text-muted transition-colors hover:text-ink">
              Formats
            </a>
          </nav>

          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="icon"
              onClick={toggle}
              aria-label={`Switch to ${resolved === "dark" ? "light" : "dark"} mode`}
            >
              {resolved === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </Button>

            {isAuthenticated ? (
              <Link to="/app">
                <Button variant="primary" size="sm" trailing={<ArrowRight className="h-3.5 w-3.5" />}>
                  Open Carinaa
                </Button>
              </Link>
            ) : (
              <>
                <Link to="/sign-in" className="hidden sm:block">
                  <Button variant="ghost" size="sm">
                    Sign in
                  </Button>
                </Link>
                <Link to="/sign-up">
                  <Button variant="primary" size="sm">
                    Get started
                  </Button>
                </Link>
              </>
            )}
          </div>
        </div>
      </header>

      {/* ================= hero ================= */}
      <section className="relative overflow-hidden">
        <div className="bg-brand-wash absolute inset-0" aria-hidden />
        <div className="bg-dotgrid absolute inset-0 opacity-40" aria-hidden />

        <div className="relative mx-auto max-w-6xl px-5 pb-16 pt-16 sm:px-7 sm:pt-20">
          <div className="mx-auto max-w-3xl text-center">
            <div className="mb-5 inline-flex animate-fade-up items-center gap-2 rounded-full border border-brand/25 bg-brand/8 px-3 py-1">
              <Sparkles className="h-3.5 w-3.5 text-brand" />
              <span className="text-2xs font-medium text-brand">
                An AI chatbot that answers using your documents
              </span>
            </div>

            <h1 className="animate-fade-up font-display text-4xl font-bold leading-[1.1] tracking-tight text-ink sm:text-5xl">
              Answers you can{" "}
              <span className="text-gradient-brand">trace back to the page</span> they came from.
            </h1>

            <p className="mx-auto mt-5 max-w-2xl animate-fade-up text-sm leading-relaxed text-muted sm:text-base">
              Ask questions normally. Carinaa finds the relevant passages in your own files,
              answers from them, and shows you exactly where every claim came from — and with
              Learning Mode, you can watch how it works, step by step.
            </p>

            <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
              <Link to={isAuthenticated ? "/app" : "/sign-up"}>
                <Button
                  variant="primary"
                  size="lg"
                  trailing={<ArrowRight className="h-4 w-4" />}
                >
                  {isAuthenticated ? "Open your workspace" : "Start with your documents"}
                </Button>
              </Link>
              <a href="#how">
                <Button variant="outline" size="lg">
                  See how it works
                </Button>
              </a>
            </div>

            <p className="mt-4 text-2xs text-faint">
              No credit card. Your documents stay in your own workspace.
            </p>
          </div>

          {/* ---- the knowledge flow: the product's story, moving ---- */}
          {/* The hero must show what Carinaa does before the reader finishes the
              sentence that says it. The flow below is the brand motif itself -
              question, search pulse, documents that light up because they are
              relevant, evidence gathering into the AI, and a cited answer coming
              out - with the cursor driving a subtle parallax so the picture
              responds to the person looking at it. */}
          <div className="mx-auto mt-10 w-full max-w-4xl">
            <KnowledgeFlow />
          </div>

          {/* ---- product mockup ---- */}
          <div className="mx-auto mt-14 max-w-3xl animate-fade-up">
            <div className="overflow-hidden rounded-2xl border border-line bg-surface shadow-lifted">
              <div className="flex items-center gap-1.5 border-b border-line bg-sunken px-4 py-2.5">
                <span className="h-2.5 w-2.5 rounded-full bg-negative/50" />
                <span className="h-2.5 w-2.5 rounded-full bg-caution/50" />
                <span className="h-2.5 w-2.5 rounded-full bg-positive/50" />
                <span className="ml-3 font-mono text-2xs text-faint">
                  carinaa / cloud-computing-notes
                </span>
              </div>

              <div className="space-y-4 p-5 text-left">
                <div className="flex justify-end">
                  <div className="max-w-md rounded-xl rounded-br-sm bg-brand px-3.5 py-2.5 text-xs leading-relaxed text-brand-ink">
                    How does virtualization improve resource utilization?
                  </div>
                </div>

                <div className="max-w-2xl space-y-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone="positive" icon={<BadgeCheck className="h-3 w-3" />}>
                      Supported
                    </Badge>
                    <Badge tone="neutral" mono>
                      Groq · 1.4 s
                    </Badge>
                    <Badge tone="accent" mono>
                      6 chunks retrieved
                    </Badge>
                  </div>

                  <div className="rounded-xl border border-line bg-canvas p-3.5">
                    <p className="text-xs leading-relaxed text-ink">
                      A physical server that would otherwise run at{" "}
                      <span className="rounded bg-brand/10 px-1 font-medium text-brand">
                        10 to 15 percent utilization
                      </span>{" "}
                      can host many virtual machines and reach{" "}
                      <span className="rounded bg-brand/10 px-1 font-medium text-brand">
                        70 to 80 percent
                      </span>
                      <span className="ml-0.5 font-mono text-2xs text-brand">[1]</span>. The
                      hypervisor allocates CPU time, memory and I/O to each guest
                      <span className="ml-0.5 font-mono text-2xs text-brand">[2]</span>.
                    </p>
                  </div>

                  <div className="grid gap-2 sm:grid-cols-2">
                    {[
                      { n: "1", name: "cloud_computing_notes.md", where: "§ Virtualization" },
                      { n: "2", name: "cloud_computing_notes.md", where: "§ Hypervisor" },
                    ].map((source) => (
                      <div
                        key={source.n}
                        className="flex items-start gap-2.5 rounded-lg border border-line bg-sunken px-3 py-2.5"
                      >
                        <span className="mt-px flex h-4.5 w-4.5 shrink-0 items-center justify-center rounded bg-brand/15 font-mono text-2xs font-semibold text-brand">
                          {source.n}
                        </span>
                        <div className="min-w-0">
                          <p className="truncate text-2xs font-medium text-ink">{source.name}</p>
                          <p className="truncate font-mono text-2xs text-faint">{source.where}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ================= how it works ================= */}
      <section id="how" className="border-t border-line bg-surface/40 py-16">
        <div className="mx-auto max-w-6xl px-5 sm:px-7">
          <div className="max-w-2xl">
            <p className="text-2xs font-semibold uppercase tracking-wider text-brand">
              How it works
            </p>
            <h2 className="mt-2 font-display text-2xl font-bold tracking-tight text-ink sm:text-3xl">
              Two pipelines. One place they meet.
            </h2>
            <p className="mt-3 text-sm leading-relaxed text-muted">
              Ingestion and querying are separate systems with separate jobs. Keeping them
              apart is what makes retrieval debuggable: when an answer is wrong, you can
              tell whether the evidence was never indexed, or was indexed but not found,
              or was found but not used well. They touch in exactly one place - the vector
              store.
            </p>
          </div>

          <div className="mt-10 grid gap-6 lg:grid-cols-[1fr_auto_1fr] lg:items-center">
            {/* ingestion */}
            <div className="rounded-2xl border border-line bg-surface p-5 shadow-card">
              <div className="mb-4 flex items-center gap-2.5">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand/10 text-brand">
                  <Layers className="h-4 w-4" />
                </span>
                <div>
                  <p className="text-xs font-semibold text-ink">Ingestion pipeline</p>
                  <p className="text-2xs text-faint">Runs once per document</p>
                </div>
              </div>

              <ol className="space-y-2.5">
                {PIPELINE_INGEST.map((item, index) => (
                  <li key={item.step} className="flex items-start gap-3">
                    <span className="mt-px flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-sunken font-mono text-2xs font-medium text-muted">
                      {index + 1}
                    </span>
                    <div className="min-w-0">
                      <p className="text-xs font-medium text-ink">{item.step}</p>
                      <p className="font-mono text-2xs text-faint">{item.detail}</p>
                    </div>
                  </li>
                ))}
              </ol>
            </div>

            {/* the meeting point */}
            <div className="flex flex-col items-center gap-2 lg:px-2">
              <div className="hidden h-px w-full bg-line lg:block" />
              <div className="flex items-center gap-2 rounded-xl border border-accent/30 bg-accent/8 px-3.5 py-2.5">
                <Boxes className="h-4 w-4 text-accent" />
                <div>
                  <p className="text-2xs font-semibold text-accent">Vector store</p>
                  <p className="font-mono text-2xs text-accent/80">the only shared state</p>
                </div>
              </div>
              <div className="hidden h-px w-full bg-line lg:block" />
            </div>

            {/* query */}
            <div className="rounded-2xl border border-line bg-surface p-5 shadow-card">
              <div className="mb-4 flex items-center gap-2.5">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent/10 text-accent">
                  <GitBranch className="h-4 w-4" />
                </span>
                <div>
                  <p className="text-xs font-semibold text-ink">Query pipeline</p>
                  <p className="text-2xs text-faint">Runs on every question</p>
                </div>
              </div>

              <ol className="space-y-2.5">
                {PIPELINE_QUERY.map((item, index) => (
                  <li key={item.step} className="flex items-start gap-3">
                    <span className="mt-px flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-sunken font-mono text-2xs font-medium text-muted">
                      {index + 1}
                    </span>
                    <div className="min-w-0">
                      <p className="text-xs font-medium text-ink">{item.step}</p>
                      <p className="font-mono text-2xs text-faint">{item.detail}</p>
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          </div>

          <div className="mt-6 rounded-xl border border-line bg-surface p-4">
            <div className="flex items-start gap-3">
              <Search className="mt-0.5 h-4 w-4 shrink-0 text-muted" />
              <p className="text-xs leading-relaxed text-muted">
                <span className="font-medium text-ink">On re-ranking:</span> it is off by
                default and deliberately limited. Re-ranking can only reorder the candidates
                retrieval already found - it cannot recover evidence that vector search
                missed. Turning it on before basic retrieval works well would just hide
                retrieval problems behind a second model.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ================= capabilities ================= */}
      <section id="capabilities" className="py-16">
        <div className="mx-auto max-w-6xl px-5 sm:px-7">
          <div className="max-w-2xl">
            <p className="text-2xs font-semibold uppercase tracking-wider text-brand">
              Capabilities
            </p>
            <h2 className="mt-2 font-display text-2xl font-bold tracking-tight text-ink sm:text-3xl">
              Built to be inspected, not just used.
            </h2>
          </div>

          <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {CAPABILITIES.map((capability) => (
              <div
                key={capability.title}
                className="rounded-xl border border-line bg-surface p-5 shadow-card transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lifted"
              >
                <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand/10 text-brand">
                  {capability.icon}
                </span>
                <h3 className="mt-3.5 text-sm font-semibold text-ink">{capability.title}</h3>
                <p className="mt-1.5 text-xs leading-relaxed text-muted">{capability.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ================= formats ================= */}
      <section id="formats" className="border-t border-line bg-surface/40 py-16">
        <div className="mx-auto max-w-6xl px-5 sm:px-7">
          <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
            <div className="max-w-xl">
              <p className="text-2xs font-semibold uppercase tracking-wider text-brand">
                Formats
              </p>
              <h2 className="mt-2 font-display text-2xl font-bold tracking-tight text-ink sm:text-3xl">
                Eight formats, one internal shape.
              </h2>
              <p className="mt-3 text-sm leading-relaxed text-muted">
                Each parser keeps what makes its format distinctive and throws away what
                does not survive into a citation. A PDF chunk knows its page. A slide knows
                its slide number. A spreadsheet row knows its sheet and row range. A JSON
                value knows its path. That provenance is what a citation is built from.
              </p>

              <div className="mt-5 flex flex-wrap gap-2">
                <Badge tone="brand" icon={<Braces className="h-3 w-3" />} mono>
                  page_number
                </Badge>
                <Badge tone="accent" mono>
                  slide_number
                </Badge>
                <Badge tone="positive" mono>
                  sheet_name · row_start
                </Badge>
                <Badge tone="info" mono>
                  section
                </Badge>
                <Badge tone="neutral" mono>
                  json_path
                </Badge>
              </div>
            </div>

            <div className="grid w-full max-w-md grid-cols-2 gap-2.5 sm:grid-cols-4 lg:grid-cols-2">
              {FORMATS.map((format) => (
                <div
                  key={format.label}
                  className="flex items-center gap-2.5 rounded-lg border border-line bg-surface px-3 py-2.5"
                >
                  <span className="text-muted">{format.icon}</span>
                  <div className="min-w-0">
                    <p className="text-2xs font-semibold text-ink">{format.label}</p>
                    <p className="truncate text-2xs text-faint">{format.note}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ================= closing CTA ================= */}
      <section className="py-16">
        <div className="mx-auto max-w-6xl px-5 sm:px-7">
          <div className="relative overflow-hidden rounded-2xl border border-line bg-surface px-6 py-12 text-center shadow-card">
            <div className="bg-brand-wash absolute inset-0" aria-hidden />
            <div className="relative">
              <h2 className="font-display text-2xl font-bold tracking-tight text-ink">
                Bring your own documents.
              </h2>
              <p className="mx-auto mt-3 max-w-lg text-sm leading-relaxed text-muted">
                Create a workspace, upload a file, and watch the ingestion pipeline run -
                stage by stage, with real timings. Then ask it something and open the trace.
              </p>
              <div className="mt-7 flex flex-wrap items-center justify-center gap-3">
                <Link to={isAuthenticated ? "/app" : "/sign-up"}>
                  <Button variant="primary" size="lg" trailing={<ArrowRight className="h-4 w-4" />}>
                    {isAuthenticated ? "Open Carinaa" : "Create your workspace"}
                  </Button>
                </Link>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ================= footer ================= */}
      <footer className="border-t border-line py-8">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-5 sm:flex-row sm:px-7">
          <Logo showTagline={false} size={22} />
          <p className="text-2xs text-faint">
            A retrieval-augmented generation project. Built to be understood.
          </p>
        </div>
      </footer>
    </div>
  );
}
