"""Daily run: pick streamers -> find moments -> make Shorts -> schedule uploads.

python -m clips_studio.run                  # normal (preview mode until YouTube is connected)
python -m clips_studio.run --dry-run        # never upload
python -m clips_studio.run --only xQc --count 1
python -m clips_studio.run --local some.mp4 # render a local file (testing)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
import traceback
from pathlib import Path

from . import captions, config, highlights, reframe, render, sources, state, titles


def make_clip(m: sources.Moment, workdir: Path, outdir: Path, idx: int) -> dict:
    stem = workdir / f"src_{idx}"
    print(f"   downloading {m.page_url} " + ("" if m.whole else f"[{m.start:.0f}s-{m.end:.0f}s]"))
    src = m.meta.get("local_file") or sources.download_moment(m, stem)
    return build_clip(Path(src), m.streamer, m.whole, m.meta, m.source_title, m.credit_url, outdir, idx)


def build_clip(src: Path, streamer: dict, whole: bool, meta: dict, source_title: str,
               credit_url: str, outdir: Path, idx: int) -> dict:
    info = sources.probe(src)
    W, H, dur = info["w"], info["h"], info["dur"]

    print("   transcribing")
    tr = captions.transcribe(src)
    if whole:
        start, end = 0.0, min(dur, config.CLIP_MAX_SEC)
    else:
        start, end = highlights.refine_bounds(tr["segments"], meta["local_start"], meta["local_end"], dur)
    if end - start < config.CLIP_MIN_SEC:
        raise ValueError(f"clip too short ({end - start:.1f}s)")

    print("   analysing layout")
    layout = reframe.analyze(src, start, end, W, H)
    graph, cap_y = reframe.filter_graph(layout, W, H)
    print(f"   layout={layout['layout']} lang={tr['language']} len={end - start:.1f}s")

    slug = f"{idx:02d}_{''.join(c for c in streamer['name'] if c.isalnum())}"
    ass = outdir / f"{slug}.ass"
    n_caps = captions.write_ass(tr, start, end, cap_y, ass)
    out = render.render(src, start, end, graph, ass if n_caps else None, outdir / f"{slug}.mp4")
    if n_caps:
        ass.unlink(missing_ok=True)

    spoken = " ".join(s["text"] for s in tr["segments"] if s["end"] > start and s["start"] < end)
    md = titles.make(streamer, source_title, streamer["kind"], spoken, credit_url)
    md.update({"streamer": streamer["name"], "layout": layout["layout"], "language": tr["language"],
               "seconds": round(end - start, 1), "transcript": spoken, "file": out.name})
    (outdir / f"{slug}.json").write_text(json.dumps(md, indent=1, ensure_ascii=False))
    return md | {"path": str(out)}


def collect(data: dict, count: int, only: list[str], workdir: Path, outdir: Path) -> list[tuple]:
    pool = config.STREAMERS
    if only:
        pool = [s for s in pool if s["name"].lower() in {o.lower() for o in only}]
    made = []
    for s in state.pick_sources(data, pool, count):
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
        for m in moments[:2]:                      # second chance if the first fails
            try:
                md = make_clip(m, workdir, outdir, len(made) + 1)
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
    count = args.count or state.posts_today(data, config.WARMUP, config.POSTS_PER_DAY)
    print(f"Posts this run: {count}" + ("" if args.count else " (warm-up schedule)"))
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
            data["uploads"].append({"youtube": vid, "streamer": md["streamer"], "title": md["title"],
                                    "source": m.credit_url, "publish_at": when.isoformat(), "made": int(now)})
            print(f"   ⬆ {md['file']} -> https://youtu.be/{vid} (publishes {when:%d %b %H:%M} IST)")
        state.save(data)

    write_summary(made, dry, outdir, uploaded)
    shutil.rmtree(workdir, ignore_errors=True)
    if not made:
        print("No clips were produced.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
