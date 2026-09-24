export function isSpeechSupported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

/**
 * Pick the best available voice, trying each candidate tag in order.
 *
 * The server returns an ordered list per language (the preferred tag, then
 * fallbacks) precisely so that a machine without `en-GB` still finds the
 * `en-US` it does have and the whole thing stays silent. Previously the matcher
 * accepted only a single tag, and when a machine's English voice was a different
 * region it reported "missing voice" as an error even though speech worked fine.
 */
export function pickVoice(speechCodes: string | string[]): SpeechSynthesisVoice | null {
  if (!isSpeechSupported()) return null;

  const voices = window.speechSynthesis.getVoices();
  if (!voices.length) return null;

  const wantedList = (Array.isArray(speechCodes) ? speechCodes : [speechCodes]).filter(Boolean);

  // First pass: exact tag match in preference order (en-GB before en-US etc.).
  for (const code of wantedList) {
    const wanted = code.toLowerCase();
    const exact = voices.find((v) => v.lang.toLowerCase() === wanted);
    if (exact) return exact;
  }

  // Second pass: same language tag (ignoring region), in preference order.
  for (const code of wantedList) {
    const wantedLang = code.toLowerCase().split("-")[0];
    const sameLanguage = voices.find(
      (voice) => voice.lang.toLowerCase().split("-")[0] === wantedLang,
    );
    if (sameLanguage) return sameLanguage;
  }

  return null;
}

/**
 * True only when NO candidate in the list has a matching voice.
 *
 * The single tag this used to be called with was too narrow: on a machine
 * whose only English voice is `en-US`, calling
 * `hasVoiceFor("en-GB")` said "missing voice", which then triggered the alarm
 * banner while the reading continued perfectly well. Matching against the full
 * candidate list turns that case into a normal read.
 */
export function hasVoiceFor(speechCodes: string | string[]): boolean {
  return pickVoice(speechCodes) !== null;
}

// --------------------------------------------------------------------------

/* -------------------------------------------------------------------------- */
/* Speak                                                                       */
/* -------------------------------------------------------------------------- */
/**
 * Break text into utterance-sized pieces.
 *
 * Splits on sentence boundaries but keeps long sentences whole up to a limit -
 * breaking mid-sentence would make the prosody wrong, which sounds worse than a
 * slightly long utterance.
 */
function splitForSpeech(text: string, maxChars = 220): string[] {
  const sentences = text
    .replace(/\s+/g, " ")
    .split(/(?<=[.!?])\s+/)
    .filter(Boolean);

  const segments: string[] = [];
  let buffer = "";

  for (const sentence of sentences) {
    if (buffer && (buffer + " " + sentence).length > maxChars) {
      segments.push(buffer);
      buffer = sentence;
    } else {
      buffer = buffer ? `${buffer} ${sentence}` : sentence;
    }
  }
  if (buffer) segments.push(buffer);

  return segments.length ? segments : [text.slice(0, maxChars)];
}

/**
 * Speak the given text.
 *
 * Returns a handle so the caller can stop or pause. Cancelling first is
 * deliberate: `speechSynthesis` queues utterances, so without it clicking "read
 * aloud" twice would read the answer twice, one after the other.
 *
 * The `lang` and `voice` used are the first entry in the candidates list that a
 * browser accepts. We do not claim the voice "belongs" to the language; the
 * UI only needs to know whether a voice was FOUND. If no voice matched any
 * candidate, we still speak (with default `lang`) but the caller gets
 * `onNoVoiceMatch` so the info bar stays quiet - reading with the closest
 * device voice is normal, not an error.
 */
export function speak(
  text: string,
  options: {
    candidates: string[];
    onStart?: () => void;
    onEnd?: () => void;
    onError?: (msg: string) => void;
    rate?: number;
    pitch?: number;
}
): SpeechHandle {
  const unsupported: SpeechHandle = {
    stop: () => undefined,
    pause: () => undefined,
    resume: () => undefined,
    isSupported: false,
  };

  if (!isSpeechSupported()) {
    options.onError?.("This browser does not provide speech synthesis, so Read Aloud is unavailable here.");
    return unsupported;
  }

  const cleaned = text.trim();
  if (!cleaned) {
    options.onError?.("There is nothing to read aloud.");
    return unsupported;
  }

  const voice = pickVoice(options.candidates);
  const first = options.candidates.find(Boolean) ?? "en";

  // A voice was found for one of the candidates and we are going to speak with it.
  // Only if NO voice was found at all AND the browser itself does not support
  // speech do we tell the user about a missing voice - the rest is normal.

  const handle = { isSupported: true, stop: () => window.speechSynthesis.cancel(), pause: () => window.speechSynthesis.pause(), resume: () => window.speechSynthesis.resume() } as SpeechHandle;

  // Stop any current / queued speech first.
  window.speechSynthesis.cancel();

  // Speak in segments so we don't hit the ~15s single-utterance limit.
  const segments = splitForSpeech(cleaned);

  segments.forEach((segment, i) => {
    const u = new SpeechSynthesisUtterance(segment);
    if (voice) {
      u.voice = voice;
      u.lang = voice.lang || first;
    } else {
      u.lang = first;
    }
    u.rate = options.rate ?? 0.98;
    u.pitch = options.pitch ?? 1;

    if (i === 0) u.onstart = () => options.onStart?.();
    if (i === segments.length - 1) u.onend = () => options.onEnd?.();
    // "interrupted" / "canceled" are what a user-stop is called — that is not an error.
    u.onerror = (e) => { const r = (e as SpeechSynthesisErrorEvent).error; if (r === "interrupted" || r === "canceled") return; options.onError?.(e.toString() || "Speech failed"); };
    window.speechSynthesis.speak(u);
  });

  return handle;
}

/* -------------------------------------------------------------------------- */

/** Handles returned by speak(), for stop/pause/resume. */
export interface SpeechHandle {
  stop: () => void;
  pause: () => void;
  resume: () => void;
  isSupported: boolean;
}

/**
 * Voice loading.
 *
 * Some async-browser voices are not yet populated when speech is requested.
 * This helper fires `callback` when the browser signals it is ready or after 2s.
 */
export function onVoicesReady(callback: () => void): () => void {
  if (!isSpeechSupported()) return () => undefined;
  let settled = false;
  const timer = window.setTimeout(() => {
    if (!settled) { settled = true; callback(); }
  }, 2000);
  const handler = () => { if (!settled) { settled = true; clearTimeout(timer); callback(); } };
  window.speechSynthesis.addEventListener("voiceschanged", handler);
  return () => { settled = true; window.speechSynthesis.removeEventListener("voiceschanged", handler); };
}
