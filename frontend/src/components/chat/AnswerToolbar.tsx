import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  Copy,
  GraduationCap,
  Languages,
  Minimize2,
  RotateCcw,
  Volume2,
  VolumeX,
  Waypoints,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { ApiError, api } from "@/lib/api";
import { pluralize } from "@/lib/format";
import { hasVoiceFor, speak, type SpeechHandle } from "@/lib/speech";
import { useToast } from "@/state/toast";
import type { Language, Message, Variant } from "@/lib/types";
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
  const [openMenu, setOpenMenu] = useState<"" | "translate" | "shorten">("");
  const [speaking, setSpeaking] = useState(false);
  const [explanation, setExplanation] = useState<string | null>(null);

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

      if (!hasVoiceFor(payload.speech_code)) {
        toast.warning(
          "No voice for this language",
          `This device has no ${payload.speech_code} voice installed, so the text will be read with the closest available voice. Nothing was sent to an external service.`,
        );
      }

      setSpeaking(true);
      speechRef.current = speak(payload.text, {
        speechCode: payload.speech_code,
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
      return;
    }
    setBusy("explain");
    try {
      const result = await api.features.explain(message.id);
      setExplanation(result.explanation);
    } catch (cause) {
      toast.error(
        "Could not generate an explanation",
        cause instanceof ApiError ? cause.message : "Please try again.",
      );
    } finally {
      setBusy("");
    }
  }, [explanation, message.id, toast]);

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
          icon={busy === "explain" ? <Spinner size={14} /> : <GraduationCap className="h-3.5 w-3.5" />}
          label={explanation ? "Hide explanation" : "Explain"}
          active={Boolean(explanation)}
          disabled={busy !== ""}
          onClick={() => void handleExplain()}
        />

        {onOpenTrace ? (
          <ToolbarButton
            icon={<Waypoints className="h-3.5 w-3.5" />}
            label="Trace"
            onClick={onOpenTrace}
          />
        ) : null}
      </div>

      {/* ---- learning-mode explanation ------------------------------ */}
      {explanation ? (
        <div className="animate-slide-down rounded-lg border border-accent/25 bg-accent/6 p-3.5">
          <p className="flex items-center gap-2 text-2xs font-semibold uppercase tracking-wide text-accent">
            <GraduationCap className="h-3.5 w-3.5" />
            Learning mode explanation
          </p>
          <p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-ink">
            {explanation}
          </p>
          <p className="mt-2 text-2xs text-faint">
            This explanation is generated separately from the answer above. The answer itself
            is unchanged.
          </p>
        </div>
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
