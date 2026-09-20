import { useState } from "react";
import { Cloud, HardDrive, Play } from "lucide-react";

import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";

/**
 * Shared question controls for the labs that run the full pipeline.
 *
 * The mode toggle is a real choice, not decoration: it decides which provider
 * answers, and the response says which one actually did. Offline mode needs the
 * local GGUF model loaded; if it is not, the error explains that rather than
 * falling back online silently.
 */
export function AskControls({
  onRun,
  running,
  defaultQuestion = "How does virtualization improve resource utilization?",
  compact = false,
}: {
  onRun: (question: string, mode: "online" | "offline") => void;
  running: boolean;
  defaultQuestion?: string;
  compact?: boolean;
}) {
  const [question, setQuestion] = useState(defaultQuestion);
  const [mode, setMode] = useState<"online" | "offline">("online");

  return (
    <div className={cn("space-y-3", compact ? "" : "p-5")}>
      <Input
        label="Question"
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") onRun(question, mode);
        }}
        placeholder="Ask something…"
      />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex rounded-lg border border-line bg-sunken p-0.5">
          {(
            [
              { value: "online", label: "Online", icon: Cloud, hint: "Gemini, Groq fallback" },
              { value: "offline", label: "Offline", icon: HardDrive, hint: "local GGUF model" },
            ] as const
          ).map((option) => {
            const Icon = option.icon;
            const active = mode === option.value;
            return (
              <button
                key={option.value}
                type="button"
                onClick={() => setMode(option.value)}
                title={option.hint}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-2xs font-medium transition",
                  active
                    ? "bg-surface text-ink shadow-card"
                    : "text-muted hover:text-ink",
                )}
              >
                <Icon className="h-3 w-3" />
                {option.label}
              </button>
            );
          })}
        </div>

        <Button onClick={() => onRun(question, mode)} loading={running} size="sm">
          <Play className="h-3.5 w-3.5" />
          Run the pipeline
        </Button>
      </div>

      {mode === "offline" ? (
        <p className="text-2xs leading-relaxed text-faint">
          Offline runs Qwen2.5-3B on the CPU. It is slower and less capable than the online
          providers, and it never contacts the network — not even as a fallback.
        </p>
      ) : null}
    </div>
  );
}

export default AskControls;
