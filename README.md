# Re:Zero Reader 🎙️

Reads the Re:Zero web novel aloud with a **different voice per character** —
Subaru, Emilia, Rem, Beatrice, Roswaal, and the rest — using local XTTS v2
voice synthesis on your GPU. Scrapes chapters from
[witchculttranslation.com](https://witchculttranslation.com/) (for personal
playback), figures out who's speaking each line, and produces one audio file
per chapter.

## What it does
1. **Scrape** a chapter URL → clean text (`src/scrape.py`)
2. **Attribute** every line to a speaker with free rules — dialogue tags,
   surrounding names, and back-and-forth alternation (`src/attribute.py`)
3. **Synthesize** each segment with that character's voice (`src/tts.py`)
4. **Stitch** it into a single chapter `.wav` and play it (`src/reader.py`)

Every character gets a **distinct built-in voice out of the box**. Want a
character to actually sound cloned? Drop a short clip in `voices/` (see
`voices/README.md`) and it's used automatically.

## Setup (once)
```powershell
./setup.ps1
```
Creates a Python 3.11 venv, installs CUDA PyTorch + Coqui XTTS, verifies your
RTX 4060 is visible. The **voice model (~1.8 GB) downloads on first synth**, not
during setup.

## The app (dark UI) — easiest way to use it
**Double-click `ReZeroReader.bat`.** It opens a native desktop window (its own
taskbar entry, no browser, no address bar) via `pywebview` + the Windows
WebView2 runtime that ships with Windows 11. Under the hood it runs the same
local server and dark UI:

- Paste a chapter URL (or switch to *Paste text*) and click **Load**.
- The chapter renders with each line color-coded by speaker, plus a sidebar with
  **analysis** (word count, estimated audio length, dialogue/narration split),
  the **cast** in the chapter, any **illustrations**, **prev/next chapter**
  links, in-body **links**, and **reader comments** (pulled from the site's
  WordPress comment API, since they load asynchronously and aren't in the page
  HTML).
- Press **▶ Read aloud** — playback streams segment-by-segment (starts almost
  immediately), highlights the current line, and auto-scrolls. Spacebar toggles
  play/pause; click any line to jump there; the speed selector adjusts playback.

If the window doesn't appear, check `app.log` in this folder for the reason
(the app runs windowless, so errors go there).

**Prefer it in a browser tab instead?** Run `python -m src.app` (or the old
flow) and open http://127.0.0.1:5000 — same UI, served to your browser.

> Why a `.bat` and not a real `.exe`? A standalone exe would have to bundle
> PyTorch + the XTTS model (~6 GB) and is fragile to build. The `.bat` launches
> the native window in one double-click. Right-click it →
> *Send to → Desktop (create shortcut)* for a desktop launcher you can give an
> icon and pin to the taskbar.

### CLI (no UI)
Prefer the terminal? The same pipeline runs headless:
```powershell
# See who the detector thinks is speaking — fast, no model needed:
./run.ps1 --url "https://witchculttranslation.com/2026/08/09/arc-10-chapter-26-a-word-of-congratulations/" --dry-run

# Read it aloud and play when done (first run downloads the model):
./run.ps1 --url "<chapter-url>" --play

# Read a chapter you pasted yourself (line 1 = title, blank line, paragraphs):
./run.ps1 --text "data/chapters/my-chapter.txt" --play

# List built-in voice names (to hand-pick in config.yaml):
./run.ps1 --list-voices
```

## Character voices (cloning)
Click **🎙 Voices** in the top bar to open the voice manager. For any character:
- **⬆ Upload** a clean 6–20s clip (any audio/video — it's transcoded automatically)
  to clone that character's voice. No clip = a distinct built-in voice.
- **▶ Preview** hears the current voice; **🗑** reverts to the built-in.

Clips live in `voices/<name>.wav`. Use audio you own — cloning a real person's
voice is for personal use only. (Clones sound *similar* to the source, not a
perfect replica.)

## AI speaker detection — the fix for "one monotone voice"
The rules engine often can't tell *who* is speaking, so most lines fall to the
narrator/unknown voice and it sounds monotone. An LLM reads the whole chapter and
names every speaker → each character gets their own voice. The top-bar toggle:

- **Rules** — free, instant heuristics (default). Limited: can't name most speakers.
- **Groq · free** — a **free** cloud LLM. Click it → paste a free key from
  [console.groq.com/keys](https://console.groq.com/keys) → reload the chapter.
  No cost, generous limits. Best free option.
- **Local** — a **free** local LLM on your own GPU via LM Studio or Ollama (no key,
  fully private). Click it and follow the setup popup (needs a chat model loaded).

Each chapter is parsed once and cached. Keys are stored locally (`groqkey.txt` /
`apikey.txt`, gitignored). Any engine falls back to rules if it isn't ready.
Paid **Claude** is also available by setting `ANTHROPIC_API_KEY` and
`attribution.engine: ai` in `config.yaml`.

## Fixing wrong speakers
Rules-based attribution isn't perfect. To correct a chapter exactly:
```powershell
# 1. Export an editable script (speaker <TAB> kind <TAB> text):
./run.ps1 --url "<chapter-url>" --script mychapter.tsv --dry-run
# 2. Edit the speaker column in mychapter.tsv
# 3. Read the corrected script:
./run.ps1 --script mychapter.tsv --play
```

## Customizing voices
Edit `config.yaml`:
- Add characters, aliases, and pick a `builtin:` voice per character
  (`./run.ps1 --list-voices` shows the options).
- Adjust `engine.speed`, `gap_ms`, `use_gpu`.

## Notes
- Text is a fan translation of copyrighted work — keep scraped chapters and
  generated audio **local and personal**; don't redistribute.
- XTTS v2 is under the Coqui Public Model License (non-commercial).
- Segments are cached in `data/cache/`, so re-runs and small edits are fast.
"# novel-reader" 
