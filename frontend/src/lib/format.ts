/**
 * Formatting helpers.
 *
 * Two rules run through all of these:
 *
 * 1. NEVER invent a number. If a value is missing we render an em dash, not a
 *    zero. "0 ms" and "unknown" mean very different things on a trace panel.
 *
 * 2. Prefer the honest unit. A 37,000 ms latency is far easier to read as
 *    "37.0 s" - but a 400 ms one is not easier to read as "0.4 s". These helpers
 *    pick the unit, they do not round away precision the user needs.
 */

const EM_DASH = "—";

export function isMissing(value: unknown): boolean {
  return value === null || value === undefined || value === "" || Number.isNaN(value);
}

/**
 * Parse an ISO timestamp from the API.
 *
 * The models store timezone-aware UTC, but SQLite drops the offset when a row is
 * read back, so the API can emit "2026-09-22T10:04:11" with no suffix. The
 * JavaScript `new Date()` reads a suffix-less ISO string as LOCAL time, which
 * shifted every timestamp by the user's UTC offset - the cause of a conversation
 * created minutes ago being labelled "6 hours ago". A missing suffix means UTC.
 */
export function parseApiDate(iso: string | null | undefined): Date | null {
  if (isMissing(iso)) return null;
  let text = String(iso).trim();
  if (!/(?:Z|[+-]\d{2}:?\d{2})$/i.test(text)) text += "Z";
  const date = new Date(text);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** `—` for missing values, otherwise the formatted number. */
export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (isMissing(value)) return EM_DASH;
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatCompact(value: number | null | undefined): string {
  if (isMissing(value)) return EM_DASH;
  const n = Number(value);
  if (Math.abs(n) < 1000) return String(n);
  return n.toLocaleString(undefined, { notation: "compact", maximumFractionDigits: 1 });
}

export function formatBytes(bytes: number | null | undefined, digits = 1): string {
  if (isMissing(bytes)) return EM_DASH;
  const n = Number(bytes);
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = n / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(digits)} ${units[unit]}`;
}

/**
 * A ratio in 0..1 rendered as a percentage.
 *
 * `ratio` values from the API are already 0..1 (Chroma cosine similarities and
 * our own metrics), so this does not multiply by 100 again - that mistake turns
 * a 0.86 relevance into "8600%".
 */
export function formatPercent(ratio: number | null | undefined, digits = 0): string {
  if (isMissing(ratio)) return EM_DASH;
  return `${(Number(ratio) * 100).toFixed(digits)}%`;
}

/** Already-percentage values (e.g. document progress) rendered as-is. */
export function formatPercentRaw(percent: number | null | undefined, digits = 0): string {
  if (isMissing(percent)) return EM_DASH;
  return `${Number(percent).toFixed(digits)}%`;
}

export function formatScore(score: number | null | undefined, digits = 3): string {
  if (isMissing(score)) return EM_DASH;
  return Number(score).toFixed(digits);
}

/** Durations pick their unit so both 40 ms and 40,000 ms stay readable. */
export function formatDuration(ms: number | null | undefined): string {
  if (isMissing(ms)) return EM_DASH;
  const n = Number(ms);
  if (n < 1) return "<1 ms";
  if (n < 1000) return `${Math.round(n)} ms`;
  if (n < 60_000) return `${(n / 1000).toFixed(n < 10_000 ? 2 : 1)} s`;
  const minutes = Math.floor(n / 60_000);
  const seconds = Math.round((n % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
}

export function formatDate(iso: string | null | undefined): string {
  const date = parseApiDate(iso);
  if (!date) return EM_DASH;
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function formatDateTime(iso: string | null | undefined): string {
  const date = parseApiDate(iso);
  if (!date) return EM_DASH;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** "14:32" - the wall-clock time of a message, in the user's own timezone. */
export function formatTimeOfDay(iso: string | null | undefined): string {
  const date = parseApiDate(iso);
  if (!date) return EM_DASH;
  return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/**
 * "3 minutes ago".
 *
 * Intl.RelativeTimeFormat does the localisation properly, including pluralisation
 * in languages we have not thought about, so we delegate rather than hand-roll it.
 * The timestamp is parsed with `parseApiDate`, so a naive UTC string from the API
 * is never mistaken for local time.
 */
export function formatRelative(iso: string | null | undefined): string {
  const date = parseApiDate(iso);
  if (!date) return EM_DASH;
  const then = date.getTime();

  const seconds = Math.round((then - Date.now()) / 1000);
  const abs = Math.abs(seconds);

  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ["year", 31_536_000],
    ["month", 2_592_000],
    ["week", 604_800],
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60],
    ["second", 1],
  ];

  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  for (const [unit, size] of units) {
    if (abs >= size || unit === "second") {
      return formatter.format(Math.round(seconds / size), unit);
    }
  }
  return EM_DASH;
}

export function truncate(text: string, max = 140): string {
  const clean = (text ?? "").trim();
  if (clean.length <= max) return clean;
  return `${clean.slice(0, max).trimEnd()}…`;
}

/** "1 document" / "3 documents" */
export function pluralize(count: number, singular: string, plural?: string): string {
  const word = count === 1 ? singular : plural ?? `${singular}s`;
  return `${formatNumber(count)} ${word}`;
}

/* ========================================================================== */
/* Domain-specific presentation                                                */
/* ========================================================================== */

/**
 * Short badge label for a file type. Derived from the filename rather than
 * trusted from the server, so an odd `file_type` cannot produce an empty badge.
 */
export function fileBadge(filename: string): string {
  const dot = (filename ?? "").lastIndexOf(".");
  if (dot < 0) return "FILE";
  const ext = filename.slice(dot + 1).toUpperCase();
  return ext.length > 5 ? "FILE" : ext;
}

/** A stable accent per file type, so the same format always looks the same. */
export function fileAccent(filename: string): string {
  const ext = (filename.split(".").pop() ?? "").toLowerCase();
  switch (ext) {
    case "pdf":
      return "text-negative bg-negative/10 border-negative/20";
    case "docx":
    case "doc":
      return "text-info bg-info/10 border-info/20";
    case "pptx":
    case "ppt":
      return "text-caution bg-caution/10 border-caution/20";
    case "xlsx":
    case "xls":
    case "csv":
      return "text-positive bg-positive/10 border-positive/20";
    case "md":
    case "markdown":
      return "text-brand bg-brand/10 border-brand/20";
    case "json":
      return "text-accent bg-accent/10 border-accent/20";
    default:
      return "text-muted bg-sunken border-line";
  }
}

/**
 * Map a grounding status onto theme classes.
 *
 * The four statuses are a deliberate vocabulary, so the colours are fixed here
 * once rather than being chosen ad hoc in each component. That is what stops
 * PARTIALLY_SUPPORTED from being amber on one screen and blue on another.
 */
export function groundingClasses(status: string): {
  chip: string;
  dot: string;
  label: string;
  description: string;
} {
  switch (status) {
    case "SUPPORTED":
      return {
        chip: "border-positive/30 bg-positive/10 text-positive",
        dot: "bg-positive",
        label: "Supported",
        description:
          "Every claim in this answer is backed by the retrieved evidence, and the citations resolve to real passages.",
      };
    case "PARTIALLY_SUPPORTED":
      return {
        chip: "border-caution/30 bg-caution/10 text-caution",
        dot: "bg-caution",
        label: "Partially supported",
        description:
          "Some of this answer is well evidenced, but at least one claim is not fully backed by the retrieved passages.",
      };
    case "INSUFFICIENT_EVIDENCE":
      return {
        chip: "border-info/30 bg-info/10 text-info",
        dot: "bg-info",
        label: "Insufficient evidence",
        description:
          "The documents in this workspace did not contain enough information to answer this confidently.",
      };
    case "CITATION_ERROR":
      return {
        chip: "border-negative/30 bg-negative/10 text-negative",
        dot: "bg-negative",
        label: "Citation error",
        description:
          "The answer referenced a source that does not exist in the retrieved evidence. Treat it with caution.",
      };
    default:
      return {
        chip: "border-line bg-sunken text-muted",
        dot: "bg-faint",
        label: "Not checked",
        description: "No grounding verdict was recorded for this answer.",
      };
  }
}

/** Colour for a similarity score, so a weak match is visibly weak. */
export function scoreTone(score: number): string {
  if (score >= 0.75) return "text-positive";
  if (score >= 0.5) return "text-caution";
  return "text-muted";
}

/** Language code -> display name, for the translate menu. */
export function languageLabel(code: string, names: Record<string, string> = {}): string {
  return names[code] ?? code.toUpperCase();
}
