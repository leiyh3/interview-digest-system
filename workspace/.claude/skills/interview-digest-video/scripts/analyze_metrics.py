#!/usr/bin/env python3
"""分析平台导出的视频数据，找出可能影响播放、收藏、涨粉的因素。

用法（抖音和视频号分开跑，不要合并）：
    python analyze_metrics.py douyin.csv   --platform 抖音   --out data/douyin_findings.md
    python analyze_metrics.py channels.csv --platform 视频号 --out data/channels_findings.md [--top 3]

视频号的分发更依赖转发和社交关系，报告里的“转发率（转发/播放）”对它最重要。
两个平台后台导出的列名会随版本变化，认不出的列会原样当作特征列分析；
必填列认不到时，脚本会列出它读到的表头，按表头改名或在 ALIASES 里加别名。

只依赖 numpy。CSV 用 UTF-8 保存，表头可以是中文（如“播放量”“收藏”“涨粉”）或下面的英文列名，
空表头模板见 templates/metrics_template.csv。必填列：标题（title）、播放量（plays）。其余列有就用，没有就跳过。
表里没有的特征（人物认知度、开头钩子类型等）可以自己加列，填数字或 0/1，脚本会一并分析。

它给出的是统计线索。把两个平台的结论合并，按 references/data_analysis.md 的格式写成 data_insights.md，
再交给数据分析师评审。这个脚本只给相关性线索，不证明因果。样本少于 20 条时尤其不能当规律用，报告里会标出。
"""
import argparse
import csv
import math
import re
import sys
from pathlib import Path

import numpy as np

ALIASES = {
    "title": ["title", "标题", "作品标题", "视频标题", "作品名称", "笔记标题"],
    "plays": ["plays", "播放量", "播放", "播放次数", "观看量", "观看", "观看次数", "阅读量"],
    "saves": ["saves", "收藏", "收藏量", "收藏数"],
    "follows": ["follows", "涨粉", "涨粉数", "新增粉丝", "粉丝增量"],
    "likes": ["likes", "点赞", "点赞量", "点赞数", "喜欢", "喜欢数"],
    "comments": ["comments", "评论", "评论量", "评论数"],
    "shares": ["shares", "转发", "分享", "转发量", "分享量", "转发数", "分享数", "分享次数"],
    "duration_sec": ["duration_sec", "时长", "视频时长", "时长(秒)", "时长（秒）"],
    "completion": ["completion", "完播率", "播完率", "完整播放率"],
    "skip_2s": ["skip_2s", "2秒跳出率", "2s跳出率", "两秒跳出率"],
}
OUTCOMES = ["plays", "saves", "follows", "comments", "shares", "save_rate", "follow_rate", "share_rate"]
OUTCOME_LABEL = {"plays": "播放", "saves": "收藏", "follows": "涨粉", "comments": "评论",
                 "shares": "转发", "save_rate": "收藏率（收藏/播放）", "follow_rate": "涨粉率（涨粉/播放）",
                 "share_rate": "转发率（转发/播放）"}
# 这几个是“结果”，不当作原因去分析；likes 与 comments/shares 一样属于结果
NOT_FEATURES = {"title", "plays", "saves", "follows", "likes", "comments", "shares"}
FEATURE_LABEL = {
    "title_len": "标题长度", "title_has_number": "标题含数字", "title_has_question": "标题含问号",
    "title_has_colon": "标题含冒号", "title_has_quote": "标题含引号",
    "duration_sec": "视频时长", "completion": "完播率", "skip_2s": "2 秒跳出率",
}
# 结果指标和特征，各自对应评分标准（assets/rubric.json）里的哪几项
OUTCOME_RUBRIC = {"plays": ["H1", "H2", "H3"], "saves": ["D2"], "follows": ["D2", "C2"],
                  "save_rate": ["D2"], "follow_rate": ["D2", "C2"], "share_rate": ["D2", "D3"]}
FEATURE_RUBRIC = {"title_len": ["D3"], "title_has_number": ["H1", "H2"], "title_has_question": ["H1"],
                  "title_has_colon": ["H2"], "title_has_quote": ["H1"], "skip_2s": ["H1"],
                  "completion": ["R1", "R2"], "duration_sec": ["D1"]}
SMALL_SAMPLE = 20  # 与 references/data_analysis.md 一致：少于 20 条只能得出“可能”的假设


def rubric_names():
    """从 assets/rubric.json 读评分项名称；读不到就只显示编号。"""
    import json
    p = Path(__file__).resolve().parent.parent / "assets" / "rubric.json"
    try:
        return {i["id"]: i["name"] for i in json.loads(p.read_text(encoding="utf-8"))["items"]}
    except (OSError, ValueError, KeyError):
        return {}


def norm(h):
    return re.sub(r"\s+", "", h.strip().lower())


def parse_num(s):
    if s is None:
        return None
    t = str(s).strip().replace(",", "").replace("，", "")
    if t in ("", "-", "--", "—", "NA", "N/A", "na", "nan", "null"):
        return None
    mult = 1.0
    if t.endswith("%"):
        mult, t = 0.01, t[:-1]
    elif t.endswith("万") or t[-1:] in ("w", "W"):
        mult, t = 1e4, t[:-1]
    elif t.endswith("亿"):
        mult, t = 1e8, t[:-1]
    elif t[-1:] in ("k", "K"):
        mult, t = 1e3, t[:-1]
    try:
        return float(t) * mult
    except ValueError:
        return None


def parse_duration(s):
    t = str(s).strip()
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{1,2})", t)
    if m:
        return int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    m = re.fullmatch(r"(?:(\d+)分)?(?:(\d+)秒)?", t)
    if m and (m.group(1) or m.group(2)):
        return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    return parse_num(s)


def load(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if len(rows) < 2:
        sys.exit("CSV 里没有数据行。")
    header, body = rows[0], rows[1:]
    amap = {norm(a): key for key, names in ALIASES.items() for a in names}
    col_key, extras = {}, []
    for i, h in enumerate(header):
        k = amap.get(norm(h))
        if k and k not in col_key.values():
            col_key[i] = k
        elif h.strip():
            extras.append((i, h.strip()))
    keys = set(col_key.values())
    missing = [{"title": "标题", "plays": "播放量"}[k] for k in ("title", "plays") if k not in keys]
    if missing:
        sys.exit("缺少必填列：" + "、".join(missing) +
                 "\n表头里认到的列：" + "、".join(header) +
                 "\n请把标题列命名为“标题”或 title，播放量列命名为“播放量”或 plays。")
    data = []
    for r in body:
        rec = {}
        for i, k in col_key.items():
            v = r[i] if i < len(r) else ""
            if k == "title":
                rec[k] = v.strip()
            elif k == "duration_sec":
                rec[k] = parse_duration(v)
            else:
                rec[k] = parse_num(v)
        for i, h in extras:
            v = parse_num(r[i]) if i < len(r) else None
            rec["extra:" + h] = v
        data.append(rec)
    # 额外列里没有任何数字的（比如日期、备注）丢掉
    extra_keys = [k for k in (data[0].keys() if data else []) if k.startswith("extra:")]
    drop = [k for k in extra_keys if sum(1 for d in data if d.get(k) is not None) < max(3, len(data) // 2)]
    for d in data:
        for k in drop:
            d.pop(k, None)
    data = [d for d in data if d.get("plays") is not None and d.get("title")]
    return data


def add_derived(data):
    for d in data:
        t = d["title"]
        d["title_len"] = float(len(t))
        # 阿拉伯数字，或“两/二到九/十/百/千/万/亿”加量词。单独的“一”不算（“一句话”“一个”是习惯说法，不是数字钩子）
        d["title_has_number"] = float(bool(re.search(r"\d|[两二三四五六七八九十百千万亿]+(?:个|条|句|年|倍|次|分|成|折|岁|天|小时|块|元)", t)))
        d["title_has_question"] = float(bool(re.search(r"[?？]", t)))
        d["title_has_colon"] = float(bool(re.search(r"[:：]", t)))
        d["title_has_quote"] = float(bool(re.search(r"[\"“”「」'‘’]", t)))
        p = d.get("plays")
        d["save_rate"] = d["saves"] / p if d.get("saves") is not None and p else None
        d["follow_rate"] = d["follows"] / p if d.get("follows") is not None and p else None
        d["share_rate"] = d["shares"] / p if d.get("shares") is not None and p else None


def rankdata(a):
    order = np.argsort(a, kind="mergesort")
    s = a[order]
    ranks = np.empty(len(a))
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and s[j + 1] == s[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    rx, ry = rankdata(x), rankdata(y)
    if rx.std() == 0 or ry.std() == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def correlations(data, outcome, features):
    res = []
    for f in features:
        pairs = [(d[f], d[outcome]) for d in data if d.get(f) is not None and d.get(outcome) is not None]
        if len(pairs) < 5:
            continue
        xs, ys = zip(*pairs)
        r = spearman(xs, ys)
        if r is None:
            continue
        n = len(pairs)
        res.append({"feature": f, "rho": r, "n": n, "clue": abs(r) > 2 / math.sqrt(n),
                    "binary": set(xs) <= {0.0, 1.0}, "xs": xs, "ys": ys})
    res.sort(key=lambda z: -abs(z["rho"]))
    return res


def label(f):
    return FEATURE_LABEL.get(f, f.replace("extra:", "") + "（你加的列）")


def fmt_num(v):
    if v is None:
        return "-"
    if abs(v) >= 1e4:
        return f"{v / 1e4:.1f}万"
    if abs(v) < 1 and v != 0:
        return f"{v:.3f}"
    return f"{v:,.0f}"


def median_text(c, outcome):
    if not c["binary"]:
        return ""
    one = [y for x, y in zip(c["xs"], c["ys"]) if x == 1.0]
    zero = [y for x, y in zip(c["xs"], c["ys"]) if x == 0.0]
    if len(one) < 2 or len(zero) < 2:
        return ""
    return f"有 → 中位数 {fmt_num(float(np.median(one)))}（{len(one)} 条）；无 → 中位数 {fmt_num(float(np.median(zero)))}（{len(zero)} 条）"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--out", required=True)
    ap.add_argument("--platform", default="", help="平台名，如 抖音、视频号，会写进报告标题")
    ap.add_argument("--top", type=int, default=3, help="每个指标列出相关性最强的几个特征，也是最好和最差各列几条")
    args = ap.parse_args()

    if not Path(args.csv).exists():
        sys.exit(f"找不到文件：{args.csv}")
    data = load(args.csv)
    if not data:
        sys.exit("没有同时有标题和播放量的数据行。")
    add_derived(data)
    n = len(data)

    present = {k for d in data for k, v in d.items() if v is not None}
    feats = [f for f in ["title_len", "title_has_number", "title_has_question", "title_has_colon",
                         "title_has_quote", "duration_sec", "completion", "skip_2s"] if f in present]
    feats += sorted(k for k in present if k.startswith("extra:"))
    outs = [o for o in OUTCOMES if o in present]

    L = [f"# 数据结论{'（' + args.platform + '）' if args.platform else ''}", ""]
    L += ["## 样本", "", f"- 视频数：{n}"]
    if n < SMALL_SAMPLE:
        L.append(f"- **样本不足**：只有 {n} 条（少于 {SMALL_SAMPLE} 条）。下面的相关性只作为下一条先试什么的假设，不是规律，写结论时要带上样本量。")
    const = [f for f in feats if len({d[f] for d in data if d.get(f) is not None}) <= 1]
    feats = [f for f in feats if f not in const]
    L.append(f"- 分析的原因特征：{'、'.join(label(f) for f in feats) if feats else '无'}")
    if const:
        L.append(f"- 没有参与分析的特征：{'、'.join(label(f) for f in const)}（所有视频取值相同，没有对比）")
    L.append(f"- 判断线索的标准：|相关系数| 大于 2/√n，即本表 {2 / math.sqrt(max(n, 1)):.2f}。没过这条线的，视为没有线索。")
    L.append("")

    ranked = sorted(data, key=lambda d: -d["plays"])
    total = sum(d["plays"] for d in data)
    L += ["## 播放的集中度", ""]
    if total > 0:
        L.append(f"- 播放最高的 1 条占全部播放的 {ranked[0]['plays'] / total:.0%}，最高的 3 条占 {sum(d['plays'] for d in ranked[:3]) / total:.0%}。")
        med = float(np.median([d["plays"] for d in data]))
        L.append(f"- 播放中位数 {fmt_num(med)}，最高值是中位数的 {ranked[0]['plays'] / med:.0f} 倍。" if med > 0 else "- 播放中位数为 0。")
        if ranked[0]["plays"] / total > 0.5:
            L.append("- 数据高度集中在一条视频上，下面的相关系数容易被它带偏，要和“最好最差对比”一起看。")
    L.append("")

    L += ["## 各指标最相关的特征", "",
          "相关系数（斯皮尔曼）范围 -1 到 1，正数表示特征越高、指标越高。“线索”列标是否超过上面的标准。", ""]
    best = {}
    for o in outs:
        cs = correlations(data, o, feats)
        L += [f"### {OUTCOME_LABEL[o]}", ""]
        if not cs:
            L += ["没有足够的数据（需要至少 5 条同时有该指标和特征的视频）。", ""]
            continue
        L += ["| 特征 | 相关系数 | 样本 | 线索 |", "|---|---|---|---|"]
        for c in cs[:args.top]:
            L.append(f"| {label(c['feature'])} | {c['rho']:+.2f} | {c['n']} | {'有' if c['clue'] else '无'} |")
        L.append("")
        for c in cs[:args.top]:
            mt = median_text(c, o)
            if c["clue"] and mt:
                L.append(f"- {label(c['feature'])}：{mt}")
        if any(c["clue"] and median_text(c, o) for c in cs[:args.top]):
            L.append("")
        best[o] = [c for c in cs if c["clue"]][:args.top]

    L += ["## 表现最好和最差的视频", ""]
    k = min(args.top, max(1, n // 2))
    def line(d):
        bits = [f"播放 {fmt_num(d['plays'])}"]
        for key, nm in (("saves", "收藏"), ("follows", "涨粉"), ("completion", "完播率")):
            if d.get(key) is not None:
                bits.append(f"{nm} {d[key]:.0%}" if key == "completion" else f"{nm} {fmt_num(d[key])}")
        return f"- {d['title']}（{'，'.join(bits)}）"
    L += ["最好：", ""] + [line(d) for d in ranked[:k]] + ["", "最差：", ""] + [line(d) for d in ranked[-k:][::-1]]
    L += ["", "对比时看这几件事：人物对观众熟不熟、标题里有没有冲突和数字、开头 3 秒给了什么、时长。", ""]

    q = max(2, n // 4)
    if n >= 8:
        top, bot = ranked[:q], ranked[-q:]

        def fmt_feat(f, v, binary):
            if binary or f in ("completion", "skip_2s"):
                return f"{v:.0%}"
            if f == "duration_sec":
                return f"{v:.0f} 秒"
            if f == "title_len":
                return f"{v:.0f} 字"
            return fmt_num(v)

        rows = []
        for f in feats:
            tv = [d[f] for d in top if d.get(f) is not None]
            bv = [d[f] for d in bot if d.get(f) is not None]
            if len(tv) < 2 or len(bv) < 2:
                continue
            binary = set(tv + bv) <= {0.0, 1.0}
            tm = float(np.mean(tv)) if binary else float(np.median(tv))
            bm = float(np.mean(bv)) if binary else float(np.median(bv))
            rows.append(f"| {label(f)}{'（占比）' if binary else '（中位数）'} | {fmt_feat(f, tm, binary)} | {fmt_feat(f, bm, binary)} |")
        if rows:
            L += [f"## 播放前 25% 与后 25% 的对比", "",
                  f"各取 {q} 条。看两组在哪些特征上差得最多，再回去看这几条视频本身。", "",
                  f"| 特征 | 前 {q} 条 | 后 {q} 条 |", "|---|---|---|"] + rows + [""]

    L += ["## 对下一条视频的建议", ""]
    sug = []
    for o in ("plays", "saves", "follows"):
        for c in best.get(o, [])[:2]:
            direction = "更高" if c["rho"] > 0 else "更低"
            sug.append(f"{label(c['feature'])}与{OUTCOME_LABEL[o]}{'正' if c['rho'] > 0 else '负'}相关（ρ={c['rho']:+.2f}，n={c['n']}）："
                       f"下一条可以试着让这个特征{'做足' if c['rho'] > 0 else '收敛'}，观察{OUTCOME_LABEL[o]}是否{direction}。")
    if sug:
        L += [f"{i + 1}. {s}" for i, s in enumerate(sug[:5])]
    else:
        L.append("没有特征超过线索标准。下一条按评分标准正常做，并把本次导出的数据与后续几条一起累积。")
    L += ["", "这些是线索，只有再发几条做对照，才能确认。", ""]

    L += ["## 给数据分析师评审的提示", ""]
    names = rubric_names()
    rub = {}
    for o, cs in best.items():
        for c in cs:
            for item in OUTCOME_RUBRIC.get(o, []) + FEATURE_RUBRIC.get(c["feature"], []):
                rub.setdefault(item, []).append(f"{OUTCOME_LABEL[o]}与{label(c['feature'])}相关")
    if rub:
        L.append("下面这些评分项在这份数据里有线索，打分时重点对照。没出现的项，按经验打分并在 notes 里写“无数据，按经验”：")
        L.append("")
        for item in sorted(rub):
            L.append(f"- {item} {names.get(item, '')}：{'；'.join(dict.fromkeys(rub[item]))}".replace("  ", " "))
        if n < SMALL_SAMPLE:
            L += ["", f"样本只有 {n} 条，以上都按假设对待，不要说成定论。"]
    else:
        L.append("没有特征超过线索标准，评分项都按经验打分，并在 notes 里写“无数据，按经验”。")
    L += ["", "## 不确定性", "",
          "- 相关不等于因果，热点、发布时间、推荐池都会影响结果，而这里没有控制。",
          f"- 样本量 {n}。",
          "- 标题特征是从标题文字里自动提取的，开头钩子、人物知名度这类特征需要你手动加列才能被分析。",
          "- 列没有出现在表里的指标，本报告没有分析。", ""]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"完成：{n} 条视频，写入 {out}")
    if n < SMALL_SAMPLE:
        print(f"提示：样本不足（{n} 条），结论只作假设。")


if __name__ == "__main__":
    main()
