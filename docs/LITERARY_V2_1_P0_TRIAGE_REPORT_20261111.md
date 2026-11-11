# V2.1 P0 故障分诊报告与 P1 预算安全底座

> 分支：`feat/elastic-literary-v2-low-blocking-20261111`
> 施工基线：`f1db66a`（本分支 HEAD）
> 依据：`SHINE_ELASTIC_LITERARY_ENGINE_V2_1_LOW_BLOCKING_PLAN_20261111.md`、`LOCAL_AGENT_SHINE_ELASTIC_LITERARY_V2_1_BUILD_PROMPT_20261111.md`
> 状态：**已实装并离线验证；未运行真实付费模型（L3）**。

## 1. 现状普查（以代码为准，不以文档假设为准）

### 1.1 四阶段 LLM 入口

| 阶段 | 入口 | 角色 | 原合同 | 原失败行为 |
| --- | --- | --- | --- | --- |
| ① 文本导入 | 不调用 LLM | — | — | 确定性切分；`source_sections` 无标题也能切 |
| ② 故事大纲 | `materialprep/story.py:_merge` → `call_material_agent` | `scriptwriter` | `OutlinePart` | 合同失败 → 子任务 `SCHEMA_VALIDATION_FAILED`，正文丢弃 |
| ② 全书蓝图 | `director/pipeline.py:run_bible_task` | `four_stage_bible_run` | `GlobalStoryBiblePayload` | 校验失败 → `NEEDS_REVIEW`，已收回答丢弃 |
| ③ 场次剧本 | `director/pipeline.py:run_scene_script_task` | `four_stage_scene_script_run` | `SceneScriptPayload`（含 `scene_key/section_id/dialogue/...`） | 模型少回一个可选字段 → 整场作废，最多一次纠正后仍失败即 `NEEDS_REVIEW` |
| ③ 备料剧本 | `materialprep/scenes.py:prepare_scenes` | `scriptwriter` | `ScriptPreparation` | 同上 |
| ③ 角色外观 | `materialprep/brief.py` | `producer` | `MaterialImageBrief` | 超长字段触发纠正，二次失败即 FAILED |
| ④ 分镜 | `agents/film.py`（五工种链） | `director/storyboard/...` | 各角色 wire contract | 直连 gateway，无统一预算与台账 |

### 1.2 已确认的三个结构性缺陷

1. **预算与发送字节不同源**：`LLMGateway.complete_for_role` 先用
   `estimate_input_tokens(system)+estimate_input_tokens(user)` 预检，再在**预检之后**把
   `schema` 追加到 `user`。发送的请求永远比被批准的请求大，长 schema（`SceneScriptPayload`
   约 1.9 KB）在长文本场次里正好把请求推过窗口。
2. **正文随合同一起被丢弃**：`openai_compatible.complete` 在 schema 不匹配时**抛出异常前**
   就丢弃了 `content`；`LLMFormatError` 的 details 里只有 `path`/`validator`。于是
   “HTTP 200 + 一整场剧本 + 一个字段类型不符” 被记成“没有可用回答”。
3. **未知窗口被当成 1M**：`agent_call.CONSERVATIVE_CONTEXT_WINDOW = 1_000_000`，未声明窗口的
   服务商被按 1M 规划，且没有任何字段说明这个数字是猜的。

### 1.3 已保留、未改动的安全机制

`request_identity` 唯一约束、`SENDING→UNKNOWN` 不盲重发、`lease_owner/state_version` fence、
`AgentArtifact` 原子提交、`text_consent` 外传门禁、`PIPELINE_PROVIDER_PIN_INVALID` 检查、
分镜/H3 生产准入——全部保留。

## 2. P0：唯一分诊矩阵

`backend/shine/literary/triage.py` 是唯一判级点，`MATRIX` 是唯一规则表。

* `INFO/WARNING`：保存候选成果，继续；
* `LOCAL_REVIEW`：正文保留、仅该单元与其真实依赖等待人工；
* `HARD_BLOCK`：具名阻断，构造时**强制**携带 `reason_code / affected_unit_ids /
  source_evidence / next_action`（缺一即 `ValueError`）。

关键判级规则：

| 情况 | 级别 | 依据 |
| --- | --- | --- |
| 收到正文 + 合同结构错误 | `LOCAL_REVIEW`（`content_saved=True`） | 正文优先于信封 |
| 429 | `LOCAL_REVIEW`，`retryable=True` | 服务商明确拒绝、未产生回答 |
| 窗口超限 | `LOCAL_REVIEW`，`retryable=True`，`NOT_SENT` | 请求可证明未发出 |
| 超时 / 连接 / 5xx | `HARD_BLOCK`，`retryable=False` | 可能已有回答，先对账 |
| 未配置 / 鉴权 / 模型不存在 | `HARD_BLOCK`，`scope=project` | 全链路都需要先修配置 |
| 未登记错误码 | `HARD_BLOCK` | 缺省不得降级为 warning |

### 2.1 本地无损抢救

* `literary/salvage.py`：代码围栏、前后夹带散文、尾随逗号、字符串内花括号、`content→text`
  别名；解析不出对象时**保留原始正文**并记 `no_json_object_raw_text_kept`。
* `literary/adapters.py`：`SceneDraft` 把 LLM 外部合同压到 `{text, continuity_out}` 两个字段；
  `scene_key/section_id/source_refs` 一律由服务端可信任务写入；对白索引由本地
  `extract_dialogue` 从正文抽取（不去重、不改写）；`continuity_out` 等**元数据**超长按名截短
  并记录 `metadata_capped:*`。
* `literary/drafts.py`：候选草稿是独立工件 `LiteraryCandidateDraft`，`approved_at=None`、
  `accepted=False`。它不在 `current_scene_script` 指针里，也不匹配 `_orphan_candidates`
  的 kind，因此**任何既有晋升路径都无法把它当成正式成果**。

### 2.2 适配器修正的实测效果

* 模型用 `content` 而非 `text` 返回完整剧本：本地别名读取，**请求次数不变**；
* `continuity_out: 123`（类型错）→ 一次定点纠正；纠正仍错 → 正文入库为候选、任务
  `NEEDS_REVIEW`，同卷其他场继续 `SUCCEEDED`。

## 3. P1：Call Governor 单一预检

`literary/requests.py::compile_request` 先渲染（含 schema 追加）再计量；
`LLMGateway.complete_for_role(precompiled=...)` 收到编译结果后**不再追加任何内容**，
`user` 字节与预编译一致（`test_the_sent_user_message_is_exactly_the_compiled_one`）。

预算（`literary/budget.py`）：

```
O_hard    = floor(0.60 × verified_effective_output_max)
O_request = min(O_hard, ceil(visible_goal + reasoning_reserve + 256))
I_final + O_request + 512 <= window
soft = floor((window - O_request - 512) × 0.80)
burst = floor((window - O_request - 512) × 0.95)
```

* `visible_goal` 按用途给出（scene/creative/outline/observation/qa/structured），
  小任务不再默认申请 60% 上限；
* 未声明窗口 → 规划窗口 262,144 且 `window_verified=False` + `CONTEXT_WINDOW_UNVERIFIED`，
  不出现 1M 的能力声明；
* 推理预留未测量时标 `REASONING_RESERVE_CONSERVATIVE`，不伪造 reasoning token 数；
* schema 的 `maxLength × maxItems` 只作为 `schema_shape_risk` 风险，不进入输出目标。

`literary/governor.py` 统一承担：编译→预算→（未通过则 `NOT_SENT` 拒绝）→调用→抢救→
至多一次定点纠正。`UnitBudget`（默认 `max_calls=4 / max_repair_rounds=1 /
max_completion_tokens=600000`）是 Writer/Memory/QA/Repair 共享的唯一额度。

## 4. 测试与真实结果

命令：

```
cd backend
python -m pytest tests/test_literary_triage.py tests/test_literary_governor.py \
  tests/test_literary_low_blocking.py -q
python -m pytest tests/test_material_brief.py tests/test_material_preparation.py \
  tests/test_material_scene_safety.py tests/test_material_worker_loop.py \
  tests/test_material_director_flow.py tests/test_story_outline_unblock.py \
  tests/test_story_preparation.py tests/test_story_section_run.py \
  tests/test_story_unknown_resend.py tests/test_story_continuity.py \
  tests/test_llm_gateway.py tests/test_llm_multi_model_routing.py \
  tests/test_llm_pinned_task_routing.py tests/test_agent_retry_and_rate_limit.py \
  tests/test_creative_script_task_lifecycle.py -q
ruff check shine/literary shine/materialprep/agent_call.py shine/director/pipeline.py shine/llm
```

| 套件 | 结果 |
| --- | --- |
| 新增 P0 单元（分诊/抢救/适配器） | 20 passed |
| 新增 P1 单元（Governor/预算） | 13 passed |
| 新增 P0 集成（低阻断回环） | 3 passed |
| 既有 material/story/llm/creative 回归 | 141 passed |
| `ruff check` | All checks passed |

未运行：任何真实付费 LLM 调用（L3）= `NOT_RUN`；真实 H3 = `NOT_RUN`。

## 5. 已知阻滞与后续

* `agents/film.py`（S4 五工种分镜链）仍直连 gateway，尚未接入 Governor 与五维检索；
  在 P5 接入，当前行为未回归。
* `materialprep.agent_call.budget_report` 保留为**排队期估算**（决定单元是否入队），
  发送期唯一权威是 Governor；两者数值不同不是缺陷，但必须在同一份文档里说明。
* 未新增数据库表（P0/P1 不需要）；记忆/台账表在 P2/P3 引入。
