# 导演模式续建实际进度（2026-11-05）

总体：**PARTIAL / NOT ACCEPTED**。本文件记录本次续建，不以历史小样替代正式旅程。

## 可接续阶段

1. S1：A01 持久输入身份/原子认领/UNKNOWN 防重放；A02 无损来源、预算拆分、场内引用。进行中。
2. S2：A03/A04/A06/A07、R1：显式选图、素材网格、主资产版本绑定、快照恢复与单项操作。
3. S3：A05：持久文字授权、撤回与 Worker 验证；主栈身份、配置、心跳恢复。
4. S4：R2：真实生成段网格、单段新修订、依赖失效、当前装配版本和新交付。
5. S5：A08/R3：Worker 持久大纲/生产段落编排、人工确认、选择范围分镜与精确失效。
6. S6：完整专项/全量回归、真实 UI、正式 Qwen、用户选用/冻结、校准和 H3、Windows 双入口/安装包。

## 开工采样

- 基线 main `693751fe77c4d613dcd6aa160bb60e9dd078b1f7`；已跟踪树干净。
- 实施分支 `codex/director-closeout-20261105`。
- 保留未跟踪 `.zcode/`、原审查/提示词/evidence，不纳入本次提交。
- 8000/5173：主栈 `.local-data`；旧 processes.json 的 API listener_pid 与当前监听不符；API 报 Worker 离线、配置变化阻塞、2 pending jobs。不得盲目启动 Worker 消费旧队列。
- 8133/5193：`.local-acceptance/live-browser`；API PID 20000，Worker PID 24496，Web PID 27188；Worker 在线、无 running/pending jobs，真实视频运行时未激活。证据与主栈隔离。
- 尚未变更运行库、停止进程、释放租约或提交模型任务。series/未知任务/GPU 台账详细采样仍待完成。

## 验收边界

IMPLEMENTED/OFFLINE_VERIFIED/REAL_QWEN_VERIFIED/REAL_H3_VERIFIED/HUMAN_REVIEW/WINDOWS_AND_PACKAGE 分别记录；本次尚无新真实生成或人工验收证据。任何必要门槛未完成维持 PARTIAL。
