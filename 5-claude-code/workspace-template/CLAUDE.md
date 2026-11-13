# 访谈精读工作区

这个文件夹专门用来做“访谈精读”短视频：把一条长访谈剪成 10 分钟以内的精读视频，发抖音和视频号。我是账号主，你负责出方案、剪预览、审片、改到能发布，最终决定由我做。

## 账号信息（我来填）

- 账号名：
- 栏目名：AI 精读
- 封面配色：深海蓝（#0E1A33 底，#FFC93C 高亮）
- 口播语气：

## 怎么做

- 每条视频都按 skill `interview-digest-video`（在 `.claude/skills/` 里）的流程走，脚本在 `.claude/skills/interview-digest-video/scripts/`。
- 每条视频一个目录：`episodes/<日期>-<短名>/`，用 `.venv/Scripts/python.exe tools/new_episode.py <短名>` 创建。所有中间文件都放在这个目录里，不要散落到别处。
- 账号数据放在 `data/`：`douyin.csv`、`channels.csv`、`data_insights.md`。`data_insights.md` 已存在且不超过 30 天时直接用，不要每条视频都重跑。
- 本机是 Windows。Python 一律用工作区里的虚拟环境 `.venv/Scripts/python.exe`，whisper 用 `.venv/Scripts/whisper.exe`；路径用正斜杠。不要用系统 Python，不要往系统里装包。
- 字幕和图卡默认用微软雅黑，不用另装字体。

## 每日素材采集

- 素材源在 `scout/sources.json`：播客和访谈（Dwarkesh Patel、Lex Fridman、No Priors、Lenny's Podcast、Acquired、a16z、Y Combinator、All-In）、大会演讲（AI 公司发布会和开发者大会、斯坦福和 MIT 公开课）、一手文章（CEO 博客、公司工程博客、论文摘要）。
- 每天定时运行 `tools/scout.py fetch` 拉新内容到 `scout/inbox/`；我输入 `/scout` 时，你从收件箱里挑选题，写进 `scout/pool/` 和 `scout/pool.md`。
- 挑素材看三点：说话的人对普通观众是否有知名度；有没有一句反直觉的话；能不能提炼出一个具体数字或对比。
- 反直觉的话和数字必须从字幕或原文里找到，写出原话和时间码；只看了标题简介的，写“未核实”。
- 只推荐，不下载视频。我选定并确认有权剪辑后，才下载。

## 必须停下来等我的地方

1. 精剪主线（`storyline.md`）写完后，停下，等我回复“开始”或修改意见。
2. 讲解段需要录音时，列出每段口播稿（片段 id、文字、预计秒数），停下等我录好放进 `vo/`。我说“先用静音占位”就继续。
3. 评审 4 轮还没通过时，停下，把分歧和两种方案给我选。
4. /scout 写完今天的选题后，停下等我选。

其余步骤不用问我，做完一步用一两句话说结果。

## 硬规则

- 只用我有权剪辑的素材。原片累计不超过成片一半，单段原片不超过 60 秒；每段原片带来源角标，简介写来源。
- 画面、字幕、口播里的每个数字、人名、引述都必须能在原访谈里找到并写出时间码；找不到的删掉或标“待核实”。翻译时人名、数字、单位照原话。
- 只用我自己的声音或我授权的声音，不复刻别人的声音。
- 没看过的东西不假装看过。看画面就抽帧看联系表；没转写的声音直接说听不到。
- 技术审片有 fail 时不进入评审；成片有 fail 时，交接记录写“不能发布”。

## 评审

用两个子智能体 `digest-analyst`（数据分析师）和 `digest-audience`（观众剪辑），分别派发，互不通气：

- 给它们的材料只有：预览视频路径、`tech_qa/frames/` 联系表、`storyline.md`、rubric、`data/data_insights.md`（有的话）、本轮评分文件路径。
- 第二个子智能体的任务描述里不能出现第一个的分数、意见或文件名。
- 你自己不打分，只负责判定（`review_gate.py check`）、修改和记录（`review_gate.py change`）。

## 输出风格

中文，简洁。需要我拍板时列出选项和你的推荐。不确定就说不确定。
