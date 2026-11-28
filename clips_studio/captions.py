"""Transcribe with faster-whisper (word timestamps) and write punchy word-by-word ASS captions."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pysubs2

from . import config, words as wordfilter

YELLOW = pysubs2.Color(255, 221, 0)
WHITE = pysubs2.Color(255, 255, 255)
BLACK = pysubs2.Color(0, 0, 0)


@lru_cache(maxsize=1)
def _model():
    from faster_whisper import WhisperModel
    return WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")


def transcribe(media: Path) -> dict:
    """Returns {"language", "segments": [{"start","end","text","words":[{"start","end","word"}]}]}.

    Times are relative to the media file (always a short clip here, < 75 s).
    """
    segs, info = _model().transcribe(
        str(media), word_timestamps=True, vad_filter=True, beam_size=5,
        condition_on_previous_text=False,
    )
    out = []
    for s in segs:
        words = [{"start": w.start, "end": w.end, "word": w.word.strip()} for w in (s.words or []) if w.word.strip()]
        out.append({"start": s.start, "end": s.end, "text": s.text.strip(), "words": words})
    return {"language": info.language, "segments": out}


def _chunks(words: list[dict]) -> list[list[dict]]:
    chunks, cur = [], []
    for w in words:
        if cur and (len(cur) >= config.CAPTION_WORDS_PER_LINE or w["start"] - cur[-1]["end"] > 0.6):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.!?,।]$", w["word"]):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def _clean(word: str) -> str:
    word = re.sub(r"[.,।]+$", "", word)
    return wordfilter.mask(word).upper()


def write_ass(transcript: dict, segments: list[tuple[float, float, float]], y: int, path: Path) -> int:
    """Captions for one or more source segments [(src_start, src_end, out_offset), ...].

    Words are re-timed into the output timeline (src time - src_start + out_offset), so a
    flash-forward teaser followed by the main clip gets correct captions for both parts.
    Returns the number of caption events written.
    """
    all_words = [w for s in transcript["segments"] for w in s["words"]]
    dur = max(off + (b - a) for a, b, off in segments)
    seg_words = []
    for a, b, off in segments:
        ws = [dict(w, start=max(w["start"], a) - a + off, end=min(w["end"], b) - a + off)
              for w in all_words if w["end"] > a and w["start"] < b]
        seg_words.append((ws, off + (b - a)))
    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = str(config.OUT_W)
    subs.info["PlayResY"] = str(config.OUT_H)
    subs.info["WrapStyle"] = "0"
    style = pysubs2.SSAStyle(
        fontname=config.CAPTION_FONT, fontsize=92, bold=True,
        primarycolor=WHITE, outlinecolor=BLACK, backcolor=pysubs2.Color(0, 0, 0, 120),
        outline=7, shadow=3, alignment=pysubs2.Alignment.MIDDLE_CENTER,
        marginl=60, marginr=60,
    )
    subs.styles["Default"] = style

    count = 0
    chunks, limits = [], []
    for ws, seg_end in seg_words:
        cs = _chunks(ws)
        chunks += cs
        limits += [seg_end] * len(cs)
    for ci, chunk in enumerate(chunks):
        nxt = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else dur
        nxt = min(nxt, limits[ci])
        for i, w in enumerate(chunk):
            a = max(0.0, w["start"])
            b = chunk[i + 1]["start"] if i + 1 < len(chunk) else min(w["end"] + 0.25, nxt)
            b = min(max(b, a + 0.08), dur)
            if b <= a:
                continue
            parts = []
            for j, x in enumerate(chunk):
                t = _clean(x["word"])
                if j == i:
                    parts.append(r"{\c&H00DDFF&\fscx112\fscy112}" + t + r"{\c&HFFFFFF&\fscx100\fscy100}")
                else:
                    parts.append(t)
            text = rf"{{\pos({config.OUT_W // 2},{y})}}" + " ".join(parts)
            subs.append(pysubs2.SSAEvent(start=int(a * 1000), end=int(b * 1000), text=text))
            count += 1
    subs.save(str(path))
    return count
