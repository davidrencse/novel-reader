"""
analyze.py — derive text analysis from attributed segments.

Produces the numbers the UI's analysis panel shows: word/char counts,
dialogue-vs-narration split, estimated spoken length, and a per-character
breakdown (segments, words, first appearance) for the colored speaker chips.
"""
from __future__ import annotations

import re
from .attribute import Segment

WORD = re.compile(r"[A-Za-z0-9']+")
WPM_BASE = 155  # rough spoken words-per-minute for the narrator voice at speed 1.0


def _words(text: str) -> int:
    return len(WORD.findall(text))


def analyze(segments: list[Segment], narrator: str = "Narrator", speed: float = 1.0) -> dict:
    total_words = 0
    dialogue_words = 0
    per: dict[str, dict] = {}

    for idx, s in enumerate(segments):
        w = _words(s.text)
        total_words += w
        if s.kind == "dialogue":
            dialogue_words += w
        p = per.setdefault(s.speaker, {
            "speaker": s.speaker, "segments": 0, "words": 0, "first": idx,
        })
        p["segments"] += 1
        p["words"] += w

    speakers = sorted(per.values(), key=lambda d: -d["words"])
    est_minutes = total_words / max(WPM_BASE * speed, 1)
    cast = [s["speaker"] for s in speakers if s["speaker"] != narrator]

    return {
        "total_words": total_words,
        "total_segments": len(segments),
        "dialogue_segments": sum(1 for s in segments if s.kind == "dialogue"),
        "narration_segments": sum(1 for s in segments if s.kind == "narration"),
        "dialogue_pct": round(100 * dialogue_words / total_words) if total_words else 0,
        "est_minutes": round(est_minutes, 1),
        "est_readtime_min": round(total_words / 240, 1),  # silent reading ~240 wpm
        "cast_size": len(cast),
        "cast": cast,
        "speakers": speakers,
    }
