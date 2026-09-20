import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

import { cn } from "@/lib/cn";

/**
 * Modal dialog.
 *
 * Rendered through a portal so it escapes any `overflow: hidden` ancestor, and
 * it does the four things a dialog must do to be usable rather than merely
 * visible:
 *
 *   1. Escape closes it.
 *   2. Clicking the backdrop closes it - but clicking the panel does not, which
 *      is the detail people get wrong.
 *   3. Body scroll is locked while open, otherwise the page scrolls behind the
 *      overlay and the dialog appears to drift.
 *   4. Focus moves into the dialog on open and returns to the trigger on close,
 *      so keyboard users are not dumped back at the top of the document.
 */

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
  /** Set false for destructive confirmations that must be answered explicitly. */
  closeOnBackdrop?: boolean;
  className?: string;
}

const SIZES = {
  sm: "max-w-sm",
  md: "max-w-lg",
  lg: "max-w-2xl",
  xl: "max-w-4xl",
};

export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = "md",
  closeOnBackdrop = true,
  className,
}: ModalProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;

    previouslyFocused.current = document.activeElement as HTMLElement | null;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKeyDown);

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    // Focus the first focusable element, falling back to the panel itself so
    // Escape still works when the dialog has no controls.
    const focusable = panelRef.current?.querySelector<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    (focusable ?? panelRef.current)?.focus();

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      previouslyFocused.current?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[90] flex items-start justify-center overflow-y-auto p-4 pt-[8vh] sm:pt-[12vh]"
      role="dialog"
      aria-modal="true"
      aria-label={typeof title === "string" ? title : undefined}
    >
      <div
        className="fixed inset-0 animate-fade-in bg-black/45 backdrop-blur-[2px]"
        onClick={closeOnBackdrop ? onClose : undefined}
        aria-hidden
      />

      <div
        ref={panelRef}
        tabIndex={-1}
        className={cn(
          "relative z-10 w-full animate-scale-in rounded-2xl border border-line bg-raised shadow-pop",
          "focus:outline-none",
          SIZES[size],
          className,
        )}
      >
        {title ? (
          <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-ink">{title}</h2>
              {description ? (
                <p className="mt-1 text-xs leading-relaxed text-muted">{description}</p>
              ) : null}
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close dialog"
              className="-mr-1 -mt-1 shrink-0 rounded-lg p-1.5 text-faint transition-colors hover:bg-sunken hover:text-ink"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        ) : null}

        <div className="px-5 py-4">{children}</div>

        {footer ? (
          <div className="flex items-center justify-end gap-2 border-t border-line px-5 py-3.5">
            {footer}
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}

/**
 * Confirmation dialog for destructive actions.
 *
 * The confirm button is only enabled once the caller says the action is safe to
 * run, and it shows the exact consequence rather than a vague "Are you sure?".
 */
export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  destructive = true,
  busy = false,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  busy?: boolean;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      size="sm"
      closeOnBackdrop={false}
      footer={
        <>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-line px-3.5 py-1.5 text-xs font-medium text-ink transition-colors hover:bg-sunken"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className={cn(
              "rounded-lg px-3.5 py-1.5 text-xs font-medium text-white transition-all disabled:opacity-50",
              destructive ? "bg-negative hover:brightness-110" : "bg-brand hover:brightness-110",
            )}
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </>
      }
    >
      <div className="text-xs leading-relaxed text-muted">{message}</div>
    </Modal>
  );
}
