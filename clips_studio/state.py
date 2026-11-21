"""Tiny JSON state: which clips/videos were used and when each source last posted."""
import json
import time

from .config import STATE_FILE


def load() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data.setdefault("used_ids", {})        # source media id -> unix time used
    data.setdefault("used_windows", {})    # youtube video id -> [[start, end], ...]
    data.setdefault("last_posted", {})     # streamer name -> unix time
    data.setdefault("uploads", [])         # history of uploaded videos
    return data


def save(data: dict) -> None:
    # keep the file small: forget ids older than 60 days
    cutoff = time.time() - 60 * 86400
    data["used_ids"] = {k: v for k, v in data["used_ids"].items() if v >= cutoff}
    data["uploads"] = data["uploads"][-500:]
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")


def pick_sources(data: dict, streamers: list, n: int) -> list:
    """Least-recently-posted first; alternate international / Indian when possible."""
    ordered = sorted(streamers, key=lambda s: data["last_posted"].get(s["name"], 0))
    intl = [s for s in ordered if s["region"] == "intl"]
    india = [s for s in ordered if s["region"] == "india"]
    mixed = []
    while intl or india:
        for bucket in (intl, india) if len(intl) >= len(india) else (india, intl):
            if bucket:
                mixed.append(bucket.pop(0))
    # return the whole ordered list; caller takes sources until it has n clips
    return mixed


def posts_today(data: dict, warmup: list, full: int) -> int:
    """Posts allowed per day, ramping up from the channel's first upload."""
    if not data["uploads"]:
        return warmup[0][1] if warmup else full
    first = min(u.get("made", time.time()) for u in data["uploads"])
    days = (time.time() - first) / 86400
    for until, n in warmup:
        if days < until:
            return n
    return full
