#!/usr/bin/env python3
"""Extract frames from a video and tile them into numbered contact sheets.

Usage:
  python3 extract_frames.py video.mp4 --out frames --fps 2 --max-frames 240

Writes frames/frame_00001.jpg ..., frames/index.json (file + timestamp per
frame) and frames/sheet_01.jpg ... Each sheet tiles 12 frames with their
timestamps so a multimodal reviewer can look at many frames per image.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def duration_of(video):
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit("ffprobe failed: " + p.stderr[-500:])
    return float(p.stdout.strip())


def extract(video, out_dir, fps=2.0, max_frames=240, width=640):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("frame_*.jpg"):
        old.unlink()
    dur = duration_of(video)
    eff = min(fps, max_frames / max(dur, 0.1))
    p = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video),
                        "-vf", f"fps={eff:.5f},scale={width}:-2", "-q:v", "3", str(out / "frame_%05d.jpg")],
                       capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit("ffmpeg failed: " + p.stderr[-800:])
    files = sorted(out.glob("frame_*.jpg"))
    index = [{"file": str(f), "t": round(i / eff, 3)} for i, f in enumerate(files)]
    (out / "index.json").write_text(json.dumps({"fps": eff, "duration": dur, "frames": index}, indent=2), encoding="utf-8")
    return index, eff, dur


def contact_sheets(index, out_dir, cols=4, rows=3, thumb_w=480):
    out = Path(out_dir)
    for old in out.glob("sheet_*.jpg"):
        old.unlink()
    try:
        font = ImageFont.load_default(size=22)
    except TypeError:
        font = ImageFont.load_default()
    per = cols * rows
    sheets = []
    for n in range(0, len(index), per):
        chunk = index[n:n + per]
        thumbs = []
        for item in chunk:
            im = Image.open(item["file"]).convert("RGB")
            h = int(im.height * thumb_w / im.width)
            thumbs.append((im.resize((thumb_w, h)), item["t"]))
        th = max(t[0].height for t in thumbs)
        sheet = Image.new("RGB", (cols * thumb_w, rows * th), (16, 16, 16))
        d = ImageDraw.Draw(sheet)
        for k, (im, t) in enumerate(thumbs):
            x, y = (k % cols) * thumb_w, (k // cols) * th
            sheet.paste(im, (x, y))
            label = f"{int(t // 60):02d}:{t % 60:05.2f}"
            d.rectangle([x, y, x + 110, y + 28], fill=(0, 0, 0))
            d.text((x + 6, y + 2), label, fill=(255, 255, 255), font=font)
        path = out / f"sheet_{len(sheets) + 1:02d}.jpg"
        sheet.save(path, quality=88)
        sheets.append({"file": str(path), "from": chunk[0]["t"], "to": chunk[-1]["t"]})
    (out / "sheets.json").write_text(json.dumps(sheets, indent=2), encoding="utf-8")
    return sheets


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--out", default="frames")
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--max-frames", type=int, default=240)
    ap.add_argument("--width", type=int, default=640)
    a = ap.parse_args()
    index, eff, dur = extract(a.video, a.out, a.fps, a.max_frames, a.width)
    sheets = contact_sheets(index, a.out)
    print(f"{len(index)} frames at {eff:.2f} fps over {dur:.1f}s -> {a.out}")
    print(f"{len(sheets)} contact sheets: " + ", ".join(Path(s['file']).name for s in sheets))


if __name__ == "__main__":
    main()
