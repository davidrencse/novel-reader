"""
attribute_ai.py — AI-powered speaker attribution via the Claude API.

Design: the quote marks already tell us mechanically which spans are dialogue vs
narration (that split is reliable), so we DON'T spend tokens re-emitting text.
Instead we run the rules splitter to get the segments, then send the whole
chapter to Claude for CONTEXT and ask it only to label WHO speaks each dialogue
line. Output is a compact JSON array of {line, speaker} — text is never altered.

Results are cached per chapter (hash of the text) under cache_dir/ai/, so each
chapter is parsed by the model only once. Requires ANTHROPIC_API_KEY (or an
`ant auth login` profile). Any failure raises — the caller falls back to rules.
"""
from __future__ import annotations

import re
import json
import hashlib
from pathlib import Path

from .attribute import attribute, Segment

_SYSTEM = (
    "You label who is speaking each line of dialogue in an English translation of "
    "the Re:Zero web novel. You are given the chapter as numbered lines. Lines "
    "marked 'D' are spoken dialogue; lines marked 'n' are narration, given only as "
    "context — never label them. For every 'D' line, decide which character says it, "
    "using the surrounding narration, dialogue tags, and the back-and-forth flow of "
    "the conversation. Prefer names from the provided cast list (use the exact cast "
    "spelling). If a speaker is clearly a character not in the cast, use a short "
    "proper name for them. If it is genuinely impossible to tell, use \"Unknown\". "
    "Return ONLY a JSON array, one object per dialogue line, like "
    '[{"line": 3, "speaker": "Subaru"}, {"line": 5, "speaker": "Emilia"}]. '
    "No prose, no code fences."
)


def _build_name_map(config: dict) -> dict[str, str]:
    m: dict[str, str] = {}
    for key, meta in (config.get("characters") or {}).items():
        m[key.lower()] = key
        for alias in (meta or {}).get("aliases", []) or []:
            m[alias.lower()] = key
    return m


def _canonical(name: str, name_map: dict[str, str], unknown: str) -> str:
    if not name or not name.strip():
        return unknown
    n = name.strip()
    low = n.lower()
    if low in name_map:
        return name_map[low]
    if low in ("unknown", "?", "???", "someone", "narration", "narrator"):
        return unknown
    # A character the model recognized but that isn't in config — keep the name
    # (TTS gives it a distinct hashed voice, and it shows up in the cast panel).
    return n[:40]


def _extract_json(text: str) -> list:
    text = text.strip()
    # Strip accidental code fences.
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\[.*\]", text, re.S)  # first [...] block
    if m:
        return json.loads(m.group(0))
    raise ValueError("model did not return a JSON array")


def _ask_llm(segments: list[Segment], config: dict, ai_cfg: dict) -> dict[str, str]:
    import anthropic

    model = ai_cfg.get("model", "claude-opus-5")
    effort = ai_cfg.get("effort", "low")
    cast = list((config.get("characters") or {}).keys())

    lines = []
    for i, s in enumerate(segments):
        tag = "D" if s.kind == "dialogue" else "n"
        t = s.text if len(s.text) <= 400 else s.text[:400] + "…"
        lines.append(f"{i}|{tag}|{t}")
    user = (
        "Cast (preferred spellings): " + ", ".join(cast) + "\n\n"
        "Lines:\n" + "\n".join(lines) + "\n\n"
        "Return the JSON array of {line, speaker} for every 'D' line."
    )

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY / ant auth profile
    msg = client.messages.create(
        model=model,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": effort},
        system=_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    data = _extract_json(text)
    return {str(item["line"]): item["speaker"]
            for item in data if isinstance(item, dict) and "line" in item and "speaker" in item}


def attribute_ai(paragraphs: list[str], config: dict) -> list[Segment]:
    """Attribute speakers with Claude; raises on failure so callers can fall back."""
    segments = attribute(paragraphs, config)               # rules split + baseline
    if not any(s.kind == "dialogue" for s in segments):
        return segments

    ai_cfg = ((config.get("attribution") or {}).get("ai") or {})
    use_cache = ai_cfg.get("cache", True)
    cache_dir = Path(config["paths"]["cache_dir"]) / "ai"
    key = hashlib.sha1(("\n".join(paragraphs)).encode("utf-8")).hexdigest()[:20]
    cache_file = cache_dir / f"{key}.json"

    labels: dict[str, str] | None = None
    if use_cache and cache_file.exists():
        try:
            labels = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            labels = None
    if labels is None:
        labels = _ask_llm(segments, config, ai_cfg)
        if use_cache:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(labels), encoding="utf-8")

    name_map = _build_name_map(config)
    unknown = config.get("unknown_speaker", "Unknown")
    for k, name in labels.items():
        try:
            i = int(k)
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(segments) and segments[i].kind == "dialogue":
            segments[i].speaker = _canonical(name, name_map, unknown)
    return segments
