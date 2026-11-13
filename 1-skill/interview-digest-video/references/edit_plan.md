# 剪辑计划与转写格式

## transcript.json

带时间戳的整句转写，单位秒。用 `prep_transcript.py merge` 从 whisper 输出生成，每项是一句完整的话；`zh` 是中文翻译，`prep_transcript.py apply` 写入。

```json
{"segments": [
  {"id": 0, "start": 0.0, "end": 5.2, "text": "First sentence.", "zh": "第一句话。"},
  {"id": 1, "start": 5.9, "end": 11.4, "text": "Second sentence."}
]}
```

没有 `zh` 的句子会用原文显示字幕，预览时警告，成片时技术审片判 fail。

翻译规则：人名、数字、单位照原话；按意思翻，不照搬语序；一句对一句；不确定的地方写 [?]，不要猜。

## plan.json

| 字段 | 含义 |
|---|---|
| `source` | 源视频路径，相对于 plan.json 所在目录 |
| `aspect` | 只支持 `9:16` |
| `style.title` | 整条视频顶部的标题条，用【】标出要高亮的词 |
| `style.source_label` | 原片片段右上角的来源角标，如"来源：节目名 · 日期" |
| `style.bg` | 背景色（十六进制），或 `blur`（原片模糊背景，上区文字可读性会变差） |
| `style.accent` | 高亮色 |
| `style.bilingual` | `true` 时原片字幕下方加一行小号英文原文 |
| `limits` | `max_source_ratio`（原片占比上限，默认 0.5）、`max_source_clip_sec`（单段原片上限，默认 60）、`max_total_sec`（默认 600） |
| `clips[]` | 按成片顺序排列，可以乱序取材 |

每个片段的字段：

| 字段 | source | narration | card |
|---|---|---|---|
| `id`、`label` | ✓ | ✓ | ✓ |
| `start` / `end` | 源视频起止秒数，自动贴到句子边界 | | |
| `card` | 上区小图卡（最多 2 行） | 中区大图卡 | 中区大图卡 |
| `speaker` | 画面左下的人名身份条 | | |
| `audio` | | 你的口播录音（wav/mp3/m4a） | 可选 |
| `lines` | | 口播稿，逐句成为字幕 | 可选 |
| `duration` | | 没录音时可指定，否则按每秒 4.5 字估算 | 秒数 |
| `source_label` | 覆盖默认角标 | | 引用外部数据时写出处 |

## 版式（9:16，三区）

- 上区：标题条 + 当前片段的小图卡
- 中区：访谈画面（原片片段）或大图卡（讲解、图卡片段）
- 下区：字幕，紧贴画面下方开始。下区下半部分会被平台的标题、简介和按钮盖住，所以不要往下放东西

位置是固定的，所以图卡和字幕不会重叠；文字太长时脚本会缩小字号，缩到下限还放不下，技术审片 T6 判 fail。

## 剪辑原则

- 开头钩子放在最前面，哪怕它在源视频的后半段。
- 每段原片从一句话开头开始，到一句话结尾结束，边界吸附在 0.6 秒以内，超过只提醒。
- 讲解段写"这句话对观众意味着什么"，不复述原话。
- 原片累计不超过成片一半，单段原片不超过 60 秒。超了先删原片，或拆成两段中间加讲解。
- 同一段源视频不在相邻片段里重复使用。
- 成片 10 分钟以内，先删信息密度低的段落。

## 下载和转写（在你自己的电脑上）

```
yt-dlp -f "bv*+ba/b" -o interview.mp4 "<视频链接>"
whisper interview.mp4 --model medium --output_format json
python3 scripts/prep_transcript.py merge interview.json -o transcript.json
```

下载只用于你有权剪辑的视频。英文访谈不要加 `--language zh`，先转写原文，再翻译用到的句子。
