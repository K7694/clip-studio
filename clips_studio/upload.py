"""YouTube upload with scheduled publishing (publishAt)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly",
          "https://www.googleapis.com/auth/yt-analytics.readonly"]   # optional: retention for learning
IST = timezone(timedelta(hours=5, minutes=30))


_CREDS = None


def _creds():
    global _CREDS
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if _CREDS is None:
        _CREDS = Credentials.from_authorized_user_file(str(config.YOUTUBE_TOKEN_FILE))
    if not _CREDS.valid and _CREDS.refresh_token:
        _CREDS.refresh(Request())
    return _CREDS


def _client(api: str = "youtube", version: str = "v3"):
    from googleapiclient.discovery import build
    return build(api, version, credentials=_creds(), cache_discovery=False)


def stats(ids: list[str]) -> dict:
    """views / likes / comments / privacy for up to hundreds of videos (1 quota unit per 50)."""
    yt, out = _client(), {}
    for i in range(0, len(ids), 50):
        r = yt.videos().list(part="statistics,status", id=",".join(ids[i:i + 50])).execute()
        for it in r.get("items", []):
            st = it.get("statistics", {})
            out[it["id"]] = {"views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                             "comments": int(st.get("commentCount", 0)),
                             "privacy": it.get("status", {}).get("privacyStatus")}
    return out


def retention(ids: list[str]) -> dict:
    """Average % watched per video. Needs the yt-analytics.readonly scope; raises without it."""
    ya = _client("youtubeAnalytics", "v2")
    out = {}
    end = datetime.now(timezone.utc).date().isoformat()
    for i in range(0, len(ids), 40):
        r = ya.reports().query(ids="channel==MINE", startDate="2026-01-01", endDate=end,
                               metrics="views,averageViewPercentage,averageViewDuration",
                               dimensions="video", filters="video==" + ",".join(ids[i:i + 40]),
                               maxResults=200).execute()
        for row in r.get("rows", []):
            out[row[0]] = {"avg_pct": float(row[2]), "avg_sec": float(row[3])}
    return out


def verify(video_id: str) -> str:
    """After upload: is it really scheduled? Returns a short human status."""
    items = _client().videos().list(part="status", id=video_id).execute().get("items", [])
    if not items:
        return "⚠ not found right after upload (YouTube may still be processing)"
    st = items[0]["status"]
    if st.get("uploadStatus") in ("rejected", "failed"):
        return f"⚠ {st.get('uploadStatus')}: {st.get('rejectionReason') or st.get('failureReason')}"
    if st.get("privacyStatus") == "private" and not st.get("publishAt"):
        return ("⚠ uploaded but LOCKED PRIVATE — Google is holding uploads from this Cloud project "
                "until it passes the YouTube API audit")
    return f"✓ scheduled ({st.get('privacyStatus')} until {st.get('publishAt')})"


def channel_title() -> str:
    """Fails loudly if the login is broken, before any clip work is done."""
    items = _client().channels().list(part="snippet", mine=True).execute().get("items", [])
    if not items:
        raise RuntimeError("This Google account has no YouTube channel yet — create one first.")
    ch = items[0]
    handle = ch["snippet"].get("customUrl")
    link = f"https://www.youtube.com/{handle}" if handle else f"https://www.youtube.com/channel/{ch['id']}"
    return f"{ch['snippet']['title']} — {link}"


def publish_times(n: int, taken: set[str] | None = None, now: datetime | None = None) -> list[datetime]:
    """Next n free slots from PUBLISH_SLOTS_IST that are at least 20 min away.

    `taken` holds isoformat times already scheduled by earlier runs, so a late
    GitHub cron never double-books a slot.
    """
    taken = taken or set()
    now = now or datetime.now(timezone.utc)
    earliest = now + timedelta(minutes=20)
    slots = []
    day = now.astimezone(IST).date()
    while len(slots) < n:
        for hm in config.PUBLISH_SLOTS_IST:
            h, m = map(int, hm.split(":"))
            t = datetime(day.year, day.month, day.day, h, m, tzinfo=IST)
            if t >= earliest and t.isoformat() not in taken and len(slots) < n:
                slots.append(t)
        day += timedelta(days=1)
    return slots


def upload(video: Path, meta: dict, publish_at: datetime, thumb: Path | None = None) -> str:
    from googleapiclient.http import MediaFileUpload

    yt = _client()
    body = {
        "snippet": {"title": meta["title"], "description": meta["description"],
                    "tags": meta["tags"], "categoryId": config.YOUTUBE_CATEGORY},
        "status": {"privacyStatus": "private",
                   "publishAt": publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                   "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(video), mimetype="video/mp4", resumable=True, chunksize=4 * 1024 * 1024)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]
