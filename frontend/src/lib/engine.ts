/**
 * Engine identity, expressed as a ROLE.
 *
 * The backend (app/core/sanitize.py) strips cloud vendor names and model IDs from
 * every public payload; `provider` arrives as "remote", "local", "" (extractive
 * failsafe) or absent. This module is the single place the UI turns those tokens
 * into words, so no component can accidentally render a raw provider string again.
 */

export type EngineTone = "brand" | "caution" | "accent" | "neutral";

export interface EngineFacts {
  provider?: string | null;
  model?: string | null;
  used_fallback?: boolean | null;
}

export function engineLabel(facts: EngineFacts): string {
  if (facts.model === "extractive") return "Extractive (no model)";
  if (facts.provider === "local") {
    const name = facts.model && facts.model !== "extractive" ? facts.model : "";
    return name ? `Local · ${name}` : "Local model";
  }
  if (facts.used_fallback) return "Remote engine · Fallback";
  return "Remote answer engine";
}

export function engineTone(facts: EngineFacts): EngineTone {
  if (facts.model === "extractive") return "caution";
  if (facts.provider === "local") return "accent";
  if (facts.used_fallback) return "caution";
  return "brand";
}

/** Short, table-friendly form. */
export function engineShort(facts: EngineFacts): string {
  if (facts.model === "extractive") return "extractive";
  if (facts.provider === "local") return "local";
  return facts.used_fallback ? "remote · fallback" : "remote";
}
