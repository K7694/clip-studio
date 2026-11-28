"""Cut, reframe, caption and encode the final 1080x1920 Short."""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import config


def render(src: Path, segments: list[tuple[float, float]], graph: str, ass: Path | None, out: Path,
           post: str | None = None) -> Path:
    """Cut `segments` (source seconds) back-to-back, reframe, add effects + captions, encode.

    One segment = plain clip; two = flash-forward teaser followed by the clip.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    n = len(segments)
    pre = [f"[0:v]split={n}" + "".join(f"[sv{i}]" for i in range(n)),
           f"[0:a]asplit={n}" + "".join(f"[sa{i}]" for i in range(n))]
    for i, (a, b) in enumerate(segments):
        pre.append(f"[sv{i}]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
        pre.append(f"[sa{i}]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]")
    pre.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[cv][ca]")
    vchain = ";".join(pre) + ";" + graph.replace("[0:v]", "[cv]")
    vout = "[v]"
    if post:
        vchain += f";{vout}{post}[vp]"
        vout = "[vp]"
    if ass is not None:
        a = str(ass).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        vchain += f";{vout}ass='{a}'[vo]"
        vout = "[vo]"
    vchain += ";[ca]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]"
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-i", str(src),
        "-filter_complex", vchain,
        "-map", vout, "-map", "[aout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
        "-profile:v", "high", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "160k",
        str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


def thumbnail(video: Path, at: float, out: Path) -> Path:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", str(video),
                    "-frames:v", "1", "-q:v", "3", str(out)], check=True)
    return out
