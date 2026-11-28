"""Text hygiene for anything burned into the video or put in the title.

* Swear words are masked (F*CK): profanity in the title or the first seconds of a
  Short gets it limited ads on YouTube; masked words keep the energy without that.
* Slurs are never shown: lines containing them are dropped as hooks and blanked in captions.
* Twitch emote names (LUL, KEKW, …) are removed from titles — they mean nothing on YouTube.
"""
from __future__ import annotations

import re

SWEARS = {
    "fuck": "F*CK", "fucking": "F*CKING", "fucked": "F*CKED", "fucker": "F*CKER", "motherfucker": "MOTHERF*CKER",
    "shit": "SH*T", "shitting": "SH*TTING", "bullshit": "BULLSH*T", "bitch": "B*TCH", "bitches": "B*TCHES",
    "asshole": "A**HOLE", "dick": "D*CK", "pussy": "P*SSY", "cunt": "C*NT", "damn": "DAMN", "wtf": "WTF",
}
SLURS = re.compile(r"\b(n+i+g+(a|er|ga|gah)s?|f+a+g+(got)?s?|retard(ed)?s?|tranny)\b", re.I)
EMOTES = re.compile(r"\b(LUL|LULW|OMEGALUL|KEKW|KEK|Pog|PogU|POGGERS|PogChamp|monkaS|monkaW|Kappa|Sadge|ICANT|"
                    r"4Head|Clueless|Aware|xdd|xddd|EZ|Copium|Prayge|PepeLaugh|pepeLaugh|Kreygasm|BibleThump|"
                    r"ResidentSleeper|WutFace|NotLikeThis|D:|catJAM|LMAO+|LMFAO+)\b")


def has_slur(text: str) -> bool:
    return bool(SLURS.search(text or ""))


def mask(text: str) -> str:
    def rep(m):
        w = m.group(0)
        r = SWEARS.get(w.lower())
        if r is None:
            return w
        return r if w.isupper() or len(w) <= 1 else (r.capitalize() if w[0].isupper() else r.lower())
    text = SLURS.sub("***", text or "")
    return re.sub(r"[A-Za-z]+", rep, text)


def clean_title(text: str) -> str:
    t = EMOTES.sub("", text or "")
    t = re.sub(r"\(\s*\)|\[\s*\]", "", t)
    return mask(re.sub(r"\s+", " ", t).strip(" -|:"))
