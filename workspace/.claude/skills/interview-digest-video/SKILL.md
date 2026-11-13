---
name: interview-digest-video
description: 把一条长访谈或播客视频剪成 10 分钟以内的"精读"短视频（原片片段 + 账号主自己的讲解和图卡 + 逐句中文字幕），发抖音和视频号，并在出片前走技术审片和评审。用户提到访谈精读、长视频剪成精读、抖音视频号精读视频、视频审片、评审团优化时使用。Turns a long interview into a sub-10-minute digest video for Douyin and WeChat Channels, with technical QA and review before release.
---

# 访谈精读视频

把一条长访谈剪成 10 分钟以内的精读视频，发抖音和视频号。成片由三种片段组成：原片片段（说话人原声 + 逐句中文字幕）、讲解段（账号主自己的口播或授权 TTS + 图卡）、纯图卡。流程分两个阶段：先出预览，审片通过后再出成片。

脚本在 `scripts/`，需要 ffmpeg（带 libx264、libass）、Python 3（Pillow、numpy）和一个中文字体（默认 Noto Sans CJK SC）。下载和转写在用户自己的电脑上做。

## 开始前先问清（缺哪项问哪项，一次问完）

1. 素材：源视频文件，加带时间戳的转写（whisper JSON 或 SRT）；用户是否有权剪辑。
2. 平台：默认抖音和视频号，竖版 9:16。
3. 目标时长：默认 10 分钟以内。
4. 账号数据表：抖音、视频号后台导出的视频列表，两个平台分开给。没有就跳过数据分析，D1 填 NA。

看不到的东西（没给的画面、没转写的声音）不假装看过，直接说看不到。

## 流程

**1. 取素材。** 用 `ffprobe` 确认时长、分辨率、有无音轨。把 whisper 的碎片合并成整句：

```
python3 scripts/prep_transcript.py merge whisper.json -o transcript.json
```

剪辑点只能落在整句边界上，所以这一步不能省。脚本会提示哪些句子不是以标点结尾的，抽查开头、结尾和中间各一处，和视频对一对。

**2. 通读并出精剪主线（`storyline.md`），然后停下等用户。** 读完整转写，有视频就抽帧确认谁在说话。主线包含：

- 3 个标题：反转型、数字冲突型、悬念型各一个，公式见 `references/topic_and_title.md`，数字必须出自访谈
- 一句话主张
- 开头 3 秒：第一句话或第一个画面，直接抛冲突、数字或悬念
- 3 到 5 个要点，每个写起止时间码、原话大意、要给观众的判断（这就是讲解段的内容）
- 结尾：一句收束，加一个行动或看下一条的理由；视频号版本要给出转发理由
- 出处表：画面和口播里出现的每个数字、人名、引述，写明时间码；访谈以外的数字写明出处，找不到的标"待核实"
- 预估时长和原片占比（原片累计不超过成片一半，单段原片不超过 60 秒）

用户回复"开始"或给出修改意见后才继续。这是唯一的人工确认点。

**3. 写剪辑计划、翻译、出预览。** 按 `references/edit_plan.md` 写 `plan.json`。只翻译用到的句子：

```
python3 scripts/prep_transcript.py todo transcript.json --plan plan.json -o todo.tsv
# 填 todo.tsv 的 zh 列：人名、数字、单位照原话；不确定的写 [?]，不要猜
python3 scripts/prep_transcript.py apply transcript.json todo.tsv
python3 scripts/cut_video.py plan.json --mode preview --transcript transcript.json --out-dir build
```

讲解段还没录音时，脚本按每秒 4.5 个字估算时长、用静音占位，标为"待配音"。预览是 540×960、低码率。

**4. 技术审片（每次出预览都做）。**

```
python3 scripts/tech_check.py build/preview.mp4 --timeline build/timeline.json --transcript transcript.json --out-dir tech_qa
```

T1 到 T4、T6 的排版部分、T8 由脚本判断；T5、T6 的观感部分、T7 必须对着 `tech_qa/frames/sheet_*.jpg` 逐张看，清单见 `references/tech_review.md`。任何 fail 先修，修完重出预览再审。不是 `cut_video.py` 出的视频用 `scripts/tech_qa.py video.mp4 --out qa/round1`。

**5. 评审循环（技术审片没有 fail 才进入）。**

5a. 有数据表时先跑数据分析，两个平台分开：

```
python3 scripts/analyze_metrics.py douyin.csv --platform 抖音 --out data/douyin_findings.md
python3 scripts/analyze_metrics.py channels.csv --platform 视频号 --out data/channels_findings.md
```

再按 `references/data_analysis.md` 写 `data_insights.md`。样本少于 20 条只当线索，写明样本量。

5b. 两个评审：数据分析师、观众剪辑，提示词在 `references/review_prompts.md`，各按 `assets/rubric.json` 的 12 项打 1 到 5 分。能起子智能体（如 Claude Code 的 Agent 工具）就各起一个，互不通气；不能起时（如网页聊天）按 `review_prompts.md` 的"降级做法"执行，并在交接记录里写明"评审不独立"。

5c. 判定：`python3 scripts/review_gate.py check --round N`。两位都达到 80 分（没有数据时 D1 填 NA，不计入）、没有低于 3 分的项、没有必须修改的问题才通过。不通过就改，每处改动用 `review_gate.py change` 记录，一轮不超过 5 处，重出预览、重审，最多 4 轮。意见冲突时：事实和硬伤 > 前 3 秒和主线 > 节奏 > 其他；拿不准就两种方案都给用户选。

**6. 出成片。** 讲解段换成真实录音（只用用户自己或已授权的声音），翻译补全，然后：

```
python3 scripts/cut_video.py plan.json --mode final --transcript transcript.json --out-dir build_final
python3 scripts/tech_check.py build_final/final.mp4 --timeline build_final/timeline.json --transcript transcript.json --out-dir tech_qa_final
python3 scripts/review_gate.py handoff --tech-report tech_qa_final/tech_report.json
```

成片模式下，"待配音"和缺翻译会被判为 fail。

**7. 交接。** 给用户成片、`HANDOFF.md`（每轮分数、每处改动及来源和理由、采纳和不采纳的建议、仍偏弱的项、发布前要人工确认的事）。技术审片有 fail 时写明"不能发布"。回复里只说结果和一个下一步。

## 边界

- 版权：只用用户有权剪辑的素材；原片少而必要，配上自己的讲解、翻译和判断；每段原片带来源角标，简介里写来源。不整段搬运。
- 事实：画面和口播里的每个数字、人名、引述都能在原访谈里找到并给出时间码，找不到的删掉或标"待核实"。
- 声音：只复刻用户自己或已授权的声音。发布时按抖音、视频号的要求标注 AI 生成内容。
- 评分只说明剪得是否规范，不能保证播放量。

## 文件

- `scripts/prep_transcript.py`：碎片合并成整句；列出要翻译的句子；回填翻译
- `scripts/cut_video.py`：按计划渲染预览或成片：三种片段、三区版式、逐句字幕、来源角标、原片占比检查
- `scripts/tech_check.py`：T1 到 T4、T6 排版、T8 自动检查，其余生成联系表人工看
- `scripts/tech_qa.py`：没有时间线的视频用的独立审片
- `scripts/extract_frames.py`：抽帧和联系表
- `scripts/analyze_metrics.py`：抖音、视频号数据表分析（含转发率）
- `scripts/review_gate.py`：评分表、通过判定、改动记录、交接清单
- `assets/rubric.json`：12 项评分标准、通过线、可填 NA 的项
- `assets/edit_plan.example.json`：剪辑计划示例
- `references/`：剪辑计划格式、技术审片清单、评审提示词、数据分析方法、选题与标题公式
- `templates/metrics_template.csv`：数据表列名模板
