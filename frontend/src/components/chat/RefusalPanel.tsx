import { Link } from "react-router-dom";
import { HelpCircle, Lightbulb } from "lucide-react";

import type { Grounding, Message } from "@/lib/types";
import { pluralize } from "@/lib/format";

/**
 * What to show when Carinaa could not answer from the documents.
 *
 * WHY THIS EXISTS
 * ---------------
 * The refusal is correct behaviour and must stay - inventing an answer is worse than
 * admitting the documents do not cover something. But a bare "I could not find this in
 * the documents" gives the user nothing to act on, and it reads the same whether the
 * workspace is empty, the question was too vague, or the right document exists but is
 * not in scope. Those need different fixes.
 *
 * So the refusal now reports what actually happened, using the real counts from the
 * run, and suggests the specific next step. Every number below is measured - none of
 * it is a guess about why the search failed.
 */
export function RefusalPanel({
  message,
  grounding,
  className,
}: {
  message: Message;
  grounding: Grounding | null;
  className?: string;
}) {
  const retrieval = message.retrieval ?? {};
  const candidates = Number(retrieval.candidates_retrieved ?? retrieval.candidates ?? 0);
  const excerpts = Number(
    retrieval.returned ?? grounding?.excerpt_count ?? 0,
  );
  const topScore = Number(retrieval.top_score ?? grounding?.top_score ?? 0);
  const scopeKind = retrieval.retrieval_scope ? String(retrieval.retrieval_scope) : null;
  const scopeSearched = Number(retrieval.workspace_documents_searched ?? 0);

  // The likely cause, chosen from the measurements rather than guessed. Each of these
  // points at a different fix, which is the whole reason for distinguishing them.
  const diagnosis = (() => {
    if (candidates === 0) {
      return {
        headline: "Nothing in this workspace matched the question.",
        detail:
          "The search returned no candidates at all, which usually means the topic is not covered by the indexed documents.",
      };
    }
    if (topScore > 0 && topScore < 0.35) {
      return {
        headline: "The closest passages were only weakly related.",
        detail: `The best match scored ${topScore.toFixed(2)}, which is low. The documents mention similar words but not this question.`,
      };
    }
    if (scopeKind === "chat" && scopeSearched > 0) {
      return {
        headline: "Nothing in the documents selected for this chat covered it.",
        detail: `This question searched ${pluralize(scopeSearched, "document")}. Other documents in the workspace may cover it.`,
      };
    }
    return {
      headline: "The retrieved passages did not answer the question.",
      detail:
        "Relevant-looking passages were found, but they did not contain the specific information needed.",
    };
  })();

  return (
    <div className={className}>
      <div className="rounded-xl border border-line bg-sunken p-3.5">
        <p className="flex items-start gap-2 text-xs leading-relaxed text-ink">
          <HelpCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted" />
          I couldn&apos;t find enough information in the selected documents to answer
          this confidently.
        </p>

        {/* ---- why ------------------------------------------------- */}
        <div className="mt-3 border-t border-line pt-2.5">
          <p className="text-2xs font-medium text-ink">Why</p>
          <p className="mt-1 text-2xs leading-relaxed text-muted">{diagnosis.headline}</p>
          <p className="mt-1 text-2xs leading-relaxed text-faint">{diagnosis.detail}</p>

          {/* The measured facts behind that reading. Real values only. */}
          <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-2xs">
            <div className="flex gap-1.5">
              <dt className="text-faint">candidates retrieved</dt>
              <dd className="font-mono text-ink">{candidates}</dd>
            </div>
            <div className="flex gap-1.5">
              <dt className="text-faint">excerpts considered</dt>
              <dd className="font-mono text-ink">{excerpts}</dd>
            </div>
            {topScore > 0 ? (
              <div className="flex gap-1.5">
                <dt className="text-faint">best score</dt>
                <dd className="font-mono text-ink">{topScore.toFixed(3)}</dd>
              </div>
            ) : null}
            {scopeKind ? (
              <div className="flex gap-1.5">
                <dt className="text-faint">scope</dt>
                <dd className="font-mono text-ink">
                  {scopeKind === "chat"
                    ? `${scopeSearched} selected document(s)`
                    : "whole workspace"}
                </dd>
              </div>
            ) : null}
          </dl>
        </div>

        {/* ---- what to try ----------------------------------------- */}
        <div className="mt-3 border-t border-line pt-2.5">
          <p className="flex items-center gap-1.5 text-2xs font-medium text-ink">
            <Lightbulb className="h-3 w-3 text-caution" />
            Try
          </p>
          <ul className="mt-1.5 space-y-1 text-2xs leading-relaxed text-muted">
            <li>
              • Ask a more specific question — naming the topic, a section, or a page
              narrows the search a lot.
            </li>
            {scopeKind === "chat" ? (
              <li>
                • Widen the scope: this chat is limited to{" "}
                {pluralize(scopeSearched, "document")}. Unchecking it in{" "}
                <span className="font-medium text-ink">This chat</span> searches the whole
                workspace.
              </li>
            ) : (
              <li>
                • Select a specific document in{" "}
                <span className="font-medium text-ink">This chat</span> to focus on the
                one you expect to contain the answer.
              </li>
            )}
            <li>
              • Upload the relevant document if it is not in the knowledge base yet —{" "}
              <Link
                to="/app/knowledge"
                className="font-medium text-brand underline decoration-brand/30 underline-offset-2"
              >
                open Knowledge Base
              </Link>
              .
            </li>
          </ul>
        </div>

        {/* The honesty statement. A refusal is a feature, and saying so prevents the
            reader from assuming the system simply failed. */}
        <p className="mt-3 border-t border-line pt-2.5 text-2xs leading-relaxed text-faint">
          Carinaa will not invent an answer when the documents do not support one. That is
          why you are seeing this instead of a guess.
        </p>
      </div>
    </div>
  );
}

export default RefusalPanel;
