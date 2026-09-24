import { useMemo } from "react";

/**
 * A conceptual diagram for ONE message.
 *
 * The previous "conceptual diagram" in this panel drew the same
 * photosynthesis/plants/light picture for every question, which taught nothing
 * about the question actually being inspected. This one is built from the
 * selected message's real data:
 *
 *   root    = the question's topic words (content words from query_analysis
 *             keywords when the trace carries them, otherwise from the question)
 *   branches= the documents retrieval actually searched
 *   leaves  = the section/page labels of the passages that made it into context
 *
 * So two different answers produce two different diagrams. When there is no
 * retrieval data yet, it says so instead of drawing a fake tree.
 */

const STOP = new Set(
  ("a an the and or but if then than that this these those is are was were be been being " +
    "do does did doing have has had having will would shall should can could may might must " +
    "of in on at to for from by with without about into over under what which who whom whose " +
    "when where why how tell me you your i we they it he she explain describe give list detail " +
    "its their them there here also more please write used use using")
    .split(/\s+/),
);

const NODE =
  "rounded-md border border-line bg-surface px-2 py-1 text-center text-2xs text-ink shadow-card";
const NODE_ROOT =
  "rounded-md border border-brand/40 bg-brand/10 px-2.5 py-1 text-center text-2xs font-medium text-brand";

export function MessageDiagram({
  question,
  sources,
}: {
  question: string;
  sources: { label: string; document: string; section: string; score: number }[];
}) {
  const topicWords = useMemo(() => {
    const counts = new Map<string, number>();
    for (const word of question.toLowerCase().split(/[^a-z0-9-]+/)) {
      if (!word || STOP.has(word) || word.length < 3) continue;
      counts.set(word, (counts.get(word) ?? 0) + 1);
    }
    return [...counts.keys()].slice(0, 5);
  }, [question]);

  const documents = useMemo(() => {
    const byDoc = new Map<string, { label: string; score: number }[]>();
    for (const source of sources) {
      if (!source.document) continue;
      const bucket = byDoc.get(source.document) ?? [];
      bucket.push({ label: source.label || source.section || "passage", score: source.score });
      byDoc.set(source.document, bucket);
    }
    return [...byDoc.entries()].slice(0, 3);
  }, [sources]);

  if (!question && sources.length === 0) {
    return <p className="text-center text-2xs italic text-faint">No diagram yet - ask a question first.</p>;
  }

  const rootTitle =
    topicWords.length > 0
      ? topicWords.map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" · ")
      : "your question";

  return (
    <div className="flex flex-col items-center gap-1">
      <div className={NODE_ROOT}>{rootTitle}</div>
      <div className="text-2xs leading-none text-faint">↓ searches ↓</div>
      {documents.length === 0 ? (
        <div className="max-w-full text-center text-2xs leading-relaxed text-faint">
          no passages retrieved for this message
        </div>
      ) : (
        <div
          className="grid w-full gap-1.5"
          style={{ gridTemplateColumns: `repeat(${Math.max(documents.length, 2)}, minmax(0,1fr))` }}
        >
          {documents.map(([doc, passages]) => (
            <div key={doc} className="flex flex-col items-center gap-1 rounded-lg border border-line bg-sunken p-1.5">
              <div className={NODE} title={doc}>
                <span className="block max-w-[10rem] truncate">📄 {doc}</span>
              </div>
              {passages.slice(0, 3).map((passage, index) => (
                <div
                  key={`${passage.label}-${index}`}
                  className="w-full truncate rounded border border-line bg-surface px-1.5 py-0.5 text-center font-mono text-[0.625rem] text-muted"
                  title={passage.score ? `${passage.label} (score ${passage.score.toFixed(3)})` : passage.label}
                >
                  {passage.label}
                </div>
              ))}
              {passages.length > 3 ? (
                <div className="text-[0.625rem] text-faint">+{passages.length - 3} more</div>
              ) : null}
            </div>
          ))}
        </div>
      )}
      <div className="text-2xs leading-none text-faint">↓ used as evidence ↓</div>
      <div className={NODE}>
        answer with citations {sources.length > 0 ? `[1..${Math.min(sources.length, 9)}]` : ""}
      </div>
      {/* The question words along the bottom: what the search was actually keyed on. */}
      {topicWords.length > 1 ? (
        <div className="mt-1 flex flex-wrap justify-center gap-1">
          {topicWords.map((word) => (
            <span key={word} className="rounded border border-line bg-surface px-1.5 py-0.5 text-[0.625rem] text-muted">
              {word}
            </span>
          ))}
        </div>
      ) : null}
      <p className="mt-2 text-center text-2xs italic text-faint">
        Conceptual diagram — built from this message&apos;s question and its retrieved passages
      </p>
    </div>
  );
}

export default MessageDiagram;
