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


# Region-specific locales the Apple engine distinguishes (different scripts or vocabularies).
# Keyed by the normalized language code as the UI and API pass it.
APPLE_REGIONAL_LOCALES: dict[str, str] = {
    "zh-tw": "zh-TW",  # Traditional Chinese, Taiwan
    "zh-hk": "zh-HK",  # Cantonese, Hong Kong
    "zh-cn": "zh-CN",
    "pt-pt": "pt-PT",
    "en-gb": "en-GB",
    "es-mx": "es-MX",
    "fr-ca": "fr-CA",
}


def apple_locale(code: str | None) -> str | None:
    """Apple locale for a code: 'zh-TW' -> 'zh-TW', 'zh' -> 'zh-CN', None when unsupported."""
    if not code or code.lower() == "auto":
        return None
    normalized = code.replace("_", "-").lower()
    if normalized in APPLE_REGIONAL_LOCALES:
        return APPLE_REGIONAL_LOCALES[normalized]
    return APPLE_LOCALES.get(normalized.split("-")[0])


def base_language(code: str | None) -> str | None:
    """'en-US' -> 'en'; 'auto' or empty -> None."""
    if not code or code.lower() == "auto":
        return None
    return code.replace("_", "-").split("-")[0].lower()


# The languages offered in the UI (web/util.js LANGUAGES, without "auto"), by the name used when
# the model is told which language to write in. A test keeps the two lists in step.
WRITTEN_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
    "sv": "Swedish",
    "da": "Danish",
    "fi": "Finnish",
    "nb": "Norwegian",
    "pl": "Polish",
    "cs": "Czech",
    "ru": "Russian",
    "uk": "Ukrainian",
    "el": "Greek",
    "hu": "Hungarian",
    "ro": "Romanian",
    "tr": "Turkish",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Simplified Chinese",
    "zh-TW": "Traditional Chinese as written in Taiwan",
    "zh-HK": "Traditional Chinese as written in Hong Kong",
    "ar": "Arabic",
    "hi": "Hindi",
    "he": "Hebrew",
    "id": "Indonesian",
    "vi": "Vietnamese",
    "th": "Thai",
}


def written_language_name(code: str | None) -> str | None:
    """Name of a UI language code for a writing instruction ("zh-tw" -> Traditional Chinese
    as written in Taiwan); None for "auto", empty or unknown codes."""
    if not code:
        return None
    wanted = code.replace("_", "-").lower()
    return next((n for c, n in WRITTEN_LANGUAGE_NAMES.items() if c.lower() == wanted), None)
