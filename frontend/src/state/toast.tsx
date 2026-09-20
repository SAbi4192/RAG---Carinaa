import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";

import { cn } from "@/lib/cn";

/**
 * Transient notifications.
 *
 * WHY NOT A LIBRARY
 * -----------------
 * This is ~100 lines and has no dependency. More importantly, the messages we
 * show are not generic: an upload failure needs to quote the server's own reason
 * ("That file is larger than the 25 MB limit."), and a partial success needs to
 * say what DID happen. A generic toast wrapper would only get in the way.
 *
 * Errors do not auto-dismiss. A message the user missed is worse than a message
 * they have to close.
 */

export type ToastTone = "success" | "error" | "warning" | "info";

export interface Toast {
  id: number;
  tone: ToastTone;
  title: string;
  description?: string;
}

interface ToastContextValue {
  toasts: Toast[];
  push: (tone: ToastTone, title: string, description?: string) => void;
  success: (title: string, description?: string) => void;
  error: (title: string, description?: string) => void;
  warning: (title: string, description?: string) => void;
  info: (title: string, description?: string) => void;
  dismiss: (id: number) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

let nextId = 1;

const TONE_STYLES: Record<ToastTone, { wrap: string; icon: string }> = {
  success: { wrap: "border-positive/30 bg-surface", icon: "text-positive" },
  error: { wrap: "border-negative/40 bg-surface", icon: "text-negative" },
  warning: { wrap: "border-caution/40 bg-surface", icon: "text-caution" },
  info: { wrap: "border-line-strong bg-surface", icon: "text-info" },
};

function ToneIcon({ tone, className }: { tone: ToastTone; className?: string }) {
  const props = { className: cn("h-4 w-4 shrink-0", className), strokeWidth: 2.2 };
  if (tone === "success") return <CheckCircle2 {...props} />;
  if (tone === "error") return <XCircle {...props} />;
  if (tone === "warning") return <AlertTriangle {...props} />;
  return <Info {...props} />;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const push = useCallback(
    (tone: ToastTone, title: string, description?: string) => {
      const id = nextId++;
      setToasts((current) => [...current.slice(-4), { id, tone, title, description }]);

      // Success and info are self-evidently transient. Errors and warnings are
      // not: they usually require the user to change something.
      if (tone === "success" || tone === "info") {
        window.setTimeout(() => dismiss(id), 4500);
      }
    },
    [dismiss],
  );

  const value = useMemo<ToastContextValue>(
    () => ({
      toasts,
      push,
      dismiss,
      success: (title, description) => push("success", title, description),
      error: (title, description) => push("error", title, description),
      warning: (title, description) => push("warning", title, description),
      info: (title, description) => push("info", title, description),
    }),
    [toasts, push, dismiss],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}

      {/* aria-live so screen readers announce messages without stealing focus. */}
      <div
        aria-live="polite"
        aria-atomic="false"
        className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2"
      >
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={cn(
              "pointer-events-auto flex animate-fade-up items-start gap-3 rounded-xl border p-3.5 shadow-lifted",
              TONE_STYLES[toast.tone].wrap,
            )}
            role="status"
          >
            <ToneIcon tone={toast.tone} className={TONE_STYLES[toast.tone].icon} />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-ink">{toast.title}</p>
              {toast.description ? (
                <p className="mt-0.5 text-xs leading-relaxed text-muted">{toast.description}</p>
              ) : null}
            </div>
            <button
              type="button"
              onClick={() => dismiss(toast.id)}
              aria-label="Dismiss notification"
              className="rounded p-0.5 text-faint transition-colors hover:text-ink"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used inside a ToastProvider.");
  return context;
}
