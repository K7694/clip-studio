"""Find candidate moments for a streamer and download just the part we need."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yt_dlp
from yt_dlp.utils import download_range_func

from . import config, highlights


@dataclass
class Moment:
    streamer: dict
    media_id: str            # unique id used for "already used" tracking
    page_url: str            # what yt-dlp downloads
    credit_url: str          # link shown in the description
    source_title: str
    start: float = 0.0       # seconds inside the source
    end: float = 0.0
    pad: float = 0.0         # extra seconds downloaded on each side for trimming
    whole: bool = False      # twitch clip: download all of it
    meta: dict = field(default_factory=dict)


def _ydl(extra: dict | None = None) -> yt_dlp.YoutubeDL:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "socket_timeout": 30,
        # several clients: if one is bot-checked from cloud IPs another may still work
        "extractor_args": {"youtube": {"player_client": ["tv", "web_safari", "mweb", "default"]}},
    }
    if extra:
        opts.update(extra)
    return yt_dlp.YoutubeDL(opts)


# ── Twitch: top viewer-made clips are already highlights ────────────────
def twitch_moments(streamer: dict, used: dict) -> list[Moment]:
    url = f"https://www.twitch.tv/{streamer['login']}/clips?filter=clips&range={config.CLIP_RANGE}"
    with _ydl({"extract_flat": "in_playlist", "playlistend": 25}) as ydl:
        info = ydl.extract_info(url, download=False)
    out = []
    for e in info.get("entries") or []:
        cid = f"twitch:{e.get('id')}"
        if not e.get("id") or cid in used:
            continue
        clip_url = e.get("url") or f"https://clips.twitch.tv/{e['id']}"
        out.append(Moment(streamer, cid, clip_url, clip_url, e.get("title") or "", whole=True,
                          meta={"views": e.get("view_count")}))
    return out


# ── YouTube: recent long videos; the loudest reaction wins ──────────────
def youtube_moments(streamer: dict, used: dict, used_windows: dict, workdir: Path) -> list[Moment]:
    with _ydl({"extract_flat": "in_playlist", "playlistend": config.YT_RECENT_VIDEOS * 3}) as ydl:
        info = ydl.extract_info(streamer["url"], download=False)
    videos = []
    for e in info.get("entries") or []:
        if e.get("live_status") in ("is_live", "is_upcoming"):
            continue
        dur = e.get("duration") or 0
        if dur and not (config.YT_MIN_VIDEO_SEC <= dur <= config.YT_MAX_VIDEO_SEC):
            continue
        videos.append(e)
        if len(videos) >= config.YT_RECENT_VIDEOS:
            break

    for v in videos:
        vid = v["id"]
        vurl = f"https://www.youtube.com/watch?v={vid}"
        audio = download_audio(vurl, workdir / f"{vid}.audio")
        if not audio:
            continue
        taken = [tuple(w) for w in used_windows.get(vid, [])]
        peaks = highlights.find_peaks(audio, taken)
        moments = []
        for p in peaks:
            start, end = highlights.window_around(p, highlights.media_duration(audio))
            mid = f"youtube:{vid}:{int(start)}"
            if mid in used:
                continue
            moments.append(Moment(streamer, mid, vurl, vurl, v.get("title") or "", start, end, pad=8,
                                  meta={"video_id": vid, "peak": p}))
        if moments:
            return moments
    return []


def download_audio(url: str, stem: Path) -> Path | None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    opts = {"format": "bestaudio[abr<=80]/bestaudio/worst", "outtmpl": str(stem) + ".%(ext)s"}
    with _ydl(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info))
    return path if path.exists() else None


def download_moment(m: Moment, dest_stem: Path) -> Path:
    """Download the clip (or the padded section of a long video) as mp4."""
    dest_stem.parent.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(dest_stem) + ".%(ext)s",
    }
    if not m.whole:
        a, b = max(0.0, m.start - m.pad), m.end + m.pad
        opts["download_ranges"] = download_range_func([], [(a, b)])
        opts["force_keyframes_at_cuts"] = True
    with _ydl(opts) as ydl:
        info = ydl.extract_info(m.page_url, download=True)
        path = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
    if not path.exists():
        # merged file may keep another extension
        cands = sorted(dest_stem.parent.glob(dest_stem.name + ".*"))
        cands = [c for c in cands if c.suffix in (".mp4", ".mkv", ".webm")]
        if not cands:
            raise RuntimeError("download produced no video file")
        path = cands[0]
    if not m.whole:
        # local offset of the original window inside the padded download
        m.meta["local_start"] = m.start - max(0.0, m.start - m.pad)
        m.meta["local_end"] = m.meta["local_start"] + (m.end - m.start)
    return path


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:format=duration", "-of", "default=nw=1", str(path)],
        capture_output=True, text=True, check=True).stdout
    d = dict(line.split("=", 1) for line in out.strip().splitlines() if "=" in line)
    return {"w": int(d["width"]), "h": int(d["height"]), "dur": float(d["duration"])}
