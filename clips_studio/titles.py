"""Title / description / tags. Free by default; uses Gemini if GEMINI_API_KEY is set."""
from __future__ import annotations

import json
import os
import random
import re
import urllib.request

GAMES = {
    "gta": "GTA", "grand theft auto": "GTA", "rp": "GTA RP", "nopixel": "GTA RP", "soulcity": "GTA RP",
    "minecraft": "Minecraft", "bgmi": "BGMI", "pubg": "PUBG", "free fire": "Free Fire",
    "valorant": "Valorant", "fortnite": "Fortnite", "cod": "COD", "warzone": "Warzone",
}
HOOKS = [
    "Couldn't Believe This 😳", "Lost It 😂", "Did NOT Expect This 💀", "Went Crazy 🤯",
    "This Was Insane 🔥", "Chat Went Wild 😂", "Wasn't Ready 😭", "Best Moment Ever 🔥",
]


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


def _gemini(streamer: str, source_title: str, transcript: str) -> str | None:
    key = os.getenv("GEMINI_API_KEY")
    if not key or not transcript.strip():
        return None
    prompt = (
        "Write ONE YouTube Shorts title (max 70 characters, 1 emoji max, no hashtags, no quotes) "
        f"for a clip of the streamer {streamer}. Stream title: {source_title!r}. "
        f"What is said in the clip: {transcript[:1200]!r}. "
        "Make it curiosity-driven and accurate to the clip. Reply with the title only."
    )
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}"
    try:
        req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        t = data["candidates"][0]["content"]["parts"][0]["text"].strip().strip('"')
        return t[:90] if t else None
    except Exception as e:  # noqa: BLE001 - titles must never break the pipeline
        print(f"   gemini title failed: {e}")
        return None


def make(streamer: dict, source_title: str, kind: str, transcript: str, credit_url: str) -> dict:
    name = streamer["name"]
    game = detect_game(source_title)
    title = _gemini(name, source_title, transcript)
    if not title:
        clean = _clean_title(source_title)
        if kind == "twitch" and 4 <= len(clean) <= 60:
            title = f"{name}: {clean}"                       # viewer-written clip titles are good hooks
        else:
            title = f"{name} {random.choice(HOOKS)}"
            if game:
                title = f"{title} | {game}"
    title = title[:90].rstrip()
    if "#shorts" not in title.lower() and len(title) <= 90:
        title += " #shorts"

    channel = (f"https://www.twitch.tv/{streamer['login']}" if streamer["kind"] == "twitch"
               else streamer["url"].rsplit("/", 1)[0])
    tags = [name, f"{name} clips", "shorts", "gaming", "stream highlights", "funny moments"]
    if game:
        tags += [game, f"{game} funny moments"]
    hashtags = " ".join(["#shorts", "#" + re.sub(r"\W", "", name), "#gaming"] + (["#" + re.sub(r"\W", "", game)] if game else []))
    desc = (
        f"🎮 Clip from {name}'s stream.\n"
        f"▶ Original: {credit_url}\n"
        f"👉 Follow {name}: {channel}\n\n"
        f"All credit goes to {name}. Clipped and captioned for highlights.\n\n"
        f"{hashtags}"
    )
    return {"title": title, "description": desc, "tags": tags[:15]}
