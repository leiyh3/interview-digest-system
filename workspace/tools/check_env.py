#!/usr/bin/env python3
"""检查本地环境能不能跑访谈精读流程。

  python3 tools/check_env.py           只检查
  python3 tools/check_env.py --smoke   再用一条 20 秒的合成视频把剪辑和技术审片跑一遍
"""
import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / ".claude" / "skills" / "interview-digest-video" / "scripts"
ok_all = True


def report(ok, name, detail="", required=True):
    global ok_all
    mark = "OK  " if ok else ("FAIL" if required else "WARN")
    if not ok and required:
        ok_all = False
    print(f"[{mark}] {name}" + (f"：{detail}" if detail else ""))


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--smoke", action="store_true")
    a = ap.parse_args()

    report(sys.version_info >= (3, 8), "Python 3.8+", sys.version.split()[0])
    for mod, pipname in (("PIL", "pillow"), ("numpy", "numpy")):
        try:
            importlib.import_module(mod)
            report(True, f"Python 库 {pipname}")
        except ImportError:
            report(False, f"Python 库 {pipname}", f"pip install {pipname}")

    for tool in ("ffmpeg", "ffprobe"):
        report(shutil.which(tool) is not None, tool, "" if shutil.which(tool) else "没找到，装好后确认在 PATH 里")
    p = run(["ffmpeg", "-hide_banner", "-encoders"])
    report(bool(p and "libx264" in p.stdout), "ffmpeg 带 libx264 编码器")
    p = run(["ffmpeg", "-hide_banner", "-filters"])
    report(bool(p and " subtitles " in p.stdout), "ffmpeg 带 libass（subtitles 滤镜，烧录字幕要用）")

    if os.name == "nt":
        yh = Path("C:/Windows/Fonts/msyh.ttc").exists() or Path("C:/Windows/Fonts/msyhbd.ttc").exists()
        report(yh, "中文字体 微软雅黑", "" if yh else "没找到 C:/Windows/Fonts/msyh.ttc，渲染时用 --font-file 指定一个中文字体")
        p = False
    else:
        p = run(["fc-match", "-f", "%{family}", "Noto Sans CJK SC"])
    if p is False:
        pass
    elif p is None:
        report(False, "fontconfig", "没装 fc-match，图卡会用系统自带中文字体兜底", required=False)
    else:
        report("CJK" in p.stdout or "Noto" in p.stdout, "中文字体 Noto Sans CJK SC",
               p.stdout.strip() or "没找到，图卡会用系统字体兜底，字幕建议加 --font", required=False)

    report(shutil.which("whisper") is not None, "whisper（转写）", "pip install openai-whisper", required=False)
    report(shutil.which("yt-dlp") is not None, "yt-dlp（下载）", "pip install yt-dlp", required=False)
    report(SCRIPTS.exists(), "skill 已放进工作区", str(SCRIPTS))

    if a.smoke and ok_all:
        smoke()
    print("\n" + ("环境可以用。" if ok_all else "有 FAIL 项，先装好再用。"))
    sys.exit(0 if ok_all else 1)


def smoke():
    print("\n用合成视频试跑……")
    py = sys.executable
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=20",
                        "-f", "lavfi", "-i", "sine=frequency=300:duration=20", "-shortest", "-c:v", "libx264",
                        "-c:a", "aac", str(d / "src.mp4")], check=True)
        segs = [{"id": i, "start": i * 5.0, "end": i * 5.0 + 4.5, "text": f"Sentence {i}.", "zh": f"第{i}句测试字幕。"}
                for i in range(4)]
        (d / "transcript.json").write_text(json.dumps({"segments": segs}, ensure_ascii=False), encoding="utf-8")
        plan = {"source": "src.mp4", "aspect": "9:16",
                "style": {"title": "环境测试：【中文】标题条", "source_label": "来源：测试"},
                "clips": [{"id": "s1", "start": 0, "end": 4.5, "card": "图卡测试【高亮】", "speaker": "测试嘉宾"},
                          {"id": "n1", "type": "narration", "card": "讲解段测试", "lines": ["这是讲解段字幕。"]},
                          {"id": "s2", "start": 10, "end": 14.5}]}
        (d / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        p = subprocess.run([py, str(SCRIPTS / "cut_video.py"), str(d / "plan.json"), "--mode", "preview",
                            "--transcript", str(d / "transcript.json"), "--out-dir", str(d / "build")],
                           capture_output=True, text=True)
        report(p.returncode == 0, "试跑剪辑 cut_video.py", "" if p.returncode == 0 else p.stdout[-800:] + p.stderr[-800:])
        if p.returncode != 0:
            return
        p = subprocess.run([py, str(SCRIPTS / "tech_check.py"), str(d / "build" / "preview.mp4"),
                            "--timeline", str(d / "build" / "timeline.json"), "--transcript", str(d / "transcript.json"),
                            "--out-dir", str(d / "qa")], capture_output=True, text=True)
        report(p.returncode in (0, 1) and "T1" in p.stdout, "试跑技术审片 tech_check.py",
               "" if "T1" in p.stdout else p.stderr[-800:])
        keep = ROOT / "data" / "smoke_preview.mp4"
        shutil.copy(d / "build" / "preview.mp4", keep)
        print(f"     试跑的预览已存到 {keep}，打开看看中文图卡和字幕显示是否正常。")


if __name__ == "__main__":
    main()
