#!/usr/bin/env python3
"""每日素材采集：拉取各个源的新内容，给 Claude 挑选用。只用标准库，联网走系统代理（HTTPS_PROXY）。

  scout.py check                 解析并测试每个源，第一次用和改了 sources.json 后跑
  scout.py fetch [--days 3]      拉取新内容，写 scout/inbox/<日期>.md 和 .json（每天定时跑这个，不花 Token）
  scout.py captions <链接或视频ID> ...
                                 下载 YouTube 字幕（不下载视频），转成带时间码的文本 scout/captions/<ID>.txt，
                                 给 Claude 找反直觉的话和数字用
  scout.py mark <链接> ...        标记为已处理，以后不再出现在收件箱

源列表在 scout/sources.json。进过收件箱的内容记在 scout/seen.json，每条只出现在一天的收件箱里。
"""
import argparse
import datetime as dt
import email.utils
import html
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCOUT = ROOT / "scout"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) digest-scout/1.0", "Accept-Language": "en-US,en;q=0.8"}
NS = {"a": "http://www.w3.org/2005/Atom", "media": "http://search.yahoo.com/mrss/",
      "yt": "http://www.youtube.com/xml/schemas/2015", "content": "http://purl.org/rss/1.0/modules/content/"}


def get(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def load_json(p, default):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def save_json(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def clean(text, limit=400):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            d = email.utils.parsedate_to_datetime(s)
        except (TypeError, ValueError):
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


# ---------- resolving sources ----------

def resolve(src, cache):
    """Return the feed URL for a source, or raise with a readable reason."""
    key = f"{src['type']}:{src.get('handle') or src.get('url') or src['name']}"
    if key in cache:
        return cache[key]
    if src["type"] == "rss":
        url = src["url"]
    elif src["type"] == "youtube":
        handle = src["handle"] if src["handle"].startswith("@") else "@" + src["handle"]
        page = get(f"https://www.youtube.com/{urllib.parse.quote(handle)}").decode("utf-8", "ignore")
        m = (re.search(r'"channelId":"(UC[\w-]{22})"', page) or re.search(r'"externalId":"(UC[\w-]{22})"', page)
             or re.search(r"youtube\.com/channel/(UC[\w-]{22})", page))
        if not m:
            raise RuntimeError(f"找不到 {handle} 的频道 ID（handle 写错，或者网络被拦在了同意页）")
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={m.group(1)}"
    elif src["type"] == "podcast":
        q = urllib.parse.urlencode({"term": src["name"], "entity": "podcast", "limit": 5})
        res = json.loads(get(f"https://itunes.apple.com/search?{q}"))["results"]
        want = (src.get("expect") or src["name"]).lower()
        hit = next((r for r in res if want in r.get("collectionName", "").lower() and r.get("feedUrl")), None)
        if not hit:
            raise RuntimeError(f"Apple 播客目录里没找到名字含“{want}”的节目")
        url = hit["feedUrl"]
    else:
        raise RuntimeError("web 类型没有订阅地址，由 Claude 在 /scout 时查看")
    cache[key] = url
    return url


def parse_feed(data, src):
    root = ET.fromstring(data)
    items = []
    if root.tag.endswith("feed"):                      # Atom (YouTube, Posthaven, ...)
        for e in root.findall("a:entry", NS):
            link = e.find("a:link[@rel='alternate']", NS)
            if link is None:
                link = e.find("a:link", NS)
            desc = e.find("media:group/media:description", NS)
            summ = e.find("a:summary", NS) if desc is None else desc
            if summ is None:
                summ = e.find("a:content", NS)
            vid = e.find("yt:videoId", NS)
            items.append({"title": (e.findtext("a:title", "", NS) or "").strip(),
                          "url": link.get("href") if link is not None else "",
                          "published": e.findtext("a:published", "", NS) or e.findtext("a:updated", "", NS),
                          "summary": clean(summ.text if summ is not None else ""),
                          "video_id": vid.text if vid is not None else None})
    else:                                              # RSS 2.0
        for e in root.iter("item"):
            enc = e.find("enclosure")
            items.append({"title": (e.findtext("title") or "").strip(),
                          "url": (e.findtext("link") or (enc.get("url") if enc is not None else "")).strip(),
                          "published": e.findtext("pubDate") or e.findtext("{http://purl.org/dc/elements/1.1/}date"),
                          "summary": clean(e.findtext("description") or e.findtext("content:encoded", "", NS)),
                          "duration": e.findtext("{http://www.itunes.com/dtds/podcast-1.0.dtd}duration"),
                          "video_id": None})
    for it in items:
        it.update(source=src["name"], category=src["category"], type=src["type"])
        if "/shorts/" in it["url"]:
            it["short"] = True
    return items


# ---------- commands ----------

def load_sources():
    cfg = load_json(SCOUT / "sources.json", None)
    if cfg is None:
        sys.exit("没找到 scout/sources.json")
    return cfg, [s for s in cfg["sources"] if s.get("enabled", True)]


def cmd_check(a):
    cfg, sources = load_sources()
    cache = {}
    bad = 0
    for s in sources:
        if s["type"] == "web":
            print(f"[WEB ] {s['name']}：由 Claude 在 /scout 时查看 {s['url']}")
            continue
        try:
            url = resolve(s, cache)
            items = parse_feed(get(url), s)
            latest = max((parse_date(i["published"]) for i in items if parse_date(i["published"])), default=None)
            print(f"[OK  ] {s['name']}：{len(items)} 条，最新 {latest:%Y-%m-%d}" if latest else f"[OK  ] {s['name']}：{len(items)} 条")
        except Exception as e:  # noqa: BLE001 - report every kind of failure per source
            bad += 1
            print(f"[FAIL] {s['name']}：{e}")
    save_json(SCOUT / "resolved.json", cache)
    print(f"\n{len(sources) - bad}/{len(sources)} 个源可用。" + ("失败的源改 sources.json 后再跑一次 check；"
          "国内网络访问 YouTube 等需要先设置代理（环境变量 HTTPS_PROXY）。" if bad else ""))


def cmd_fetch(a):
    cfg, sources = load_sources()
    days = a.days or cfg.get("lookback_days", 3)
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    cache = load_json(SCOUT / "resolved.json", {})
    seen = load_json(SCOUT / "seen.json", {})
    new, errors, web = [], [], []
    for s in sources:
        if s["type"] == "web":
            web.append(s)
            continue
        try:
            for it in parse_feed(get(resolve(s, cache)), s):
                d = parse_date(it["published"])
                if not it["url"] or it["url"] in seen or it.get("short") or (d and d < since):
                    continue
                it["published"] = d.strftime("%Y-%m-%d") if d else ""
                new.append(it)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{s['name']}：{e}")
    save_json(SCOUT / "resolved.json", cache)
    today = dt.date.today().isoformat()
    for it in new:                      # each item shows up in exactly one day's inbox
        seen[it["url"]] = today
    save_json(SCOUT / "seen.json", seen)
    box = SCOUT / "inbox" / f"{today}.json"
    found = len(new)
    new = load_json(box, {}).get("items", []) + new      # same-day reruns add to the inbox, never wipe it
    new.sort(key=lambda x: (x["category"], x["published"]))
    save_json(box, {"date": today, "items": new, "web": web, "errors": errors})
    md = [f"# 素材收件箱 {today}", "", f"过去 {days} 天的新内容共 {len(new)} 条（已去重，已排除 Shorts）。", ""]
    for cat in ("播客访谈", "大会演讲", "一手文章"):
        rows = [i for i in new if i["category"] == cat]
        if not rows:
            continue
        md += [f"## {cat}（{len(rows)}）", ""]
        for i in rows:
            dur = f"，时长 {i['duration']}" if i.get("duration") else ""
            md += [f"- **{i['title']}**", f"  {i['source']} · {i['published']}{dur} · {i['url']}"]
            if i["summary"]:
                md.append(f"  {i['summary']}")
        md.append("")
    if web:
        md += ["## 需要 Claude 用网页工具查看的源", ""] + [f"- {w['name']}：{w['url']}" for w in web] + [""]
    if errors:
        md += ["## 拉取失败", ""] + [f"- {e}" for e in errors] + [""]
    out = SCOUT / "inbox" / f"{today}.md"
    out.write_text("\n".join(md), encoding="utf-8")
    print(f"本次新增 {found} 条，今天收件箱共 {len(new)} 条，失败 {len(errors)} 个源：{out}")


def vtt_to_text(vtt):
    """Auto captions repeat lines as they roll; keep each line once with its first timestamp."""
    out, last = [], ""
    for block in re.split(r"\n\s*\n", vtt):
        m = re.search(r"(\d{2}):(\d{2}):(\d{2})\.\d{3}\s+-->", block)
        if not m:
            continue
        t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        lines = [re.sub(r"<[^>]+>", "", ln).strip() for ln in block.split("\n")[1:] if "-->" not in ln]
        for ln in lines:
            ln = html.unescape(ln)
            if ln and ln != last and not (last and last.endswith(ln)):
                out.append(f"[{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}] {ln}")
                last = ln
    return "\n".join(out)


def video_id(s):
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/live/)([\w-]{11})", s)
    return m.group(1) if m else s


def cmd_captions(a):
    cap = SCOUT / "captions"
    cap.mkdir(parents=True, exist_ok=True)
    for raw in a.videos:
        vid = video_id(raw)
        out = cap / f"{vid}.txt"
        if out.exists():
            print(f"[已有] {out}")
            continue
        p = subprocess.run([sys.executable, "-m", "yt_dlp", "--skip-download", "--no-simulate", "--write-subs",
                            "--write-auto-subs", "--sub-langs", "en.*,en", "--sub-format", "vtt",
                            "--print", "%(duration)s|%(title)s", "-o", str(cap / "%(id)s.%(ext)s"),
                            f"https://www.youtube.com/watch?v={vid}"], capture_output=True, text=True,
                           encoding="utf-8", errors="ignore")
        vtts = sorted(cap.glob(f"{vid}*.vtt"), key=lambda f: ("orig" not in f.name, len(f.name)))
        if not vtts:
            print(f"[失败] {vid}：没有英文字幕。{p.stderr.strip()[-300:]}")
            continue
        head = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "|"
        dur, _, title = head.partition("|")
        text = vtt_to_text(vtts[0].read_text(encoding="utf-8", errors="ignore"))
        mins = f"{int(float(dur)) // 60} 分钟" if dur.replace(".", "").isdigit() else "未知"
        out.write_text(f"# {title}\n# https://www.youtube.com/watch?v={vid}\n# 时长 {mins}；自动字幕，"
                       f"时间码和用词可能不准，引用前要对照视频核实\n\n{text}\n", encoding="utf-8")
        for f in vtts:
            f.unlink()
        print(f"[OK  ] {out}（{mins}）")


def cmd_mark(a):
    seen = load_json(SCOUT / "seen.json", {})
    for u in a.urls:
        seen[u] = dt.date.today().isoformat()
    save_json(SCOUT / "seen.json", seen)
    print(f"已标记 {len(a.urls)} 条")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    f = sub.add_parser("fetch")
    f.add_argument("--days", type=int)
    f.set_defaults(fn=cmd_fetch)
    c = sub.add_parser("captions")
    c.add_argument("videos", nargs="+")
    c.set_defaults(fn=cmd_captions)
    m = sub.add_parser("mark")
    m.add_argument("urls", nargs="+")
    m.set_defaults(fn=cmd_mark)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
