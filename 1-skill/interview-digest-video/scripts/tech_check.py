#!/usr/bin/env python3
"""Technical review of a rendered video.

Usage:
  python3 tech_check.py build/preview.mp4 --timeline build/timeline.json \
      --transcript transcript.json --out-dir tech_qa

Automatic checks (pass / warn / fail):
  T1 blank or solid frames
  T2 shot length (flags shots under --min-shot, tells a short spoken line from a flash)
  T3 repeated footage between adjacent clips
  T4 cuts that land inside a sentence
  T6 (layout part) title/card text boxes vs. the subtitle zone, from timeline.json
  T8 share of source footage and longest source clip, from timeline.json
Narration and card clips (type != source) are skipped by the source-based checks.
Manual checks the script cannot judge (status "manual"): a reviewer must look at
the contact sheets it writes for
  T5 person labels vs. who is on screen, T6 title card vs. subtitle overlap,
  T7 every number shown on screen has a source.
Exit code is 1 when any automatic check fails.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_frames  # noqa: E402


def ff(args):
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *args], capture_output=True, text=True)
    return p.stderr


def load_segments(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    segs = data["segments"] if isinstance(data, dict) else data
    return [{"start": float(s["start"]), "end": float(s["end"]), "text": s.get("text", "")} for s in segs]


def dhash(path):
    im = Image.open(path).convert("L").resize((9, 8))
    a = np.asarray(im, dtype=np.int16)
    return (a[:, 1:] > a[:, :-1]).flatten()


def fmt(t):
    return f"{int(t // 60):02d}:{t % 60:05.2f}"


def check_solid(index):
    bad = []
    for item in index:
        a = np.asarray(Image.open(item["file"]).convert("L").resize((96, 54)), dtype=np.float32)
        if a.std() < 2.5:
            kind = "black" if a.mean() < 12 else "white" if a.mean() > 243 else "solid color"
            bad.append((item["t"], kind))
    return bad


def check_black_intervals(video):
    err = ff(["-i", str(video), "-vf", "blackdetect=d=0.08:pix_th=0.10", "-an", "-f", "null", "-"])
    return [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", err)]


def scene_times(video, thr=0.35):
    err = ff(["-i", str(video), "-vf", f"select='gt(scene,{thr})',showinfo", "-an", "-f", "null", "-"])
    return sorted(float(t) for t in re.findall(r"pts_time:([\d.]+)", err))


def silence_intervals(media):
    err = ff(["-i", str(media), "-af", "silencedetect=noise=-35dB:d=0.12", "-vn", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    return list(zip(starts, ends))


def out_to_src(timeline, t):
    for c in timeline["clips"]:
        if c["out_start"] - 1e-3 <= t <= c["out_end"] + 1e-3:
            if c.get("src_start") is None:
                return None
            return c["src_start"] + (t - c["out_start"])
    return None


def overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--timeline", help="timeline.json written by cut_video.py")
    ap.add_argument("--transcript", help="whisper-style JSON of the SOURCE video")
    ap.add_argument("--out-dir", default="tech_qa")
    ap.add_argument("--min-shot", type=float, default=0.8)
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--max-frames", type=int, default=240)
    a = ap.parse_args()

    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    index, eff, dur = extract_frames.extract(a.video, out / "frames", a.fps, a.max_frames)
    sheets = extract_frames.contact_sheets(index, out / "frames")
    timeline = json.loads(Path(a.timeline).read_text(encoding="utf-8")) if a.timeline else None
    segs = load_segments(a.transcript) if a.transcript else None
    checks = []

    # T1 blank or solid frames
    bad = check_solid(index)
    black = check_black_intervals(a.video)
    det = [f"{fmt(t)} {kind} frame" for t, kind in bad] + [f"{fmt(s)}-{fmt(e)} black interval" for s, e in black]
    checks.append({"id": "T1", "name": "黑屏、白屏、纯色帧", "status": "fail" if det else "pass",
                   "detail": det or [f"checked {len(index)} frames at {eff:.2f} fps, none found"]})

    # T2 shot length
    cuts = scene_times(a.video)
    if timeline:
        cuts += [c["out_start"] for c in timeline["clips"][1:]]
    cuts = sorted(set(round(c, 2) for c in cuts if 0.05 < c < dur - 0.05))
    bounds = [0.0] + cuts + [dur]
    shots = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
    short = [(s, e) for s, e in shots if e - s < a.min_shot]
    t2, status2 = [], "pass"
    for s, e in short:
        verdict = "needs a human look"
        if timeline and segs:
            m0, m1 = out_to_src(timeline, s), out_to_src(timeline, max(s, e - 0.01))
            if m0 is None or m1 is None:
                verdict = "narration or card clip, look at the contact sheet"
            elif m0 is not None and m1 is not None:
                spoken = sum(overlap(m0, m1, g["start"], g["end"]) for g in segs)
                if spoken >= 0.7 * max(m1 - m0, 1e-3):
                    verdict = "short spoken line, probably fine"
                else:
                    verdict = "no speech under it, possible flash"
                    status2 = "warn"
        else:
            status2 = "warn"
        t2.append(f"{fmt(s)}-{fmt(e)} ({e - s:.2f}s): {verdict}")
    shortest = min((e - s for s, e in shots), default=0)
    checks.append({"id": "T2", "name": "镜头时长", "status": status2,
                   "detail": t2 or [f"{len(shots)} shots, shortest {shortest:.2f}s (limit {a.min_shot}s)"]})

    # T3 repeated footage between adjacent clips
    t3, status3 = [], "pass"
    if timeline:
        cl = timeline["clips"]
        for x, y in zip(cl, cl[1:]):
            if x.get("src_start") is None or y.get("src_start") is None:
                continue
            ov = overlap(x["src_start"], x["src_end"], y["src_start"], y["src_end"])
            if ov > 0.3:
                t3.append(f"{x['id']} and {y['id']} reuse {ov:.1f}s of the same source footage")
                status3 = "fail"
        mids = []
        for c in cl:
            mid = (c["out_start"] + c["out_end"]) / 2
            f = min(index, key=lambda it: abs(it["t"] - mid))
            mids.append((c["id"], dhash(f["file"])))
        for (i1, h1), (i2, h2) in zip(mids, mids[1:]):
            dist = int((h1 != h2).sum())
            if dist <= 4:
                t3.append(f"{i1} and {i2} look almost identical (hash distance {dist})")
                status3 = "warn" if status3 == "pass" else status3
    else:
        status3 = "warn"
        t3.append("no timeline.json given, only a visual comparison would be possible")
    checks.append({"id": "T3", "name": "相邻镜头重复素材", "status": status3,
                   "detail": t3 or ["adjacent clips use different source ranges and look different"]})

    # T4 cuts inside a sentence
    t4, status4 = [], "pass"
    if timeline:
        src_path = timeline.get("source")
        sil = None
        if not segs and src_path and Path(src_path).exists():
            sil = silence_intervals(src_path)
        for c in timeline["clips"]:
            if c.get("src_start") is None:
                continue
            for edge, t in (("start", c["src_start"]), ("end", c["src_end"])):
                if segs:
                    inside = [g for g in segs if g["start"] + 0.35 < t < g["end"] - 0.35]
                    if inside:
                        t4.append(f"{c['id']} {edge} at source {fmt(t)} is inside a sentence: {inside[0]['text'][:30]!r}")
                        status4 = "fail"
                elif sil is not None:
                    near = any(s - 0.25 <= t <= e + 0.25 for s, e in sil)
                    if not near:
                        t4.append(f"{c['id']} {edge} at source {fmt(t)} is not near a pause")
                        status4 = "warn" if status4 == "pass" else status4
        if not segs and sil is None:
            status4 = "warn"
            t4.append("no transcript and source video not found, cannot check sentence endings")
    else:
        status4 = "warn"
        t4.append("no timeline.json given")
    checks.append({"id": "T4", "name": "原声是否停在完整句", "status": status4,
                   "detail": t4 or ["every clip starts and ends on a sentence boundary"]})

    sheet_list = [f"{Path(s['file']).name} ({fmt(s['from'])}-{fmt(s['to'])})" for s in sheets]
    sheets_line = "contact sheets: " + ", ".join(sheet_list)
    checks.append({"id": "T5", "name": "人物标签与画面一致", "status": "manual", "detail": [
        "对每个出现人名或身份的画面，核对谁在画面里、坐哪边、穿什么，旁白说的人是否对得上", sheets_line]})

    # T6 layout part: text boxes recorded by cut_video.py vs. the subtitle zone
    t6, status6 = [], "manual"
    if timeline and timeline.get("layout"):
        W, H = timeline.get("size", [0, 0])
        sub_top = timeline["layout"]["sub_top"]
        sub_bottom = sub_top + 2.6 * timeline.get("subtitle_font_px", 28)
        for c in timeline["clips"]:
            for name, b in (c.get("boxes") or {}).items():
                if not b:
                    continue
                x0, y0, x1, y1 = b[:4]
                if y1 > sub_top and y0 < sub_bottom:
                    t6.append(f"{c['id']} {name} text reaches into the subtitle zone (y {y0}-{y1}, subtitles from {sub_top})")
                    status6 = "fail"
                if x0 < 0 or y0 < 0 or x1 > W or y1 > H:
                    t6.append(f"{c['id']} {name} text is cut off by the frame edge")
                    status6 = "fail"
                if name in ("title", "card") and len(b) > 5 and b[5] > (2 if name == "title" or c.get("type") == "source" else 5):
                    t6.append(f"{c['id']} {name} needed {b[5]} lines even at {b[4]}px, shorten the text")
                    status6 = "fail"
        if status6 == "manual":
            t6.append("自动排版检查通过：图卡和标题都在字幕区之上、没有出界")
    else:
        t6.append("no layout in timeline.json, check by eye only")
    t6 += ["仍需看联系表：大字和字幕是否清晰、有没有压住人脸、同一时刻是否超过两层文字", sheets_line]
    checks.append({"id": "T6", "name": "图卡大字与底部字幕不打架", "status": status6, "detail": t6})
    checks.append({"id": "T7", "name": "画面外部数字都带出处", "status": "manual", "detail": [
        "列出画面上出现的每个数字，逐个对照来源，没有出处的要标出来", sheets_line]})

    # T8 share of source footage
    if timeline and timeline.get("stats"):
        st = timeline["stats"]
        lim = st.get("limits", {})
        t8, status8 = [f"source footage {st['source_sec']}s = {st['source_ratio']:.0%} of the video "
                       f"(limit {lim.get('max_source_ratio', 0.5):.0%})"], "pass"
        if st["source_ratio"] > lim.get("max_source_ratio", 0.5):
            status8 = "warn"
        for c in timeline["clips"]:
            if c.get("src_start") is not None and c["src_end"] - c["src_start"] > lim.get("max_source_clip_sec", 60):
                t8.append(f"{c['id']} is one {c['src_end'] - c['src_start']:.0f}s source clip")
                status8 = "warn"
        pend = [c["id"] for c in timeline["clips"] if c.get("pending_voice")]
        if pend:
            t8.append("narration still without voice (待配音): " + ", ".join(pend))
            status8 = "warn" if timeline.get("mode") == "preview" else "fail"
        if st.get("missing_zh"):
            t8.append(f"{st['missing_zh']} subtitle sentences have no Chinese yet")
            status8 = "warn" if timeline.get("mode") == "preview" else "fail"
        checks.append({"id": "T8", "name": "原片占比、配音与翻译完整", "status": status8, "detail": t8})

    report = {"video": str(a.video), "duration": dur, "frames_checked": len(index), "checks": checks}
    (out / "tech_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    mark = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "manual": "LOOK"}
    md = [f"# 技术审片报告\n", f"视频：`{a.video}`，时长 {dur:.1f}s，检查 {len(index)} 帧\n",
          "| 编号 | 检查项 | 结果 |", "|---|---|---|"]
    md += [f"| {c['id']} | {c['name']} | {mark[c['status']]} |" for c in checks]
    for c in checks:
        md.append(f"\n## {c['id']} {c['name']} — {mark[c['status']]}")
        md += [f"- {d}" for d in c["detail"]]
    (out / "tech_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    for c in checks:
        print(f"{mark[c['status']]:5} {c['id']} {c['name']}")
    print(f"report: {out / 'tech_report.md'}")
    sys.exit(1 if any(c["status"] == "fail" for c in checks) else 0)


if __name__ == "__main__":
    main()
