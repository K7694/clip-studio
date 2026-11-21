"""clips-studio settings. Everything you might want to tweak lives here."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK_DIR = ROOT / "work"          # scratch downloads (gitignored)
OUT_DIR = ROOT / "out"            # finished clips (gitignored, uploaded as artifacts)
STATE_FILE = ROOT / "state" / "used.json"

# ── Sources ────────────────────────────────────────────────────────────
# kind="twitch":  top viewer-made clips from the last CLIP_RANGE (already highlights)
# kind="youtube": recent streams/videos; the highlight is found by audio analysis
# Rotation picks the least-recently-used sources each day. Add/remove freely;
# a source that fails (renamed, offline, blocked) is skipped for that day.
STREAMERS = [
    # ── International ──
    {"name": "IShowSpeed",    "kind": "youtube", "url": "https://www.youtube.com/@IShowSpeed/streams", "region": "intl"},
    {"name": "xQc",           "kind": "twitch",  "login": "xqc",       "region": "intl"},
    {"name": "Kai Cenat",     "kind": "twitch",  "login": "kaicenat",  "region": "intl"},
    {"name": "summit1g",      "kind": "twitch",  "login": "summit1g",  "region": "intl"},
    {"name": "Buddha",        "kind": "twitch",  "login": "buddha",    "region": "intl"},
    # ── India ──
    {"name": "CarryMinati",   "kind": "youtube", "url": "https://www.youtube.com/@CarryisLive/streams",          "region": "india"},
    {"name": "Techno Gamerz", "kind": "youtube", "url": "https://www.youtube.com/@TechnoGamerzOfficial/videos",  "region": "india"},
    {"name": "Total Gaming",  "kind": "youtube", "url": "https://www.youtube.com/@TotalGaming093/streams",       "region": "india"},
    {"name": "GamerFleet",    "kind": "youtube", "url": "https://www.youtube.com/@GamerFleet/streams",           "region": "india"},
    {"name": "Mortal",        "kind": "youtube", "url": "https://www.youtube.com/@MortaL/streams",               "region": "india"},
]

POSTS_PER_DAY = int(os.getenv("POSTS_PER_DAY", "5"))
# Warm-up for a brand-new channel: (days since first upload, posts/day).
# Ramps 2 -> 3 -> 4 -> POSTS_PER_DAY so the channel never looks like a spam burst.
WARMUP = [(7, 2), (14, 3), (21, 4)]
CLIP_RANGE = "7d"                 # twitch clip window: 24hr | 7d | 30d
YT_RECENT_VIDEOS = 4              # how many recent uploads to consider per YouTube source
YT_MIN_VIDEO_SEC = 8 * 60         # skip shorts/trailers
YT_MAX_VIDEO_SEC = 6 * 3600       # skip 12h marathons (audio download too big)

# ── Clip shape ─────────────────────────────────────────────────────────
CLIP_MIN_SEC = 18                 # for clips cut from long streams
TWITCH_MIN_SEC = 10               # viewer-made twitch clips can be short and still great
CLIP_MAX_SEC = 55                 # Shorts allow 3 min, but < 60s performs best
SKIP_EDGES_SEC = 4 * 60           # ignore stream intros/outros when hunting peaks
OUT_W, OUT_H = 1080, 1920

# ── Captions ───────────────────────────────────────────────────────────
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
CAPTION_FONT = "Noto Sans"        # fontconfig falls back to Noto Devanagari for Hindi
CAPTION_WORDS_PER_LINE = 3

# ── Upload / schedule ──────────────────────────────────────────────────
# Videos are uploaded once a day and scheduled with publishAt, so GitHub's
# cron delays (hours, as seen in you-shorts) don't move the posting times.
PUBLISH_SLOTS_IST = ["12:30", "15:30", "18:30", "20:30", "22:30"]
YOUTUBE_CATEGORY = "20"           # Gaming
YOUTUBE_TOKEN_FILE = ROOT / "youtube_token.json"
# Preview mode = full pipeline, no upload. On automatically until the token exists.
DRY_RUN = (os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")
           or not YOUTUBE_TOKEN_FILE.exists() or YOUTUBE_TOKEN_FILE.stat().st_size < 20)
