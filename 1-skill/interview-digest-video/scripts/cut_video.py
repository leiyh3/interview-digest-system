#!/usr/bin/env python3
"""Render an edit plan into a preview or final digest video with ffmpeg.

Usage:
  python3 cut_video.py plan.json --mode preview --transcript transcript.json --out-dir build
  python3 cut_video.py plan.json --mode final   --transcript transcript.json --out-dir build_final

Clip types (field "type", default "source"):
  source     a range of the interview. Edges snap to sentence boundaries; subtitles come
             from the transcript, one per sentence (Chinese from "zh", optional English line).
  narration  the account owner's own explanation: a voice file ("audio") or, before it is
             recorded, an estimated duration with a silent track. Subtitles come from "lines".
  card       a full-screen title card ("card" text), optional "audio" and "lines".

9:16 layout, three fixed zones, so title cards never collide with subtitles:
  top     persistent title bar ("style.title") and the clip's card text
  middle  the interview picture (source clips) or the big card (card/narration clips)
  bottom  subtitles, starting just under the picture
Source clips carry a source label (top right of the picture) and an optional speaker label.
Text in 【】 is drawn in the accent colour.

Every run writes <out-dir>/timeline.json (clip map, layout boxes, source ratio), which
tech_check.py reads.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ZH_CPS = 4.5            # estimated Chinese speaking speed, characters per second
SUB_CHUNK = 18          # max Chinese characters per subtitle line before splitting


def sh(cmd):
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit("command failed:\n" + " ".join(str(c) for c in cmd) + "\n" + p.stderr[-2500:])
    return p


def probe(path):
    p = sh(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path])
    info = json.loads(p.stdout)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    out = {"duration": float(info["format"]["duration"]), "audio": has_audio}
    if v is not None:
        out.update(width=v["width"], height=v["height"])
    return out


def load_segments(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    segs = data["segments"] if isinstance(data, dict) else data
    return [{"start": float(s["start"]), "end": float(s["end"]), "text": s.get("text", "").strip(),
             "zh": (s.get("zh") or "").strip()} for s in segs]


def snap_clip(clip, segs, tol):
    """Move clip edges onto the nearest sentence boundary within tol seconds."""
    notes = []
    s, e = clip["start"], clip["end"]
    ns = min(segs, key=lambda g: abs(g["start"] - s))
    ne = min(segs, key=lambda g: abs(g["end"] - e))
    if abs(ns["start"] - s) <= tol:
        if abs(ns["start"] - s) > 0.01:
            notes.append(f"start {s:.2f} -> {ns['start']:.2f}")
        clip["start"] = ns["start"]
    else:
        notes.append(f"WARN start {s:.2f} is not near a sentence start")
    if abs(ne["end"] - e) <= tol:
        if abs(ne["end"] - e) > 0.01:
            notes.append(f"end {e:.2f} -> {ne['end']:.2f}")
        clip["end"] = ne["end"]
    else:
        notes.append(f"WARN end {e:.2f} is not near a sentence end")
    return notes


# ---------- text rendering ----------

FALLBACK_FONTS = [   # used when fontconfig (fc-match) is not installed, e.g. on Windows
    "C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
]


def find_font(family, bold=True):
    pattern = family + (":style=Bold" if bold else "")
    try:
        p = subprocess.run(["fc-match", "-f", "%{file}|%{index}", pattern], capture_output=True, text=True)
        if p.returncode == 0 and "|" in p.stdout:
            f, idx = p.stdout.split("|", 1)
            return f, int(idx or 0)
    except FileNotFoundError:
        pass
    for f in FALLBACK_FONTS:
        if Path(f).exists():
            return f, 0
    sys.exit(f"cannot find font {family!r}; install it, or pass --font-file path/to/font.ttc")


class Fonts:
    def __init__(self, family, font_file=None):
        if font_file:
            self.bold = self.reg = (font_file, 0)
        else:
            self.bold = find_font(family, True)
            self.reg = find_font(family, False)
        self.cache = {}

    def get(self, size, bold=True):
        key = (size, bold)
        if key not in self.cache:
            f, i = self.bold if bold else self.reg
            self.cache[key] = ImageFont.truetype(f, size, index=i)
        return self.cache[key]


def parse_marked(text):
    """'前半【重点】后半' -> [(text, highlighted)]"""
    parts, buf, hi = [], "", False
    for ch in text:
        if ch == "【" and not hi:
            if buf:
                parts.append((buf, False))
            buf, hi = "", True
        elif ch == "】" and hi:
            if buf:
                parts.append((buf, True))
            buf, hi = "", False
        else:
            buf += ch
    if buf:
        parts.append((buf, hi))
    return parts


def wrap_marked(parts, font, max_w):
    """Greedy wrap by character; an explicit newline forces a break."""
    lines, cur, w = [], [], 0.0
    for txt, hi in parts:
        for ch in txt:
            if ch == "\n":
                lines.append(cur)
                cur, w = [], 0.0
                continue
            cw = font.getlength(ch)
            if cur and w + cw > max_w:
                lines.append(cur)
                cur, w = [], 0.0
            cur.append((ch, hi))
            w += cw
    if cur:
        lines.append(cur)
    return lines


def draw_block(draw, fonts, text, box, size, min_size, max_lines, fg, accent, align="left"):
    """Fit marked text into box=(x0,y0,x1,y1). Returns [x0,y0,x1,y1,font_px,lines] actually drawn."""
    if not text:
        return None
    x0, y0, x1, y1 = box
    parts = parse_marked(text)
    s = size
    while True:
        font = fonts.get(s)
        lines = wrap_marked(parts, font, x1 - x0)
        lh = int(s * 1.25)
        orphan = len(lines) > 1 and len(lines[-1]) <= 2      # avoid a lone character on the last line
        if (len(lines) <= max_lines and lh * len(lines) <= y1 - y0 and not orphan) or s <= min_size:
            break
        s -= 2
    total_h = lh * len(lines)
    y = y0 if align == "left" else y0 + max(0, (y1 - y0 - total_h) // 2)
    bx0, by0, bx1 = x1, y, x0
    for ln in lines:
        lw = sum(font.getlength(ch) for ch, _ in ln)
        x = x0 if align == "left" else x0 + (x1 - x0 - lw) / 2
        bx0, bx1 = min(bx0, x), max(bx1, x + lw)
        for ch, hi in ln:
            off = max(2, s // 22)
            draw.text((x + off, y + off), ch, font=font, fill=(0, 0, 0, 150))
            draw.text((x, y), ch, font=font, fill=accent if hi else fg)
            x += font.getlength(ch)
        y += lh
    return [int(bx0), int(by0), int(bx1), int(y), s, len(lines)]


def hex_rgb(h, alpha=255):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (alpha,)


def tag(draw, fonts, text, xy, size, fg, bg, anchor="left"):
    font = fonts.get(size, bold=False)
    pad = size // 2
    w = font.getlength(text) + 2 * pad
    h = int(size * 1.5)
    x, y = xy
    if anchor == "right":
        x -= w
    draw.rounded_rectangle([x, y, x + w, y + h], radius=size // 3, fill=bg)
    draw.text((x + pad, y + (h - size) / 2 - size * 0.08), text, font=font, fill=fg)
    return [int(x), int(y), int(x + w), int(y + h)]


def layout(W, H, src_w, src_h):
    k = W / 1080
    fw = W
    fh = int(round(W * src_h / src_w / 2) * 2)
    if fh > H * 0.55:                          # tall source: fit by height instead
        fh = int(H * 0.55) // 2 * 2
        fw = int(round(fh * src_w / src_h / 2) * 2)
    vtop = int(H * 0.47 - fh / 2) // 2 * 2
    return {
        "k": k, "fg_w": fw, "fg_h": fh, "video_top": vtop, "video_bottom": vtop + fh,
        "title_box": [int(60 * k), int(150 * k), W - int(60 * k), int(330 * k)],
        "card_box": [int(60 * k), int(345 * k), W - int(60 * k), vtop - int(28 * k)],
        "big_card_box": [int(80 * k), int(345 * k), W - int(80 * k), vtop + fh],
        "sub_top": vtop + fh + int(44 * k),
    }


def render_overlay(path, W, H, L, style, clip, fonts):
    k = L["k"]
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    fg, accent = hex_rgb(style.get("fg", "#FFFFFF")), hex_rgb(style.get("accent", "#FFC93C"))
    boxes = {}
    if style.get("title"):
        boxes["title"] = draw_block(d, fonts, style["title"], L["title_box"], int(64 * k), int(40 * k), 2, fg, accent)
    if clip["type"] == "source":
        if clip.get("card"):
            boxes["card"] = draw_block(d, fonts, clip["card"], L["card_box"], int(58 * k), int(36 * k), 2, fg, accent)
        label = clip.get("source_label") or style.get("source_label")
        if label:
            boxes["source_label"] = tag(d, fonts, label, (W - int(24 * k), L["video_top"] + int(20 * k)), int(26 * k),
                                        (255, 255, 255, 230), (0, 0, 0, 140), anchor="right")
        if clip.get("speaker"):
            boxes["speaker"] = tag(d, fonts, clip["speaker"], (int(24 * k), L["video_bottom"] - int(66 * k)),
                                   int(30 * k), (20, 20, 20, 255), accent)
    else:
        boxes["card"] = draw_block(d, fonts, clip.get("card", ""), L["big_card_box"], int(84 * k), int(48 * k), 5,
                                   fg, accent, align="center")
        if clip.get("source_label"):
            boxes["source_label"] = tag(d, fonts, clip["source_label"],
                                        (W - int(24 * k), L["big_card_box"][3] - int(50 * k)), int(26 * k),
                                        (255, 255, 255, 230), (0, 0, 0, 140), anchor="right")
    im.save(path)
    return boxes


# ---------- subtitles ----------

def split_chunks(text, limit=SUB_CHUNK):
    """Split a sentence at punctuation into display chunks of at most `limit` characters."""
    pieces = [p for p in re.split(r"(?<=[，。；！？、,.;!?])", text) if p.strip()]
    out, cur = [], ""
    for p in pieces:
        while len(p) > limit:
            if cur:
                out.append(cur)
                cur = ""
            out.append(p[:limit])
            p = p[limit:]
        if cur and len(cur) + len(p) > limit:
            out.append(cur)
            cur = p
        else:
            cur += p
    if cur.strip():
        out.append(cur)
    return [c.strip().rstrip("，、,。") for c in out if c.strip()]


def timed_chunks(text, t0, t1, en=""):
    chunks = split_chunks(text)
    if not chunks:
        return []
    total = sum(len(c) for c in chunks)
    res, t = [], t0
    for c in chunks:
        dt = (t1 - t0) * len(c) / total
        res.append({"start": t, "end": t + dt, "text": c, "en": en if len(chunks) == 1 else ""})
        t += dt
    return res


def ass_time(t):
    cs = int(round(max(t, 0) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(t):
    return t.replace("\\", "＼").replace("{", "（").replace("}", "）").replace("\n", " ")


def write_ass(path, subs, W, H, L, font, bilingual):
    k = L["k"]
    zh, en = int(66 * k), int(34 * k)
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        f"Style: Sub,{font},{zh},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,"
        f"{max(2, int(3 * k))},0,8,{int(60 * k)},{int(60 * k)},{L['sub_top']},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    lines = []
    for s in subs:
        txt = ass_escape(s["text"])
        if bilingual and s.get("en"):
            txt += "\\N{\\fs" + str(en) + "\\b0\\c&H00D0D0D0&}" + ass_escape(s["en"])
        lines.append(f"Dialogue: 0,{ass_time(s['start'])},{ass_time(s['end'])},Sub,,0,0,0,,{txt}")
    Path(path).write_text(head + "\n".join(lines) + "\n", encoding="utf-8")


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    ap.add_argument("--mode", choices=["preview", "final"], default="preview")
    ap.add_argument("--transcript", help="sentence-level JSON from prep_transcript.py; enables snapping and subtitles")
    ap.add_argument("--snap-tol", type=float, default=0.6, help="max seconds an edge may move when snapping")
    ap.add_argument("--out-dir", default="build")
    ap.add_argument("--preset", default="veryfast", help="x264 preset; use slow for the best final quality")
    ap.add_argument("--no-subs", action="store_true", help="do not burn subtitles")
    ap.add_argument("--burn-subs", action="store_true", help="kept for old commands; subtitles are on by default")
    ap.add_argument("--font", default="Microsoft YaHei" if os.name == "nt" else "Noto Sans CJK SC",
                    help="font family for cards and subtitles (default: Microsoft YaHei on Windows)")
    ap.add_argument("--font-file", help="font file for title cards when the family name cannot be resolved")
    ap.add_argument("--dry-run", action="store_true", help="print the plan after snapping and stop")
    a = ap.parse_args()

    plan_path = Path(a.plan).resolve()
    base = plan_path.parent
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    style = plan.get("style", {})
    limits = {"max_source_ratio": 0.5, "max_source_clip_sec": 60, "max_total_sec": 600, **plan.get("limits", {})}
    src = Path(plan["source"])
    src = src if src.is_absolute() else base / src
    if not src.exists():
        sys.exit(f"source video not found: {src}")
    info = probe(src)
    if "width" not in info:
        sys.exit(f"no video stream in {src}")
    if plan.get("aspect", "9:16") != "9:16":
        sys.exit("this version renders 9:16 only")
    preview = a.mode == "preview"
    W, H = (540, 960) if preview else (1080, 1920)
    L = layout(W, H, info["width"], info["height"])
    clips = [dict(c) for c in plan["clips"]]
    segs = load_segments(a.transcript) if a.transcript else None
    warns = []

    for i, c in enumerate(clips, 1):
        c.setdefault("id", f"c{i}")
        c.setdefault("type", "source")
        if c["type"] == "source":
            if segs:
                c["snap_notes"] = snap_clip(c, segs, a.snap_tol)
            if c["end"] <= c["start"]:
                sys.exit(f"clip {c['id']} has end <= start")
            if c["end"] > info["duration"] + 0.05:
                sys.exit(f"clip {c['id']} ends at {c['end']:.2f}s but the source is {info['duration']:.2f}s")
            c["dur"] = c["end"] - c["start"]
            if c["dur"] > limits["max_source_clip_sec"]:
                warns.append(f"{c['id']}: source clip runs {c['dur']:.0f}s (limit {limits['max_source_clip_sec']}s)")
        elif c["type"] in ("narration", "card"):
            text = "".join(c.get("lines", [])) or c.get("card", "")
            if c.get("audio"):
                apath = Path(c["audio"]) if Path(c["audio"]).is_absolute() else base / c["audio"]
                if not apath.exists():
                    sys.exit(f"clip {c['id']}: audio not found: {apath}")
                c["audio_path"] = apath
                c["dur"] = probe(apath)["duration"] + 0.3
            elif c.get("duration"):
                c["dur"] = float(c["duration"])
            else:
                c["dur"] = max(2.0, len(re.sub(r"\s|【|】", "", text)) / ZH_CPS + 0.4)
            if c["type"] == "narration" and not c.get("audio"):
                c["pending_voice"] = True
                warns.append(f"{c['id']}: narration has no audio yet, silent placeholder {c['dur']:.1f}s (待配音)")
        else:
            sys.exit(f"clip {c['id']}: unknown type {c['type']!r}")

    total = sum(c["dur"] for c in clips)
    src_total = sum(c["dur"] for c in clips if c["type"] == "source")
    ratio = src_total / total if total else 0
    if ratio > limits["max_source_ratio"]:
        warns.append(f"source footage is {ratio:.0%} of the video (limit {limits['max_source_ratio']:.0%}); "
                     "add narration or cut source")
    if total > limits["max_total_sec"]:
        warns.append(f"video is {total:.0f}s, longer than {limits['max_total_sec']}s")

    print(f"{len(clips)} clips, {total:.1f}s total, source footage {ratio:.0%}, mode {a.mode}")
    for c in clips:
        for n in c.get("snap_notes", []):
            print(f"  {c['id']}: {n}")
    for w in warns:
        print("  WARN " + w)
    if a.dry_run:
        return

    out = Path(a.out_dir)
    seg_dir = out / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    fonts = Fonts(a.font, a.font_file)
    crf = "30" if preview else "20"
    preset = "veryfast" if preview else a.preset
    bg = style.get("bg", "#0E1A33")
    bilingual = bool(style.get("bilingual"))
    timeline, concat, subs, t, missing_zh = [], [], [], 0.0, 0

    for i, c in enumerate(clips, 1):
        dur = c["dur"]
        ov = seg_dir / f"overlay_{i:03d}.png"
        boxes = render_overlay(ov, W, H, L, style, c, fonts)
        seg = seg_dir / f"seg_{i:03d}.mp4"
        enc = ["-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-t", f"{dur:.3f}", seg]
        if c["type"] == "source":
            ins = ["-ss", f"{c['start']:.3f}", "-t", f"{dur:.3f}", "-i", src, "-loop", "1", "-i", ov]
            if bg == "blur":
                vf = (f"[0:v]split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                      f"boxblur=20:3,eq=brightness=-0.25[bg];[b]")
            else:
                ins += ["-f", "lavfi", "-i", f"color=c={bg}:s={W}x{H}:r=30:d={dur:.3f}"]
                vf = "[2:v]null[bg];[0:v]"
            vf += (f"scale={L['fg_w']}:{L['fg_h']},setsar=1[fg];[bg][fg]overlay=(W-w)/2:{L['video_top']}:shortest=1[base];"
                   f"[base][1:v]overlay=0:0:shortest=1,setsar=1,fps=30,format=yuv420p[v]")
            if info["audio"]:
                vf += (f";[0:a]afade=t=in:d=0.03,afade=t=out:st={max(dur - 0.03, 0):.3f}:d=0.03,"
                       "aresample=48000[a]")
                amap = "[a]"
            else:
                ins += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
                amap = f"{3 if bg != 'blur' else 2}:a"
            sh(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *ins, "-filter_complex", vf,
                "-map", "[v]", "-map", amap, *enc])
            if segs:
                for g in segs:
                    s0, s1 = max(g["start"], c["start"]), min(g["end"], c["end"])
                    if s1 - s0 < 0.2:
                        continue
                    if not g["zh"]:
                        missing_zh += 1
                    subs += timed_chunks(g["zh"] or g["text"], t + s0 - c["start"], t + s1 - c["start"],
                                         en=g["text"] if g["zh"] else "")
            elif c.get("caption"):
                subs.append({"start": t, "end": t + dur, "text": c["caption"], "en": ""})
        else:
            ins = ["-f", "lavfi", "-i", f"color=c={bg}:s={W}x{H}:r=30:d={dur:.3f}", "-loop", "1", "-i", ov]
            if c.get("audio_path"):
                ins += ["-i", c["audio_path"]]
                af = f"[2:a]aresample=48000,apad,afade=t=out:st={max(dur - 0.05, 0):.3f}:d=0.05[a]"
            else:
                ins += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
                af = "[2:a]anull[a]"
            vf = f"[0:v][1:v]overlay=0:0:shortest=1,setsar=1,fps=30,format=yuv420p[v];{af}"
            sh(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *ins, "-filter_complex", vf,
                "-map", "[v]", "-map", "[a]", *enc])
            lines = c.get("lines", [])
            if lines:
                tot, tt = sum(len(x) for x in lines), t
                speak = dur - 0.3 if c.get("audio_path") else dur
                for x in lines:
                    d = speak * len(x) / tot
                    subs += timed_chunks(x, tt, tt + d)
                    tt += d
        timeline.append({"id": c["id"], "type": c["type"], "label": c.get("label", ""),
                         "src_start": round(c["start"], 3) if c["type"] == "source" else None,
                         "src_end": round(c["end"], 3) if c["type"] == "source" else None,
                         "out_start": round(t, 3), "out_end": round(t + dur, 3),
                         "card": c.get("card", ""), "speaker": c.get("speaker", ""),
                         "pending_voice": bool(c.get("pending_voice")), "boxes": boxes})
        concat.append(f"file '{seg.resolve()}'")
        t += dur
        print(f"  rendered clip {i}/{len(clips)} ({c['type']})")

    (out / "concat.txt").write_text("\n".join(concat) + "\n", encoding="utf-8")
    joined = out / "joined.mp4"
    sh(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", out / "concat.txt",
        "-c", "copy", joined])
    result = out / f"{a.mode}.mp4"
    write_ass(out / "subs.ass", subs, W, H, L, a.font, bilingual)
    if subs and not a.no_subs:
        p = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", joined.name, "-vf",
                            "subtitles=subs.ass", "-c:v", "libx264", "-preset", preset, "-crf", crf,
                            "-pix_fmt", "yuv420p", "-c:a", "copy", result.name], cwd=out, capture_output=True, text=True)
        if p.returncode != 0:
            sys.exit("burning subtitles failed:\n" + p.stderr[-2000:])
        joined.unlink()
    else:
        joined.replace(result)
    if missing_zh:
        print(f"  WARN {missing_zh} subtitle sentences have no Chinese yet (shown in the original language); "
              "see prep_transcript.py todo")

    (out / "timeline.json").write_text(json.dumps({
        "source": str(src), "aspect": "9:16", "mode": a.mode, "size": [W, H], "duration": round(t, 3),
        "layout": {"video_top": L["video_top"], "video_bottom": L["video_bottom"], "sub_top": L["sub_top"]},
        "subtitle_font_px": int(66 * L["k"]),
        "stats": {"source_sec": round(src_total, 1), "source_ratio": round(ratio, 3), "limits": limits,
                  "missing_zh": missing_zh, "warnings": warns},
        "clips": timeline}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"done: {result}  ({t:.1f}s)  timeline: {out / 'timeline.json'}")


if __name__ == "__main__":
    main()
