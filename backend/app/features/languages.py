"""
Supported languages.

WHY THIS IS ONE FILE
--------------------
The language list appears in the API, in the translation prompt, in the Read Aloud
voice matcher, and in the frontend dropdown. If it were duplicated in four places
they would drift. This is the single source of truth; the frontend fetches it from
`GET /api/settings/languages`.

BCP-47 CODES
------------
`speech_code` is the tag handed to the browser's SpeechSynthesis API. Getting this
right is what makes Read Aloud pick a Tamil voice for Tamil text instead of reading
it with an English voice and mangling the pronunciation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Language:
    code: str            # internal code used in the API and database
    name: str            # English name
    native_name: str     # name in its own script, shown in the picker
    flag: str
    speech_code: str     # preferred BCP-47 tag for browser speech synthesis
    rtl: bool = False
    default: bool = False
    # Ordered fallbacks used when `speech_code` has no installed voice - see
    # `speech_candidates()`. English first for Tanglish is correct, not lazy:
    # the text IS Roman script and an English voice reads it phonetically.
    speech_fallbacks: tuple[str, ...] = ()

    # Expected OUTPUT tokens per CHARACTER of the source, when translating INTO
    # this language. Used to size the generation budget.
    #
    # A translation is a rewrite, not a summary: it carries the same content as the
    # source. For Latin scripts that is cheaper in tokens than the source's
    # character count suggests; for Indic scripts it is much more expensive,
    # because the script tokenises poorly and the text gets longer.
    #
    # This is not theory. A 552-character English answer became 948 characters of
    # Tamil, and the old budget - derived from source characters alone - stopped
    # the model mid-sentence. The translation was silently incomplete.
    #
    # The numbers are deliberately generous: over-budgeting costs nothing when the
    # model stops on its own, whereas under-budgeting truncates the answer.
    token_expansion: float = 0.6


LANGUAGES: tuple[Language, ...] = (
    Language(
        "en",
        "English",
        "English",
        "GB",
        # Deliberately NOT a fixed regional tag. A hard-coded "en-GB" made
        # Read Aloud warn "no en-GB voice installed" on machines whose only
        # English voice is en-US - a warning about a non-event. The browser
        # picks the user's default English voice when only the language is given.
        "en",
        default=True,
        token_expansion=0.4,
        speech_fallbacks=("en-US", "en-AU", "en-IN"),
    ),
    Language("it", "Italian", "Italiano", "IT", "it-IT", token_expansion=0.7),
    Language("ja", "Japanese", "日本語", "JP", "ja-JP", token_expansion=1.4),
    Language("hi", "Hindi", "हिन्दी", "IN", "hi-IN", token_expansion=2.2),
    Language("te", "Telugu", "తెలుగు", "IN", "te-IN", token_expansion=2.3),
    Language("ta", "Tamil", "தமிழ்", "IN", "ta-IN", token_expansion=2.4),
    Language("ml", "Malayalam", "മലയാളം", "IN", "ml-IN", token_expansion=2.6),
    Language(
        "ta-ta",
        "Tanglish",
        "Tanglish (Tamil in English letters)",
        "IN",
        # Tamil words in Roman script are read best by an English voice.
        "en-IN",
        token_expansion=0.8,
        speech_fallbacks=("en", "en-GB"),
    ),
)

_BY_CODE: dict[str, Language] = {lang.code: lang for lang in LANGUAGES}

DEFAULT_LANGUAGE = "en"


def is_supported(code: str) -> bool:
    return (code or "").strip().lower() in _BY_CODE


def get_language(code: str) -> Language:
    """Look up a language, falling back to English for anything unknown."""
    return _BY_CODE.get((code or "").strip().lower(), _BY_CODE[DEFAULT_LANGUAGE])


def language_name(code: str) -> str:
    """English name, for embedding inside a prompt."""
    return get_language(code).name


def native_name(code: str) -> str:
    return get_language(code).native_name


def translation_token_budget(
    source: str, code: str, *, floor: int = 1024, ceiling: int = 4096
) -> int:
    """How many output tokens to allow when translating `source` into `code`.

    Sized from the source length and the target script's expansion factor, with a
    floor so short answers still get room and a ceiling so one long answer cannot
    ask a small local model for more than its context window can hold.

    Under-budgeting truncates the translation mid-sentence, which is the failure
    this exists to prevent.
    """
    estimate = int(len(source or "") * get_language(code).token_expansion)
    return max(floor, min(estimate, ceiling))


def speech_code(code: str) -> str:
    return get_language(code).speech_code


def speech_candidates(code: str) -> list[str]:
    """Preferred BCP-47 tags, best first, for choosing a browser voice.

    The frontend tries these in order and treats the first match as normal
    behaviour, so a machine without one regional English voice still reads
    English aloud without being told anything is wrong.
    """
    language = get_language(code)
    return [language.speech_code, *language.speech_fallbacks]


def translation_hint(code: str) -> str:
    """Extra instruction appended to the translation prompt for the language.

    Only Tanglish needs one. It is not a language with its own script, so the
    model must be told explicitly what output it is producing: Tamil words and
    grammar written with English letters. Without this, models drift into either
    pure English or Tamil script, and both look like a broken picker to the user.
    """
    if (code or "").strip().lower() == "ta-ta":
        return (
            "TANGlish (Tamil written in English/Roman letters): translate the meaning "
            "into Tamil, but write every Tamil word using English letters "
            "(transliteration), the way a Tamil speaker types on a phone. Never use the "
            "Tamil script itself, and never leave the answer in English. Keep technical "
            "terms, names, numbers and citation markers exactly as written in the "
            "source. Example: \"Plants sunlight-a use panni food-a prepare panra "
            "process dhan photosynthesis.\" [1]"
        )
    return ""


def as_list() -> list[dict]:
    """Serialisable list for the frontend picker."""
    return [
        {
            "code": lang.code,
            "name": lang.name,
            "native_name": lang.native_name,
            "flag": lang.flag,
            "speech_code": lang.speech_code,
            "speech_candidates": speech_candidates(lang.code),
            "rtl": lang.rtl,
            "default": lang.default,
        }
        for lang in LANGUAGES
    ]
