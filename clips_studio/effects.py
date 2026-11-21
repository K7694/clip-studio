"""Editing layer on top of the raw clip — what makes it feel produced, not reposted.

  hook     first ~3 s: big boxed headline at the top (why should I keep watching?)
  punch    quick zoom-ins on the loudest reactions in the clip
  tag      small "@streamer" credit, always visible
  progress thin bar along the bottom (people watch to the end of a bar)
  outro    last ~3 s: follow + "comment who to clip next" (comments boost Shorts)

No intro card on purpose: Shorts viewers swipe in the first second, so the
"intro" is the hook headline over the action, not a black title screen.
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import numpy as np
import pysubs2

from . import config, highlights

YELLOW = pysubs2.Color(255, 221, 0)
WHITE = pysubs2.Color(255, 255, 255)
BLACK = pysubs2.Color(0, 0, 0)

HOOK_SEC = 3.0
OUTRO_SEC = 3.0


def _plain(text: str) -> str:
    """Burned-in text can't show colour emoji (libass), so keep letters only."""
    text = re.sub(r"#\w+", "", text)
    text = "".join(ch for ch in text if ord(ch) < 0x2190 or 0x0900 <= ord(ch) <= 0x097F)
    return re.sub(r"\s+", " ", text).strip(" -|:")


def hook_text(title: str, streamer: str) -> str:
    t = _plain(title).split("|")[0].strip()
    if t.lower().startswith(streamer.lower() + ":"):
        t = t[len(streamer) + 1:].strip()
        if streamer.split()[0].lower() not in t.lower() and len(t) < 28:
            t = f"{streamer}: {t}"
    t = t.upper()
    lines = textwrap.wrap(t, 20)[:3]
    return r"\N".join(lines)


def punch_points(src: Path, start: float, end: float, k: int = 2) -> list[float]:
    """Times (relative to the clip) of the loudest moments, for zoom-ins."""
    curve = highlights.loudness_curve(src)
    hop = highlights.HOP
    a, b = int(start / hop), int(end / hop)
    seg = curve[a:b]
    if len(seg) < int(6 / hop):
        return []
    base = float(np.median(seg))
    s = seg.copy()
    s[: int(1.0 / hop)] = -99                       # not in the first second
    s[-int((OUTRO_SEC + 0.5) / hop):] = -99         # not under the outro
    pts = []
    for _ in range(k):
        i = int(np.argmax(s))
        if s[i] < base + 6:                          # needs a real spike (+6 dB)
            break
        pts.append(round(i * hop, 2))
        s[max(0, i - int(6 / hop)): i + int(6 / hop)] = -99
    return sorted(pts)


def post_filter(points: list[float], dur: float) -> str:
    """ffmpeg chain applied to the finished 1080x1920 frame: punch zooms + progress bar."""
    OW, OH = config.OUT_W, config.OUT_H
    chain = []
    if points:
        # fast in (0.12 s), short hold, ease out (0.5 s); +12 % zoom
        bumps = "+".join(
            f"0.12*if(between(t,{p - 0.12:.2f},{p:.2f}),(t-{p - 0.12:.2f})/0.12,"
            f"if(between(t,{p:.2f},{p + 0.35:.2f}),1,"
            f"if(between(t,{p + 0.35:.2f},{p + 0.85:.2f}),1-(t-{p + 0.35:.2f})/0.5,0)))"
            for p in points)
        z = f"(1+{bumps})"
        chain.append(f"scale=w='2*trunc({OW // 2}*{z})':h='2*trunc({OH // 2}*{z})':eval=frame:flags=bicubic")
        chain.append(f"crop={OW}:{OH}")
    chain.append(f"drawbox=x=0:y={OH - 14}:w='iw*t/{dur:.3f}':h=14:color=0xFFDD00@0.95:t=fill")
    return ",".join(chain)


def add_overlays(ass_path: Path, dur: float, streamer: str, hook: str) -> None:
    """Append hook / tag / outro to the captions file (creates it if missing)."""
    if ass_path.exists():
        subs = pysubs2.load(str(ass_path))
    else:
        subs = pysubs2.SSAFile()
        subs.info["PlayResX"] = str(config.OUT_W)
        subs.info["PlayResY"] = str(config.OUT_H)
    font = config.CAPTION_FONT
    subs.styles["Hook"] = pysubs2.SSAStyle(
        fontname=font, fontsize=74, bold=True, primarycolor=BLACK, outlinecolor=YELLOW,
        backcolor=YELLOW, borderstyle=3, outline=16, shadow=0,
        alignment=pysubs2.Alignment.TOP_CENTER, marginv=90)
    subs.styles["Tag"] = pysubs2.SSAStyle(
        fontname=font, fontsize=40, bold=True, primarycolor=pysubs2.Color(255, 255, 255, 60),
        outlinecolor=BLACK, outline=3, shadow=0, alignment=pysubs2.Alignment.BOTTOM_LEFT,
        marginl=36, marginv=40)
    subs.styles["Outro"] = pysubs2.SSAStyle(
        fontname=font, fontsize=56, bold=True, primarycolor=WHITE, outlinecolor=pysubs2.Color(225, 29, 72),
        backcolor=pysubs2.Color(225, 29, 72), borderstyle=3, outline=14, shadow=0,
        alignment=pysubs2.Alignment.BOTTOM_CENTER, marginv=150)

    ms = lambda s: int(max(0.0, s) * 1000)  # noqa: E731
    if hook:
        subs.append(pysubs2.SSAEvent(start=0, end=ms(min(HOOK_SEC, dur - 1)), style="Hook",
                                     text=r"{\fad(0,250)\t(0,120,\fscx108\fscy108)\t(120,240,\fscx100\fscy100)}" + hook))
    subs.append(pysubs2.SSAEvent(start=0, end=ms(dur), style="Tag", text="@" + _plain(streamer).replace(" ", "")))
    if dur >= 12:
        name = _plain(streamer).upper()
        subs.append(pysubs2.SSAEvent(
            start=ms(dur - OUTRO_SEC), end=ms(dur), style="Outro",
            text=r"{\fad(200,0)}FOLLOW FOR DAILY " + name + r" CLIPS\N{\c&H00DDFF&}COMMENT WHO TO CLIP NEXT"))
    subs.save(str(ass_path))
