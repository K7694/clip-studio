"""Content-safety gate: never post nudity or sexually suggestive scenes.

Streamers react to all kinds of things on screen (games, movies, other streams).
A clip that shows nudity or near-nudity can get a new channel age-restricted,
demonetised or struck, so every candidate is scanned before it is used.

Uses NudeNet (open source, model bundled in the pip package, runs on CPU) on one
frame per second — the whole frame plus its left and right halves, so small
overlays and side-by-side layouts are caught too.
"""
from __future__ import annotations

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
    classes = {}
    for d in dets:
        classes[d["class"]] = max(classes.get(d["class"], 0.0), d["score"])
    for c in EXPOSED:
        if classes.get(c, 0) >= 0.35:
            return f"nudity ({c.lower()})"
    sug = max((classes.get(c, 0) for c in SUGGESTIVE), default=0)
    skin = max((classes.get(c, 0) for c in SKIN), default=0)
    if sug >= 0.55 and skin >= 0.25:
        return "suggestive (revealing clothing)"
    if sug >= 0.75:
        return "suggestive"
    return None


def check(path: Path, start: float = 0.0, end: float | None = None, step: float = 1.0) -> str | None:
    """None if the clip looks safe, else a short reason. Fails open only if the model can't load."""
    try:
        det = _detector()
    except Exception as e:  # noqa: BLE001
        print(f"   (safety check unavailable: {e})")
        return None
    cap = cv2.VideoCapture(str(path))
    if end is None:
        end = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / (cap.get(cv2.CAP_PROP_FPS) or 30)
    t, flagged = start, []
    while t < end:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if ok:
            h, w = frame.shape[:2]
            views = [frame] + ([frame[:, : w // 2], frame[:, w // 2:]] if w > h else [])
            for v in views:
                why = _frame_verdict(det.detect(v))
                if why:
                    flagged.append((round(t, 1), why))
                    break
        t += step
    cap.release()
    if flagged:
        t0, why = flagged[0]
        return f"{why} at {t0}s"
    return None
