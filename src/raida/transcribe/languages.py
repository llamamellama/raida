"""Language sets and normalization for transcription routing."""

from __future__ import annotations

# Languages covered by nvidia/parakeet-tdt-0.6b-v3 (25 European languages, automatic ID).
PARAKEET_V3_LANGUAGES: frozenset[str] = frozenset(
    {
        "bg",
        "hr",
        "cs",
        "da",
        "nl",
        "en",
        "et",
        "fi",
        "fr",
        "de",
        "el",
        "hu",
        "it",
        "lv",
        "lt",
        "mt",
        "pl",
        "pt",
        "ro",
        "sk",
        "sl",
        "es",
        "sv",
        "ru",
        "uk",
    }
)

# BCP-47 locales accepted by Apple's SpeechTranscriber for a base language code.
APPLE_LOCALES: dict[str, str] = {
    "en": "en-US",
    "de": "de-DE",
    "fr": "fr-FR",
    "es": "es-ES",
    "it": "it-IT",
    "pt": "pt-BR",
    "ja": "ja-JP",
    "ko": "ko-KR",
    "zh": "zh-CN",
    "nl": "nl-NL",
    "sv": "sv-SE",
    "da": "da-DK",
    "fi": "fi-FI",
    "nb": "nb-NO",
    "no": "nb-NO",
    "pl": "pl-PL",
    "ru": "ru-RU",
    "tr": "tr-TR",
    "ar": "ar-SA",
    "he": "he-IL",
    "hi": "hi-IN",
    "id": "id-ID",
    "ms": "ms-MY",
    "th": "th-TH",
    "uk": "uk-UA",
    "vi": "vi-VN",
    "cs": "cs-CZ",
    "el": "el-GR",
    "hu": "hu-HU",
    "ro": "ro-RO",
    "sk": "sk-SK",
    "ca": "ca-ES",
    "hr": "hr-HR",
}


def base_language(code: str | None) -> str | None:
    """'en-US' -> 'en'; 'auto' or empty -> None."""
    if not code or code.lower() == "auto":
        return None
    return code.replace("_", "-").split("-")[0].lower()
