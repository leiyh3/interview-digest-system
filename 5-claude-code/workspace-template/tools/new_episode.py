#!/usr/bin/env python3
"""新建一期：episodes/<日期>-<短名>/，并建好子目录和备注模板。

  python3 tools/new_episode.py andreessen
  python3 tools/new_episode.py andreessen --video ~/Downloads/interview.mp4
  python3 tools/new_episode.py andreessen --url https://www.youtube.com/watch?v=xxxx
      用 yt-dlp 下载到 source/（最高 1080p），只下载你有权剪辑的视频
"""
import argparse
import datetime
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

NOTES = """# 本期备注

- 节目和链接：
- 发布日期：
- 我是否有权剪辑 / 授权说明：
- 嘉宾和主持人（画面里谁坐哪边）：
- 我自己想讲的角度：
- 不想用的段落：
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="短名，英文或拼音，如 andreessen")
    ap.add_argument("--video", help="源视频路径，会复制成 source/interview.<后缀>")
    ap.add_argument("--url", help="视频链接，用 yt-dlp 下载成 source/interview.mp4")
    a = ap.parse_args()
    d = ROOT / "episodes" / f"{datetime.date.today():%Y-%m-%d}-{a.name}"
    for sub in ("source", "vo", "reviews"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    notes = d / "notes.md"
    if not notes.exists():
        notes.write_text(NOTES, encoding="utf-8")
    if a.video:
        src = Path(a.video).expanduser()
        shutil.copy(src, d / "source" / f"interview{src.suffix.lower()}")
    if a.url:
        notes.write_text(notes.read_text(encoding="utf-8").replace("- 节目和链接：", f"- 节目和链接：{a.url}"),
                         encoding="utf-8")
        p = subprocess.run([sys.executable, "-m", "yt_dlp", "-f", "bv*[height<=1080]+ba/b[height<=1080]",
                            "--merge-output-format", "mp4", "--write-subs", "--write-auto-subs", "--sub-langs", "en.*,en",
                            "--sub-format", "vtt", "-o", str(d / "source" / "interview.%(ext)s"), a.url])
        if p.returncode != 0:
            sys.exit("下载失败：检查链接和网络（国内需要先设置代理 HTTPS_PROXY）")
    print(f"已创建 {d}")
    print("下一步：把视频和 whisper 转写放进 source/，填好 notes.md，然后在 Claude Code 里输入：")
    print(f"  /digest {d.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
