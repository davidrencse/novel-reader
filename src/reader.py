"""
reader.py — the main entry point.

  # Scrape a chapter and read it aloud, then play it:
  python -m src.reader --url https://witchculttranslation.com/2026/08/09/arc-10-chapter-26-a-word-of-congratulations/ --play

  # Read a text file you pasted yourself (line 1 = title, then paragraphs):
  python -m src.reader --text data/chapters/my-chapter.txt --play

  # Just SEE who the detector thinks is speaking (no audio, fast):
  python -m src.reader --url <url> --dry-run

  # Export/read an editable script, so you can hand-fix speakers:
  python -m src.reader --url <url> --script out.tsv --dry-run   # write script
  python -m src.reader --script out.tsv --play                  # read edited script

  # List the built-in XTTS voice names (to fill config.yaml `builtin:`):
  python -m src.reader --list-voices
"""
from __future__ import annotations

import sys
import argparse
import subprocess
from pathlib import Path

import yaml

from . import scrape
from .attribute import attribute, Segment
from .tts import VoiceSynth, concat_wavs

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Path) -> dict:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    # Anchor relative paths to the project root so the CLI works from any CWD.
    for k, v in config.get("paths", {}).items():
        p = Path(v)
        config["paths"][k] = str(p if p.is_absolute() else (ROOT / p))
    return config


def write_script(segments: list[Segment], path: Path) -> None:
    """Editable TSV:  speaker<TAB>kind<TAB>text  (edit the speaker column freely)."""
    lines = ["# speaker\tkind\ttext  — edit the speaker column, keep the tabs"]
    for s in segments:
        text = s.text.replace("\t", " ").replace("\n", " ")
        lines.append(f"{s.speaker}\t{s.kind}\t{text}")
    path.write_text("\n".join(lines), encoding="utf-8")


def read_script(path: Path) -> list[Segment]:
    segs: list[Segment] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.strip() or ln.startswith("#"):
            continue
        parts = ln.split("\t")
        if len(parts) < 3:
            continue
        speaker, kind, text = parts[0].strip(), parts[1].strip(), "\t".join(parts[2:]).strip()
        if text:
            segs.append(Segment(speaker, text, kind or "narration"))
    return segs


def print_attribution(segments: list[Segment]) -> None:
    counts: dict[str, int] = {}
    for s in segments:
        counts[s.speaker] = counts.get(s.speaker, 0) + 1
    print("\n=== Speaker breakdown ===")
    for name, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {name:<12} {n} segments")
    print("\n=== First 25 segments ===")
    for s in segments[:25]:
        tag = "  " if s.kind == "narration" else "» "
        preview = (s.text[:70] + "…") if len(s.text) > 70 else s.text
        print(f"{tag}[{s.speaker}] {preview}")
    if len(segments) > 25:
        print(f"  … {len(segments) - 25} more")


def synthesize(segments: list[Segment], config: dict, out_path: Path) -> Path:
    synth = VoiceSynth(config)
    eng = config["engine"]
    gap = int(eng.get("gap_ms", 250))
    para_gap = int(eng.get("paragraph_gap_ms", 550))

    wavs: list[Path] = []
    gaps: list[int] = []
    total = len(segments)
    for i, s in enumerate(segments, 1):
        print(f"[{i:>4}/{total}] {s.speaker:<12} {s.text[:50]!r}")
        wavs.append(synth.say(s.text, s.speaker))
        # Bigger pause after the last segment of a paragraph (narration end / dialogue end).
        gaps.append(para_gap if s.kind == "narration" else gap)

    print(f"[mix] stitching {len(wavs)} segments → {out_path}")
    return concat_wavs(wavs, out_path, gaps_ms=gaps)


def play(path: Path) -> None:
    """Play via ffplay (bundled with ffmpeg). Falls back to OS default."""
    try:
        subprocess.run(
            ["ffplay", "-autoexit", "-nodisp", "-loglevel", "quiet", str(path)],
            check=False,
        )
    except FileNotFoundError:
        import os
        os.startfile(str(path))  # type: ignore[attr-defined]  # Windows fallback


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Read a Re:Zero chapter with per-character voices.")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--url", help="chapter URL on witchculttranslation.com")
    src.add_argument("--text", help="path to a chapter text file (line 1 = title)")
    src.add_argument("--script", help="path to an editable TSV script")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--out", default=None, help="output wav path")
    ap.add_argument("--engine", choices=["rules", "ai"], default=None,
                    help="speaker attribution engine (overrides config); 'ai' needs ANTHROPIC_API_KEY")
    ap.add_argument("--dry-run", action="store_true", help="attribute only; no audio")
    ap.add_argument("--play", action="store_true", help="play when finished")
    ap.add_argument("--list-voices", action="store_true", help="print built-in XTTS voices and exit")
    args = ap.parse_args(argv)

    config = load_config(Path(args.config))

    if args.list_voices:
        for v in VoiceSynth(config).builtin_speakers:
            print(v)
        return 0

    # ---- obtain segments --------------------------------------------------
    title = "chapter"
    if args.script and not (args.url or args.text):
        segments = read_script(Path(args.script))
        title = Path(args.script).stem
    else:
        if args.url:
            path, title, paragraphs = scrape.scrape_to_file(
                args.url, Path(config["paths"]["text_dir"]))
            print(f"[scrape] {title} — {len(paragraphs)} paragraphs → {path}")
        elif args.text:
            title, paragraphs = scrape.load_text_file(Path(args.text))
            print(f"[text] {title} — {len(paragraphs)} paragraphs")
        else:
            ap.error("provide one of --url / --text / --script")
            return 2
        engine = args.engine or (config.get("attribution") or {}).get("engine", "rules")
        if engine == "ai":
            try:
                from .attribute_ai import attribute_ai
                segments = attribute_ai(paragraphs, config)
                print("[attribute] engine: ai (Claude)")
            except Exception as e:
                print(f"[attribute] AI engine failed ({e}); falling back to rules.", file=sys.stderr)
                segments = attribute(paragraphs, config)
        else:
            segments = attribute(paragraphs, config)

    if not segments:
        print("No readable segments found.", file=sys.stderr)
        return 1

    print_attribution(segments)

    # If asked, also write an editable script alongside a dry run.
    if args.script and (args.url or args.text):
        write_script(segments, Path(args.script))
        print(f"\n[script] wrote editable script → {args.script}")

    if args.dry_run:
        return 0

    out = Path(args.out) if args.out else Path(config["paths"]["audio_dir"]) / f"{scrape.slugify(title)}.wav"
    out_path = synthesize(segments, config, out)
    print(f"\n✓ Done: {out_path}")

    if args.play:
        play(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
