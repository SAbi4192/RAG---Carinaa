import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/cn";

/**
 * Markdown answer renderer.
 *
 * Two things beyond `ReactMarkdown`:
 *
 * 1. GFM is enabled, so tables and strikethrough work. Answers about spreadsheets
 *    and comparisons are much clearer as tables, and models produce them readily.
 *
 * 2. Citation markers become clickable. `[1]` in the raw answer is plain text, so
 *    before rendering we rewrite the VALID ones as markdown links with a private
 *    `#cite-N` href, then intercept those in the anchor renderer. The user can
 *    therefore click a marker in the prose and jump to the source card.
 *
 *    Only numbers that actually resolved are linkified. A fabricated `[7]` stays
 *    as inert text, which is the correct behaviour: making it look clickable would
 *    imply a source exists.
 */

/** Split on fenced code blocks so we never rewrite markers inside code samples. */
function splitCodeFences(markdown: string): { text: string; code: boolean }[] {
  const parts: { text: string; code: boolean }[] = [];
  const regex = /```[\s\S]*?```/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = regex.exec(markdown)) !== null) {
    if (match.index > lastIndex) {
      parts.push({ text: markdown.slice(lastIndex, match.index), code: false });
    }
    parts.push({ text: match[0], code: true });
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < markdown.length) {
    parts.push({ text: markdown.slice(lastIndex), code: false });
  }

  return parts.length ? parts : [{ text: markdown, code: false }];
}

export function linkifyCitations(markdown: string, validNumbers: number[]): string {
  if (!validNumbers.length) return markdown;
  const valid = new Set(validNumbers);

  return splitCodeFences(markdown)
    .map((part) => {
      if (part.code) return part.text;
      // Avoid touching inline code spans: `arr[0]` is not a citation.
      return part.text
        .split(/(`[^`\n]*`)/g)
        .map((chunk) => {
          if (chunk.startsWith("`") && chunk.endsWith("`")) return chunk;
          return chunk.replace(/\[(\d{1,3})\]/g, (whole, digits: string) => {
            const number = Number.parseInt(digits, 10);
            if (!valid.has(number)) return whole;
            return `[[${number}]](#cite-${number})`;
          });
        })
        .join("");
    })
    .join("");
}

export interface MarkdownProps {
  content: string;
  validCitationNumbers?: number[];
  onCitationClick?: (number: number) => void;
  className?: string;
}

export const Markdown = memo(function Markdown({
  content,
  validCitationNumbers = [],
  onCitationClick,
  className,
}: MarkdownProps) {
  const prepared = linkifyCitations(content, validCitationNumbers);

  return (
    <div className={cn("answer-body", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // External links open in a new tab with the right rel attributes.
          a({ href, children, ...props }) {
            const target = href ?? "";

            if (target.startsWith("#cite-")) {
              const number = Number.parseInt(target.slice("#cite-".length), 10);
              return (
                <button
                  type="button"
                  onClick={() => onCitationClick?.(number)}
                  className="mx-0.5 inline-flex items-center rounded border border-brand/30 bg-brand/10 px-1 py-px align-baseline font-mono text-2xs font-semibold text-brand transition-colors hover:bg-brand/20"
                  title={`Jump to source ${number}`}
                >
                  [{number}]
                </button>
              );
            }

            return (
              <a href={target} target="_blank" rel="noopener noreferrer" {...props}>
                {children}
              </a>
            );
          },

          // Tables need a scroll container: a wide table would otherwise force the
          // whole message column to scroll horizontally.
          table({ children, ...props }) {
            return (
              <div className="my-3 overflow-x-auto scrollbar-thin">
                <table {...props}>{children}</table>
              </div>
            );
          },
        }}
      >
        {prepared}
      </ReactMarkdown>
    </div>
  );
});
