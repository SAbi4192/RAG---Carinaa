import { useEffect, useRef, useState } from "react";
import {
  Columns2,
  Maximize2,
  Menu,
  PanelLeft,
  PanelLeftClose,
  X,
} from "lucide-react";

import { cn } from "@/lib/cn";

/**
 * The ☰ control: expand or minimise the side panels.
 *
 * WHY A MENU AND NOT A SINGLE TOGGLE
 * ----------------------------------
 * There are two independent side surfaces - the conversation history rail and (in
 * Learning Mode) the RAG pipeline column - and the user may want either at full
 * width. A single button could only cycle through combinations, and cycling is
 * guesswork: you cannot see what the next press will do. So the button opens a
 * small menu that states the current state and lets you pick the next one.
 *
 * The options are deliberately plain words rather than icons alone:
 *
 *   Hidden / Beside chat / Full screen
 *
 * STATE LIVES IN THE PARENT
 * -------------------------
 * This component is presentational. Chat owns `railVisible` and `pipelineMode` and
 * persists them, so the layout survives a reload and a trip through Learning Mode.
 */

export type PipelineMode = "hidden" | "side" | "full";

export interface PanelMenuProps {
  railVisible: boolean;
  onRailVisibleChange: (next: boolean) => void;
  /** Omitted in plain Chat, where there is no pipeline column. */
  pipelineMode?: PipelineMode;
  onPipelineModeChange?: (next: PipelineMode) => void;
  className?: string;
}

const PIPELINE_OPTIONS: { value: PipelineMode; label: string; hint: string }[] = [
  { value: "hidden", label: "Hidden", hint: "Chat fills the screen" },
  { value: "side", label: "Beside chat", hint: "Chat 60% · pipeline 40%" },
  { value: "full", label: "Full screen", hint: "Pipeline only" },
];

export function PanelMenu({
  railVisible,
  onRailVisibleChange,
  pipelineMode,
  onPipelineModeChange,
  className,
}: PanelMenuProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Close on outside click and on Escape, so the menu never traps the user.
  useEffect(() => {
    if (!open) return;

    const onPointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const hasPipeline = Boolean(pipelineMode && onPipelineModeChange);

  return (
    <div ref={containerRef} className={cn("relative", className)}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-label="Show or hide the side panels"
        title="Show or hide the side panels"
        className={cn(
          "inline-flex h-8 items-center gap-1.5 rounded-lg border px-2 text-2xs font-medium transition",
          open
            ? "border-brand/40 bg-brand/10 text-brand"
            : "border-line bg-surface text-muted hover:border-line-strong hover:text-ink",
        )}
      >
        <Menu className="h-4 w-4" />
        <span className="hidden xl:inline">Panels</span>
      </button>

      {open ? (
        <div
          role="menu"
          className="absolute right-0 z-[70] mt-1.5 w-64 animate-scale-in rounded-xl border border-line bg-surface p-2 shadow-pop"
        >
          <div className="flex items-center justify-between px-2 pb-1.5 pt-1">
            <p className="text-2xs font-semibold text-ink">Panels</p>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="rounded p-0.5 text-faint transition hover:text-ink"
              aria-label="Close"
            >
              <X className="h-3 w-3" />
            </button>
          </div>

          {/* ---- conversation history -------------------------------- */}
          <button
            type="button"
            role="menuitem"
            onClick={() => onRailVisibleChange(!railVisible)}
            className="flex w-full items-start gap-2.5 rounded-lg px-2 py-2 text-left transition hover:bg-sunken"
          >
            <span className="mt-0.5 text-muted">
              {railVisible ? (
                <PanelLeftClose className="h-3.5 w-3.5" />
              ) : (
                <PanelLeft className="h-3.5 w-3.5" />
              )}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-2xs font-medium text-ink">
                Conversation history
              </span>
              <span className="mt-0.5 block text-2xs leading-relaxed text-faint">
                {railVisible ? "Shown on the left" : "Hidden — chat is wider"}
              </span>
            </span>
            <span
              className={cn(
                "mt-0.5 h-3.5 w-6 shrink-0 rounded-full border transition",
                railVisible ? "border-brand/40 bg-brand/25" : "border-line bg-sunken"
              )}
            >
              <span
                className={cn(
                  "block h-2.5 w-2.5 rounded-full bg-brand transition-transform",
                  railVisible ? "translate-x-3" : "translate-x-0.5",
                  !railVisible && "bg-faint"
                )}
              />
            </span>
          </button>

          {/* ---- RAG pipeline (Learning Mode only) ------------------- */}
          {hasPipeline ? (
            <div className="mt-1 border-t border-line pt-1.5">
              <p className="flex items-center gap-1.5 px-2 pb-1 text-2xs font-semibold text-ink">
                <Columns2 className="h-3 w-3" /> RAG pipeline
              </p>

              {PIPELINE_OPTIONS.map((option) => {
                const active = pipelineMode === option.value;
                return (
                  <button
                    key={option.value}
                    type="button"
                    role="menuitemradio"
                    aria-checked={active}
                    onClick={() => onPipelineModeChange?.(option.value)}
                    className={cn(
                      "flex w-full items-start gap-2.5 rounded-lg px-2 py-1.5 text-left transition hover:bg-sunken",
                      active && "bg-brand/8",
                    )}
                  >
                    <span
                      className={cn(
                        "mt-1 h-2.5 w-2.5 shrink-0 rounded-full border",
                        active ? "border-brand bg-brand" : "border-line-strong",
                      )}
                    />
                    <span className="min-w-0 flex-1">
                      <span
                        className={cn(
                          "block text-2xs font-medium",
                          active ? "text-brand" : "text-ink",
                        )}
                      >
                        {option.label}
                      </span>
                      <span className="mt-0.5 block text-2xs leading-relaxed text-faint">
                        {option.hint}
                      </span>
                    </span>
                    {option.value === "full" ? (
                      <Maximize2 className="mt-0.5 h-3 w-3 shrink-0 text-faint" />
                    ) : null}
                  </button>
                );
              })}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export default PanelMenu;
