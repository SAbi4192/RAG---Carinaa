import { useEffect, useRef } from "react";

import { cn } from "@/lib/cn";

/**
 * The Carinaa brand motif, animated: a question crosses a cloud of documents,
 * the relevant ones light up and flow into the AI point, and an answer with
 * citations comes out the other side.
 *
 * This is the hero of the landing page and the visual signature used across the
 * product (the logo is its distilled form). The brief asked for a hero that
 * explains what Carinaa does before a line of copy is read, and for the RAG
 * pipeline to be a brand element rather than a diagram - this is that element.
 *
 * How it is built, and why
 * ------------------------
 * Pure SVG + CSS, one small piece of JavaScript. There is no animation library
 * because there is nothing here that needs one:
 *
 *   - the perpetual motion (the search pulse, the flow particles, the document
 *     drift) is CSS keyframes on a GPU-composited transform;
 *   - the cursor parallax reads pointer position and writes three CSS custom
 *     properties (--px/--py) on the wrapper. Layers translate by
 *     calc(var(--px) * Npx) in CSS. A pointer move therefore costs three style
 *     writes and zero React renders - which is the whole reason the hero can be
 *     full-bleed and still feel instant on a student laptop running a demo.
 *
 * Every document node is real evidence, not decoration: the dim ones are the
 * irrelevant pages, the lit ones the passages that actually get read, and the
 * count is deliberately small so nobody mistakes the picture for a claim about
 * scale.
 *
 * Reduced motion
 * --------------
 * The keyframes stop at their resting state and the parallax listener is never
 * attached; the composition itself - question, lit evidence, AI, answer - is the
 * static picture, so the story is still fully legible with zero movement.
 */

interface KnowledgeFlowProps {
  className?: string;
}

// Nine document tiles: which are the ones the search "finds" (true) is the only
// semantic information the illustration carries; everything else is placement.
const DOCUMENTS: Array<{ x: number; y: number; found: boolean; w: number; h: number }> = [
  { x: 300, y: 96, found: true, w: 34, h: 44 },
  { x: 372, y: 150, found: false, w: 34, h: 44 },
  { x: 316, y: 214, found: true, w: 34, h: 44 },
  { x: 402, y: 268, found: false, w: 34, h: 44 },
  { x: 338, y: 322, found: false, w: 34, h: 44 },
  { x: 244, y: 268, found: true, w: 34, h: 44 },
  { x: 226, y: 158, found: false, w: 34, h: 44 },
  { x: 414, y: 96, found: false, w: 34, h: 44 },
];

export function KnowledgeFlow({ className }: KnowledgeFlowProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);

  // Pointer parallax: write --px/--py on the host. Values are normalised to
  // roughly -1..1 from the element's own box, then CSS multiplies them by depth.
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    if (typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      return;
    }
    let frame = 0;
    let pointerX = 0;
    let pointerY = 0;

    const apply = () => {
      frame = 0;
      host.style.setProperty("--px", pointerX.toFixed(3));
      host.style.setProperty("--py", pointerY.toFixed(3));
    };
    const onMove = (event: PointerEvent) => {
      const rect = host.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return;
      pointerX = clamp01((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointerY = clamp01((event.clientY - rect.top) / rect.height) * 2 - 1;
      // Coalesce to one write per frame; a 200Hz mouse must not cost 200 style
      // updates, and an unthrottled listener is the usual cause of a hero that
      // drops frames during a demo.
      if (!frame) frame = requestAnimationFrame(apply);
    };
    const onLeave = () => {
      pointerX = 0;
      pointerY = 0;
      if (frame) cancelAnimationFrame(frame);
      apply();
    };

    host.addEventListener("pointermove", onMove);
    host.addEventListener("pointerleave", onLeave);
    return () => {
      host.removeEventListener("pointermove", onMove);
      host.removeEventListener("pointerleave", onLeave);
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  return (
    <div
      ref={hostRef}
      className={cn("hero-parallax relative", className)}
      aria-hidden
    >
      <svg viewBox="0 0 960 420" className="h-full w-full" fill="none">
        {/* the faint web between documents - a knowledge graph, barely there */}
        <g className="hero-depth hero-depth-far" stroke="currentColor" strokeWidth={1} opacity={0.16}>
          {DOCUMENTS.map((doc, index) =>
            DOCUMENTS.slice(index + 1).map((other, offset) => {
              const distance = Math.hypot(doc.x - other.x, doc.y - other.y);
              if (distance > 150) return null;
              return (
                <line
                  key={`${index}-${index + offset + 1}`}
                  x1={doc.x}
                  y1={doc.y}
                  x2={other.x}
                  y2={other.y}
                />
              );
            }),
          )}
        </g>

        {/* question, entering from the left */}
        <g className="hero-depth hero-depth-near">
          <rect
            x={28}
            y={188}
            width={186}
            height={44}
            rx={12}
            className="fill-brand-wash"
            stroke="currentColor"
            strokeWidth={1.4}
            style={{ color: "rgb(var(--brand) / 0.55)" }}
          />
          <text
            x={121}
            y={215}
            textAnchor="middle"
            className="fill-current"
            style={{ color: "rgb(var(--ink))", fontSize: 14, fontWeight: 600 }}
          >
            What is virtualization?
          </text>
          {/* the travelling search pulse: one dot, looping down the wire */}
          <line x1={214} y1={210} x2={330} y2={210} stroke="url(#flow-wire)" strokeWidth={2} />
          <circle r={5} className="kb-pulse" fill="rgb(var(--brand))" />
        </g>

        {/* document tiles */}
        <g className="hero-depth hero-depth-mid">
          {DOCUMENTS.map((doc, index) => (
            <g
              key={doc.x}
              className="kb-drift"
              style={{ animationDelay: `${index * 0.45}s` }}
              transform={`translate(${doc.x} ${doc.y})`}
            >
              <rect
                x={-doc.w / 2}
                y={-doc.h / 2}
                width={doc.w}
                height={doc.h}
                rx={7}
                fill="rgb(var(--surface))"
                stroke="currentColor"
                strokeWidth={doc.found ? 1.8 : 1.2}
                opacity={doc.found ? 1 : 0.62}
                style={{ color: doc.found ? "rgb(var(--brand))" : "rgb(var(--line-strong))" }}
              />
              {doc.found ? (
                <rect
                  x={-doc.w / 2}
                  y={-doc.h / 2}
                  width={doc.w}
                  height={doc.h}
                  rx={7}
                  fill="rgb(var(--brand) / 0.10)"
                />
              ) : null}
              {/* two text lines */}
              <line
                x1={-doc.w / 2 + 8}
                y1={-8}
                x2={doc.w / 2 - 8}
                y2={-8}
                stroke="currentColor"
                strokeWidth={2}
                opacity={0.4}
                style={{ color: "rgb(var(--ink))" }}
              />
              <line
                x1={-doc.w / 2 + 8}
                y1={0}
                x2={doc.w / 2 - 16}
                y2={0}
                stroke="currentColor"
                strokeWidth={2}
                opacity={0.28}
                style={{ color: "rgb(var(--ink))" }}
              />
            </g>
          ))}
        </g>

        {/* flow lines into the AI point */}
        <g opacity={0.85}>
          <path
            d="M 344 96 C 520 130, 640 170, 700 206"
            stroke="url(#flow-wire)"
            strokeWidth={2}
            strokeDasharray="2 8"
            className="kb-gather"
          />
          <path
            d="M 350 214 C 500 214, 620 210, 700 208"
            stroke="url(#flow-wire)"
            strokeWidth={2}
            strokeDasharray="2 8"
            className="kb-gather"
            style={{ animationDelay: "0.7s" }}
          />
          <path
            d="M 261 268 C 460 320, 640 260, 700 212"
            stroke="url(#flow-wire)"
            strokeWidth={2}
            strokeDasharray="2 8"
            className="kb-gather"
            style={{ animationDelay: "1.3s" }}
          />
        </g>

        {/* the AI point */}
        <g className="hero-depth hero-depth-near">
          <circle cx={724} cy={209} r={34} className="fill-brand-wash" />
          <circle
            cx={724}
            cy={209}
            r={26}
            fill="rgb(var(--brand))"
            opacity={0.18}
            className="kb-orb-halo"
          />
          <circle cx={724} cy={209} r={20} fill="rgb(var(--brand))" />
          {/* a tiny spark inside - the point where context becomes an answer */}
          <circle cx={724} cy={209} r={7} fill="rgb(var(--accent))" className="kb-spark" />
          <text
            x={724}
            y={272}
            textAnchor="middle"
            style={{ fontSize: 11, letterSpacing: "0.12em", color: "rgb(var(--muted))" }}
            className="uppercase"
            fill="currentColor"
          >
            AI
          </text>
        </g>

        {/* the answer, exiting right */}
        <g className="hero-depth hero-depth-near">
          <rect
            x={796}
            y={150}
            width={138}
            height={118}
            rx={14}
            fill="rgb(var(--surface))"
            stroke="currentColor"
            strokeWidth={1.4}
            style={{ color: "rgb(var(--line))" }}
          />
          <rect x={812} y={168} width={92} height={7} rx={3.5} fill="rgb(var(--ink))" opacity={0.8} />
          <rect x={812} y={184} width={106} height={6} rx={3} fill="rgb(var(--ink))" opacity={0.45} />
          <rect x={812} y={196} width={66} height={6} rx={3} fill="rgb(var(--ink))" opacity={0.45} />
          {/* a citation pill, the signature detail: the answer always carries its source */}
          <g className="kb-spark">
            <rect x={876} y={192} width={30} height={18} rx={9} fill="rgb(var(--brand) / 0.14)" stroke="rgb(var(--brand) / 0.5)" strokeWidth={1} />
            <text x={891} y={205} textAnchor="middle" style={{ fontSize: 10, fontWeight: 700 }} fill="rgb(var(--brand))">
              [1]
            </text>
          </g>
          <rect x={812} y={216} width={86} height={6} rx={3} fill="rgb(var(--ink))" opacity={0.45} />
          <rect x={812} y={228} width={102} height={6} rx={3} fill="rgb(var(--ink))" opacity={0.3} />
          <rect x={812} y={244} width={58} height={6} rx={3} fill="rgb(var(--accent))" opacity={0.5} />
        </g>

        <defs>
          <linearGradient id="flow-wire" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="rgb(var(--brand))" />
            <stop offset="100%" stopColor="rgb(var(--accent))" />
          </linearGradient>
        </defs>
      </svg>
    </div>
  );
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

export default KnowledgeFlow;
