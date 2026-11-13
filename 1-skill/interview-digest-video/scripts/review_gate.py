#!/usr/bin/env python3
"""Score gate, change log and handoff for the two independent reviewers.

Commands:
  review_gate.py init    --dir reviews --round 1       write blank score sheets
  review_gate.py check   --dir reviews --round 1       evaluate the round, append to review_log
  review_gate.py change  --dir reviews --round 1 --item H1 --change "..." [--reason "..."]
  review_gate.py handoff --dir reviews                 write HANDOFF.md (all changes + score history)

Each reviewer fills reviews/round_N_analyst.json and reviews/round_N_audience.json
on their own, without seeing the other file:
  {"reviewer": "analyst", "scores": {"H1": 4, ...12 items...},
   "notes": {"H1": "why"}, "blockers": ["anything that must be fixed before release"]}
The gate passes only when BOTH reviewers reach the pass score, nobody scores an
item below the block level, and nobody lists a blocker.
"""
import argparse
import json
import sys
from pathlib import Path

RUBRIC = json.loads((Path(__file__).resolve().parent.parent / "assets" / "rubric.json").read_text(encoding="utf-8"))
ITEM_IDS = [i["id"] for i in RUBRIC["items"]]
NAMES = {i["id"]: i["name"] for i in RUBRIC["items"]}
REVIEWERS = ["analyst", "audience"]
LABEL = {"analyst": "数据分析师", "audience": "观众剪辑"}
NA_OK = set(RUBRIC.get("na_allowed", []))


def scored(scores):
    """Items that carry a number (NA items are left out of the total and the checks)."""
    return [i for i in ITEM_IDS if scores.get(i) != "NA"]


def read_json(path, default=None):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_init(a):
    d = Path(a.dir)
    d.mkdir(parents=True, exist_ok=True)
    for who in REVIEWERS:
        p = d / f"round_{a.round}_{who}.json"
        if p.exists():
            print(f"exists, kept: {p}")
            continue
        write_json(p, {"reviewer": who, "scores": {i: None for i in ITEM_IDS},
                       "notes": {i: "" for i in ITEM_IDS}, "blockers": []})
        print(f"created {p}")


def load_sheet(d, rnd, who):
    p = Path(d) / f"round_{rnd}_{who}.json"
    sheet = read_json(p)
    if sheet is None:
        sys.exit(f"missing {p}")
    scores = sheet.get("scores", {})
    bad_na = [i for i in ITEM_IDS if scores.get(i) == "NA" and i not in NA_OK]
    if bad_na:
        sys.exit(f"{p.name}: only {', '.join(sorted(NA_OK)) or 'no items'} may be NA, got NA for {', '.join(bad_na)}")
    missing = [i for i in ITEM_IDS if scores.get(i) != "NA" and
               (not isinstance(scores.get(i), int) or not 1 <= scores[i] <= 5)]
    if missing:
        sys.exit(f"{p.name}: items without a 1-5 integer score: {', '.join(missing)}")
    return sheet


def total(scores):
    items = scored(scores)
    return round(sum(scores[i] for i in items) / len(items) * 20, 1)


def cmd_check(a):
    sheets = {who: load_sheet(a.dir, a.round, who) for who in REVIEWERS}
    pass_score = a.pass_score if a.pass_score is not None else RUBRIC["pass_score"]
    block = RUBRIC["block_below"]
    result = {"round": a.round, "reviewers": {}, "reasons": []}
    for who, sh in sheets.items():
        s = sh["scores"]
        low = [i for i in scored(s) if s[i] < block]
        result["reviewers"][who] = {"total": total(s), "low_items": low, "blockers": sh.get("blockers", []),
                                    "scores": s, "notes": sh.get("notes", {})}
        if total(s) < pass_score:
            result["reasons"].append(f"{LABEL[who]} 总分 {total(s)} 低于 {pass_score}")
        if low:
            result["reasons"].append(f"{LABEL[who]} 给出低于 {block} 分的项：" + "、".join(f"{i} {NAMES[i]}" for i in low))
        if sh.get("blockers"):
            result["reasons"].append(f"{LABEL[who]} 列出必须修改的问题 {len(sh['blockers'])} 条")
    both = set(scored(sheets["analyst"]["scores"])) & set(scored(sheets["audience"]["scores"]))
    gaps = [i for i in ITEM_IDS if i in both and abs(sheets["analyst"]["scores"][i] - sheets["audience"]["scores"][i]) >= 2]
    result["disagreements"] = gaps
    if not result["reasons"]:
        result["decision"] = "PASS"
    elif a.round >= RUBRIC["max_rounds"]:
        result["decision"] = "STOP_AND_HANDOFF"
    else:
        result["decision"] = "REVISE"

    log_path = Path(a.dir) / "review_log.json"
    log = [r for r in read_json(log_path, []) if r["round"] != a.round]
    log.append(result)
    log.sort(key=lambda r: r["round"])
    write_json(log_path, log)
    write_log_md(a.dir, log)

    print(f"round {a.round}: 数据分析师 {result['reviewers']['analyst']['total']} / 观众剪辑 {result['reviewers']['audience']['total']}"
          f"  -> {result['decision']}")
    for r in result["reasons"]:
        print("  - " + r)
    if gaps:
        print("  两位评审分歧 2 分以上：" + "、".join(f"{i} {NAMES[i]}" for i in gaps))
    if result["decision"] == "STOP_AND_HANDOFF":
        print(f"  已达到 {RUBRIC['max_rounds']} 轮上限，停止自动修改，交给人工决定。")
    sys.exit(0 if result["decision"] == "PASS" else 2)


def write_log_md(d, log):
    lines = ["# 评审记录\n", "| 轮次 | 数据分析师 | 观众剪辑 | 结论 | 分歧项 |", "|---|---|---|---|---|"]
    for r in log:
        lines.append(f"| {r['round']} | {r['reviewers']['analyst']['total']} | {r['reviewers']['audience']['total']} | "
                     f"{r['decision']} | {', '.join(r['disagreements']) or '无'} |")
    for r in log:
        lines.append(f"\n## 第 {r['round']} 轮 — {r['decision']}")
        for who in REVIEWERS:
            rv = r["reviewers"][who]
            lines.append(f"\n**{LABEL[who]}**（{rv['total']} 分）")
            weak = sorted(scored(rv["scores"]), key=lambda i: rv["scores"][i])[:3]
            for i in weak:
                note = rv["notes"].get(i, "")
                lines.append(f"- {i} {NAMES[i]}：{rv['scores'][i]} 分" + (f"，{note}" if note else ""))
            for b in rv["blockers"]:
                lines.append(f"- 必须修改：{b}")
    (Path(d) / "review_log.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_change(a):
    p = Path(a.dir) / "changes.json"
    ch = read_json(p, [])
    ch.append({"round": a.round, "item": a.item, "change": a.change, "reason": a.reason or ""})
    write_json(p, ch)
    print(f"logged change for round {a.round}: {a.item} {a.change}")


def cmd_handoff(a):
    log = read_json(Path(a.dir) / "review_log.json", [])
    changes = read_json(Path(a.dir) / "changes.json", [])
    tech = read_json(a.tech_report) if a.tech_report else None
    lines = ["# 交接清单\n"]
    if log:
        last = log[-1]
        lines.append(f"最终结论：**{last['decision']}**（第 {last['round']} 轮）\n")
        lines.append("## 分数变化\n")
        lines.append("| 轮次 | 数据分析师 | 观众剪辑 |")
        lines.append("|---|---|---|")
        for r in log:
            lines.append(f"| {r['round']} | {r['reviewers']['analyst']['total']} | {r['reviewers']['audience']['total']} |")
    lines.append("\n## 所有改动\n")
    if changes:
        for r in sorted({c["round"] for c in changes}):
            lines.append(f"**第 {r} 轮**")
            for c in [x for x in changes if x["round"] == r]:
                why = f"（{c['reason']}）" if c["reason"] else ""
                lines.append(f"- [{c['item']} {NAMES.get(c['item'], '')}] {c['change']}{why}")
            lines.append("")
    else:
        lines.append("没有记录到改动。\n")
    if log:
        lines.append("## 仍然偏弱的项（最后一轮低于 4 分）\n")
        any_weak = False
        for who in REVIEWERS:
            rv = log[-1]["reviewers"][who]
            for i in scored(rv["scores"]):
                if rv["scores"][i] < 4:
                    any_weak = True
                    note = rv["notes"].get(i, "")
                    lines.append(f"- {LABEL[who]}：{i} {NAMES[i]} {rv['scores'][i]} 分" + (f"，{note}" if note else ""))
        if not any_weak:
            lines.append("无。")
    if tech:
        lines.append("\n## 技术审片\n")
        for c in tech["checks"]:
            lines.append(f"- {c['id']} {c['name']}：{c['status']}")
        if any(c["status"] == "fail" for c in tech["checks"]):
            lines.append("\n**技术审片仍有 fail 项，评审分数再高也不能发布，必须先处理。**")
    lines.append("\n## 发布前请人工确认\n")
    lines += ["- 人物标签、数字出处、引述原话（对照 storyline.md 的出处表和时间码）",
              "- 版权：原片占比、来源角标、简介里的来源说明，必要时联系版权方",
              "- AI 内容标注：用了 TTS、语音复刻或 AI 生成画面时，按抖音、视频号发布页的要求勾选或声明",
              "- 封面与标题：抖音、视频号各一版，是否符合账号一贯风格",
              "- 评分是同一个模型给出的时，只当参考，不代表播放量"]
    out = Path(a.dir) / "HANDOFF.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("init", cmd_init), ("check", cmd_check), ("change", cmd_change), ("handoff", cmd_handoff)):
        sp = sub.add_parser(name)
        sp.add_argument("--dir", default="reviews")
        if name != "handoff":
            sp.add_argument("--round", type=int, required=True)
        if name == "check":
            sp.add_argument("--pass-score", type=float, default=None)
        if name == "change":
            sp.add_argument("--item", required=True)
            sp.add_argument("--change", required=True)
            sp.add_argument("--reason")
        if name == "handoff":
            sp.add_argument("--tech-report", help="tech_report.json to include")
        sp.set_defaults(fn=fn)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
