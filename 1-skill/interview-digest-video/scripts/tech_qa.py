#!/usr/bin/env python3
"""独立技术审片：对任意一个视频文件做可自动判断的检查，不需要剪辑时间线，并生成供逐张查看的联系表。

用在视频不是 cut_video.py 出的时候（比如别的工具渲染的、用户直接给的成片）。这时 tech_check.py
没有时间线，只能对重复素材和半句切断给泛泛的 warn；本脚本直接从画面和声音里把具体时间点找出来。
cut_video.py 出的视频仍然用 tech_check.py，它能用时间线和转写做更准的判断。

用法：
    python tech_qa.py VIDEO --out work/qa/round1 [--fps 2] [--scene 0.25]
                      [--min-shot 1.0] [--cut-tolerance 0.3]

依赖：ffmpeg、ffprobe、Pillow、numpy。

输出：
    OUT/frames/        抽出的帧（文件名里的序号对应时间）
    OUT/sheets/        联系表，每张 20 帧，带时间码，供逐张查看
    OUT/report.json    机器可读报告
    OUT/report.md      人可读报告
    OUT/tech_report.json   与 tech_check.py 同结构，可直接给 review_gate.py handoff --tech-report 用

退出码：有 FAIL 项返回 1，否则返回 0。

脚本给出的是线索，不是最终结论。尤其是“半句切断”和“重复素材”只是候选，需要人或模型确认。
脚本查不出的项（人物标签、旁白与画面对应、图卡与字幕重叠、数字出处）靠联系表人工或模型逐张看。
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def probe(video):
    r = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(video)])
    if r.returncode != 0:
        sys.exit("ffprobe 读取失败：" + r.stderr.strip())
    info = json.loads(r.stdout)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    if v is None:
        sys.exit("文件里没有视频流")
    num, den = (v.get("avg_frame_rate") or "0/1").split("/")
    fps = float(num) / float(den) if float(den) else 0.0
    return {
        "duration": float(info["format"].get("duration", 0)),
        "width": int(v["width"]),
        "height": int(v["height"]),
        "fps": round(fps, 2),
        "has_audio": a is not None,
    }


def extract_frames(video, frames_dir, fps):
    frames_dir.mkdir(parents=True, exist_ok=True)
    for old in frames_dir.glob("f_*.jpg"):
        old.unlink()
    r = run(["ffmpeg", "-v", "error", "-y", "-i", str(video),
             "-vf", f"fps={fps},scale=360:-2", "-q:v", "4",
             str(frames_dir / "f_%05d.jpg")])
    if r.returncode != 0:
        sys.exit("抽帧失败：" + r.stderr.strip())
    return sorted(frames_dir.glob("f_*.jpg"))


def frame_time(index, fps):
    return index / fps


def fmt(t):
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:05.2f}"


def merge_ranges(times, step):
    """把连续的时间点合并成区间，返回 [(开始, 结束)]。"""
    out = []
    for t in times:
        if out and t - out[-1][1] <= step * 1.01:
            out[-1][1] = t
        else:
            out.append([t, t])
    return [(a, b + step) for a, b in out]


def check_flat_frames(frames, fps):
    black, white, solid = [], [], []
    for i, f in enumerate(frames):
        arr = np.asarray(Image.open(f).convert("L"), dtype=np.float32)
        mean, std = float(arr.mean()), float(arr.std())
        t = frame_time(i, fps)
        if mean < 12 and std < 6:
            black.append(t)
        elif mean > 243 and std < 6:
            white.append(t)
        elif std < 4:
            solid.append(t)
    step = 1.0 / fps
    return {
        "black": merge_ranges(black, step),
        "white": merge_ranges(white, step),
        "solid": merge_ranges(solid, step),
    }


def detect_cuts(video, threshold):
    r = run(["ffmpeg", "-v", "info", "-i", str(video),
             "-vf", f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"])
    return sorted(float(m) for m in re.findall(r"pts_time:([0-9.]+)", r.stderr))


def build_shots(cuts, duration):
    edges = [0.0] + [c for c in cuts if 0 < c < duration] + [duration]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1) if edges[i + 1] - edges[i] > 0.01]


def dhash(path):
    """16×16 差分哈希（256 位）。返回 (哈希, 是否有画面内容)。"""
    g = np.asarray(Image.open(path).convert("L").resize((17, 16)), dtype=np.float32)
    return (g[:, 1:] > g[:, :-1]).flatten(), float(g.std()) >= 4.0


def find_repeated_footage(frames, fps, thr=6, min_run_sec=1.5, window_sec=60.0):
    """找出视频里“先后两次出现的同一段画面”。

    对每个抽样帧计算哈希，在 window_sec 范围内寻找连续 min_run_sec 以上都相似的两段。
    两个过滤条件，用来压低误报：
      1. 纯色帧不参与比较；
      2. 两段之间的中点画面必须明显不同，否则只是一个长时间静止的镜头，不算重复。
    结果只是候选，需要人工确认。
    """
    n = len(frames)
    if n < 4:
        return []
    hs, ok = zip(*(dhash(f) for f in frames))
    hs, ok = np.array(hs), np.array(ok)
    run = max(2, int(round(min_run_sec * fps)))
    max_gap = min(n - 1, int(window_sec * fps))
    found = []
    for g in range(run, max_gap + 1):
        m = n - g
        d = (hs[:m] != hs[g:]).sum(1)
        mid = (hs[:m] != hs[g // 2: g // 2 + m]).sum(1)
        hit = (d <= thr) & ok[:m] & ok[g:] & (mid > thr * 2)
        i = 0
        while i < m:
            if not hit[i]:
                i += 1
                continue
            j = i
            while j < m and hit[j]:
                j += 1
            if j - i >= run:
                found.append((i, j, g))
            i = j
    # 同一段内容可能在相邻间隔上重复命中，保留最长的，去掉被包含的
    found.sort(key=lambda x: -(x[1] - x[0]))
    kept = []
    for i, j, g in found:
        if not any(i >= a - 1 and j <= b + 1 and abs(g - gg) <= 1 for a, b, gg in kept):
            kept.append((i, j, g))
    kept.sort()
    return [{"first": [round(i / fps, 2), round(j / fps, 2)],
             "repeat": [round((i + g) / fps, 2), round((j + g) / fps, 2)]} for i, j, g in kept]


def audio_checks(video, cuts, tol):
    result = {"silences": [], "mid_sound_cuts": [], "mean_volume_db": None, "max_volume_db": None}
    r = run(["ffmpeg", "-v", "info", "-i", str(video), "-af", "silencedetect=noise=-35dB:d=0.25",
             "-vn", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: ([0-9.\-]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", r.stderr)]
    sil = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else float("inf")
        sil.append((max(s, 0.0), e))
    result["silences"] = [(round(a, 2), None if b == float("inf") else round(b, 2)) for a, b in sil]
    for c in cuts:
        near = any(a - tol <= c <= b + tol for a, b in sil)
        if not near:
            result["mid_sound_cuts"].append(round(c, 2))
    v = run(["ffmpeg", "-v", "info", "-i", str(video), "-af", "volumedetect", "-vn", "-f", "null", "-"])
    mean = re.search(r"mean_volume: (-?[0-9.]+) dB", v.stderr)
    mx = re.search(r"max_volume: (-?[0-9.]+) dB", v.stderr)
    if mean:
        result["mean_volume_db"] = float(mean.group(1))
    if mx:
        result["max_volume_db"] = float(mx.group(1))
    return result


def make_sheets(frames, fps, sheets_dir, per_sheet=20, cols=5):
    sheets_dir.mkdir(parents=True, exist_ok=True)
    for old in sheets_dir.glob("sheet_*.jpg"):
        old.unlink()
    if not frames:
        return []
    tw, th = Image.open(frames[0]).size
    rows = (per_sheet + cols - 1) // cols
    out = []
    for n in range(0, len(frames), per_sheet):
        chunk = frames[n:n + per_sheet]
        sheet = Image.new("RGB", (cols * tw, rows * th), (20, 20, 20))
        d = ImageDraw.Draw(sheet)
        for k, f in enumerate(chunk):
            x, y = (k % cols) * tw, (k // cols) * th
            sheet.paste(Image.open(f), (x, y))
            label = fmt(frame_time(n + k, fps))
            d.rectangle([x, y, x + 74, y + 16], fill=(0, 0, 0))
            d.text((x + 3, y + 2), label, fill=(255, 255, 255))
        p = sheets_dir / f"sheet_{n // per_sheet + 1:02d}.jpg"
        sheet.save(p, quality=85)
        out.append(str(p))
    return out


def judge(report, min_shot):
    """给每个检查项一个 PASS / WARN / FAIL。"""
    items = []
    flat = report["flat_frames"]
    flat_n = sum(len(v) for v in flat.values())
    items.append(("黑屏、白屏、纯色帧", "PASS" if flat_n == 0 else "FAIL",
                  "未发现" if flat_n == 0 else f"发现 {flat_n} 段，时间段见 report.md 的“纯色帧时间段”"))
    short = [s for s in report["shots"] if s["length"] < min_shot]
    items.append(("过短镜头", "PASS" if not short else "WARN",
                  f"最短镜头 {report['shortest_shot']:.2f} 秒；短于 {min_shot} 秒的共 {len(short)} 个，需确认是台词本身短还是闪帧"))
    d = report["repeated_footage"]
    items.append(("重复素材", "PASS" if not d else "WARN",
                  "未发现疑似重复" if not d else f"{len(d)} 处同一段画面出现了两次，需人工确认"))
    a = report["audio"]
    if report["info"]["has_audio"]:
        m = a["mid_sound_cuts"]
        items.append(("原声是否停在完整句子", "PASS" if not m else "WARN",
                      "每个切点附近都有静音" if not m else f"{len(m)} 个切点附近没有静音，疑似半句切断，需听前后 1 秒"))
        mx, mean = a["max_volume_db"], a["mean_volume_db"]
        if mx is not None and mx > -0.5:
            items.append(("音量削波", "FAIL", f"最大音量 {mx} dB，可能削波"))
        elif mean is not None and mean < -30:
            items.append(("整体音量", "WARN", f"平均音量 {mean} dB，偏小"))
        else:
            items.append(("音量", "PASS", f"平均 {mean} dB，最大 {mx} dB"))
    else:
        items.append(("音频", "WARN", "文件没有音轨"))
    return [{"check": c, "status": s, "detail": t} for c, s, t in items]


def to_review_gate_report(report):
    """转成 review_gate.py handoff --tech-report 能读的格式（与 tech_check.py 的 tech_report.json 同结构）。"""
    flat = report["flat_frames"]
    t1 = [f"{fmt(a)}-{fmt(b)} {name}" for k, name in (("black", "black"), ("white", "white"), ("solid", "solid color"))
          for a, b in flat[k]]
    short = [s for s in report["shots"] if s["length"] < report["min_shot"]]
    t2 = [f"{fmt(s['start'])}-{fmt(s['end'])} ({s['length']:.2f}s): needs a human look" for s in short]
    t3 = [f"{fmt(d['first'][0])}-{fmt(d['first'][1])} appears again at {fmt(d['repeat'][0])}-{fmt(d['repeat'][1])}"
          for d in report["repeated_footage"]]
    if report["info"]["has_audio"]:
        t4 = [f"{fmt(t)} cut with sound on both sides, may split a sentence" for t in report["audio"]["mid_sound_cuts"]]
        t4_status = "warn" if t4 else "pass"
        t4 = t4 or ["every cut has silence nearby"]
    else:
        t4, t4_status = ["no audio track"], "warn"
    look = ["look at the contact sheets in sheets/"]
    return {
        "video": report["video"], "duration": report["info"]["duration"], "frames_checked": report["frame_count"],
        "checks": [
            {"id": "T1", "name": "黑屏、白屏、纯色帧", "status": "fail" if t1 else "pass", "detail": t1 or ["no blank or solid frames"]},
            {"id": "T2", "name": "镜头时长", "status": "warn" if t2 else "pass",
             "detail": t2 or [f"{len(report['shots'])} shots, shortest {report['shortest_shot']:.2f}s (limit {report['min_shot']}s)"]},
            {"id": "T3", "name": "相邻镜头重复素材", "status": "warn" if t3 else "pass", "detail": t3 or ["no repeated footage"]},
            {"id": "T4", "name": "原声是否停在完整句", "status": t4_status, "detail": t4},
            {"id": "T5", "name": "人物标签与画面一致", "status": "manual", "detail": look},
            {"id": "T6", "name": "图卡大字与底部字幕不打架", "status": "manual", "detail": look},
            {"id": "T7", "name": "画面外部数字都带出处", "status": "manual", "detail": look},
        ],
    }


def write_md(report, path):
    info = report["info"]
    lines = ["# 技术审片报告", "",
             f"- 文件：{report['video']}",
             f"- 时长：{fmt(info['duration'])}　分辨率：{info['width']}×{info['height']}　帧率：{info['fps']}",
             f"- 抽帧：每秒 {report['fps']} 帧，共 {report['frame_count']} 帧",
             f"- 镜头数：{len(report['shots'])}（场景阈值 {report['scene_threshold']}）", "",
             "## 结果", "", "| 检查项 | 结果 | 说明 |", "|---|---|---|"]
    for it in report["verdicts"]:
        lines.append(f"| {it['check']} | {it['status']} | {it['detail']} |")
    flat = report["flat_frames"]
    if any(flat.values()):
        lines += ["", "## 纯色帧时间段", ""]
        for k, name in (("black", "黑屏"), ("white", "白屏"), ("solid", "其他纯色")):
            for a, b in flat[k]:
                lines.append(f"- {name}：{fmt(a)} 到 {fmt(b)}")
    short = [s for s in report["shots"] if s["length"] < report["min_shot"]]
    if short:
        lines += ["", "## 过短镜头", ""] + [f"- {fmt(s['start'])} 到 {fmt(s['end'])}（{s['length']:.2f} 秒）" for s in short]
    if report["repeated_footage"]:
        lines += ["", "## 疑似重复的素材", ""]
        for d in report["repeated_footage"]:
            lines.append(f"- {fmt(d['first'][0])} 到 {fmt(d['first'][1])} 的画面，在 {fmt(d['repeat'][0])} 到 {fmt(d['repeat'][1])} 又出现了一次")
    mid = report["audio"]["mid_sound_cuts"]
    if mid:
        lines += ["", "## 疑似半句切断的切点", ""] + [f"- {fmt(t)}" for t in mid]
    lines += ["", "## 下一步", "",
              "按 `references/tech-qa-checklist.md` 逐张查看 `sheets/` 里的联系表，",
              "补查脚本查不出的项：人物标签、旁白与画面对应、图卡与字幕是否重叠、数字出处。"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=float, default=2.0, help="抽帧频率，默认每秒 2 帧")
    ap.add_argument("--scene", type=float, default=0.25, help="场景切换阈值，越小越敏感")
    ap.add_argument("--min-shot", type=float, default=1.0, help="短于这个秒数的镜头会被列出")
    ap.add_argument("--cut-tolerance", type=float, default=0.3, help="切点与静音的容差（秒）")
    args = ap.parse_args()

    video = Path(args.video)
    if not video.exists():
        sys.exit(f"找不到文件：{video}")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    info = probe(video)
    frames = extract_frames(video, out / "frames", args.fps)
    flat = check_flat_frames(frames, args.fps)
    cuts = detect_cuts(video, args.scene)
    shots_raw = build_shots(cuts, info["duration"])
    shots = [{"start": round(a, 2), "end": round(b, 2), "length": round(b - a, 2)} for a, b in shots_raw]
    dup = find_repeated_footage(frames, args.fps)
    audio = audio_checks(video, cuts, args.cut_tolerance) if info["has_audio"] else {
        "silences": [], "mid_sound_cuts": [], "mean_volume_db": None, "max_volume_db": None}
    sheets = make_sheets(frames, args.fps, out / "sheets")

    report = {
        "video": str(video), "info": info, "fps": args.fps, "frame_count": len(frames),
        "scene_threshold": args.scene, "min_shot": args.min_shot,
        "flat_frames": flat, "shots": shots,
        "shortest_shot": min((s["length"] for s in shots), default=0.0),
        "repeated_footage": dup, "audio": audio, "sheets": sheets,
    }
    report["verdicts"] = judge(report, args.min_shot)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "tech_report.json").write_text(json.dumps(to_review_gate_report(report), ensure_ascii=False, indent=2), encoding="utf-8")
    write_md(report, out / "report.md")

    print(f"完成：{len(frames)} 帧，{len(shots)} 个镜头，{len(sheets)} 张联系表")
    for it in report["verdicts"]:
        print(f"[{it['status']}] {it['check']}：{it['detail']}")
    print(f"报告：{out / 'report.md'}")
    sys.exit(1 if any(v["status"] == "FAIL" for v in report["verdicts"]) else 0)


if __name__ == "__main__":
    main()
