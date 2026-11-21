"""YouTube upload with scheduled publishing (publishAt)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]
IST = timezone(timedelta(hours=5, minutes=30))


def _client():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(config.YOUTUBE_TOKEN_FILE))
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        config.YOUTUBE_TOKEN_FILE.write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def channel_title() -> str:
    """Fails loudly if the login is broken, before any clip work is done."""
    items = _client().channels().list(part="snippet", mine=True).execute().get("items", [])
    if not items:
        raise RuntimeError("This Google account has no YouTube channel yet — create one first.")
    return items[0]["snippet"]["title"]


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
