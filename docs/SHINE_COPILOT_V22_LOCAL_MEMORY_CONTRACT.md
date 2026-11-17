# Shine AI 管家 V2.2 本地长期记忆合同

本文件固定 V2.2 的数据、API、检索、删除与隐私合同。实现位于：

- `backend/shine/support/memory/`
- `backend/shine/api_support_memory.py`
- `backend/shine/api_support.py`（统一 preferences）
- `backend/shine/support/agent.py`（上下文注入）
- `backend/shine/support/models.py`
- `backend/alembic/versions/f180f0010006_copilot_persona_memory.py`
- `frontend/src/components/CopilotSettingsPage.tsx`
- `frontend/src/copilotProfile.ts`

## 1. 权威与派生

| 数据 | 唯一权威 | 派生/索引 |
| --- | --- | --- |
| 用户配置 | `AppSetting.copilot_user_profile` | 浏览器 `copilotProfile` 只是显示缓存 |
| 原始对话 | `SupportSession` / `SupportMessage` | 无 |
| 长期记忆 | `copilot_memories` | `copilot_memory_fts`、`copilot_memory_vectors` |
| 业务裁决 | 正式 setup/job/delivery/style/asset API | 记忆只作交流参考 |

禁止把 `copilot_memory_vectors` 或 FTS 表当作第二份记忆正文。它们可以在任意时刻删除并重建。

## 2. 配置 API

### GET `/api/v1/support/preferences`

返回：

```json
{
  "assistant_nickname": "AI 管家",
  "user_salutation": "",
  "startup_memory": "",
  "memory_enabled": true,
  "auto_memory_enabled": false,
  "remote_memory_processing_consent": false,
  "remote_context_consent": false,
  "config_revision": 0,
  "updated_at": null,
  "updated_by": null,
  "web_search": false,
  "limits": {"assistant_nickname": 20, "user_salutation": 20, "startup_memory": 4000, "memory_content": 2000},
  "defaults": {"...": "..."}
}
```

### POST `/api/v1/support/preferences`

接受任意子集（`extra=forbid`）：

| 字段 | 校验 |
| --- | --- |
| `web_search` | bool，仍写原 `support_agent_web_search` |
| `assistant_nickname` | 1..20；非空；无 HTML/指令/密钥 |
| `user_salutation` | 0..20；安全校验同上 |
| `startup_memory` | 0..4000；无密钥/敏感路径/可执行标签 |
| `memory_enabled` / `auto_memory_enabled` / `remote_*_consent` | bool |

行为：

- 仅当值实际变化时 `config_revision += 1`；
- 任何有效变更写一条 `SUPPORT_PREFERENCES_UPDATED` 审计（不记录正文）；
- 返回与 GET 相同结构；
- 所有写操作要求 loopback 用户动作头 `X-Shine-Local-Action: 1`（与现有本地桌面一致）。

错误示例：

```json
{"detail": {"code": "HTML_NOT_ALLOWED", "message": "管家昵称 不能包含 HTML 标签或尖括号。"}}
```

## 3. 记忆 API

所有读取无需确认；所有写操作要求本地用户动作头。自然语言 Agent 没有调用这些写接口的能力。

### GET `/api/v1/support/memories`

查询参数：

- `scope=GLOBAL|PROJECT`（可选）
- `project_id`（可选，真实项目 ID）
- `q`（可选，最长 200）
- `limit` 1..200，默认 100
- `offset` ≥ 0

返回：

```json
{
  "memories": [
    {
      "id": "...",
      "scope": "PROJECT",
      "project_id": "...",
      "project_title": "苏镇",
      "content": "...",
      "memory_type": "PROJECT_DECISION",
      "source": "AUTO",
      "source_session_id": "...",
      "source_message_ids": ["..."],
      "sources": [{"message_id": "...", "role": "user", "excerpt": "..."}],
      "content_revision": 1,
      "status": "ACTIVE",
      "confirmed": false,
      "supersedes_id": null,
      "created_at": "...",
      "updated_at": "..."
    }
  ],
  "total": 1,
  "counts": {
    "active": 1, "global": 0, "project": 1, "auto": 1, "manual": 0,
    "deleted_tombstones": 0, "by_project": {"...": 1}
  },
  "limit": 100,
  "offset": 0
}
```

只返回 `ACTIVE`。`sources` 最多 3 条消息摘录，正文 180 字符并脱敏。

### GET `/api/v1/support/memories/status`

```json
{
  "state": "OK",
  "fts5": "AVAILABLE",
  "fts_rows": 2,
  "vector": {
    "provider": "local-hash-ngram-v1",
    "dim": 256,
    "available": true,
    "semantic_model": "NOT_CONFIGURED",
    "semantic_model_verified": false,
    "mode": "CPU 本地哈希向量（无模型、无 GPU、无网络）"
  },
  "vector_rows": 2,
  "stale_vectors": 0,
  "active_memories": 2,
  "counts": {"...": "..."},
  "memory_enabled": true,
  "auto_memory_enabled": false,
  "privacy": {"...": "..."}
}
```

`state`：

- `OK`：FTS/向量行数与 ACTIVE 记忆一致，无过期向量；
- `REBUILD_REQUIRED`：索引行数不一致或存在过期向量；
- `DEGRADED`：FTS5 不可用或读取失败，LIKE 仍可检索；
- `CORRUPT`：状态读取异常；
- `DISABLED`：长期记忆关闭且索引本身正常。

### POST `/api/v1/support/memories`

```json
{"scope": "GLOBAL|PROJECT", "project_id": "...", "content": "...", "memory_type": "PREFERENCE"}
```

- `PROJECT` 必须有存在的 `project_id`；
- 内容 2..2000，无密钥/标签；
- 手动创建默认 `confirmed=true`、`source=MANUAL`；
- 完全重复返回原记忆 + `deduplicated=true`；
- 语义近似按更新信号决定合并/跳过，不产生副本。

### PATCH `/api/v1/support/memories/{id}`

```json
{"content": "新内容", "memory_type": "CONSTRAINT"}
```

- 内容变化：`content_revision += 1`；FTS/向量重建；旧向量立即不再匹配；
- 用户编辑后 `source=MANUAL`，避免被「清除全部自动记忆」误删；
- 已删除记忆返回 404。

### DELETE `/api/v1/support/memories/{id}`

- `status=DELETED`、`content=""`、`deleted_at`、`deleted_reason=USER_DELETED`；
- 删除 FTS 与向量条目；
- 保留 `content_hash` 墓碑，阻止 AUTO 提炼再次创建完全相同内容；
- 幂等：重复删除返回 `already_deleted=true`。

### POST `/api/v1/support/memories/clear`

```json
{"scope": "PROJECT|AUTO", "project_id": "...", "confirm": true}
```

- `confirm` 必须为 true，否则 400 `CONFIRMATION_REQUIRED`；
- `PROJECT` 只清除该项目 `PROJECT` 记忆；
- `AUTO` 只清除 `source=AUTO` 的 ACTIVE 记忆；
- 不删除原始对话。

### POST `/api/v1/support/memories/rebuild`

- 从 `copilot_memories` 重建全部 FTS/向量；
- 不修改记忆正文；
- 返回重建统计和最新状态。

### POST `/api/v1/support/memory/extract`

```json
{"session_id": "...", "force": true}
```

- 使用当前会话最近消息；
- 远端模型必须先有 `remote_memory_processing_consent`，否则 409 `REMOTE_CONSENT_REQUIRED`；
- 无模型返回 `NO_LLM`；关闭记忆返回 `MEMORY_DISABLED`；
- 只创建经过筛选的少量记忆；返回 `created/updated/suppressed/skipped`，不含正文。

## 4. 字段与不变量

1. `scope=GLOBAL` 必须 `project_id IS NULL`。
2. `scope=PROJECT` 必须有真实存在的 `project_id`。
3. `status=DELETED` 的记忆正文必须为空，`content_hash` 保留。
4. `status=ACTIVE` 的记忆必须同时有 `content` 与 `content_hash`。
5. 向量行必须匹配 `memory_id + content_revision + content_hash + provider + dim`，否则不可用于召回。
6. FTS 行只能描述 ACTIVE 记忆；删除/取代必须同步删除。
7. 记忆正文永不进入审计 payload；审计只记录 scope/project/type/source/revision/数量。
8. 记忆正文不得包含密钥、敏感路径、可执行网页标签。

## 5. 召回合同

`retrieve(project_id, query)` 返回：

```json
{
  "enabled": true,
  "state": "OK|DEGRADED|DISABLED|ERROR|REMOTE_CONTEXT_WITHHELD",
  "project_id": "...",
  "query": "...",
  "count": 1,
  "retrieval": {"fts": 1, "vector": 1, "like": 0, "degraded": false},
  "memories": [
    {"id": "...", "scope": "PROJECT", "project_id": "...", "memory_type": "...", "content": "...", "updated_at": "..."}
  ]
}
```

- 最多 6 条；每条 500 字符；
- `query` 为空时按更新时间返回最近记忆；
- 无项目上下文时只返回 GLOBAL；
- 远端 LLM 且无 `remote_context_consent` 时返回 `REMOTE_CONTEXT_WITHHELD` 且不发送任何记忆；
- 任何异常返回空列表与 `ERROR`，不抛到聊天链路。

## 6. 预算合同

`fit_prompt_payload` 固定：

- 总上限 12,000 字符；
- 启动预读 1,800；
- 记忆正文 1,200（每条 500，最多 6 条）；
- 普通检索资料 500；
- 业务事实优先保留；系统规则不在该 payload 内，单独且永不裁剪；
- 返回的 `budget` 必须包含 `system_rules_truncated=false` 与各段 kept/requested。

## 7. 自动提炼合同

- 阈值：每会话每 6 条用户消息最多一次；
- 后台执行；
- 模型：既有 `support_agent` 路由；
- 发送：最近 24/30 条消息，每条 700 字符；
- 输出：最多 5 条，每条 ≤800 字符；
- 远端必须显式授权；
- 内容必须通过 `validate_content`；失败逐条 skipped，不影响其它条；
- 已删除内容墓碑抑制；
- 审计 `SUPPORT_MEMORY_EXTRACTED`，不含正文。

## 8. 明确不做

- 不提供跨项目自动召回；
- 不提供“记忆即事实”的裁决捷径；
- 不把记忆写回项目风格、镜头、资产或生产任务；
- 不提供 shell/git/任意 HTTP/源码写入；
- 不要求 Chroma/Qdrant/Docker/云端向量库；
- 不把模型权重、数据库、密钥或用户对话提交到 Git。
