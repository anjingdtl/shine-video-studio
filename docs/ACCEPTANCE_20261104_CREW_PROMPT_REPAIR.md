# 剧组提示词修复与导演链阻滞根因报告（2026-11-04）

**结论：两次失败已定位到同一个根因并修复；真实模型链未被本轮重跑，因此本报告是
「离线复现 + 代码修复 + 回归测试通过」，不是「真实链路验收通过」。**

## 1. 现场事实（只读，来自运行中的真实数据库）

数据源：`.local-acceptance/real-user-journey-final/data/shine.db`，
项目 `2d39fe79`《阿德的一天》，模型 `MiniMax-M3.1-Flash`（provider `27d84d38`）。

| 任务 | 时间 | 角色 | 结果 | 拒绝码 | HTTP | provider_code |
| --- | --- | --- | --- | --- | --- | --- |
| `3cbc01c6` | 02:45:58 | scriptwriter | SUCCEEDED | – | 200 | 0 |
| `5cb3a8fc` | 07:19:31 | director | REJECTED | `EDITORIAL_TIMING_CONFLICT` | 200 | 0 |
| `170afa21` | 07:19:46 | director | REJECTED | `EDITORIAL_TIMING_CONFLICT` | 200 | 0 |
| `18dbc740` | 08:19:51 | scriptwriter | SUCCEEDED | – | 200 | 0 |
| `b6f2bda9` | 08:20:33 | director | REJECTED | `LLM_FORMAT` | 200 | 0 |
| `31ead07a` | 08:21:21 | director | REJECTED | `LLM_FORMAT` | 200 | 0 |

两次都是**编剧成功、导演被拒**：模型都返回了内容（HTTP 200、provider_code 0），
被拒发生在服务端对导演答案的确定性校验阶段。第一轮 `phase=VALIDATING_RESULT`，
第二轮 `phase=WAITING_PROVIDER`（JSON 不符合 `DirectorialPlan` 契约）。

上游事实：`18dbc740` 产出 5 个 beat（`B01`–`B05`，无台词），每个 beat 都列了全部
5 个人物；项目有 5 个人物主资产 + 1 个场景主资产；**该项目没有任何已登记的
workflow profile**（`workflow_profiles` 里其他项目的档案不能跨项目套用）。

## 2. 根因：同一个矛盾，三种表现

服务端的校验是确定性的（`shine/agents/film.py::validate_output`）：

```
elapsed == editorial_timing.total_s
timeline == list(zip(beats, editorial_timing.cut_starts_s))
```

也就是说，导演必须**原样复现**服务端已经算好的切点表：每个生成段的
`duration_s`、每个内部切点的 `start_s`、以及所有 `duration_s` 之和，都必须与
剧本切点表逐位相等。

而当时的导演提示词（提交 `8548e93` 引入）从未把这些数字交给模型，只说了两句话：

1. `Its suggested groups may be adjusted only at original cut boundaries.`
   —— 明确邀请模型重新分组。
2. `Set each shot's duration_s long enough to contain the latest dialogue end_s in
   its beat plus a short natural tail.`
   —— 明确要求给每个镜头加自然尾巴。

两句都与上面的确定性校验直接冲突，于是：

- 模型采纳「加尾巴」→ 总时长 ≠ `total_s` → `EDITORIAL_TIMING_CONFLICT`；
- 模型采纳「重新分组」→ 分组切点变了 → 同样 `EDITORIAL_TIMING_CONFLICT`；
  若新分组的某段短于 H3 的 4 秒下限 → `duration_s` 越界 →
  JSON schema 校验失败 → `LLM_FORMAT`（这正是第二轮两次失败的最可能路径）。

两次重试失败的原因不是模型不听话，而是**提示词与校验互相矛盾**：提示词给出的
自由度，正好是服务端唯一不接受的自由度。

叠加两个次生问题：

- `LLM_FORMAT` 只落 `http_status`/`provider_code`，**丢掉了是哪个字段坏了**
  （jsonschema 的 path/message），操作者看到的是无信息量的 `LLM_FORMAT`。
- 切点表若**无法**分组成 4–15 秒的生成段（例如 2 × 1.0s），服务端没有任何拒绝路径，
  只能把一个**任何答案都无法满足**的请求丢给模型反复试。

## 3. 本轮修复

### 3.1 提示词成为唯一可执行契约（`shine/agents/crew_prompts.py`，新建）

5 个角色的提示词抽到一个模块，逐条可审阅：

- **共用段**：官方 H3 对齐段。明确写出 Ref2VA 编译输出的六段顺序
  `subject_definitions → summary → retention_analysis → detailed_description →
  overall_soundscape → non_diegetic_music`，以及规则包里的机器规则
  `H3_ENGLISH` / `H3_TIMING` / `H3_DIALOGUE` / `H3_LIMITS` / `H3_LABELS`。
  剧组不写提示词，提示词只由编译器产出，所以角色只需产出编译器读取的结构化字段。
- **编剧**：有切点表时，一个 editorial shot 一个 beat，按表序；台词窗口不超过该
  editorial shot 的长度；**只列本 beat 真正出现的人物**（每个列出的人物都会变成
  渲染必需的 subject 参考图，全员复读会直接把参考槽撑爆）。
- **导演**：把服务端算好的 `editorial_timing_plan` **逐条写进提示词**——
  每段的 `beat_id`、精确 `duration_s`、每个内部切点的 `beat_id` 与精确 `start_s`，
  并写死三条禁令：不合并、不拆分、不改偏移、不加尾巴；台词装不下是场记要报的
  冲突，不是移动边界的理由。
- **摄影**：一段一条机位，内部切点写进父段的机位字段；补齐官方指南要求的
  运镜类型／幅度／速度与构图要素；机位字段不得出现台词或中文。
- **场记**：固定的分段时长与切点本身**不是**冲突；只有台词超出所属 editorial
  shot 区间才是真冲突（按 `H3_TIMING` 报，并给出镜头 id 与时间码）。
- **剪辑**：每个生成段一条 clip，`planned_duration_s` 必须等于该段的 `duration_s`。

### 3.2 服务端下发确定性配方（`shine/agents/editorial_timing.py`）

新增 `editorial_timing_plan()`：把切点表换算成「每段 opening beat、每段精确时长、
每个内部切点的相对偏移」，并随上下文下发给 5 个角色。导演在上下文里拿到的是
**算好的答案**，不是建议。

新增分组回退：原实现只搜索三段式分组，两镜表（以及本例的五镜表）会得到空分组。
现在在前三段式无解时按「最长段最小、再优先更多段」回退搜索；仍无解时给出
`EDITORIAL_TIMING_UNGROUPABLE`，指明每镜长度、最短镜长与 4–15 秒窗口。
既有 8 镜表的分组结果（4.3 / 5.1 / 5.6 秒）保持不变。

### 3.3 不可能完成的请求不再消耗模型调用

- `EDITORIAL_TIMING_UNGROUPABLE`：切点表无法分组成 H3 生成段 → 在任何模型调用
  之前拒绝，理由写进任务 trace。
- `EDITORIAL_BEAT_COUNT_MISMATCH`：编剧 beat 数与切点表行数不一致 → 导演调用
  之前拒绝，并同时给出两个数字。

两者都记在 `agent_task_records.trace.editorial_refusal`，并作为
`rejection_reason_code` 回传到工作台。

### 3.4 诊断可读

- `LLM_FORMAT` 现在保留 `provider_schema_path`（坏掉的字段路径）与
  `provider_schema_validator`（jsonschema 校验关键字，如 `maximum`），
  不落任何模型原文或异常文本。
- 分镜链的拒绝项现在始终带 `rejection_reason_code`；工作台优先显示精确原因
  （`EDITORIAL_TIMING_CONFLICT` / `EDITORIAL_TIMING_UNGROUPABLE` /
  `EDITORIAL_BEAT_COUNT_MISMATCH` / `LLM_FORMAT` 都有中文文案），
  不再只显示笼统的 `CONTRACT_OR_POLICY_REJECTED`。

## 4. 测试证据

新增 `backend/tests/test_crew_prompt_contract.py`（16 项，全部通过）：

| 用例 | 断言 |
| --- | --- |
| `test_a_plan_built_from_the_prompt_is_accepted` | 按提示词逐字构造的分镜方案**被服务端接受**（PROPOSED，五步全调用） |
| `test_a_natural_tail_still_breaks_the_chain` | 同一方案只加 0.3 秒尾巴 → 被 `EDITORIAL_TIMING_CONFLICT` 拒绝（证明尾巴就是现场失败原因） |
| `test_an_ungroupable_cut_table_is_refused_without_a_model_call` | 2×1.0s 切点表 → `EDITORIAL_TIMING_UNGROUPABLE`，**模型调用次数为 0** |
| `test_a_beat_count_that_cannot_match_the_table_is_refused_before_the_director` | 编剧 3 beat vs 5 行表 → `EDITORIAL_BEAT_COUNT_MISMATCH`，导演**未被调用** |
| `test_every_crew_prompt_carries_the_official_h3_rules` | 5 个角色的提示词都带六段顺序与 5 条官方规则编号 |
| `test_the_director_prompt_states_the_plan_and_forbids_the_two_deviations` | 提示词写出了具体数字，且不再含旧的两句自相矛盾指令 |
| `test_the_provider_keeps_the_field_that_broke_the_contract` / `..._in_the_task_record` | 字段路径与校验关键字进任务 trace |

既有套件复跑：`tests/test_storyboard.py`（27）、`tests/test_film_crew.py`、
`tests/test_h3_compiler.py`、`tests/test_film_unknown_outcomes.py`、
`tests/test_character_binding.py`、新增文件全部通过；前端 `npm.cmd test` 115/115、
`npm.cmd run build` 通过；`ruff check` 通过。

## 5. 仍未解除的真实阻滞（非本轮代码可解）

1. **本项目没有登记任何 workflow profile**。导演现在能给出合规的参考集，
   但渲染仍会以 `PENDING_WORKFLOW_MAPPING` 阻断，直到用户在「制作环境」为本项目
   登记并验证一个 H3 Ref2VA 档案。
2. **参考槽位要够宽**。本项目 5 个人物主资产 + 1 个场景主资产 = 每镜 6 张参考图。
   已验证的 3 图档案装不下；项目 `b28fcd5c` 里已有一个已验证的 9 图档案
   （`suzhen-ref2va-9img-1aud-portrait`），但档案按项目隔离，需要在本项目重新登记
   并在真实渲染器上验证。
3. **真实模型链未重跑**。本轮没有用真实 MiniMax 调用复验这两次失败的剧本；
   「提示词描述的方案能被服务端接受」是离线契约测试的结论，不是真实出片结论。
