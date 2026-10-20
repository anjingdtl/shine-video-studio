# Shine视频工作台 · Shine Video Studio

> Local-first autonomous short-drama production workbench built around **MiniMax-H3**, with a configurable independent LLM Agent runtime.

**Current status: planning baseline committed; implementation not yet started.** This repository is private. No working H3 renderer, autonomous QC loop, or production-ready installer has been delivered yet.

## Product boundaries
- **Video model:** locally deployed MiniMax-H3 via ComfyUI; no silent substitution with online video services or other models.
- **Deployment:** Windows local Web application; backend FastAPI, frontend React/TypeScript, persistent SQLite jobs and independent worker.
- **LLM/VLM:** provider-configurable API routes; Codex is a development assistant only, not a required runtime.
- **Assets:** user-supplied, user-approved master character/scene/prop/voice assets only.
- **Style:** film-wide camera language, lighting/color, editing rhythm and sound rules are reviewed and frozen by the user **before** unattended production.
- **Production:** shot planning, reference mapping, H3 prompt compilation, execution, technical/semantic QC, bounded per-shot repair and edit/delivery. All work auditable and recoverable.

## Authoritative project documents
- [V1.0 项目建设方案](docs/Shine视频工作台_V1.0_项目建设方案.md)
- [Codex 云端 Agent 首轮开发总提示词](docs/Codex云端Agent_建仓及首轮开发总提示词.md)
- [AGENTS.md — 开发规范](AGENTS.md)

## How to start Codex Cloud development
1. In Codex, select the existing GitHub repository `anjingdtl/shine-video-studio` and its `main` branch, or open an authorized feature branch.
2. Ask Codex to read `AGENTS.md` and both documents in `docs/`.
3. **Skip repository creation:** this private repo already exists. Start with upstream source/license audit and M0/M1 implementation.
4. Commit tested code and report actual commit/PR URL, test results, and tasks awaiting real Windows + GPU + H3 acceptance.

Suggested opening instruction:

```text
请在当前已连接的 anjingdtl/shine-video-studio 私有仓库中，先完整阅读 AGENTS.md 和 docs/ 下的两份 V1.0 建设文件。跳过建仓步骤；从上游源码与许可证审计开始，按方案实际开发并测试 M0 + M1。保持唯一状态库/任务调度器、人工资产锁定、Style Bible 冻结和本地 H3 唯一视频引擎等强约束；云端无 GPU 的环节只使用 Mock 测试并标注待本机验收。完成后提交代码，提供真实测试结果及下一阶段清单。
```

## Security
The repo must not store `.env` values, provider API keys, private assets, checkpoint weights, output videos, local SQLite databases, or logs. Keep third-party repository audits and licenses traceable.
