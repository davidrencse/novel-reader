"""
tts.py — XTTS v2 voice synthesis with per-character voices + caching.

Each character resolves to ONE of:
  * a cloned voice   -> voices/<ref>.wav exists  (speaker_wav)
  * a built-in voice -> the configured `builtin` name, validated against the
                        model's speaker list; falls back to a stable hashed
                        pick so every character still sounds distinct.

Synthesized segments are cached under paths.cache_dir keyed by
(voice, text, speed), so re-runs and edits only regenerate what changed.
"""
from __future__ import annotations

import os
import hashlib
import threading
from pathlib import Path

import numpy as np
import soundfile as sf

# Agree to the Coqui model license non-interactively (personal, non-commercial use).
os.environ.setdefault("COQUI_TOS_AGREED", "1")

XTTS_SR = 24000  # XTTS v2 output sample rate


class VoiceSynth:
    def __init__(self, config: dict):
        eng = config["engine"]
        self.model_name = eng["model"]
        self.language = eng.get("language", "en")
        self.use_gpu = bool(eng.get("use_gpu", True))
        self.speed = float(eng.get("speed", 1.0))

        paths = config["paths"]
        self.voices_dir = Path(paths["voices_dir"])
        self.cache_dir = Path(paths["cache_dir"])
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.characters = config.get("characters", {})
        self._tts = None
        self._builtin_speakers: list[str] = []
        self._voice_cache: dict[str, dict] = {}
        self._load_lock = threading.Lock()
        self._warmed = False

    @property
    def loaded(self) -> bool:
        return self._tts is not None

    def warm_up(self) -> None:
        """Load the model AND run one throwaway inference so the first real
        segment doesn't pay CUDA kernel-compile latency. Safe to call anytime."""
        self._ensure_model()
        if self._warmed:
            return
        try:
            spk = self._builtin_speakers[0] if self._builtin_speakers else None
            with self._load_lock:
                if not self._warmed:
                    self._tts.tts(text="Ready.", language=self.language,
                                  speaker=spk, split_sentences=False)
                    self._warmed = True
        except Exception:
            self._warmed = True  # don't retry forever on a warm-up hiccup

    # ---- lazy model load --------------------------------------------------
    def _ensure_model(self):
        if self._tts is not None:
            return
        with self._load_lock:
            if self._tts is not None:          # re-check after acquiring the lock
                return
            import torch
            from TTS.api import TTS

            device = "cuda" if (self.use_gpu and torch.cuda.is_available()) else "cpu"
            print(f"[tts] loading {self.model_name} on {device} (first run downloads ~1.8GB)…")
            tts = TTS(self.model_name).to(device)
            spk = getattr(tts, "speakers", None) or []
            self._builtin_speakers = list(spk)
            self._tts = tts                    # publish only after fully initialized
            print(f"[tts] ready — {len(self._builtin_speakers)} built-in voices available.")

    @property
    def builtin_speakers(self) -> list[str]:
        self._ensure_model()
        return self._builtin_speakers

    # ---- voice resolution -------------------------------------------------
    def resolve_voice(self, speaker: str) -> dict:
        """Return {'kind','id','ref'|'builtin'} describing how to voice `speaker`."""
        if speaker in self._voice_cache:
            return self._voice_cache[speaker]
        self._ensure_model()

        meta = self.characters.get(speaker, {}) or {}
        ref_name = meta.get("ref")
        ref_path = self.voices_dir / ref_name if ref_name else None

        if ref_path and ref_path.exists():
            # Include mtime so re-uploading a new clip busts the per-segment cache.
            stamp = int(ref_path.stat().st_mtime)
            voice = {"kind": "clone", "id": f"clone:{ref_path.name}:{stamp}", "ref": str(ref_path)}
        else:
            builtin = meta.get("builtin")
            if not builtin or (self._builtin_speakers and builtin not in self._builtin_speakers):
                builtin = self._hashed_builtin(speaker)
            voice = {"kind": "builtin", "id": f"builtin:{builtin}", "builtin": builtin}

        self._voice_cache[speaker] = voice
        return voice

    def invalidate_voice(self, speaker: str) -> None:
        """Forget a cached voice resolution (after a clip is added/removed)."""
        self._voice_cache.pop(speaker, None)

    def _hashed_builtin(self, speaker: str) -> str:
        if not self._builtin_speakers:
            return ""
        h = int(hashlib.sha1(speaker.encode()).hexdigest(), 16)
        return self._builtin_speakers[h % len(self._builtin_speakers)]

    # ---- synthesis --------------------------------------------------------
    def _cache_path(self, voice_id: str, text: str) -> Path:
        key = f"{voice_id}|{self.speed:.2f}|{text}"
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
        return self.cache_dir / f"{digest}.wav"

    def say(self, text: str, speaker: str) -> Path:
        """Synthesize one segment; return path to a cached/created wav.

        Text is sanitized to clean ASCII first (see textutil). If nothing
        speakable remains — e.g. a scene-break divider — a short silence is
        emitted instead of feeding XTTS junk it would mispronounce or error on.
        """
        from .textutil import sanitize_for_tts
        clean = sanitize_for_tts(text)
        voice = self.resolve_voice(speaker)
        out = self._cache_path(voice["id"], clean or "\x00silence")
        if out.exists():
            return out

        if not clean:
            sf.write(str(out), np.zeros(int(XTTS_SR * 0.12), dtype="float32"), XTTS_SR)
            return out

        self._ensure_model()
        kwargs = dict(
            text=clean,
            language=self.language,
            file_path=str(out),
            split_sentences=True,
            speed=self.speed,
        )
        if voice["kind"] == "clone":
            kwargs["speaker_wav"] = voice["ref"]
        else:
            kwargs["speaker"] = voice["builtin"]

        try:
            self._tts.tts_to_file(**kwargs)
        except TypeError:
            # Older TTS builds don't accept `speed`; retry without it.
            kwargs.pop("speed", None)
            self._tts.tts_to_file(**kwargs)
        return out


def concat_wavs(wav_paths: list[Path], out_path: Path,
                gaps_ms: list[int] | None = None, sr: int = XTTS_SR) -> Path:
    """Concatenate wavs (mono, XTTS_SR) with per-segment trailing silence."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    chunks: list[np.ndarray] = []
    for i, p in enumerate(wav_paths):
        data, file_sr = sf.read(str(p), dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        chunks.append(data)
        gap = (gaps_ms[i] if gaps_ms else 250) / 1000.0
        if gap > 0:
            chunks.append(np.zeros(int(file_sr * gap), dtype="float32"))
    audio = np.concatenate(chunks) if chunks else np.zeros(1, dtype="float32")
    sf.write(str(out_path), audio, sr)
    return out_path
