import { cn } from "@/lib/cn";

/**
 * The Carinaa mark.
 *
 * The idea: a stack of document lines, with a single line breaking away to a node
 * on the right. That is the product in one glyph - an answer, connected back to
 * the source it came from. It is drawn with `currentColor` plus the brand token so
 * it recolours correctly in both themes rather than being a fixed-colour image.
 */
export function LogoMark({ className, size = 28 }: { className?: string; size?: number }) {
  return (
    <svg
      viewBox="0 0 32 32"
      width={size}
      height={size}
      fill="none"
      className={cn("shrink-0", className)}
      role="img"
      aria-label="Carinaa"
    >
      <rect
        x="1"
        y="1"
        width="30"
        height="30"
        rx="9"
        className="fill-brand"
      />
      {/* Document lines */}
      <rect x="8" y="9.5" width="10" height="2" rx="1" className="fill-brand-ink" opacity="0.95" />
      <rect x="8" y="14.5" width="13" height="2" rx="1" className="fill-brand-ink" opacity="0.75" />
      <rect x="8" y="19.5" width="7" height="2" rx="1" className="fill-brand-ink" opacity="0.55" />

      {/* The citation link: the last line routes out to a source node. */}
      <path
        d="M15 20.5 H19.5 C21.4 20.5 22.8 19.1 22.8 17.2 V14.6"
        className="stroke-brand-ink"
        strokeWidth="1.6"
        strokeLinecap="round"
        opacity="0.9"
      />
      <circle cx="22.8" cy="12.4" r="2.5" className="fill-brand-ink" />
    </svg>
  );
}

/**
 * Mark plus wordmark. `compact` drops the tagline for tight spaces like a
 * collapsed sidebar.
 */
export function Logo({
  className,
  size = 28,
  showTagline = true,
}: {
  className?: string;
  size?: number;
  showTagline?: boolean;
}) {
  return (
    <div className={cn("flex items-center gap-2.5", className)}>
      <LogoMark size={size} />
      <div className="min-w-0">
        <p className="font-display text-[0.9375rem] font-bold leading-tight tracking-tight text-ink">
          Carinaa
        </p>
        {showTagline ? (
          <p className="text-2xs leading-tight text-faint">Every answer, traceable</p>
        ) : null}
      </div>
    </div>
  );
}
