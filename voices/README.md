# Voice reference clips (optional)

Drop clean voice clips here to **clone** a character. If a character has no
clip, it automatically uses a distinct built-in XTTS voice instead — so the
reader works with this folder empty.

## How
- File name must match `ref:` in `config.yaml`, e.g. `subaru.wav`, `emilia.wav`, `narrator.wav`.
- **Format:** WAV, mono, 16-bit, 22 kHz or 24 kHz.
- **Length:** 6–20 seconds of clean speech — one speaker, no music, no SFX, no overlap.
- More/cleaner reference audio = better clone.

## Convert anything to the right format with ffmpeg
```bash
ffmpeg -i input.mp3 -ac 1 -ar 24000 -sample_fmt s16 subaru.wav
```

Note: XTTS v2 is under the Coqui Public Model License (non-commercial).
Cloning real voice actors is for personal use only — don't redistribute the output.
