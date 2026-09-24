import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  Copy,
  Download,
  Languages,
  Minimize2,
  RotateCcw,
  Sparkles,
  Volume2,
  VolumeX,
  Waypoints,
  X,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { ApiError, api } from "@/lib/api";
import { pluralize } from "@/lib/format";
import { hasVoiceFor, speak, type SpeechHandle } from "@/lib/speech";
import { useToast } from "@/state/toast";
import type { Language, Message, Variant } from "@/lib/types";
import { Markdown } from "@/components/chat/Markdown";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Feedback";

/**
 * The response toolbar.
 *
 * THE CANONICAL ANSWER PRINCIPLE, MADE VISIBLE
 * --------------------------------------------
 * The answer the model produced is the canonical one. Translate, Shorten and
 * Explain all produce VARIANTS - they are presentation transforms, and none of
 * them overwrite the original. The toolbar enforces that in the interface:
 *
 *   * when a variant is showing, a banner says exactly which one and offers a
 *     one-click return to the original;
 *   * a rejected shortening is reported as a refusal, not as a silent no-op, so
 *     the user learns that the system protects meaning rather than guessing;
 *   * "Show original" is always available, never more than one click away.
 *
 * That last point is the whole reason this is a toolbar and not a settings
 * toggle. A translation the user cannot get out of is a trap.
 */

const SHORTEN_LABELS: Record<string, string> = {
  normal: "Normal",
  short: "Short",
  very_short: "Very short",
};

export interface AnswerToolbarProps {
  message: Message;
  /** The text currently displayed (original or variant). */
  displayedContent: string;
  citations: { number: number; marker: string; location_label: string }[];
  onVariantChange: (variant: Variant | null) => void;
  activeVariant: Variant | null;
  onOpenTrace?: () => void;
  languages: Language[];
  defaultLanguage?: string;
}

export function AnswerToolbar({
  message,
  displayedContent,
  onVariantChange,
  activeVariant,
  onOpenTrace,
  languages,
  defaultLanguage = "en",
}: AnswerToolbarProps) {
  const toast = useToast();

  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState<"" | "translate" | "shorten" | "explain">("");
  const [openMenu, setOpenMenu] = useState<"" | "translate" | "shorten" | "export">("");
  /** Separate from `busy`: an export is a download, not a mutation of the answer,
   *  so it must not grey out Translate/Shorten/Explain while it runs. */
  const [exporting, setExporting] = useState<"" | "md" | "html">("");
  const [speaking, setSpeaking] = useState(false);
  const [explanation, setExplanation] = useState<string | null>(null);
  /**
   * Why an explanation could not be produced.
   *
   * Kept inline under the toolbar rather than in a toast: the failure belongs to
   * this exact control, so the message appears where the result would have been.
   */
  const [explainError, setExplainError] = useState("");

  const speechRef = useRef<SpeechHandle | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  /* ---- stop speech when the message changes or we unmount ------------- */
  useEffect(() => {
    return () => {
      speechRef.current?.stop();
    };
  }, [message.id]);

  /* ---- close menus on outside click ---------------------------------- */
  useEffect(() => {
    if (!openMenu) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setOpenMenu("");
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpenMenu("");
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [openMenu]);

  /* ---- copy ---------------------------------------------------------- */
  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(displayedContent);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      toast.error("Could not copy", "Your browser blocked clipboard access.");
    }
  }, [displayedContent, toast]);

  /* ---- export evidence pack ------------------------------------------ */
  const handleExport = useCallback(
    async (format: "md" | "html") => {
      setOpenMenu("");
      setExporting(format);
      try {
        await api.chat.downloadEvidencePack(message.id, format);
        toast.success(
          "Evidence pack saved",
          format === "html"
            ? "Open it and use Print → Save as PDF for a PDF."
            : "Markdown, with the trace, sources and evidence.",
        );
      } catch (cause) {
        toast.error(
          "Could not export",
          cause instanceof ApiError ? cause.message : "Please try again.",
        );
      } finally {
        setExporting("");
      }
    },
    [message.id, toast],
  );

  /* ---- translate ----------------------------------------------------- */
  const handleTranslate = useCallback(
    async (language: string) => {
      setOpenMenu("");
      setBusy("translate");
      try {
        const variant = await api.features.translate(message.id, language);
        onVariantChange(variant);
        if (variant.warning) {
          toast.warning("Translation needs a look", variant.warning);
        } else {
          toast.success(
            `Translated to ${languages.find((l) => l.code === language)?.name ?? language}`,
            variant.cached
              ? "Loaded from cache."
              : "The original answer was not modified.",
          );
        }
      } catch (cause) {
        // Offline mode refusing to translate is a FEATURE, and the server says so
        // clearly. Passing that message through is more useful than a generic
        // failure notice.
        toast.error(
          "Could not translate",
          cause instanceof ApiError
            ? cause.message
            : "The translation service is unavailable right now.",
        );
      } finally {
        setBusy("");
      }
    },
    [message.id, onVariantChange, languages, toast],
  );

  /* ---- shorten ------------------------------------------------------- */
  const handleShorten = useCallback(
    async (level: string) => {
      setOpenMenu("");
      setBusy("shorten");
      try {
        const variant = await api.features.shorten(message.id, level);
        onVariantChange(variant);
        toast.success(
          `Condensed to "${SHORTEN_LABELS[level] ?? level}"`,
          "Concepts, numbers and citations were preserved.",
        );
      } catch (cause) {
        if (cause instanceof ApiError && cause.code === "shorten_rejected") {
          // A rejection is the system working correctly. Say why, and be clear
          // that nothing changed.
          const issues = (cause.detail?.issues as string[] | undefined) ?? [];
          toast.warning(
            "Shortening rejected - original kept",
            issues.length
              ? `${issues[0]} The original answer is unchanged.`
              : "The condensed version could not be verified as preserving the original's meaning.",
          );
        } else {
          toast.error(
            "Could not shorten",
            cause instanceof ApiError ? cause.message : "Please try again.",
          );
        }
      } finally {
        setBusy("");
      }
    },
    [message.id, onVariantChange, toast],
  );

  /* ---- read aloud ---------------------------------------------------- */
  const handleSpeak = useCallback(async () => {
    if (speaking) {
      speechRef.current?.stop();
      speechRef.current = null;
      setSpeaking(false);
      return;
    }

    const language = activeVariant?.language || defaultLanguage;

    try {
      const payload = await api.features.speech(message.id, language);

      // Ordered candidate tags: the exact preferred voice first, then fallbacks.
      const candidates =
        payload.speech_candidates && payload.speech_candidates.length
          ? payload.speech_candidates
          : [payload.speech_code];

      const matched = hasVoiceFor(candidates);

      setSpeaking(true);
      if (!matched && candidates[0] !== "en") {
        // Only genuinely no-voice-any-match is worth mentioning, and even then it
        // is an information line, not an error: the text will still be read with
        // whatever voice the browser falls back to. Nothing alarmed the user
        // before speech actually failed; that was the whole point of this fix.
        toast.warning(
          "Using the closest available voice",
          `No ${candidates[0]} voice is installed on this device. Your browser will read the text with its default voice. Nothing was sent to an external service.`,
          3000,
        );
      }
      speechRef.current = speak(payload.text, {
        candidates,
        onEnd: () => setSpeaking(false),
        onError: (messageText) => {
          setSpeaking(false);
          toast.error("Read Aloud failed", messageText);
        },
      });
    } catch (cause) {
      toast.error(
        "Could not prepare Read Aloud",
        cause instanceof ApiError ? cause.message : "Please try again.",
      );
    }
  }, [speaking, activeVariant, defaultLanguage, message.id, toast]);

  /* ---- explain ------------------------------------------------------- */
  const handleExplain = useCallback(async () => {
    if (explanation) {
      setExplanation(null);
      setExplainError("");
      return;
    }
    setBusy("explain");
    setExplainError("");
    try {
      const result = await api.features.explain(message.id);
      setExplanation(result.explanation);
    } catch (cause) {
      setExplainError(
        cause instanceof ApiError
          ? cause.message
          : "The explanation could not be generated. Please try again.",
      );
    } finally {
      setBusy("");
    }
  }, [explanation, message.id]);

  const translateOptions = languages.filter((language) => language.code !== (activeVariant?.language ?? "en"));

  return (
    <div ref={menuRef} className="space-y-3">
      {/* ---- active variant banner ---------------------------------- */}
      {activeVariant ? (
        <div className="flex flex-wrap items-center justify-between gap-2 animate-slide-down rounded-lg border border-brand/25 bg-brand/8 px-3 py-2">
          <div className="flex items-center gap-2">
            <Badge tone="brand">
              {activeVariant.kind === "translated"
                ? `Translated · ${activeVariant.language.toUpperCase()}`
                : activeVariant.kind === "shortened"
                  ? `Condensed · ${SHORTEN_LABELS[activeVariant.level] ?? activeVariant.level}`
                  : "Variant"}
            </Badge>
            <span className="text-2xs text-muted">
              The original answer is preserved and unchanged.
            </span>
          </div>
          <Button
            variant="ghost"
            size="sm"
            icon={<RotateCcw className="h-3 w-3" />}
            onClick={() => onVariantChange(null)}
          >
            Show original
          </Button>
        </div>
      ) : null}

      {/* ---- toolbar ------------------------------------------------ */}
      <div className="flex flex-wrap items-center gap-1">
        <ToolbarButton
          icon={copied ? <Check className="h-3.5 w-3.5 text-positive" /> : <Copy className="h-3.5 w-3.5" />}
          label={copied ? "Copied" : "Copy"}
          onClick={() => void handleCopy()}
        />

        {/* translate */}
        <div className="relative">
          <ToolbarButton
            icon={busy === "translate" ? <Spinner size={14} /> : <Languages className="h-3.5 w-3.5" />}
            label="Translate"
            trailing={<ChevronDown className="h-3 w-3" />}
            active={openMenu === "translate"}
            disabled={busy !== ""}
            onClick={() => setOpenMenu(openMenu === "translate" ? "" : "translate")}
          />

          {openMenu === "translate" ? (
            <div className="absolute bottom-full left-0 z-30 mb-1.5 w-52 animate-slide-down rounded-lg border border-line bg-raised p-1 shadow-pop">
              <p className="px-2 py-1.5 text-2xs font-semibold uppercase tracking-wide text-faint">
                Translate answer
              </p>
              <div className="max-h-64 overflow-y-auto scrollbar-thin">
                {translateOptions.map((language) => (
                  <button
                    key={language.code}
                    type="button"
                    onClick={() => void handleTranslate(language.code)}
                    className="flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-1.5 text-left transition-colors hover:bg-sunken"
                  >
                    <span className="text-xs text-ink">{language.name}</span>
                    <span className="font-mono text-2xs text-faint">
                      {language.native_name || language.code}
                    </span>
                  </button>
                ))}
              </div>
              <p className="border-t border-line px-2 pb-1 pt-1.5 text-2xs leading-relaxed text-faint">
                Citations and numbers are preserved. Offline mode will decline rather than
                call an online translator.
              </p>
            </div>
          ) : null}
        </div>

        {/* shorten */}
        <div className="relative">
          <ToolbarButton
            icon={busy === "shorten" ? <Spinner size={14} /> : <Minimize2 className="h-3.5 w-3.5" />}
            label="Shorten"
            trailing={<ChevronDown className="h-3 w-3" />}
            active={openMenu === "shorten"}
            disabled={busy !== ""}
            onClick={() => setOpenMenu(openMenu === "shorten" ? "" : "shorten")}
          />

          {openMenu === "shorten" ? (
            <div className="absolute bottom-full left-0 z-30 mb-1.5 w-64 animate-slide-down rounded-lg border border-line bg-raised p-1 shadow-pop">
              <p className="px-2 py-1.5 text-2xs font-semibold uppercase tracking-wide text-faint">
                Condense answer
              </p>
              {[
                { value: "short", label: "Short", hint: "Roughly 70% of the original" },
                { value: "very_short", label: "Very short", hint: "Roughly 40% of the original" },
              ].map((level) => (
                <button
                  key={level.value}
                  type="button"
                  onClick={() => void handleShorten(level.value)}
                  className="w-full rounded-md px-2.5 py-1.5 text-left transition-colors hover:bg-sunken"
                >
                  <span className="block text-xs text-ink">{level.label}</span>
                  <span className="block text-2xs text-faint">{level.hint}</span>
                </button>
              ))}
              <p className="border-t border-line px-2 pb-1 pt-1.5 text-2xs leading-relaxed text-faint">
                This compresses the answer - it does not rewrite it. If concepts, numbers or
                citations would be lost, the shortening is rejected and the original kept.
              </p>
            </div>
          ) : null}
        </div>

        <ToolbarButton
          icon={speaking ? <VolumeX className="h-3.5 w-3.5" /> : <Volume2 className="h-3.5 w-3.5" />}
          label={speaking ? "Stop" : "Read aloud"}
          active={speaking}
          onClick={() => void handleSpeak()}
        />

        <ToolbarButton
          icon={busy === "explain" ? <Spinner size={14} /> : <Sparkles className="h-3.5 w-3.5" />}
          label={explanation ? "Hide explanation" : "Explain"}
          active={Boolean(explanation)}
          disabled={busy !== ""}
          title={
            explanation
              ? "Hide the simple explanation"
              : "Explain this answer in simple words"
          }
          onClick={() => void handleExplain()}
        />

        {onOpenTrace ? (
          <ToolbarButton
            icon={<Waypoints className="h-3.5 w-3.5" />}
            label="Trace"
            onClick={onOpenTrace}
          />
        ) : null}

        {/* export - a menu, because the two formats answer two different needs:
            a portable document (Markdown) and a printable page that becomes a PDF
            through the browser's own, faithful renderer. */}
        <div className="relative">
          <ToolbarButton
            icon={exporting ? <Spinner size={14} /> : <Download className="h-3.5 w-3.5" />}
            label="Export"
            trailing={<ChevronDown className="h-3 w-3" />}
            active={openMenu === "export"}
            disabled={exporting !== ""}
            title="Download the answer with its sources and trace"
            onClick={() => setOpenMenu(openMenu === "export" ? "" : "export")}
          />

          {openMenu === "export" ? (
            <div className="absolute bottom-full left-0 z-30 mb-1.5 w-72 animate-slide-down rounded-lg border border-line bg-raised p-1 shadow-pop">
              <p className="px-2 py-1.5 text-2xs font-semibold uppercase tracking-wide text-faint">
                Export evidence pack
              </p>
              <button
                type="button"
                onClick={() => void handleExport("md")}
                className="w-full rounded-md px-2.5 py-1.5 text-left transition-colors hover:bg-sunken"
              >
                <span className="block text-xs text-ink">Markdown (.md)</span>
                <span className="block text-2xs text-faint">
                  Question, answer, sources, evidence, trace and provider.
                </span>
              </button>
              <button
                type="button"
                onClick={() => void handleExport("html")}
                className="w-full rounded-md px-2.5 py-1.5 text-left transition-colors hover:bg-sunken"
              >
                <span className="block text-xs text-ink">Printable page (.html)</span>
                <span className="block text-2xs text-faint">
                  Styled for Print → Save as PDF, including citation links.
                </span>
              </button>
              <p className="border-t border-line px-2 pb-1 pt-1.5 text-2xs leading-relaxed text-faint">
                Assembled from the stored answer and its recorded trace. Nothing is
                re-run or regenerated for export.
              </p>
            </div>
          ) : null}
        </div>
      </div>

      {/* ---- the simple explanation ---------------------------------
          Deliberately shaped like help, not like another technical panel:
          a friendly header, the restated answer, and the key ideas as a
          scan-friendly list. The canonical answer above is untouched. */}
      {explanation ? (
        <div className="animate-slide-down overflow-hidden rounded-xl border border-brand/25 bg-brand/[0.04] shadow-card">
          <div className="flex items-center gap-2 border-b border-brand/20 bg-brand/8 px-3.5 py-2">
            <Sparkles className="h-3.5 w-3.5 shrink-0 text-brand" />
            <p className="min-w-0 flex-1 text-xs font-semibold text-ink">
              Let&apos;s make this simpler
            </p>
            <button
              type="button"
              onClick={() => {
                setExplanation(null);
                setExplainError("");
              }}
              aria-label="Close the simple explanation"
              className="rounded p-0.5 text-faint transition-colors hover:text-ink"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>

          <div className="space-y-2 px-3.5 py-3">
            <div>
              <p className="text-[0.625rem] font-semibold uppercase tracking-wider text-faint">
                Original answer
              </p>
              <p className="mt-1 line-clamp-3 whitespace-pre-wrap text-2xs leading-relaxed text-muted">
                {displayedContent}
              </p>
            </div>

            <div className="border-t border-brand/15 pt-2">
              <Markdown content={explanation} className="text-xs" />
            </div>

            <p className="text-[0.625rem] leading-relaxed text-faint">
              Generated separately to help you understand the answer above. The answer
              itself, and its citations, are unchanged.
            </p>
          </div>
        </div>
      ) : null}

      {explainError && !explanation ? (
        <p className="rounded-lg border border-caution/30 bg-caution/8 px-2.5 py-1.5 text-2xs leading-relaxed text-caution">
          {explainError}
        </p>
      ) : null}
    </div>
  );
}

/* -------------------------------------------------------------------------- */

function ToolbarButton({
  icon,
  label,
  onClick,
  active,
  disabled,
  trailing,
  title,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  active?: boolean;
  disabled?: boolean;
  trailing?: React.ReactNode;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title ?? label}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-2xs font-medium transition-all duration-150",
        "disabled:cursor-not-allowed disabled:opacity-50",
        active
          ? "bg-brand/10 text-brand"
          : "text-muted hover:bg-sunken hover:text-ink",
      )}
    >
      {icon}
      <span className="hidden sm:inline">{label}</span>
      {trailing}
    </button>
  );
}

export { pluralize };
