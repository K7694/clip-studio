"""Learning module — measure every Short, learn what works, steer the next picks.

Every upload is stored with its "features":
    streamer, hook_style, teaser (flash-forward yes/no), layout, length bucket, publish slot
Every run pulls fresh stats from YouTube (views / likes / comments, and retention
when the Analytics scope is granted) and turns them into a score per Short:

    score = log10(views + 1)  +  small engagement bonus (likes, comments)

Scores are compared at the same age (first reading after 48 h, else latest
after 24 h), so a fresh Short is never judged against a week-old one.

Decisions it steers (Thompson-style sampling, so it keeps exploring):
    * which streamers get today's slots
    * which hook style each Short uses
    * whether the Short opens with a flash-forward teaser

Everything is written to LEARNING.md in the repo after every run, so the
reasoning is visible on GitHub (and on a phone).
"""
from __future__ import annotations

import math
import random
import statistics
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

HOOK_STYLES = ["wait", "quote", "viewer", "reaction"]
HOOK_LABELS = {
    "wait": "WAIT FOR IT…",
    "quote": "streamer's own line",
    "viewer": "viewer clip title",
    "reaction": "<name> reaction line",
}
MIN_AGE_H = 24          # don't judge a Short before it had a day
MIN_SCORED = 6          # below this, explore evenly instead of exploiting
EXPLORE = 0.15          # always keep testing alternatives this often
REPORT = config.ROOT / "LEARNING.md"
IST = timezone(timedelta(hours=5, minutes=30))


# ── measuring ──────────────────────────────────────────────────────────────
def _published_ts(u: dict) -> float:
    try:
        return datetime.fromisoformat(u["publish_at"]).timestamp()
    except (KeyError, ValueError):
        return float(u.get("made", time.time()))


def age_h(u: dict) -> float:
    return (time.time() - _published_ts(u)) / 3600


def refresh(data: dict, get_stats, get_retention=None) -> int:
    """Pull fresh numbers for Shorts published in the last 3 weeks."""
    todo = [u for u in data["uploads"] if u.get("youtube") and 0 < age_h(u) < 24 * 21]
    if not todo:
        return 0
    ids = [u["youtube"] for u in todo]
    stats = get_stats(ids)
    ret = {}
    if get_retention:
        try:
            ret = get_retention(ids)
        except Exception as e:  # noqa: BLE001 - analytics is optional
            print(f"   (retention unavailable: {str(e)[:80]})")
    for u in todo:
        s = stats.get(u["youtube"])
        if not s:
            continue
        a = age_h(u)
        u["stats"] = dict(s, age_h=round(a, 1), at=int(time.time()))
        if a >= 48 and "views_48h" not in u:
            u["views_48h"] = s["views"]
        if u["youtube"] in ret:
            u["retention"] = ret[u["youtube"]]
    return len(todo)


def score(u: dict) -> float | None:
    s = u.get("stats")
    if not s or s.get("age_h", 0) < MIN_AGE_H:
        return None
    views = u.get("views_48h", s.get("views", 0))
    eng = (s.get("likes", 0) + 3 * s.get("comments", 0)) / max(views, 1)
    bonus = min(eng * 5, 0.4)
    if "retention" in u and u["retention"].get("avg_pct") is not None:
        bonus += (u["retention"]["avg_pct"] - 70) / 200          # ±0.15 around 70 % viewed
    return math.log10(views + 1) + bonus


def _scored(data: dict) -> list[tuple[dict, float]]:
    return [(u, sc) for u in data["uploads"] if (sc := score(u)) is not None]


# ── deciding ───────────────────────────────────────────────────────────────
def _arm_table(data: dict, key: str) -> dict:
    groups = defaultdict(list)
    for u, sc in _scored(data):
        if key in u:
            groups[u[key]].append(sc)
    return groups


def _sample(groups: dict, values: list, rng: random.Random) -> dict:
    allv = [x for v in groups.values() for x in v]
    prior = statistics.mean(allv) if allv else 2.0
    sd = statistics.pstdev(allv) if len(allv) > 1 else 0.6
    sd = max(sd, 0.3)
    out = {}
    for v in values:
        xs = groups.get(v, [])
        k = 2                                                   # prior weight (pseudo-observations)
        mean = (sum(xs) + prior * k) / (len(xs) + k)
        out[v] = rng.gauss(mean, sd / math.sqrt(len(xs) + k))
    return out


def order_streamers(data: dict, pool: list, rng: random.Random | None = None) -> list:
    """Best-looking streamers first, but nobody twice within ~20 h, and always one explorer."""
    rng = rng or random.Random()
    by_recent = sorted(pool, key=lambda s: data["last_posted"].get(s["name"], 0))
    if len(_scored(data)) < MIN_SCORED:
        return by_recent                                         # not enough data yet: rotate
    samples = _sample(_arm_table(data, "streamer"), [s["name"] for s in pool], rng)
    ranked = sorted(pool, key=lambda s: -samples[s["name"]])
    cutoff = time.time() - 20 * 3600
    fresh = [s for s in ranked if data["last_posted"].get(s["name"], 0) < cutoff]
    stale = [s for s in ranked if s not in fresh]
    ordered = fresh + stale
    explorer = by_recent[0]                                      # least recently used gets a seat
    if explorer in ordered[:3]:
        return ordered
    return ordered[:2] + [explorer] + [s for s in ordered[2:] if s is not explorer]


def pick_hook_style(data: dict, rng: random.Random | None = None) -> str:
    rng = rng or random.Random()
    groups = _arm_table(data, "hook_style")
    if sum(len(v) for v in groups.values()) < MIN_SCORED or rng.random() < EXPLORE:
        return rng.choice(HOOK_STYLES)
    s = _sample(groups, HOOK_STYLES, rng)
    return max(s, key=s.get)


def pick_teaser(data: dict, rng: random.Random | None = None) -> bool:
    rng = rng or random.Random()
    groups = _arm_table(data, "teaser")
    if sum(len(v) for v in groups.values()) < MIN_SCORED:
        return rng.random() < 0.8                                # believed to help: mostly on
    if rng.random() < EXPLORE:
        return rng.random() < 0.5
    s = _sample(groups, [True, False], rng)
    return s[True] >= s[False]


def scheduled_ahead(data: dict) -> int:
    """Shorts uploaded but not yet public. The daily run tops this up to the daily target,
    so a late cron, a retry run or an extra manual run never double-posts or under-posts."""
    now = time.time()
    return sum(1 for u in data["uploads"] if u.get("youtube") and _published_ts(u) > now)


def length_bucket(sec: float) -> str:
    return "<20s" if sec < 20 else "20-35s" if sec < 35 else "35-50s" if sec < 50 else "50s+"


# ── reporting ──────────────────────────────────────────────────────────────
def _table(data: dict, key: str, label: str, fmt=str) -> list[str]:
    groups = defaultdict(list)
    counts = defaultdict(int)
    for u in data["uploads"]:
        if key in u:
            counts[u[key]] += 1
    for u, sc in _scored(data):
        if key in u:
            groups[u[key]].append(u.get("views_48h", u["stats"]["views"]))
    if not counts:
        return []
    rows = sorted(counts, key=lambda k: -(statistics.median(groups[k]) if groups[k] else -1))
    out = [f"\n### By {label}\n", f"| {label} | Shorts | Measured | Median views | Best |", "|---|---|---|---|---|"]
    for k in rows:
        g = groups[k]
        med = f"{statistics.median(g):,.0f}" if g else "–"
        best = f"{max(g):,}" if g else "–"
        out.append(f"| {fmt(k)} | {counts[k]} | {len(g)} | {med} | {best} |")
    return out


def write_report(data: dict, target_views: int = 10_000) -> str:
    ups = data["uploads"]
    total = sum(u.get("stats", {}).get("views", 0) for u in ups)
    now_ist = datetime.now(IST)
    month = [u for u in ups if datetime.fromtimestamp(_published_ts(u), IST).month == now_ist.month]
    month_views = sum(u.get("stats", {}).get("views", 0) for u in month)
    lines = [
        "# 📈 clip-studio learning report",
        f"_Updated {now_ist:%d %b %Y %H:%M} IST — rewritten after every run._\n",
        f"- **Shorts uploaded:** {len(ups)}  ·  measured (≥{MIN_AGE_H} h old): {len(_scored(data))}",
        f"- **Total views:** {total:,}  ·  this month: {month_views:,} (goal {target_views:,})",
    ]
    if len(_scored(data)) < MIN_SCORED:
        lines.append(f"- **Mode:** exploring — needs {MIN_SCORED} measured Shorts before it starts favouring winners.")
    else:
        lines.append("- **Mode:** learning — favours what's working, still tries something new every day.")
    top = sorted(((u, u.get("stats", {}).get("views", 0)) for u in ups), key=lambda x: -x[1])[:5]
    if top and top[0][1] > 0:
        lines += ["\n### Top Shorts\n", "| Views | Title | Streamer | Hook |", "|---|---|---|---|"]
        for u, v in top:
            lines.append(f"| {v:,} | [{u.get('title', '')[:60]}](https://youtu.be/{u['youtube']}) | "
                         f"{u.get('streamer', '')} | {HOOK_LABELS.get(u.get('hook_style'), '–')} |")
    lines += _table(data, "streamer", "Streamer")
    lines += _table(data, "hook_style", "Hook style", lambda k: HOOK_LABELS.get(k, k))
    lines += _table(data, "teaser", "Flash-forward opener", lambda k: "yes" if k else "no")
    lines += _table(data, "length", "Length")
    lines += _table(data, "slot", "Publish time (IST)")
    lines += _table(data, "layout", "Layout")
    text = "\n".join(lines) + "\n"
    REPORT.write_text(text)
    return text
