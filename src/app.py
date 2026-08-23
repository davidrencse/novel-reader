"""
app.py — local web server for the Re:Zero Reader UI.

Serves the dark reading UI and exposes a tiny JSON API:
  POST /api/load        {url|text}  -> title, segments, analysis, images, links, nav
  GET  /api/audio/<i>              -> WAV for segment i (synthesized once, then cached)
  POST /api/prewarm                -> load the voice model ahead of time

Run:  python -m src.app         (opens http://127.0.0.1:5000 in your browser)
"""
from __future__ import annotations

import os
import threading
import webbrowser
from pathlib import Path

from flask import Flask, request, jsonify, send_file, send_from_directory
import yaml

from . import scrape
from .attribute import attribute, Segment
from .analyze import analyze
from .tts import VoiceSynth

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

app = Flask(__name__, static_folder=str(WEB), static_url_path="")

_config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
# Anchor all paths to the project root as absolute paths, so cache writes and
# Flask's send_file (which resolves relative paths against src/) agree.
for _k, _v in _config.get("paths", {}).items():
    _p = Path(_v)
    _config["paths"][_k] = str(_p if _p.is_absolute() else (ROOT / _p))

# Convenience: read API keys from local files (gitignored) if not already in the
# environment, so enabling an AI parser is one paste.
_KEYFILES = {
    "ANTHROPIC_API_KEY": ROOT / "apikey.txt",
    "GROQ_API_KEY": ROOT / "groqkey.txt",
}
for _env, _kf in _KEYFILES.items():
    if not os.environ.get(_env) and _kf.exists():
        _k0 = _kf.read_text(encoding="utf-8").strip()
        if _k0:
            os.environ[_env] = _k0

_synth: VoiceSynth | None = None
_synth_lock = threading.Lock()      # XTTS inference is not thread-safe -> serialize
_synth_create_lock = threading.Lock()

# In-memory state for the one chapter currently loaded (single local user).
BOOK: dict = {"title": None, "segments": [], "ready": 0}
_pregen_stop = threading.Event()


def get_synth() -> VoiceSynth:
    global _synth
    with _synth_create_lock:
        if _synth is None:
            _synth = VoiceSynth(_config)
    return _synth


def start_background_warm() -> None:
    """Load + warm the voice model in the background so it's ready before the
    user presses play (hides the multi-second cold start behind navigation)."""
    def work():
        try:
            get_synth().warm_up()
        except Exception as e:
            print(f"[warm] model warm-up failed: {e}")
    threading.Thread(target=work, daemon=True).start()


def _start_pregen(segments: list[Segment]) -> None:
    """Synthesize the whole chapter in the background so playback is instant.
    Cancels any prior run; yields the GPU to on-demand audio via the synth lock."""
    global _pregen_stop
    _pregen_stop.set()                       # stop a previous chapter's pregen
    stop = threading.Event()
    _pregen_stop = stop
    BOOK["ready"] = 0
    total = len(segments)

    def work():
        synth = get_synth()
        for i, s in enumerate(segments):
            if stop.is_set():
                return
            try:
                with _synth_lock:
                    synth.say(s.text, s.speaker)
            except Exception:
                pass
            BOOK["ready"] = i + 1
    threading.Thread(target=work, daemon=True).start()


def _segments_json(segments: list[Segment]) -> list[dict]:
    return [{"i": i, "speaker": s.speaker, "kind": s.kind, "text": s.text}
            for i, s in enumerate(segments)]


def _run_attribution(paragraphs: list[str], engine: str):
    """Return (segments, engine_used, note). Falls back to rules on failure."""
    if engine == "ai":
        try:
            from .attribute_ai import attribute_ai
            return attribute_ai(paragraphs, _config), "ai", None
        except Exception as e:
            return (attribute(paragraphs, _config), "rules",
                    f"Cloud AI parser unavailable ({type(e).__name__}); used rules instead. "
                    f"Set ANTHROPIC_API_KEY to enable it.")
    if engine == "local":
        try:
            from .attribute_local import attribute_local
            return attribute_local(paragraphs, _config), "local", None
        except Exception as e:
            return (attribute(paragraphs, _config), "rules",
                    f"Local AI unavailable ({type(e).__name__}); used rules instead. "
                    f"Start your local model server (LM Studio/Ollama) with a chat model loaded.")
    if engine == "groq":
        try:
            from .attribute_local import attribute_groq
            return attribute_groq(paragraphs, _config), "groq", None
        except Exception as e:
            return (attribute(paragraphs, _config), "rules",
                    f"Groq unavailable ({type(e).__name__}); used rules instead. "
                    f"Add a free GROQ_API_KEY to enable it.")
    return attribute(paragraphs, _config), "rules", None


@app.get("/api/local_status")
def api_local_status():
    """Report whether a local LLM server has a usable chat model loaded."""
    from .attribute_local import discover
    base = ((_config.get("attribution") or {}).get("local") or {}).get(
        "base_url", "http://localhost:1234/v1")
    models = discover(base)
    return jsonify({"available": bool(models), "models": models, "base_url": base})


@app.get("/")
def index():
    return send_from_directory(str(WEB), "index.html")


@app.get("/api/config")
def api_config():
    import os
    chars = _config.get("characters", {})
    return jsonify({
        "narrator": _config.get("narrator", "Narrator"),
        "characters": list(chars.keys()),
        "speed": _config.get("engine", {}).get("speed", 1.0),
        "engine": (_config.get("attribution") or {}).get("engine", "rules"),
        "ai_key_present": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "groq_key_present": bool(os.environ.get("GROQ_API_KEY")),
    })


@app.post("/api/load")
def api_load():
    data = request.get_json(force=True) or {}
    url = (data.get("url") or "").strip()
    text = (data.get("text") or "").strip()

    extras = {"images": [], "links": [], "nav": None, "categories": [],
              "comments": [], "source": None}
    if url:
        if not url.lower().startswith(("http://", "https://")):
            return jsonify({"error": "URL must start with http:// or https://"}), 400
        try:
            html = scrape.fetch_html(url)
            full = scrape.parse_full(html, base_url=url)
        except Exception as e:  # network error, 404, layout change, etc.
            return jsonify({"error": f"Could not fetch or parse that page: {e}"}), 502
        title, paragraphs = full["title"], full["paragraphs"]
        extras.update({k: full[k] for k in ("images", "links", "nav", "categories")})
        extras["source"] = url
        extras["comments"] = scrape.fetch_comments(url, full.get("post_id"))  # never raises
        scrape.save(title, paragraphs, Path(_config["paths"]["text_dir"]))
    elif text:
        lines = [ln.strip() for ln in text.splitlines()]
        nonempty = [ln for ln in lines if ln]
        if not nonempty:
            return jsonify({"error": "No text provided."}), 400
        from .textutil import clean_paragraphs
        title = nonempty[0]
        paragraphs = clean_paragraphs(nonempty[1:] or nonempty)
        if not paragraphs:
            return jsonify({"error": "No readable text after cleaning."}), 400
    else:
        return jsonify({"error": "Provide a url or text."}), 400

    engine = (data.get("engine") or
              (_config.get("attribution") or {}).get("engine", "rules"))
    segments, engine_used, note = _run_attribution(paragraphs, engine)
    narrator = _config.get("narrator", "Narrator")
    speed = _config.get("engine", {}).get("speed", 1.0)
    analysis = analyze(segments, narrator=narrator, speed=speed)

    BOOK["title"] = title
    BOOK["segments"] = segments
    _start_pregen(segments)   # begin voicing the chapter in the background

    return jsonify({
        "title": title,
        "segments": _segments_json(segments),
        "analysis": analysis,
        "narrator": narrator,
        "engine": engine_used,
        "note": note,
        **extras,
    })


@app.post("/api/prewarm")
def api_prewarm():
    start_background_warm()
    return jsonify({"ok": True})


@app.post("/api/set_key")
def api_set_key():
    """Store the user's own API key locally (gitignored). Routes by key prefix:
    Groq keys start 'gsk_'; Anthropic keys start 'sk-ant'."""
    data = request.get_json(force=True) or {}
    key = (data.get("key") or "").strip()
    if key.startswith("gsk_"):
        env, provider = "GROQ_API_KEY", "groq"
    elif key.startswith("sk-"):
        env, provider = "ANTHROPIC_API_KEY", "anthropic"
    else:
        return jsonify({"error": "Unrecognized key. Groq keys start 'gsk_', Anthropic 'sk-ant'."}), 400
    os.environ[env] = key
    try:
        _KEYFILES[env].write_text(key, encoding="utf-8")
    except OSError:
        pass
    return jsonify({"ok": True, "provider": provider})


@app.get("/api/status")
def api_status():
    total = len(BOOK.get("segments", []))
    return jsonify({
        "model_loaded": _synth is not None and _synth.loaded,
        "ready": BOOK.get("ready", 0),
        "total": total,
    })


@app.get("/api/audio/<int:i>")
def api_audio(i: int):
    segments: list[Segment] = BOOK["segments"]
    if not segments or i < 0 or i >= len(segments):
        return jsonify({"error": "segment out of range"}), 404
    seg = segments[i]
    synth = get_synth()
    with _synth_lock:
        wav_path = synth.say(seg.text, seg.speaker)
    return send_file(str(wav_path), mimetype="audio/wav", conditional=True)


def _voice_ref_path(name: str) -> Path | None:
    """Absolute path where `name`'s clone clip lives, or None if not a known char."""
    chars = _config.get("characters", {})
    if name not in chars:
        return None
    ref = (chars[name] or {}).get("ref") or f"{name.lower()}.wav"
    return Path(_config["paths"]["voices_dir"]) / Path(ref).name  # basename only (no traversal)


@app.get("/api/voices")
def api_voices():
    chars = _config.get("characters", {})
    out = []
    for name, meta in chars.items():
        p = _voice_ref_path(name)
        out.append({
            "name": name,
            "cloned": bool(p and p.exists()),
            "builtin": (meta or {}).get("builtin"),
        })
    return jsonify({"voices": out})


@app.post("/api/voices/<name>")
def api_voice_upload(name: str):
    import subprocess, tempfile, os
    dest = _voice_ref_path(name)
    if dest is None:
        return jsonify({"error": "unknown character"}), 404
    if "clip" not in request.files:
        return jsonify({"error": "no file uploaded (field 'clip')"}), 400
    f = request.files["clip"]
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Save upload to a temp file, then transcode to mono 24k 16-bit wav via ffmpeg.
    suffix = os.path.splitext(f.filename or "")[1][:8] or ".bin"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        f.save(tmp.name)
        tmp.close()
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp.name, "-ac", "1", "-ar", "24000",
             "-sample_fmt", "s16", str(dest)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0 or not dest.exists():
            return jsonify({"error": "could not process audio: " + proc.stderr[-300:]}), 400
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    if _synth is not None:
        _synth.invalidate_voice(name)
    return jsonify({"ok": True, "name": name, "cloned": True})


@app.delete("/api/voices/<name>")
def api_voice_delete(name: str):
    dest = _voice_ref_path(name)
    if dest is None:
        return jsonify({"error": "unknown character"}), 404
    if dest.exists():
        dest.unlink()
    if _synth is not None:
        _synth.invalidate_voice(name)
    return jsonify({"ok": True, "name": name, "cloned": False})


@app.get("/api/voices/<name>/sample")
def api_voice_sample(name: str):
    if name not in _config.get("characters", {}):
        return jsonify({"error": "unknown character"}), 404
    line = f"Hello, I am {name}. This is how my voice sounds."
    synth = get_synth()
    with _synth_lock:
        wav_path = synth.say(line, name)
    return send_file(str(wav_path), mimetype="audio/wav", conditional=True)


def open_browser(host: str, port: int):
    webbrowser.open(f"http://{host}:{port}/")


def main():
    host, port = "127.0.0.1", 5000
    threading.Timer(1.2, open_browser, args=(host, port)).start()
    start_background_warm()   # load the voice model while the page opens
    print(f"Re:Zero Reader running at http://{host}:{port}/  (Ctrl+C to stop)")
    # threaded so audio prefetch + playback requests don't block each other.
    app.run(host=host, port=port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
