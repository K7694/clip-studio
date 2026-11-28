# clip-studio

Fully automatic stream-highlight Shorts. Runs once a day on GitHub Actions — no PC needed.

```
pick 5 streamers (rotating) → find the hype moment → download only that part
→ vertical 9:16 (facecam-on-top / face-tracking / blurred fill) → word-by-word captions
→ loudness-normalised export → scheduled upload to YouTube (12:30, 15:30, 18:30, 20:30, 22:30 IST)
```

Separate from `you-shorts` — different repo, different channel, different Google Cloud project.

## How moments are found
* **Twitch** (xQc, Kai Cenat, summit1g, Buddha, CaseOh, Jynxzi, shroud, Tarik, Asmongold, Stable Ronaldo): the most-viewed viewer-made clips of the last 7 days — already the best moments.
* **YouTube** (supported, currently off — YouTube blocks cloud downloads without cookies): the audio of recent streams is scanned for the biggest sudden loudness spikes (screams, laughs, reactions). Only ~1 minute of video is downloaded.
* Cuts snap to sentence boundaries; nothing is ever posted twice (`state/used.json`).

**3 Shorts a day**, published at 22:30 / 01:30 / 06:30 IST (US peak hours). A second run 3 h after the
daily one fills in if the first failed; nothing is ever posted twice.

## What makes each Short
* **Best candidate wins:** the top 3 viewer clips per streamer are scored on reaction energy, views and length.
* **Flash-forward opener:** the loudest reaction plays for 1.6 s first, a white flash, then the clip from the start.
* **Hook headline** (4 styles: *wait for it*, the streamer's own line, viewer title, reaction line),
  zoom-ins on reactions, word-by-word captions, @tag, progress bar, follow/comment outro.
* Optional secret `GEMINI_API_KEY`: Gemini writes the hook + title in the chosen style from the transcript.

## Learning module (`clips_studio/learn.py`)
Every run pulls views/likes/comments (and retention, if the Analytics scope is granted) for recent Shorts,
scores each one at the same age, and steers the next picks — streamers, hook style, flash-forward on/off —
while still exploring 15 % of the time. The reasoning is in **[LEARNING.md](LEARNING.md)**, rewritten every run.

Edit streamers, posts per day and publish times in `clips_studio/config.py`.

## Preview mode
Until YouTube is connected every run is a **preview**: the clips are made but not uploaded.
Open the run in **Actions → clips-studio — daily Shorts → (run) → Artifacts → clips-N** to download them.
Run one by hand: Actions → *clips-studio — daily Shorts* → **Run workflow**.

## Connecting YouTube (once)
1. New Gmail → create the YouTube channel → verify it at youtube.com/verify.
2. console.cloud.google.com → new project `clips-studio` → enable **YouTube Data API v3**.
3. OAuth consent screen → External → add the Gmail as test user → **Publish app**
   (otherwise the login expires every 7 days).
4. Credentials → Create → OAuth client ID → **Desktop app** → download JSON → save as `client_secrets.json`.
5. `pip install google-auth-oauthlib && python auth_youtube.py` (or `--manual` on a machine without a browser).
6. Repo → Settings → Secrets and variables → Actions → **New repository secret**
   `YOUTUBE_TOKEN_JSON` = contents of `youtube_token.json`. Delete both local files.

Optional: secret `GEMINI_API_KEY` → AI-written titles (free tier). Without it, template titles are used.

## Limits worth knowing
* YouTube API: 10,000 free units/day, one upload ≈ 1,600 → max ~6 uploads/day per Google Cloud project.
* New Google Cloud projects may have API uploads **locked to private** until Google's free
  "YouTube API Services audit" is approved. If uploads stay private, submit that form.
* YouTube sometimes blocks downloads from cloud servers; Twitch does not. Failing sources are skipped for the day.

## Local run
```
pip install -r requirements.txt   # + ffmpeg on PATH
python -m clips_studio.run --dry-run --only xQc --count 1
```
