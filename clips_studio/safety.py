"""Content-safety gate: never post nudity or sexually suggestive scenes.

Streamers react to all kinds of things on screen (games, movies, other streams).
A clip that shows nudity or near-nudity can get a new channel age-restricted,
demonetised or struck, so every candidate is scanned before it is used.

Uses NudeNet (open source, model bundled in the pip package, runs on CPU) on one
frame every half second — the whole frame, its centre, and its left/right halves, so
small overlays and side-by-side layouts are caught too. Clip titles are checked as well.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import cv2

EXPOSED = {
    "FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED",
    "BUTTOCKS_EXPOSED", "ANUS_EXPOSED",
}
SUGGESTIVE = {"FEMALE_BREAST_COVERED", "BUTTOCKS_COVERED", "FEMALE_GENITALIA_COVERED"}
SKIN = {"BELLY_EXPOSED", "ARMPITS_EXPOSED", "FEET_EXPOSED"}


@lru_cache(maxsize=1)
def _detector():
    from nudenet import NudeDetector
    return NudeDetector()


def _frame_verdict(dets: list[dict]) -> str | None:
    """Thresholds are deliberately low: game/CGI nudity scores much lower than real photos,
    and a false alarm only costs us one candidate clip, while a miss can cost the channel."""
    c = {}
    for d in dets:
        c[d["class"]] = max(c.get(d["class"], 0.0), d["score"])
    for k in ("FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED", "ANUS_EXPOSED"):
        if c.get(k, 0) >= 0.22:
            return f"nudity ({k.lower()})"
    for k in ("FEMALE_BREAST_EXPOSED", "BUTTOCKS_EXPOSED"):
        if c.get(k, 0) >= 0.30:
            return f"nudity ({k.lower()})"
    belly, armpit = c.get("BELLY_EXPOSED", 0), c.get("ARMPITS_EXPOSED", 0)
    if belly >= 0.5 and (c.get("MALE_BREAST_EXPOSED", 0) >= 0.45 or armpit >= 0.3):
        return "undressed person (bare torso)"
    sug = max((c.get(k, 0) for k in SUGGESTIVE), default=0)
    skin = max(belly, armpit, c.get("FEET_EXPOSED", 0))
    if sug >= 0.5 and skin >= 0.25:
        return "suggestive (revealing clothing)"
    if sug >= 0.7:
        return "suggestive"
    return None


BAD_TITLE = re.compile(r"(nsfw|18\+|\bnude|naked|nudity|boob|tits|thicc|lewd|onlyfans|\bsexy|\bsex\b|strip(per|club)|"
                       r"porn|hentai|\bdick\b|\bcock\b|pussy|\bbutt\b|twerk|bikini|lingerie)", re.I)


def title_check(title: str) -> str | None:
    m = BAD_TITLE.search(title or "")
    return f"unsafe title ('{m.group(0)}')" if m else None


def check(path: Path, start: float = 0.0, end: float | None = None, step: float = 0.5) -> str | None:
    """None if the clip looks safe, else a short reason. Fails closed: no check, no post."""
    try:
        det = _detector()
    except Exception as e:  # noqa: BLE001
        return f"safety check unavailable ({str(e)[:60]})"
    cap = cv2.VideoCapture(str(path))
    if end is None:
        end = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / (cap.get(cv2.CAP_PROP_FPS) or 30)
    t, flagged = start, []
    while t < end:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if ok:
            h, w = frame.shape[:2]
            views = [frame, frame[h // 5: h - h // 5, w // 5: w - w // 5]]          # whole + centre
            if w > h:
                views += [frame[:, : w // 2], frame[:, w // 2:]]                     # left / right halves
            for v in views:
                why = _frame_verdict(det.detect(v))
                if why:
                    cap.release()
                    return f"{why} at {round(t, 1)}s"
        t += step
    cap.release()
    if flagged:
        t0, why = flagged[0]
        return f"{why} at {t0}s"
    return None
