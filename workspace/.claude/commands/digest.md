---
description: 把一条长访谈做成精读视频（访谈精读完整流程）
argument-hint: <期目录，如 episodes/2026-11-14-andreessen>
---

用 skill `interview-digest-video`，按 CLAUDE.md 的规则，处理这一期：$ARGUMENTS

1. 先检查这一期目录里有什么：源视频、whisper 转写、我的备注（notes.md）。缺素材就一次问完。
2. 转写还不是整句的，先用 prep_transcript.py merge 合并。
3. 通读转写、抽帧确认谁在说话，写 storyline.md（3 个标题、一句话主张、开头 3 秒、3 到 5 个要点、结尾、出处表、预估时长和原片占比），然后停下来等我。
