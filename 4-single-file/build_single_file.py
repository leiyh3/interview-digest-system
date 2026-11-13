#!/usr/bin/env python3
"""Rebuild interview-digest-video-ALL-IN-ONE.md from the skill folder, so the two never drift apart.

  python3 4-single-file/build_single_file.py
"""
import json
import re
from pathlib import Path

root = Path(__file__).resolve().parent.parent
skill = root / "1-skill" / "interview-digest-video"


def demote(md):
    return re.sub(r"^(#{1,5}) ", lambda m: "#" + m.group(1) + " ", md, flags=re.M)


body = (skill / "SKILL.md").read_text(encoding="utf-8")
front, text = body.split("---", 2)[1], body.split("---", 2)[2]
desc = re.search(r"description: (.*)", front).group(1)
parts = [
    "# 访谈精读视频（单文件版）\n",
    "> 这是 skill `interview-digest-video` 的单文件版，给不能加载文件夹的工具用，由 `build_single_file.py` 从 skill 文件夹生成，不要手改。",
    f"> 适用场景：{desc}",
    ">",
    "> **没有脚本**：文中的 `scripts/` 命令只在 `interview-digest-video.zip` 里。没有脚本时，在自己电脑上运行，或让当前工具照着流程和清单手工做。",
    ">",
    "> 文中的 `references/xxx.md`、`assets/xxx.json` 对应本文件最后的同名附录。只想在聊天里用，可以直接用同目录的 `chat-prompt.md`。\n",
    "---\n", demote(text.strip()), ""]
apps = [("A", "references/edit_plan.md"), ("B", "references/tech_review.md"), ("C", "references/review_prompts.md"),
        ("D", "references/data_analysis.md"), ("E", "references/topic_and_title.md")]
for letter, rel in apps:
    parts += ["---\n", f"# 附录 {letter}：{rel}\n", demote((skill / rel).read_text(encoding="utf-8").strip()), ""]
rubric = json.loads((skill / "assets/rubric.json").read_text(encoding="utf-8"))
parts += ["---\n", "# 附录 F：assets/rubric.json（12 项评分标准）\n",
          f"每项 1 到 5 分，5 分最好。通过线：两位评审都不低于 {rubric['pass_score']} 分（计分项平均分乘以 20），"
          f"任何一项不低于 {rubric['block_below']} 分，没有必须修改的问题，最多 {rubric['max_rounds']} 轮。{rubric.get('na_rule', '')}。\n",
          "| 编号 | 名称 | 看什么 |", "|---|---|---|"]
parts += [f"| {i['id']} | {i['name']} | {i['ask']} |" for i in rubric["items"]]
parts += ["", "---\n", "# 附录 G：assets/edit_plan.example.json（剪辑计划示例）\n", "```json",
          (skill / "assets/edit_plan.example.json").read_text(encoding="utf-8").strip(), "```", "",
          "---\n", "# 附录 H：templates/metrics_template.csv（数据表列名）\n", "```",
          (skill / "templates/metrics_template.csv").read_text(encoding="utf-8-sig").strip(), "```", "",
          "必填：标题、播放量。其余有就用，没有就跳过，可以自己加列。抖音、视频号各一张表。", ""]
out = Path(__file__).resolve().parent / "interview-digest-video-ALL-IN-ONE.md"
out.write_text("\n".join(parts), encoding="utf-8")
print(f"wrote {out}")
