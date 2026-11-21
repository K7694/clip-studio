"""Cut, reframe, caption and encode the final 1080x1920 Short."""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import config


def render(src: Path, start: float, end: float, graph: str, ass: Path | None, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    vchain = graph
    if ass is not None:
        a = str(ass).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        vchain += f";[v]ass='{a}'[vo]"
        vout = "[vo]"
    else:
        vout = "[v]"
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(src),
        "-filter_complex", vchain,
        "-map", vout, "-map", "0:a:0?",
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-profile:v", "high", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "160k",
        "-shortest", str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


def thumbnail(video: Path, at: float, out: Path) -> Path:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", str(video),
                    "-frames:v", "1", "-q:v", "3", str(out)], check=True)
    return out
