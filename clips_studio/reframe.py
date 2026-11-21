"""Decide how to turn a 16:9 stream into a 9:16 Short, and build the ffmpeg filter for it.

Layouts:
  facecam  small webcam overlay found in a fixed spot -> webcam on top, gameplay below
  track    big face (IRL / full-cam) -> 9:16 window that follows the face
  fill     no usable face -> gameplay centred over a blurred copy of itself
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from . import config

YUNET = config.ROOT / "models" / "face_detection_yunet_2023mar.onnx"
CAM_H = 640                       # facecam panel height in the output (rest is gameplay)
BOTTOM_CUT = 0.10                 # ignore the bottom 10% of the stream (burned-in subs, alerts)


class FaceDetector:
    def __init__(self):
        self.yunet = None
        if YUNET.exists():
            self.yunet = cv2.FaceDetectorYN.create(str(YUNET), "", (320, 320), 0.75, 0.3, 50)
        else:
            self.haar = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    def __call__(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        h, w = frame.shape[:2]
        scale = 960 / w
        small = cv2.resize(frame, (960, int(h * scale)))
        if self.yunet is not None:
            self.yunet.setInputSize((small.shape[1], small.shape[0]))
            _, faces = self.yunet.detect(small)
            boxes = [] if faces is None else [tuple(f[:4]) for f in faces]
        else:
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            boxes = self.haar.detectMultiScale(gray, 1.1, 6, minSize=(22, 22))
        return [tuple(int(v / scale) for v in b) for b in boxes]


def _sample(path: Path, start: float, end: float, step: float):
    cap = cv2.VideoCapture(str(path))
    t = start
    while t < end:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if ok:
            yield t, frame
        t += step
    cap.release()


def analyze(path: Path, start: float, end: float, W: int, H: int) -> dict:
    det = FaceDetector()
    step = max(0.5, (end - start) / 24)
    samples = [(t, det(f)) for t, f in _sample(path, start, end, step)]
    n = max(1, len(samples))

    # facecam: a small face that sits in roughly the same place in most frames
    small = [(t, b) for t, faces in samples for b in faces if b[2] < 0.16 * W]
    if small:
        grid = {}
        for t, (x, y, w, h) in small:
            key = (round((x + w / 2) / W * 10), round((y + h / 2) / H * 10))
            grid.setdefault(key, []).append((x, y, w, h))
        key, boxes = max(grid.items(), key=lambda kv: len(kv[1]))
        if len(boxes) >= 0.4 * n:
            x, y, w, h = np.median(np.array(boxes), axis=0)
            return {"layout": "facecam", "cam": _cam_box(x, y, w, h, W, H)}

    # track: a big face (IRL streams, full-screen cam)
    big = [(t, max(faces, key=lambda b: b[2] * b[3])) for t, faces in samples
           if faces and max(b[2] for b in faces) >= 0.08 * W]
    if len(big) >= 0.5 * n:
        pts = [(t - start, x + w / 2) for t, (x, y, w, h) in big]
        return {"layout": "track", "path": _smooth(pts, end - start)}

    return {"layout": "fill"}


def _cam_box(x, y, w, h, W, H):
    """Webcam region around the face, with the output panel's aspect ratio."""
    aspect = config.OUT_W / CAM_H                      # ~1.69
    cw = min(W, w * 3.6)
    ch = cw / aspect
    if ch > H:
        ch, cw = H, H * aspect
    cx, cy = x + w / 2, y + h / 2 + h * 0.35           # include shoulders
    x0 = int(np.clip(cx - cw / 2, 0, W - cw))
    y0 = int(np.clip(cy - ch / 2, 0, H - ch))
    return [x0, y0, int(cw) // 2 * 2, int(ch) // 2 * 2]


def _smooth(pts, dur, every=2.0):
    """Face x-centre every `every` seconds, gently smoothed (no jittery camera)."""
    ts = np.array([p[0] for p in pts])
    xs = np.array([p[1] for p in pts])
    grid = np.arange(0, dur + every, every)
    vals = np.interp(grid, ts, xs)
    k = np.array([1, 2, 3, 2, 1], dtype=float)
    k /= k.sum()
    vals = np.convolve(np.pad(vals, 2, mode="edge"), k, mode="valid")
    return [(float(t), float(v)) for t, v in zip(grid, vals)]


def _x_expr(path, cw, W):
    """Piecewise-linear crop x(t) as an ffmpeg expression."""
    def clamp(v):
        return float(np.clip(v - cw / 2, 0, W - cw))
    expr = f"{clamp(path[-1][1]):.1f}"
    for (t0, v0), (t1, v1) in reversed(list(zip(path, path[1:]))):
        a, b = clamp(v0), clamp(v1)
        expr = f"if(lt(t,{t1:.2f}),{a:.1f}+({b - a:.1f})*(t-{t0:.2f})/{t1 - t0:.2f},{expr})"
    return expr


def filter_graph(layout: dict, W: int, H: int) -> tuple[str, int]:
    """Returns (filter_complex producing [v], caption y position)."""
    OW, OH = config.OUT_W, config.OUT_H
    if W / H < 1.2:                                    # already vertical/square: just fit it
        g = (f"[0:v]split=2[a][b];[a]scale={OW}:{OH}:force_original_aspect_ratio=increase,"
             f"crop={OW}:{OH},boxblur=24:2[bg];[b]scale={OW}:{OH}:force_original_aspect_ratio=decrease[fg];"
             f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30[v]")
        return g, int(OH * 0.72)
    if layout["layout"] == "facecam":
        x, y, w, h = layout["cam"]
        gh = OH - CAM_H
        Hc = int(H * (1 - BOTTOM_CUT)) // 2 * 2
        gw = int(Hc * OW / gh) // 2 * 2                # gameplay crop with the panel's aspect
        gw = min(gw, W)
        gx = (W - gw) // 2
        g = (f"[0:v]split=2[a][b];"
             f"[a]crop={w}:{h}:{x}:{y},scale={OW}:{CAM_H}:flags=lanczos,setsar=1[cam];"
             f"[b]crop={gw}:{Hc}:{gx}:0,scale={OW}:{gh}:flags=lanczos,setsar=1[game];"
             f"[cam][game]vstack=inputs=2,fps=30[v]")
        return g, CAM_H + 150
    if layout["layout"] == "track":
        cw = int(H * 9 / 16) // 2 * 2
        xe = _x_expr(layout["path"], cw, W)
        g = f"[0:v]crop={cw}:{H}:'{xe}':0,scale={OW}:{OH}:flags=lanczos,setsar=1,fps=30[v]"
        return g, int(OH * 0.70)
    # fill
    fw = int(W * 0.72) // 2 * 2
    fx = (W - fw) // 2
    Hc = int(H * (1 - BOTTOM_CUT)) // 2 * 2
    fh = int(round(Hc * OW / fw / 2)) * 2
    g = (f"[0:v]split=2[a][b];"
         f"[a]scale={OW}:{OH}:force_original_aspect_ratio=increase,crop={OW}:{OH},"
         f"boxblur=24:2,eq=brightness=-0.12[bg];"
         f"[b]crop={fw}:{Hc}:{fx}:0,scale={OW}:{fh}:flags=lanczos,setsar=1[fg];"
         f"[bg][fg]overlay=0:(H-h)/2,setsar=1,fps=30[v]")
    return g, (OH + fh) // 2 + 170
