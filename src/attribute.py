"""
attribute.py — rules-based speaker detection (the free path).

Turns a list of paragraphs into an ordered list of Segments:
    Segment(speaker, text, kind)   kind in {"narration", "dialogue"}

How it decides who speaks a quoted line, in priority order:
  1. A dialogue tag in the SAME paragraph, e.g.  "…," Subaru said.  /  said Emilia.
  2. A single known character named in the surrounding narration.
  3. Alternation — an untagged back-and-forth flips between the last two speakers.
  4. Fall back to the configured unknown-speaker.

It's heuristic, not perfect. Run `reader.py --dry-run` to see the attribution
and hand-fix a chapter's script if you want it exact.
"""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass

# Opening/closing quote styles we recognise. Curly is what the site uses;
# JP corner brackets and straight quotes are supported too.
QUOTE_SPAN = re.compile(r"(“[^”]*”|「[^」]*」|\"[^\"]*\")")

# Verbs that introduce/close speech.
_VERBS = (
    "said|says|asked|asks|replied|replies|answered|answers|muttered|murmured|"
    "whispered|shouted|yelled|cried|exclaimed|added|continued|spoke|responded|"
    "retorted|sighed|laughed|grumbled|snapped|called|remarked|noted|breathed|"
    "gasped|growled|stated|declared|interrupted|agreed|nodded|began|insisted|"
    "admitted|offered|wondered|demanded|ordered|teased|chuckled|hummed|mused|"
    "spat|scoffed|huffed|groaned|screamed|repeated|responded|questioned"
)
VERB_RE = _VERBS


@dataclass
class Segment:
    speaker: str
    text: str
    kind: str  # "narration" | "dialogue"


class Attributor:
    def __init__(self, config: dict):
        self.narrator = config.get("narrator", "Narrator")
        self.unknown = config.get("unknown_speaker", self.narrator)
        chars = config.get("characters", {})

        # Map every name/alias (lowercased) -> canonical character key.
        self.name_to_key: dict[str, str] = {}
        for key, meta in chars.items():
            self.name_to_key[key.lower()] = key
            for alias in (meta or {}).get("aliases", []) or []:
                self.name_to_key[alias.lower()] = key
        # Longest names first so "Natsuki Subaru" beats "Subaru".
        names = sorted(self.name_to_key.keys(), key=len, reverse=True)
        self.names_re = re.compile(
            r"\b(" + "|".join(re.escape(n) for n in names) + r")\b", re.I
        ) if names else None

        # Rolling memory of who has spoken, for alternation.
        self._recent: deque[str] = deque(maxlen=2)
        self._last_narr_names: list[str] = []

    # ---- name/tag helpers -------------------------------------------------
    def _names_in(self, text: str) -> list[str]:
        if not text or not self.names_re:
            return []
        out: list[str] = []
        for m in self.names_re.finditer(text):
            key = self.name_to_key[m.group(1).lower()]
            if key not in out:
                out.append(key)
        return out

    def _tagged_speaker(self, near: str) -> str | None:
        """Resolve a dialogue tag by the name CLOSEST to a speech verb.

        Dialogue tags put the name right next to the verb ("Subaru said",
        "said Emilia"). We scan every speech verb and pick the nearest adjacent
        name, so 'As Subaru watched, Emilia said' correctly yields Emilia, not
        the earlier Subaru.
        """
        if not near or not self.names_re:
            return None
        names_alt = "|".join(map(re.escape, self.name_to_key))
        best_name: str | None = None
        best_gap = 10 ** 9

        for vm in re.finditer(r"\b(?:" + VERB_RE + r")\b", near, re.I):
            vs, ve = vm.start(), vm.end()
            # Name immediately AFTER the verb: "said Emilia", "asked a weary Subaru".
            am = re.match(r"[^A-Za-z“”\"]{0,15}?\b(" + names_alt + r")\b",
                          near[ve:ve + 30], re.I)
            if am and am.start(1) < best_gap:
                best_gap, best_name = am.start(1), am.group(1)
            # Name BEFORE the verb: take the LAST (closest) name, provided no
            # sentence break sits between it and the verb. "As Subaru watched,
            # Emilia said" -> Emilia (comma ok); ". Emilia said" after a period
            # still ok, but "Subaru. Emilia said" won't reach back past Subaru.
            before = near[max(0, vs - 40):vs]
            last = None
            for m in re.finditer(r"\b(" + names_alt + r")\b", before, re.I):
                last = m  # finditer is non-overlapping L->R, so this is the closest
            if last:
                tail = before[last.end():]                 # text between name and verb
                if not re.search(r"[.?!“”\"]", tail) and len(tail) < best_gap:
                    best_gap, best_name = len(tail), last.group(1)

        return self.name_to_key[best_name.lower()] if best_name else None

    def _alternate(self) -> str:
        """Pick the speaker who didn't just speak (for untagged ping-pong)."""
        if len(self._recent) == 2:
            a, b = self._recent[0], self._recent[1]
            return a if b != a else b
        if len(self._recent) == 1:
            return self._recent[0]
        return self.unknown

    def _remember(self, speaker: str) -> None:
        if speaker == self.narrator:
            return
        if not self._recent or self._recent[-1] != speaker:
            self._recent.append(speaker)

    # ---- main -------------------------------------------------------------
    def attribute(self, paragraphs: list[str]) -> list[Segment]:
        segments: list[Segment] = []
        for para in paragraphs:
            parts = QUOTE_SPAN.split(para)
            # parts alternate: [narration, dialogue, narration, dialogue, ...]
            # Attribute quotes using the narration on either side of them.
            dialogue_idx = [i for i in range(len(parts)) if i % 2 == 1]

            if not dialogue_idx:
                # Pure narration paragraph.
                txt = para.strip()
                if txt:
                    segments.append(Segment(self.narrator, txt, "narration"))
                    self._last_narr_names = self._names_in(txt)
                continue

            for i, part in enumerate(parts):
                part = part.strip()
                if not part:
                    continue
                if i % 2 == 0:
                    # Narration fragment between/around quotes.
                    segments.append(Segment(self.narrator, part, "narration"))
                    continue

                # Dialogue fragment — strip the enclosing quote chars for TTS.
                inner = part[1:-1].strip() if len(part) >= 2 else part
                if not inner:
                    continue
                before = parts[i - 1] if i - 1 >= 0 else ""
                after = parts[i + 1] if i + 1 < len(parts) else ""

                speaker = (
                    self._tagged_speaker(after)      # tags usually follow the line
                    or self._tagged_speaker(before)
                )
                if speaker is None:
                    # Single named character in surrounding narration?
                    ctx = self._names_in(after) or self._names_in(before)
                    if len(ctx) == 1:
                        speaker = ctx[0]
                    elif len(self._last_narr_names) == 1:
                        speaker = self._last_narr_names[0]
                if speaker is None:
                    speaker = self._alternate()

                self._remember(speaker)
                segments.append(Segment(speaker, inner, "dialogue"))

            self._last_narr_names = []  # reset context after a dialogue paragraph
        return segments


def attribute(paragraphs: list[str], config: dict) -> list[Segment]:
    return Attributor(config).attribute(paragraphs)
