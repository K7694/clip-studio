"""Find the hype moments in a long stream from its audio alone (cheap: no video, no AI).

Idea: a reaction moment is where the streamer gets suddenly much louder than
they were in the minute around it (screaming, laughing, chat-bait). We measure
loudness every half second, subtract the local baseline, and take the biggest
sustained spikes.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from . import config

SR = 8000           # analysis sample rate (plenty for loudness)
HOP = 0.5           # seconds per loudness value


def media_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def loudness_curve(path: Path) -> np.ndarray:
    """dBFS per HOP seconds, streamed so a 6-hour stream stays small in memory."""
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR),
                             "-f", "s16le", "-"], stdout=subprocess.PIPE)
    hop = int(SR * HOP)
    vals, buf = [], b""
    while True:
        chunk = proc.stdout.read(hop * 2 * 512)
        if not chunk:
            break
        buf += chunk
        n = len(buf) // (hop * 2)
        if n:
            a = np.frombuffer(buf[: n * hop * 2], dtype=np.int16).astype(np.float32).reshape(n, hop)
            rms = np.sqrt((a ** 2).mean(axis=1)) / 32768.0
            vals.extend(20 * np.log10(rms + 1e-6))
            buf = buf[n * hop * 2:]
    proc.wait()
    return np.asarray(vals, dtype=np.float32)


def _rolling(x: np.ndarray, win: int, fn) -> np.ndarray:
    win = max(1, min(win, len(x)))
    pad = win // 2
    xp = np.pad(x, (pad, win - pad - 1), mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(xp, win)
    return fn(view, axis=1)


def excitement(curve: np.ndarray) -> np.ndarray:
    smooth = _rolling(curve, int(3 / HOP), np.mean)             # 3 s sustain
    baseline = _rolling(curve, int(90 / HOP), np.median)         # 90 s context
    score = smooth - baseline
    score[curve < -45] = -99                                     # silence is never a highlight
    return score


def find_peaks(audio: Path, taken: list[tuple[float, float]] | None = None, k: int = 6) -> list[float]:
    curve = loudness_curve(audio)
    if len(curve) < int(120 / HOP):
        return []
    score = excitement(curve)
    edge = int(config.SKIP_EDGES_SEC / HOP)
    if len(score) > 4 * edge:
        score[:edge] = -99
        score[-edge:] = -99
    for a, b in taken or []:
        score[max(0, int((a - 30) / HOP)): int((b + 30) / HOP)] = -99
    peaks = []
    gap = int(120 / HOP)                                          # moments >= 2 min apart
    s = score.copy()
    for _ in range(k):
        i = int(np.argmax(s))
        if s[i] < 3.0:                                            # < 3 dB above baseline: nothing special
            break
        peaks.append(i * HOP)
        s[max(0, i - gap): i + gap] = -99
    return peaks


def window_around(peak: float, total: float) -> tuple[float, float]:
    """Build-up before the spike matters more than the aftermath."""
    start = max(0.0, peak - 24)
    end = min(total, peak + 14)
    return start, end


def refine_bounds(segments: list[dict], start: float, end: float, total: float) -> tuple[float, float]:
    """Snap the cut to sentence boundaries so we never start/stop mid-word.

    segments: [{"start","end"}] in the same timeline as start/end.
    """
    starts = [s["start"] for s in segments if start - 7 <= s["start"] <= start + 5]
    ends = [s["end"] for s in segments if end - 5 <= s["end"] <= end + 7]
    if starts:
        start = min(starts, key=lambda t: abs(t - start))
    if ends:
        end = min(ends, key=lambda t: abs(t - end))
    start = max(0.0, start - 0.15)
    end = min(total, end + 0.35)
    if end - start > config.CLIP_MAX_SEC:
        end = start + config.CLIP_MAX_SEC
    return start, end
