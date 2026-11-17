# Shine AI 管家 V2.2：个性化配置与本地长期记忆架构

- 版本：V2.2.0
- 开发分支：`feat/shine-copilot-v22-persona-local-memory`
- 开发基线：PR #59 `ead511b95c62122d2f7047c4e049d634890e89ed`（V2.1 HEAD）
- 最新 main（开发时核实）：`a1d05498e5fd654656981f3e2ad364b561bb2462`
- 数据库迁移 head：`f180f0010006`

## 1. 目标与边界

V2.2 在**不新增第二个 Agent、不复制业务事实、不改变 H3 生产链路**的前提下，为既有
`support.agent` 增加四项能力：

1. 左侧导航「AI 管家配置」；
2. 管家昵称与用户称呼；
3. 启动预读记忆；
4. 基于本机 SQLite 的长期记忆（FTS5 + 轻量向量混合召回）。

记忆是**派生数据**。原始对话仍以 `SupportSession` / `SupportMessage` 为唯一来源；
生产状态、模型就绪、风格冻结、资产确认、工作流裁决与任务结果仍以正式业务接口为准。

### 四条硬边界

| 边界 | 实现 |
| --- | --- |
| 一套事实 | 配置只写既有 `AppSetting`；对话只写既有 `SupportSession/SupportMessage`；记忆只写新增的派生表 |
| 一个裁决 | 记忆标注为「历史参考，不是当前业务事实」；prompt 明确要求以本轮证据为准 |
| 一个执行者 | 继续使用 `support.agent` + LLM Gateway + Support Action/Delegation + 正式业务 API |
| 运行时源码零修改 | 没有 shell/git/任意 HTTP/源码写入能力；记忆写操作只走受控配置接口 |

## 2. 总体数据流

```
用户消息
  │
  ▼
support.agent.handle_message
  ├─ 读取 AppSetting(copilot_user_profile)          ← 唯一配置来源
  ├─ 读取 SupportSession.project_id                 ← 真实项目身份
  ├─ memory.retrieve(global + 当前项目)
  │    ├─ FTS5 关键词召回
  │    ├─ 本地哈希向量召回
  │    └─ LIKE 降级（索引不可用）
  ├─ build_evidence_snapshot / diagnose / investigate ← 正式业务事实
  ├─ fit_prompt_payload（统一预算、固定优先级）
  └─ 原 support_agent LLM Gateway（可选）
        │
        ▼
   assistant 消息 + memory evidence（只记录 id/数量/预算）
```

配置页和记忆管理页只调用 `/api/v1/support/preferences` 与 `/api/v1/support/memories*`，
不会接触生产执行器。自动提炼在响应后以 BackgroundTask 运行；它复用同一个 LLM Gateway，
不新增调度器。

## 3. 配置合同

唯一 key：`AppSetting("copilot_user_profile")`。

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `assistant_nickname` | string(1..20) | `AI 管家` | 拒绝 HTML、控制字符、系统指令改写 |
| `user_salutation` | string(0..20) | 空 | 空则不强制称呼 |
| `startup_memory` | string(0..4000) | 空 | 启动预读；拒绝密钥/敏感路径/可执行网页标签 |
| `memory_enabled` | bool | true | 关闭后停止召回与自动提炼，不删除记忆 |
| `auto_memory_enabled` | bool | false | 关闭后不产生新的自动记忆 |
| `remote_memory_processing_consent` | bool | false | 允许远端 LLM 处理历史对话做提炼 |
| `remote_context_consent` | bool | false | 允许召回记忆进入远端 LLM 上下文 |
| `config_revision` | int | 0 | 每次有效变更 +1 |
| `updated_at` | ISO string | null | 最近一次变更时间 |

原 `AppSetting("support_agent_web_search")` 保持原 key、原语义；统一响应中增加
`web_search` 字段，避免第二套配置 API。`GET/POST /api/v1/support/preferences` 是唯一入口；
`POST` 接受部分字段，原 `{"web_search": bool}` 调用保持兼容。

### 安全校验

- 昵称/称呼：NFKC 归一化；1..20 字符；拒绝 `< >`、可执行网页标签、控制字符、
  「忽略/绕过系统规则」类指令、密钥与敏感路径。
- 启动预读：0..4000 字符；允许正常换行；拒绝密钥、敏感路径、`script/iframe/svg/img` 等标签。
- 记忆正文：2..2000 字符；拒绝密钥、敏感路径、可执行网页标签、控制字符。

## 4. 启动预读

- 新建会话时只在 `context_json` 记录 `profile_revision` 与昵称，不复制正文到第二份存储。
- 每个用户回合重新读取配置，因此配置页保存后**下一条消息立即生效**。
- 预读内容只在本轮确实需要调用 LLM 时进入 prompt；**不会为了预读额外产生一次付费请求**。
- 预读与记忆均放在明确的用户偏好区块，不得扩大权限或覆盖系统规则。

## 5. 长期记忆实体

表 `copilot_memories`（权威记忆）：

| 字段 | 说明 |
| --- | --- |
| `id` | UUID |
| `scope` | `GLOBAL` / `PROJECT` |
| `project_id` | 项目记忆的真实项目 ID；全局为空 |
| `content` | 记忆正文 |
| `memory_type` | `PREFERENCE / PROJECT_DECISION / CREATIVE_HABIT / CONSTRAINT / EXPERIENCE / SUMMARY` |
| `source` | `MANUAL` / `AUTO` |
| `source_session_id` / `source_message_ids` | 来源会话与消息 |
| `content_hash` | 归一化内容哈希，用于去重与删除墓碑 |
| `content_revision` | 内容修订号 |
| `embedding_revision` | 最近一次建立向量的修订号 |
| `status` | `ACTIVE / SUPERSEDED / DELETED` |
| `confirmed` | 用户确认状态 |
| `supersedes_id` | 新记忆取代的旧记忆 |
| `details` | 轻量来源/编辑标记 |
| `created_at` / `updated_at` / `deleted_at` / `deleted_reason` | 时间与删除原因 |

派生索引：

- `copilot_memory_vectors`：`memory_id`、`content_revision`、`content_hash`、`provider`、`dim`、`vector`、`updated_at`。
- `copilot_memory_fts`：SQLite FTS5 虚拟表，`memory_id UNINDEXED, content`。
  中文索引时把 CJK 二元组附加在正文后，规避 `unicode61` 对连续汉字的整段切分。

两者都**可以随时从 `copilot_memories` 重建**。读取时必须同时校验：
`status=ACTIVE`、`content_revision` 一致、`content_hash` 一致、`provider/dim` 一致。
因此旧向量不可能召回已编辑或已删除的文本。

## 6. 检索与项目隔离

`retrieve(project_id, query)`：

1. 若 `memory_enabled=false`，直接返回 `DISABLED`，不召回。
2. 范围固定为 `GLOBAL` + `PROJECT(project_id=当前真实项目)`；没有项目上下文时只查全局。
3. FTS5 召回 + 向量召回混合排序；两者都不可用时使用分词后的 LIKE 降级。
4. 只返回 `ACTIVE` 记录；按内容长度裁剪到 500 字符/条，最多 6 条。
5. 项目 ID 只来自 `SupportSession.project_id`，不由 LLM 猜测。

配置页可以按项目筛选查看全部记忆，这是用户显式管理行为；聊天链路从不跨项目自动召回。

## 7. 去重、更新与取代

- 完全重复：同 scope/project 的 ACTIVE 内容哈希相同 → 复用原记录，不新增副本。
- 语义近似：同 scope/project/type 的哈希向量相似度 ≥ 0.93；
  - 新内容更长或含「改为/不再/现在/以后」等更新信号 → 原地更新并递增修订号；
  - 否则视为重复，跳过。
- 明确冲突：自动提炼 prompt 可返回 `replaces_memory_id`；服务在验证归属后把旧记忆标为
  `SUPERSEDED`，并移除其索引。
- 编辑：内容变化 → `content_revision += 1`，FTS/向量立即重建；旧索引失效。
- 删除：`status=DELETED`、正文清空、哈希保留为墓碑；FTS 与向量立即删除。
- 自动提炼遇到同内容墓碑时直接 `SUPPRESSED`，不会未经用户许可恢复已删内容。

## 8. 上下文预算

`memory.fit_prompt_payload` 使用一个统一上限 12,000 字符：

优先级固定为：

```
系统安全规则（独立 system message，永不裁剪）
  > 实时业务事实 / 裁决 / 授权
  > 用户启动预读
  > 自动提炼记忆
  > 普通检索资料（项目百科标题、官方参考）
```

- 业务证据超预算时先省略调查步骤的详细 payload，再限制事实与步骤条数；
- 启动预读最多 1,800 字符，超出时安全截断；
- 长期记忆最多 6 条、每条最多 500 字符，按相关性裁剪；
- 普通检索资料最多 500 字符；
- 返回 `budget` 证据：`limit/used/truncated/reasons/sections/context_priority/system_rules_truncated`。

不存在第二个 LLM Gateway、第二个 Token 计费或第二套系统提示。

## 9. 自动提炼

- 触发：`auto_memory_enabled` + 有可用文本模型 + 距离上次提炼累计 ≥ 6 条用户消息。
- 运行：FastAPI BackgroundTask，响应后执行；聊天不被阻塞。
- 远端模型：必须有 `remote_memory_processing_consent`；本地 loopback/mock 不要求。
- 发送范围：仅当前会话最近消息（最多 24/30 条，每条最多 700 字符）+ 最多 12 条相关既有记忆。
- 输出：最多 5 条，正文最长 800 字符；临时状态、密钥、未确认推测、大段原文被 prompt 禁止，
  服务端再做密钥/标签/长度/重复/墓碑校验。
- 失败：`NO_LLM / REMOTE_CONSENT_REQUIRED / NOT_CONFIGURED / UNAVAILABLE`，均只影响自动提炼，
  不阻塞聊天，也不影响生产。

## 10. 隐私与删除语义

- 存储：本机 SQLite，与 Support 会话同一数据库；不依赖云端向量库。
- 关闭长期记忆：停止召回与自动提炼，保留已有记忆。
- 删除单条：移除正文、FTS、向量，留下内容哈希墓碑。
- 清除项目：只删除该项目 `PROJECT` 记忆；其它项目与全局记忆保留。
- 清除全部自动记忆：只删除 `source=AUTO`；手动记忆与用户编辑过的记忆保留。
- 重建索引：只重建 FTS/向量，不修改记忆正文。
- 所有清空操作必须 `confirm=true`；单条删除在 UI 中二次确认。

## 11. 技术选型说明

| 候选 | 结论 |
| --- | --- |
| SQLite FTS5 | **采用**。本机 SQLite 编译支持；不可用时状态页明确显示并降级 LIKE |
| sqlite-vec | **未采用**。需要额外扩展加载，Windows 打包/多版本 SQLite 兼容性未在本轮验证；不引入不可控运行时依赖 |
| bge-small-zh-v1.5 / ONNX | **未打包、未验证**。模型权重、许可证与离线分发未纳入本轮交付；状态页显示 `NOT_CONFIGURED` |
| 本地哈希向量 | **采用**。256 维、CJK 字符 n-gram + ASCII 词，纯 Python、CPU 微秒级、无网络、无 GPU、可重建 |
| Chroma/Qdrant/Docker 向量库 | **禁止**，未引入 |

向量适配层记录 `provider/dim`；未来接入经过验证的本地语义模型时，可通过重建索引显式升级，
不会把旧字节误读成新模型向量。

## 12. 性能与降级

- 不在启动阶段加载模型；索引按需初始化。
- 默认 CPU；不进入 ComfyUI/H3 的 GPU 租约。
- 相同 `content_revision/content_hash` 不重复生成向量。
- 向量召回最多扫描 800 条候选，向量维度 256。
- FTS5/向量任何一方失败都只降低召回质量；权威记忆表仍在，LIKE 兜底。
- 索引损坏可在配置页一键重建；状态页显示 `OK / DEGRADED / REBUILD_REQUIRED / CORRUPT / DISABLED`。
- 记忆组件异常不会抛出到聊天主链路或生产 Worker。

## 13. 与 V2.1 契约同步

- `APP_VERSION = 2.2.0`，`CONTRACT_VERSION = ...-v22`。
- 新 feature：`copilot-persona-memory`。
- 新 READ 能力：
  - `read_copilot_profile` → `GET /api/v1/support/preferences`
  - `read_copilot_memories` → `GET /api/v1/support/memories`
- 记忆写入/删除/清空/重建在矩阵中为 `HUMAN_REQUIRED`，没有注册为自然语言可委托动作。
- `support_web_search_update` 因请求模型扩展把 `contract_revision` 提升到 3。
- 构建产物 `backend/shine/support/generated/copilot_contracts.json` 与
  `knowledge_manifest.json` 由 `scripts/generate_shine_copilot_contracts.py` 重新生成；
  `--check` 通过。

## 14. 数据库迁移

`f180f0010006_copilot_persona_memory.py`：

- 新增 `copilot_memories`、`copilot_memory_vectors`；
- 尝试创建 `copilot_memory_fts`（FTS5 不可用时跳过并记录真实能力）；
- 外键到 `projects.id`，项目删除时级联删除记忆；
- 迁移可降级并再次升级；`alembic check` 忽略 FTS5 影子表，避免误判漂移。

老用户从 `f180f0010005` 升级保留全部 Support 会话、消息与委托；V1/V2/V2.1 表不变。
