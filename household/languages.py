"""Languages the front desk can serve, and how far each one is served by speech (verified against AWS docs on 2026-09-05).

Sources: Amazon Polly neural voice table (docs.aws.amazon.com/polly/latest/dg/neural-voices.html); Nova Sonic language
announcements (aws.amazon.com/about-aws/whats-new/2025/06, 2025/07, 2025/12). Browser support is the Web Speech API in
Chromium browsers; TTS voice availability there depends on the operating system.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str
    name_en: str
    name_native: str
    transcribe: bool
    polly_voice: str | None
    nova_sonic: bool
    browser: bool

    @property
    def voice_ready(self) -> bool:
        return self.polly_voice is not None or self.browser

    @property
    def mode(self) -> str:
        if self.polly_voice and self.nova_sonic:
            return "voice (full)"
        if self.polly_voice or self.browser:
            return "voice (partial)"
        return "text-only"


LANGUAGES: dict[str, Language] = {
    "en": Language("en", "English", "English", True, "en-US Joanna", True, True),
    "es": Language("es", "Spanish", "Español", True, "es-US Lupe / es-MX Mia", True, True),
    "pt": Language("pt", "Portuguese", "Português", True, "pt-BR Camila", True, True),
    "fr": Language("fr", "French", "Français", True, "fr-CA Gabrielle", True, True),
    "ar": Language("ar", "Arabic", "العربية", True, "ar-AE Hala", False, False),
    "ti": Language("ti", "Tigrinya", "ትግርኛ", False, None, False, False),
    "tl": Language("tl", "Tagalog", "Tagalog", True, None, False, False),
    "fa": Language("fa", "Farsi", "فارسی", True, None, False, False),
}

DEMO_LANGUAGE = "es"


def get_language(code: str) -> Language:
    try:
        return LANGUAGES[code]
    except KeyError as exc:
        raise ValueError(f"Unsupported language {code!r}; choose one of {sorted(LANGUAGES)}") from exc
