/**
 * Read Aloud via the browser's own speech synthesis.
 *
 * WHY THE BROWSER AND NOT A SERVICE
 * ---------------------------------
 * `window.speechSynthesis` runs on the user's device. That gives us three things
 * a cloud TTS service would not:
 *
 *   1. It works offline. Offline mode is supposed to be genuinely offline, and
 *      sending the answer to a speech API would break that promise silently.
 *   2. Nothing leaves the machine. The answer text is often the user's own
 *      documents, and shipping it to a third party to be read aloud is a privacy
 *      decision they never agreed to.
 *   3. It is free and instant - no upload, no waiting for a round trip.
 *
 * The trade-off is honest and worth stating: voice quality depends on what the
 * operating system provides, and the set of available voices varies by machine.
 * We surface that rather than pretending every user hears the same thing.
 *
 * The TEXT is prepared server-side (`/features/speech`), because the backend
 * strips citation markers and flattens Markdown using the same tested code path
 * every time. This module only speaks what it is given.
 */

export interface SpeechHandle {
  stop: () => void;
  pause: () => void;
  resume: () => void;
  isSupported: boolean;
}

export function isSpeechSupported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

/**
 * Pick the best available voice for a BCP-47 tag like "en-GB" or "ta-IN".
 *
 * Matching is deliberately tolerant. A machine may have `en-GB` or only `en-US`;
 * a Tamil voice may be entirely absent. We prefer an exact tag, then the same
 * language in any region, then a voice whose name mentions the language, and
 * otherwise return null so the caller can say so plainly instead of silently
 * reading Tamil text in an English accent.
 */
export function pickVoice(speechCode: string): SpeechSynthesisVoice | null {
  if (!isSpeechSupported()) return null;

  const voices = window.speechSynthesis.getVoices();
  if (!voices.length) return null;

  const wanted = speechCode.toLowerCase();
  const [wantedLang] = wanted.split("-");

  const exact = voices.find((voice) => voice.lang.toLowerCase() === wanted);
  if (exact) return exact;

  const sameLanguage = voices.find(
    (voice) => voice.lang.toLowerCase().split("-")[0] === wantedLang,
  );
  if (sameLanguage) return sameLanguage;

  return null;
}

/** True when a voice exists for this language on this device. */
export function hasVoiceFor(speechCode: string): boolean {
  return pickVoice(speechCode) !== null;
}

/** Voices load asynchronously in most browsers; this waits for the first batch. */
export function onVoicesReady(callback: () => void): () => void {
  if (!isSpeechSupported()) return () => undefined;

  if (window.speechSynthesis.getVoices().length) {
    callback();
    return () => undefined;
  }

  const handler = () => callback();
  window.speechSynthesis.addEventListener("voiceschanged", handler);
  return () => window.speechSynthesis.removeEventListener("voiceschanged", handler);
}

export interface SpeakOptions {
  /** BCP-47 tag, e.g. "en-GB". */
  speechCode: string;
  onStart?: () => void;
  onEnd?: () => void;
  onError?: (message: string) => void;
  /** 0.1 - 10. Slightly under 1 reads technical prose more comfortably. */
  rate?: number;
  pitch?: number;
}

/**
 * Speak the given text.
 *
 * Returns a handle so the caller can stop or pause. Cancelling first is
 * deliberate: `speechSynthesis` queues utterances, so without it clicking "read
 * aloud" twice would read the answer twice, one after the other.
 */
export function speak(text: string, options: SpeakOptions): SpeechHandle {
  const unsupported: SpeechHandle = {
    stop: () => undefined,
    pause: () => undefined,
    resume: () => undefined,
    isSupported: false,
  };

  if (!isSpeechSupported()) {
    options.onError?.(
      "This browser does not provide speech synthesis, so Read Aloud is unavailable here.",
    );
    return unsupported;
  }

  const clean = text.trim();
  if (!clean) {
    options.onError?.("There is nothing to read aloud.");
    return unsupported;
  }

  window.speechSynthesis.cancel();

  // Long answers are chunked into sentences. Some browsers truncate a single
  // utterance after roughly 15 seconds, which would cut the answer off mid-way.
  const segments = splitForSpeech(clean);

  const voice = pickVoice(options.speechCode);

  segments.forEach((segment, index) => {
    const utterance = new SpeechSynthesisUtterance(segment);
    utterance.lang = options.speechCode;
    utterance.rate = options.rate ?? 0.98;
    utterance.pitch = options.pitch ?? 1;
    if (voice) utterance.voice = voice;

    if (index === 0) utterance.onstart = () => options.onStart?.();
    if (index === segments.length - 1) {
      utterance.onend = () => options.onEnd?.();
    }
    utterance.onerror = (event) => {
      // "interrupted" and "canceled" are what we get when the user stops it
      // deliberately - that is not an error worth reporting.
      const reason = (event as SpeechSynthesisErrorEvent).error;
      if (reason === "interrupted" || reason === "canceled") return;
      options.onError?.(`Speech playback failed (${reason}).`);
    };

    window.speechSynthesis.speak(utterance);
  });

  return {
    isSupported: true,
    stop: () => window.speechSynthesis.cancel(),
    pause: () => window.speechSynthesis.pause(),
    resume: () => window.speechSynthesis.resume(),
  };
}

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

export function stopSpeaking(): void {
  if (isSpeechSupported()) window.speechSynthesis.cancel();
}
