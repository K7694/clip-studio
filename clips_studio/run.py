"""Daily run: pick streamers -> find moments -> make Shorts -> schedule uploads.

python -m clips_studio.run                  # normal (preview mode until YouTube is connected)
python -m clips_studio.run --dry-run        # never upload
python -m clips_studio.run --only xQc --count 1
python -m clips_studio.run --local some.mp4 # render a local file (testing)
"""
from __future__ import annotations

import argparse
import json
import re
import os
import shutil
import time
import traceback
from pathlib import Path

import math
import random

import numpy as np

from . import captions, config, effects, highlights, learn, reframe, render, safety, sources, state, titles


def make_clip(m: sources.Moment, workdir: Path, outdir: Path, idx: int, choices: dict) -> dict:
    stem = workdir / ("src_" + re.sub(r"\W+", "_", m.media_id))
    src = m.meta.get("local_file")
    if not src:
        print(f"   downloading {m.page_url} " + ("" if m.whole else f"[{m.start:.0f}s-{m.end:.0f}s]"))
        src = sources.download_moment(m, stem)
    return build_clip(Path(src), m.streamer, m.whole, m.meta, m.source_title, m.credit_url, outdir, idx, choices)


def build_clip(src: Path, streamer: dict, whole: bool, meta: dict, source_title: str,
               credit_url: str, outdir: Path, idx: int, choices: dict | None = None) -> dict:
    choices = choices or {"hook_style": "reaction", "teaser": True}
    info = sources.probe(src)
    W, H, dur = info["w"], info["h"], info["dur"]

    print("   transcribing")
    tr = captions.transcribe(src)
    if whole:
        start, end = 0.0, min(dur, config.CLIP_MAX_SEC)
    else:
        start, end = highlights.refine_bounds(tr["segments"], meta["local_start"], meta["local_end"], dur)
    if end - start < (config.TWITCH_MIN_SEC if whole else config.CLIP_MIN_SEC):
        raise ValueError(f"clip too short ({end - start:.1f}s)")
    unsafe = safety.title_check(source_title) or safety.check(src, start, end)
    if unsafe:
        raise ValueError(f"skipped for safety: {unsafe}")

    # flash-forward: open on the loudest reaction for 1.6 s, then play the clip from the start
    peak = effects.best_moment(src, start, end)
    teaser = None
    if choices.get("teaser") and peak is not None:
        T = effects.TEASER_SEC
        t0 = max(start, start + peak - 1.0)
        teaser = (t0, t0 + T)
        end = min(end, start + config.CLIP_MAX_SEC - T)        # keep the whole Short under ~55 s
    T = (teaser[1] - teaser[0]) if teaser else 0.0
    segments = ([teaser] if teaser else []) + [(start, end)]
    total = T + (end - start)

    print("   analysing layout")
    layout = reframe.analyze(src, start, end, W, H)
    graph, cap_y = reframe.filter_graph(layout, W, H)
    print(f"   layout={layout['layout']} lang={tr['language']} len={total:.1f}s teaser={'yes' if teaser else 'no'}")

    spoken = " ".join(s["text"] for s in tr["segments"] if s["end"] > start and s["start"] < end)
    quote = effects.pick_quote(tr["segments"], start, end, peak)
    md = titles.make(streamer, source_title, streamer["kind"], spoken, credit_url,
                     style=choices.get("hook_style", "reaction"), quote=quote)

    slug = f"{idx:02d}_{''.join(c for c in streamer['name'] if c.isalnum())}"
    ass = outdir / f"{slug}.ass"
    seg_map = ([(teaser[0], teaser[1], 0.0)] if teaser else []) + [(start, end, T)]
    captions.write_ass(tr, seg_map, cap_y, ass)
    hook = effects.format_hook(md["hook"])
    effects.add_overlays(ass, total, streamer["name"], hook)
    punches = [round(p + T, 2) for p in effects.punch_points(src, start, end)]
    if teaser:
        punches = [0.9] + [p for p in punches if p > 3]          # zoom on the teaser's peak too
    post = effects.post_filter(punches, total, flash_at=T if teaser else None)
    out = render.render(src, segments, graph, ass, outdir / f"{slug}.mp4", post=post)
    ass.unlink(missing_ok=True)
    md["edits"] = {"hook": hook.replace(r"\N", " "), "zooms_at": punches, "teaser": bool(teaser)}
    md.update({"streamer": streamer["name"], "layout": layout["layout"], "language": tr["language"],
               "seconds": round(total, 1), "transcript": spoken, "file": out.name,
               "teaser": bool(teaser)})
    (outdir / f"{slug}.json").write_text(json.dumps(md, indent=1, ensure_ascii=False))
    return md | {"path": str(out)}


def clip_energy(path: Path) -> float:
    """How 'eventful' a clip sounds: loud spikes over its own baseline (dB)."""
    try:
        c = highlights.loudness_curve(path)
    except Exception:  # noqa: BLE001
        return 0.0
    if len(c) < 10:
        return 0.0
    return float(np.percentile(c, 98) - np.median(c))


def rank_twitch(moments: list, workdir: Path, k: int = 3) -> list:
    """Download the top-k viewer clips and rank them: reaction energy + popularity + length."""
    scored = []
    for m in moments[:k]:
        stem = workdir / ("src_" + re.sub(r"\W+", "_", m.media_id))
        try:
            path = sources.download_moment(m, stem)
        except Exception as e:  # noqa: BLE001
            print(f"   ✗ download failed: {str(e)[:100]}")
            continue
        m.meta["local_file"] = str(path)
        d = sources.probe(path)["dur"]
        e = clip_energy(path)
        views = m.meta.get("views") or 0
        sc = e + 3 * math.log10(views + 10) - (6 if d < 12 else 0) + (2 if 18 <= d <= 45 else 0)
        print(f"   candidate {d:4.1f}s energy={e:4.1f}dB views={views:<7} score={sc:5.1f}  {m.source_title[:40]}")
        scored.append((sc, m))
    return [m for _, m in sorted(scored, key=lambda x: -x[0])] + moments[k:]


def collect(data: dict, count: int, only: list[str], workdir: Path, outdir: Path) -> list[tuple]:
    pool = config.STREAMERS
    if only:
        pool = [s for s in pool if s["name"].lower() in {o.lower() for o in only}]
    made = []
    rng = random.Random()
    for s in learn.order_streamers(data, pool, rng):
        if len(made) >= count:
            break
        print(f"\n▶ {s['name']} ({s['kind']})")
        try:
            if s["kind"] == "twitch":
                moments = sources.twitch_moments(s, data["used_ids"])
            else:
                moments = sources.youtube_moments(s, data["used_ids"], data["used_windows"], workdir)
        except Exception as e:  # noqa: BLE001 - one bad source must not stop the day
            print(f"   ✗ could not list moments: {e}")
            continue
        if not moments:
            print("   ✗ no new moments")
            continue
        if s["kind"] == "twitch":
            moments = rank_twitch(moments, workdir)
        choices = {"hook_style": learn.pick_hook_style(data, rng), "teaser": learn.pick_teaser(data, rng)}
        print(f"   choices: hook={choices['hook_style']} teaser={choices['teaser']}")
        for m in moments[:4]:                      # more chances if one fails
            try:
                md = make_clip(m, workdir, outdir, len(made) + 1, choices)
                print(f"   ✓ {md['title']}")
                made.append((m, md))
                break
            except Exception as e:  # noqa: BLE001
                print(f"   ✗ {e}")
                traceback.print_exc(limit=2)
    return made


def write_summary(made: list[tuple], dry: bool, outdir: Path, uploaded: dict) -> None:
    lines = [f"# clips-studio run — {'PREVIEW (not uploaded)' if dry else 'uploaded'}\n",
             "| # | Streamer | Layout | Length | Title | YouTube |", "|---|---|---|---|---|---|"]
    for i, (m, md) in enumerate(made, 1):
        yt = uploaded.get(md["file"], "")
        lines.append(f"| {i} | {md['streamer']} | {md['layout']} | {md['seconds']}s | {md['title']} | {yt} |")
    text = "\n".join(lines) + "\n"
    (outdir / "SUMMARY.md").write_text(text)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(text)
    print("\n" + text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--count", type=int, default=None)
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--local", help="render a local video file (testing)")
    ap.add_argument("--check", action="store_true", help="only test the YouTube login, stats and analytics")
    args = ap.parse_args()
    dry = args.dry_run or config.DRY_RUN

    workdir, outdir = config.WORK_DIR, config.OUT_DIR
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True)
    outdir.mkdir(parents=True, exist_ok=True)

    if args.local:
        s = {"name": "Test", "kind": "twitch", "login": "test", "region": "intl"}
        md = build_clip(Path(args.local), s, True, {}, "GTA RP test clip", "https://example.com", outdir, 1)
        print(json.dumps(md, indent=1, ensure_ascii=False))
        return 0

    data = state.load()
    if args.check:
        from . import upload
        print(f"YouTube channel: {upload.channel_title()}")
        ids = [u["youtube"] for u in data["uploads"] if u.get("youtube")]
        print(f"Stats access: OK ({len(upload.stats(ids))} of {len(ids)} videos readable)")
        try:
            upload.retention(ids or ["none"])
            print("Analytics access: OK (retention data will be used for learning)")
        except Exception as e:  # noqa: BLE001
            print(f"Analytics access: NOT available ({str(e)[:120]})")
        return 0
    if not dry:
        from . import upload
        print(f"YouTube channel: {upload.channel_title()}")
        try:
            n = learn.refresh(data, upload.stats, upload.retention)
            print(f"Learning: refreshed stats for {n} Shorts")
        except Exception as e:  # noqa: BLE001 - learning must never block posting
            print(f"Learning: stats refresh failed ({str(e)[:100]})")

    if args.count:
        count = args.count
    else:
        target = state.posts_today(data, config.WARMUP, config.POSTS_PER_DAY)
        already = learn.scheduled_ahead(data)
        count = max(0, target - already)
        print(f"Daily target {target}, already scheduled and not yet public: {already}")
    print(f"Posts this run: {count}")
    if count == 0:
        learn.write_report(data)
        state.save(data)
        print("Nothing to do — today's Shorts are already scheduled.")
        return 0
    made = collect(data, count, args.only, workdir, outdir)
    uploaded = {}

    if made and not dry:
        from . import upload
        slots = upload.publish_times(len(made), {u.get("publish_at") for u in data["uploads"]})
        for (m, md), when in zip(made, slots):
            try:
                vid = upload.upload(Path(md["path"]), md, when)
            except Exception as e:  # noqa: BLE001
                print(f"   ✗ upload failed for {md['file']}: {e}")
                if "quota" in str(e).lower():
                    break
                continue
            uploaded[md["file"]] = f"https://youtu.be/{vid}"
            now = time.time()
            data["used_ids"][m.media_id] = now
            data["last_posted"][m.streamer["name"]] = now
            if "video_id" in m.meta:
                data["used_windows"].setdefault(m.meta["video_id"], []).append([m.start, m.end])
            data["uploads"].append({
                "youtube": vid, "streamer": md["streamer"], "title": md["title"], "hook": md.get("hook"),
                "source": m.credit_url, "publish_at": when.isoformat(), "made": int(now),
                # features the learning module compares
                "hook_style": md.get("hook_style"), "packaging": md.get("packaging"),
                "teaser": md.get("teaser", False), "layout": md.get("layout"),
                "length": learn.length_bucket(md.get("seconds", 0)), "slot": when.strftime("%H:%M"),
            })
            print(f"   ⬆ {md['file']} -> https://youtu.be/{vid} (publishes {when:%d %b %H:%M} IST)")
            try:
                print(f"     {upload.verify(vid)}")
            except Exception as e:  # noqa: BLE001
                print(f"     (could not verify: {str(e)[:80]})")
            state.save(data)                       # save after every upload: no repeats if a later one fails
        learn.write_report(data)
        state.save(data)

    write_summary(made, dry, outdir, uploaded)
    shutil.rmtree(workdir, ignore_errors=True)
    if not made:
        print("No clips were produced.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
