"""
Read Aloud (spec sections 31-33).

WHY THE SERVER DOES ALMOST NOTHING HERE
---------------------------------------
Speech synthesis happens in the browser, using the device's own voices via the
`SpeechSynthesis` API. That is a deliberate choice:

  * it works OFFLINE. A browser voice is already on the machine, so Read Aloud
    keeps working in Offline mode with no network request (spec section 33).
  * the answer never leaves the device. Sending a private document answer to a
    third-party text-to-speech service would be a privacy problem, and would break
    the offline guarantee the moment the user switched modes.

So the server's job is limited to preparing the TEXT and telling the client which
language tag to use. The client does the speaking.

WHAT THE TEXT PREPARATION DOES
------------------------------
  * strips `[1]` markers, so the voice does not read "bracket one" aloud
  * flattens Markdown, so it does not read "asterisk asterisk bold"
  * drops code fences to a short spoken placeholder rather than reading punctuation
  * collapses whitespace and stray punctuation left behind

That last one matters more than it sounds: removing `[1]` from "resource
utilization [1]." leaves "resource utilization ." which a voice reads with an odd
pause. We clean it up.

IF THE BROWSER HAS NO VOICE FOR THE LANGUAGE
--------------------------------------------
The client detects this and tells the user. We do NOT silently fall back to an
online TTS service - that would contradict both the privacy stance and the offline
guarantee. `voice_hint` below is advisory text the UI shows when no matching voice
is found.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.features.languages import get_language, is_supported
from app.rag.citations import speech_text

# Some voices stumble on very long single utterances, so the client is advised to
# chunk by sentence. We tell it where the sentences end.
_MAX_UTTERANCE_CHARS = 3200


@dataclass
class SpeechPayload:
    text: str
    language: str
    speech_code: str
    native_name: str
    characters: int
    voice_hint: str
    sentences: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "language": self.language,
            "speech_code": self.speech_code,
            "native_name": self.native_name,
            "characters": self.characters,
            "sentences": self.sentences,
            "voice_hint": self.voice_hint,
            "recommended_chunk_chars": _MAX_UTTERANCE_CHARS,
            "speaks_locally": True,
            "note": (
                "Read Aloud uses your browser's built-in speech synthesis, which runs on "
                "your device. Carinaa does not send the answer to an external speech "
                "service, so this works in Offline mode."
            ),
        }


def prepare_speech(text: str, language: str = "en") -> SpeechPayload:
    """Turn an answer into speakable plain text."""
    code = (language or "en").strip().lower()
    if not is_supported(code):
        code = "en"

    language_info = get_language(code)
    spoken = speech_text(text)

    # A browser voice that speaks the wrong language produces gibberish for Indic
    # scripts and CJK, so we tell the user what to expect rather than letting them
    # discover it by listening to nonsense.
    voice_hint = ""
    if code != "en":
        voice_hint = (
            f"Read Aloud will look for a {language_info.name} ({language_info.speech_code}) "
            f"voice on your device. If your system does not have one installed, install it "
            f"from your operating system's language settings - Carinaa will not send the "
            f"text to an online speech service to work around a missing voice."
        )
    else:
        voice_hint = (
            "Read Aloud will use an English voice from your device. If none is available, "
            "your browser will report that speech synthesis is unsupported."
        )

    sentences = sum(1 for _ in _split_for_speech(spoken))

    return SpeechPayload(
        text=spoken,
        language=code,
        speech_code=language_info.speech_code,
        native_name=language_info.native_name,
        characters=len(spoken),
        voice_hint=voice_hint,
        sentences=sentences,
    )


def _split_for_speech(text: str):
    """Yield sentence-ish chunks. Advisory only - the client may use its own."""
    buffer = ""
    for character in text:
        buffer += character
        if character in ".!?\n" and len(buffer) > 40:
            yield buffer.strip()
            buffer = ""
        elif len(buffer) >= _MAX_UTTERANCE_CHARS:
            yield buffer.strip()
            buffer = ""
    if buffer.strip():
        yield buffer.strip()


def read_aloud_enabled() -> bool:
    return settings.read_aloud_enabled
