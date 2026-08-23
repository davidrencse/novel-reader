"""
textutil.py — clean scraped/pasted text so only speakable English reaches TTS.

Two jobs:
  * clean_paragraphs() — run at ingestion (scrape/paste). Strips decorative
    symbols and DROPS scene-break divider lines (the reference-mark rows,
    * * *, dash rules, diamond rows) so they never become a spoken segment.
  * sanitize_for_tts() — final guard right before synthesis: fold smart
    punctuation to plain ASCII, drop the characters XTTS mispronounces, and
    return "" when nothing speakable remains so the caller can emit silence
    instead of feeding the model junk (which it blurts or errors on).
"""
from __future__ import annotations

import re
import unicodedata

# Characters allowed to survive cleaning at ingestion. Letters/digits/space plus
# ordinary sentence punctuation AND the quote styles the attributor relies on.
_ALLOWED = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    " \t.,!?;:'\"()[]{}-/&%$…—–“”‘’«»"
)

# Replacements applied just before TTS. XTTS mispronounces or blurts artifacts on
# smart punctuation, dashes, ellipses, and stray double quotes — so fold them to
# plain ASCII: dashes/ellipses become a comma pause; double quotes are dropped
# (they don't change pronunciation); smart apostrophes become a plain '.
_TTS_MAP = {
    "“": "", "”": "", "„": "", "«": "", "»": "", '"': "",
    "‘": "'", "’": "'", "‚": "'", "`": "'",
    "—": ", ", "–": ", ", "―": ", ", "‒": ", ",
    "…": ", ",
    "&": " and ", "/": " ", "\\": " ", "*": " ", "•": " ", "·": " ",
    "_": " ", "|": " ", "~": " ", "^": " ", "=": " ", "+": " ", "@": " at ",
    "#": " ", " ": " ", "　": " ",
}

# Characters XTTS handles cleanly. Everything else is stripped before synth.
_TTS_KEEP = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,!?;:'()"
)

_SPEAKABLE = re.compile(r"[A-Za-z0-9]")


def clean_paragraph(text: str) -> str:
    """Strip disallowed symbols and collapse whitespace. May return ''."""
    text = "".join(ch if ch in _ALLOWED else " " for ch in text)
    return re.sub(r"\s+", " ", text).strip()


def is_speakable(text: str) -> bool:
    """True if the text has at least one letter/digit to actually voice."""
    return bool(_SPEAKABLE.search(text))


def clean_paragraphs(paragraphs: list[str]) -> list[str]:
    """Clean each paragraph and drop divider / symbol-only lines."""
    out: list[str] = []
    for p in paragraphs:
        c = clean_paragraph(p)
        if c and is_speakable(c):
            out.append(c)
    return out


def sanitize_for_tts(text: str) -> str:
    """Normalize text into clean ASCII XTTS reads reliably. '' = nothing to say."""
    # Decompose accents (café -> cafe) so the KEEP filter drops the marks, not
    # the base letters, leaving clean ASCII.
    text = unicodedata.normalize("NFKD", text)
    for k, v in _TTS_MAP.items():
        text = text.replace(k, v)
    # Keep only the clean speakable set.
    text = "".join(ch if ch in _TTS_KEEP else " " for ch in text)
    # Collapse whitespace.
    text = re.sub(r"\s+", " ", text).strip()
    # Collapse runs of terminal/again punctuation: "?!?!" -> "?", "!!!" -> "!",
    # ".." -> ".", and stray " , ," -> ",".
    text = re.sub(r"\s*,\s*(?:,\s*)+", ", ", text)
    text = re.sub(r"[.!?]{2,}", lambda m: m.group(0)[0], text)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    # Trim leading punctuation/pauses so a line never opens on a comma or dash.
    text = re.sub(r"^[\s,.;:!?'()-]+", "", text).strip()
    if not is_speakable(text):
        return ""
    # Ensure a terminal mark so XTTS doesn't trail into noise.
    if text[-1] not in ".!?":
        text += "."
    return text
