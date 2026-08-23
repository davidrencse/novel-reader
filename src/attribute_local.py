"""
attribute_local.py — FREE speaker attribution via a local LLM.

Talks to any OpenAI-compatible local server — LM Studio (port 1234) or Ollama
(port 11434) — so it runs entirely on your own GPU with no API key and no cost.

Because local models have small context windows, the chapter is processed in
overlapping chunks (a few lines of prior context carry over for continuity), and
only the lines inside each chunk's own range are labeled. Results are cached per
chapter, same as the cloud engine. Any failure raises so the caller falls back.
"""
from __future__ import annotations

import json
import hashlib
import urllib.request
import urllib.error
from pathlib import Path

from .attribute import attribute, Segment
from .attribute_ai import _build_name_map, _canonical

CHUNK = 40        # segments labeled per model call
OVERLAP = 6       # extra prior segments included as context (not relabeled)

_SYSTEM = (
    "You identify who speaks each line of dialogue in an English translation of the "
    "Re:Zero web novel. Lines are numbered and tagged 'D' (spoken dialogue) or 'n' "
    "(narration, context only — never label narration). For every 'D' line in the "
    "requested range, decide which character says it using dialogue tags, the "
    "surrounding narration, and the back-and-forth of the conversation. Prefer the "
    "provided cast spellings; if a speaker is a character not listed, use a short "
    "proper name; if truly unknowable, use \"Unknown\". Respond with a JSON object "
    'of the form {"labels": [{"line": 3, "speaker": "Subaru"}, ...]} and nothing else.'
)


def _normalize(url: str) -> str:
    url = url.rstrip("/")
    return url if url.endswith("/v1") else url + "/v1"


def discover(base_url: str, timeout: float = 3.0) -> list[str]:
    """List chat-capable model ids on a local server (excludes embedding models)."""
    try:
        req = urllib.request.Request(_normalize(base_url) + "/models")
        data = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except Exception:
        return []
    out = []
    for m in data.get("data", []):
        mid = m.get("id", "")
        if mid and "embed" not in mid.lower():
            out.append(mid)
    return out


def _chat(base_url: str, model: str, system: str, user: str,
          api_key: str | None, timeout: float) -> str:
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Content-Type": "application/json",
        # A real UA avoids Cloudflare bot blocks (error 1010) on some endpoints.
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) rezero-reader/1.0",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = json.dumps(body).encode()
    url = _normalize(base_url) + "/chat/completions"
    for attempt in range(4):  # retry on rate-limit (free cloud tiers throttle)
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            resp = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
            return resp["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                import time
                time.sleep(float(e.headers.get("retry-after", 3)) + 1)
                continue
            raise
    raise RuntimeError("chat request failed after retries")


def _parse_labels(content: str) -> dict[str, str]:
    content = content.strip()
    try:
        data = json.loads(content)
    except Exception:
        import re
        m = re.search(r"\{.*\}", content, re.S)
        data = json.loads(m.group(0)) if m else {}
    items = data.get("labels") if isinstance(data, dict) else data
    if isinstance(data, dict) and items is None:
        # a bare {line: speaker} object
        return {str(k): v for k, v in data.items()}
    out = {}
    for it in (items or []):
        if isinstance(it, dict) and "line" in it and "speaker" in it:
            out[str(it["line"])] = it["speaker"]
    return out


def _ask_local(segments: list[Segment], config: dict, cfg: dict) -> dict[str, str]:
    base_url = cfg.get("base_url", "http://localhost:1234/v1")
    api_key = cfg.get("api_key")
    timeout = float(cfg.get("timeout", 180))
    model = cfg.get("model") or ""
    if not model:
        models = discover(base_url)
        if not models:
            raise RuntimeError("no chat model available on the local server")
        model = models[0]

    chunk = int(cfg.get("chunk", CHUNK))
    overlap = int(cfg.get("overlap", OVERLAP))
    cast = ", ".join((config.get("characters") or {}).keys())
    labels: dict[str, str] = {}
    n = len(segments)
    for start in range(0, n, chunk):
        end = min(n, start + chunk)
        ctx_start = max(0, start - overlap)
        lines = []
        for gi in range(ctx_start, end):
            s = segments[gi]
            tag = "D" if s.kind == "dialogue" else "n"
            t = s.text if len(s.text) <= 300 else s.text[:300] + "…"
            lines.append(f"{gi}|{tag}|{t}")
        user = (
            f"Cast (preferred spellings): {cast}\n\n"
            f"Lines:\n" + "\n".join(lines) + "\n\n"
            f"Label ONLY the 'D' lines numbered {start} to {end - 1}. "
            f"Lines below {start} are earlier context — do not label them."
        )
        content = _chat(base_url, model, _SYSTEM, user, api_key, timeout)
        for k, v in _parse_labels(content).items():
            try:
                if start <= int(k) < end:
                    labels[k] = v
            except (TypeError, ValueError):
                continue
    return labels


def _attribute_via(paragraphs: list[str], config: dict, cfg: dict, cache_ns: str) -> list[Segment]:
    """Core: attribute speakers via an OpenAI-compatible server described by cfg."""
    segments = attribute(paragraphs, config)
    if not any(s.kind == "dialogue" for s in segments):
        return segments

    use_cache = cfg.get("cache", True)
    cache_dir = Path(config["paths"]["cache_dir"]) / cache_ns
    key = hashlib.sha1(("\n".join(paragraphs)).encode("utf-8")).hexdigest()[:20]
    cache_file = cache_dir / f"{key}.json"

    labels: dict[str, str] | None = None
    if use_cache and cache_file.exists():
        try:
            labels = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            labels = None
    if labels is None:
        labels = _ask_local(segments, config, cfg)
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


def attribute_local(paragraphs: list[str], config: dict) -> list[Segment]:
    """FREE local LLM (LM Studio / Ollama). Raises on failure for graceful fallback."""
    cfg = ((config.get("attribution") or {}).get("local") or {})
    return _attribute_via(paragraphs, config, cfg, "local")


def attribute_groq(paragraphs: list[str], config: dict) -> list[Segment]:
    """FREE Groq cloud (OpenAI-compatible). Needs GROQ_API_KEY. Raises on failure."""
    import os
    cfg = dict((config.get("attribution") or {}).get("groq") or {})
    cfg.setdefault("base_url", "https://api.groq.com/openai/v1")
    cfg.setdefault("model", "llama-3.3-70b-versatile")
    cfg.setdefault("chunk", 120)   # 32k context -> fewer, larger calls
    cfg["api_key"] = os.environ.get("GROQ_API_KEY")
    if not cfg["api_key"]:
        raise RuntimeError("GROQ_API_KEY not set")
    return _attribute_via(paragraphs, config, cfg, "groq")
