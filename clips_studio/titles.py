"""Packaging: on-screen hook + title + description + tags.

Four hook styles (chosen by the learning module): wait / quote / viewer / reaction.
Free templates by default; if the GEMINI_API_KEY secret is set, Gemini writes the hook
and title in the chosen style from what is actually said in the clip.
"""
from __future__ import annotations

import json
import os
import random
import re
import urllib.request

from . import words

GAMES = {
    "gta": "GTA", "grand theft auto": "GTA", "rp": "GTA RP", "nopixel": "GTA RP", "soulcity": "GTA RP",
    "minecraft": "Minecraft", "bgmi": "BGMI", "pubg": "PUBG", "free fire": "Free Fire",
    "valorant": "Valorant", "fortnite": "Fortnite", "cod": "COD", "warzone": "Warzone",
}


def detect_game(text: str) -> str | None:
    t = text.lower()
    for k, v in GAMES.items():
        if re.search(rf"\b{re.escape(k)}\b", t):
            return v
    return None


def _clean_title(t: str) -> str:
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[#@]\S+", "", t)
    t = re.sub(r"\s+", " ", t).strip(" -|")
    return t


REACTIONS = [
    "WASN'T READY FOR THIS", "COULDN'T BELIEVE IT", "LOST IT", "DID NOT SEE THIS COMING",
    "WENT CRAZY", "NEEDED A MINUTE", "CAN'T STOP LAUGHING", "WAS SPEECHLESS",
]
STYLE_BRIEF = {
    "wait": "The hook is a 'wait for it' tease that promises a payoff, e.g. 'WAIT FOR HIS REACTION'.",
    "quote": "The hook is the funniest/most shocking short line the streamer actually says in the clip.",
    "viewer": "The hook is a punchy curiosity headline about what happens, like a viral clip title.",
    "reaction": "The hook names the streamer and their reaction, e.g. 'KAI COULDN'T BREATHE'.",
}


def _gemini(streamer: str, source_title: str, transcript: str, style: str) -> dict | None:
    key = os.getenv("GEMINI_API_KEY")
    if not key or not transcript.strip():
        return None
    prompt = (
        f"You write YouTube Shorts packaging for gaming stream clips. Streamer: {streamer}. "
        f"Clip/stream title: {source_title!r}. What is said in the clip: {transcript[:1500]!r}.\n"
        f"{STYLE_BRIEF.get(style, '')}\n"
        "Return JSON only: {\"hook\": \"on-screen hook, max 6 words, no emoji\", "
        "\"title\": \"YouTube title, max 70 chars, at most 1 emoji, no hashtags, includes the streamer name\"}. "
        "Be accurate to the clip — never invent events. Curiosity beats description."
    )
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9}}).encode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}"
    try:
        req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=40) as r:
            data = json.load(r)
        out = json.loads(data["candidates"][0]["content"]["parts"][0]["text"])
        hook, title = str(out.get("hook", "")).strip(), str(out.get("title", "")).strip().strip('"')
        if not hook or not title:
            return None
        return {"hook": " ".join(hook.split()[:7]), "title": title[:90]}
    except Exception as e:  # noqa: BLE001 - packaging must never break the pipeline
        print(f"   gemini packaging failed: {str(e)[:120]}")
        return None


def _viewer_title(source_title: str) -> str | None:
    clean = words.clean_title(_clean_title(source_title))
    real_words = [w for w in re.findall(r"[A-Za-z\u0900-\u097F]+", clean) if len(w) >= 3]
    if 8 <= len(clean) <= 60 and len(real_words) >= 2:
        return clean
    return None


def _template(streamer: dict, source_title: str, kind: str, style: str, quote: str | None) -> tuple[str, str, str]:
    """(hook, title, style actually used) without any AI."""
    name = streamer["name"]
    viewer = _viewer_title(source_title) if kind == "twitch" else None
    reaction = random.choice(REACTIONS)
    if style == "quote" and quote:
        hook = f"“{quote}”"
    elif style == "viewer" and viewer:
        hook = viewer
    elif style == "wait":
        hook = random.choice(["WAIT FOR IT…", "WAIT FOR HIS REACTION", "WATCH TILL THE END", "KEEP WATCHING…"])
    else:
        style = "reaction"
        hook = f"{name.split()[0]} {reaction}"
    title = f"{name}: {viewer}" if viewer else f"{name} {reaction.title()} {random.choice(['😳', '😂', '💀', '🔥', '😭'])}"
    return hook, title, style


def make(streamer: dict, source_title: str, kind: str, transcript: str, credit_url: str,
         style: str = "reaction", quote: str | None = None) -> dict:
    name = streamer["name"]
    game = detect_game(source_title + " " + transcript[:300])
    ai = _gemini(name, source_title, transcript, style)
    if ai:
        hook, title, packaging = ai["hook"], ai["title"], "ai"
    else:
        hook, title, style = _template(streamer, source_title, kind, style, quote)
        packaging = "template"
    hook = words.mask(hook)
    title = words.clean_title(title)[:90].rstrip()
    if "#shorts" not in title.lower() and len(title) <= 90:
        title += " #shorts"

    channel = (f"https://www.twitch.tv/{streamer['login']}" if streamer["kind"] == "twitch"
               else streamer["url"].rsplit("/", 1)[0])
    tags = [name, f"{name} clips", f"{name} funny moments", "shorts", "gaming", "stream highlights",
            "funny moments", "twitch clips"]
    if game:
        tags += [game, f"{game} funny moments"]
    hashtags = " ".join(["#shorts", "#" + re.sub(r"\W", "", name), "#gaming", "#twitchclips"]
                        + (["#" + re.sub(r"\W", "", game)] if game else []))
    desc = (
        f"{hook if packaging == 'ai' else title.replace(' #shorts', '')}\n\n"
        f"🎮 Clip from {name}'s stream.\n"
        f"▶ Original: {credit_url}\n"
        f"👉 Follow {name}: {channel}\n\n"
        f"💬 Who should we clip next? Tell us in the comments!\n"
        f"🔔 Subscribe for daily stream highlights.\n\n"
        f"All credit goes to {name}. Clipped, edited and captioned for highlights.\n\n"
        f"{hashtags}"
    )
    return {"title": title, "description": desc, "tags": tags[:15], "hook": hook,
            "hook_style": style, "packaging": packaging}
