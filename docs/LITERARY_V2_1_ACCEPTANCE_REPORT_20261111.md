# Shine 弹性文学引擎 V2.1 低阻断验收报告

- 分支：`feat/elastic-literary-v2-low-blocking-20261111`
- 工作树：`F:\ClaudeWorkSpace\worktrees\shine-v21`（独立 worktree，未触碰其他并行开发）
- 依据：`docs/SHINE_ELASTIC_LITERARY_ENGINE_V2_1_LOW_BLOCKING_PLAN_20261111.md`、
  `docs/LOCAL_AGENT_SHINE_ELASTIC_LITERARY_V2_1_BUILD_PROMPT_20261111.md`
- 测试三层：L1 纯函数单测 → L2 故障注入集成 → L3 真实模型（本报告标注 NOT_RUN 的部分）

## 1. 提交序列

| 阶段 | 提交 | 内容 |
| --- | --- | --- |
| P0+P1 | `c1334e9` | 分诊矩阵、抢救层、统一 Call Governor、单一预算 |
| P2 | `2ce1a65` | 调用台账、分段超时、SSE 可观测传输、UNKNOWN 对账 |
| P3 | `cb1befb` | 单一五维事实库、证据与密级、按场检索、flags |
| P4 | `72c12db` | 蓝图分层归并、场次切拍、无损拼装、依赖传播 |
| P5 | `cdea30f` | L0/L1 双向终校、确认门禁、分镜记忆视图、QA 接口 |
| P6 | 本提交 | 无标题长文硬切、终校状态进入界面、旧数据兼容与验收 |

## 2. 各阶段验收证据

### P0 故障分诊与 JSON 合同精简

- 唯一矩阵 `INFO / WARNING / LOCAL_REVIEW / HARD_BLOCK`；`HARD_BLOCK` 构造时必须带
  reason_code、受影响单元、来源证据与下一步。
- 外部场次合同压到 `{text, continuity_out}`；`scene_key/section_id/source_refs`
  由服务端可信任务写入，模型无法伪造来源。
- 完整但结构不规范的正文落 `LiteraryCandidateDraft`（`accepted=false`、未批准），
  不进入确认指针，也不会被 orphan 采纳路径当成正式稿。
- 通过的用例：`tests/test_literary_triage.py`（20）、
  `tests/test_literary_low_blocking.py` 中「纠正一次即成功」「纠正失败仍保留正文」。

### P1 统一预算与推理 Token 治理

- `compile_request` 先渲染（含 schema）再计量，得到唯一请求指纹；网关收到预编译请求
  不再追加 schema，发送字节与受检字节同源。
- `O_hard = 60%` 已验证输出上限；`O_request` 按可见目标 + 推理预留动态确定；
  未知窗口不再伪装 1M（`window_verified=false`）；soft/burst 输入阈值。
- 通过的用例：`tests/test_literary_governor.py`（13，含「追加 Schema 不改变已检预算」
  「超限请求未发出」）。

### P2 传输与恢复安全

- 每次物理请求先落 `SENDING` 意图、后落真实结果；台账故障只记录、不影响文学任务。
- 连接 / 首事件 / 空闲 / 总时限分离；流式默认关闭，未探测服务商不会收到流式请求。
- 收到字节后断流 → `external_outcome=UNKNOWN` + 保留可见片段，绝不自动重发；
  无任何事件才回退非流式。
- 通过的用例：`tests/test_literary_transport_and_ledger.py`（6）、
  `tests/test_four_stage_closeout.py` 的 UNKNOWN / 新尝试 / 围栏用例。

### P3 五维事实库

- 一个事件一行、`dimensions[]` 关联多维，不产生五套权威副本；模型观测默认
  `authority=candidate`，只能由人晋升。
- 观测字段无效只影响该条（`MEMORY_OBSERVATION_INVALID=WARNING`）；空维度合法；
  大纲不会因为记忆失败而重做。
- 检索三档（mandatory / preferred / optional），整条纳入或整条省略；`production_only`
  默认不进入写作视图。
- 通过的用例：`tests/test_literary_memory.py`（11）。

### P4 蓝图归并与切拍

- `VolumeDigest → merge_digests`：预算不足先压散文、保留首末事件，所有被压缩
  `event_id` 记入 `compressed_ids`；覆盖缺失具名上报。
- `plan_beats` 只在自然边界切，硬切记 note；`coverage()` 证明无缝隙；
  `assemble()` 逐字拼装，不去重不截断。
- 每拍一次请求、成功即落工件；失败拍只把依赖它的后续拍标 `WAITING_DEPENDENCY`，
  已写出的拍保留。
- 通过的用例：`tests/test_literary_beats.py`（11）、
  `test_literary_low_blocking.py::test_a_long_scene_is_written_beat_by_beat_and_a_failed_beat_keeps_the_rest`。

### P5 分镜接入与双向终校

- L0 全部本地可证：原文跨度、未知人物、秘密提前泄露、缺少收尾状态、节拍覆盖双向证明。
- L1 只在 L0 提出具名疑点时调用一次；合同把 `severity` 限死为 `WARNING/LOCAL_REVIEW`，
  模型意见无法升级为交付阻断；只给它疑点 + 证据 + 正文节选。
- 终校报告是独立版本化工件，绑定被评正文的 `script_artifact_id/sha256`；
  硬阻断只拦「确认」，正文与编辑出口保留。
- 单场逻辑单元预算 = Writer + 一次纠正 + 终校 ≤ 3 次调用；`qa=off` 不产出、
  `shadow` 只记 L0、`on` 才买一次 L1。
- 通过的用例：`tests/test_literary_qa.py`（15）、前端
  `fourStagePanel.test.tsx::终校硬阻断时用中文说明并保留编辑出口`。

### P6 界面收束、长文与兼容

- 无标题长文：单节超过 55,000 字时按段落边界硬切，切口写入
  `split_note` 与 `VOLUME_HARD_SPLIT` 警告，区间连续、一字不丢；低于阈值仍是一卷。
- 界面：每场只显示一句中文终校结论；硬阻断时确认按钮不可点、编辑保留；
  内部检查项、调用台账、事实 id 不进普通界面。
- 旧数据：没有任何 V2 记录的项目照常读取，`qa` 为 `null`，终校接口如实返回 `NOT_RUN`；
  旧 trace 的任务仍能进入任务摘要。
- 通过的用例：`tests/test_literary_acceptance.py`（4）、
  `tests/test_pipeline.py::test_oversized_single_chapter_volume_carries_a_warning`
  （断言按新契约更新：硬切警告或超限警告必须出现，覆盖必须完整）。

## 3. 真实运行结果

后端（Python 3，pytest；解释器
`F:\ClaudeWorkSpace\projects\shine-video-studio\backend\.venv\Scripts\python.exe`）：

| 模块 | 用例数 | 结果 |
| --- | --- | --- |
| `test_literary_triage.py` | 20 | PASS |
| `test_literary_governor.py` | 13 | PASS |
| `test_literary_beats.py` | 11 | PASS |
| `test_literary_memory.py` | 11 | PASS |
| `test_literary_transport_and_ledger.py` | 6 | PASS |
| `test_literary_low_blocking.py` | 4 | PASS |
| `test_literary_qa.py` | 15 | PASS |
| `test_literary_acceptance.py` | 4 | PASS |
| `test_pipeline.py` | 19 | PASS |
| `test_four_stage_closeout.py` | 16 | PASS |
| `test_storyboard.py` | 33 | PASS |
| `test_film_unknown_outcomes.py` | 24 | PASS |
| `test_film_crew.py` | 32 | PASS |
| 材料 / 故事 / LLM 回归（13 个模块） | 141 | PASS |

静态检查：`ruff check shine tests` → All checks passed。

前端（`frontend/`，TypeScript 5.8 / Vitest 2.1.9 / Vite 6.4.5）：

- `tsc -b` → 通过（无输出）
- `vite build` → 成功（848.97 kB js / 166.62 kB css）
- `vitest run` → 全部测试文件通过；本机并发执行时出现 1 条
  `EPERM ... Temp\...\web\...` 的未处理错误，来源是运行环境的临时文件写入权限，
  与断言无关，单文件重跑无此错误。

## 4. NOT_RUN（未执行，不得视为通过）

| 项 | 原因 | 复现方式 |
| --- | --- | --- |
| 真实付费 LLM 四阶段端到端 | 本次未获得真实模型调用费用授权 | 配置真实 Provider → 导入母本 → 逐卷大纲 → 蓝图 → 场次剧本 → 分镜草案 → 落镜 → 交付 |
| 真实 H3 出片与生产准入 | 属独立生产准入关卡，需 GPU 与授权 | 按 `docs/` 中 H3 生产准入流程执行 |
| 新机升级回归（Windows 全新安装 → 升级） | 需一台干净机器与安装包 | 安装旧版本 → 升级到本分支 → 打开 V1 项目继续处理 |
| 断网 / 429 / 慢事件的真实服务商行为 | 已用假服务器与故障注入覆盖（L2），真实服务商未验证 | 断网后重跑场次；观察 `NOT_SENT` / `UNKNOWN` 与不重发 |

## 5. 已知限制

1. **分集与交付仍走原有门禁**：V2.1 只新增终校证据，未改动落镜、分集、导演包的
   既有校验；因此「节拍未覆盖」目前阻断的是 `PROPOSED` 与剧本确认，落镜入口仍以
   原有连续性校验为准。
2. **五维记忆默认 `shadow`**：默认只记录不改请求。需要 `SHINE_LITERARY_MEMORY=on`
   或项目级 `literary-feature:<project>:memory=on` 才会进入写作/分镜提示词。
3. **QA 默认 `shadow`**：默认只做 L0 并留证；`SHINE_LITERARY_QA=on` 才会为具名疑点
   购买一次 L1。
4. **无标题长文按 45,000 字目标硬切**：切口落在段落，若原文整段只有一个段落，
   只能在字符处硬切，`split_note` 会说明。
5. **`VOLUME_MAY_EXCEED_STORYBOARD_INLINE_LIMIT` 在实际路径中已难触发**：
   超过 55,000 字的单节会先被硬切，因此实际出现的是 `VOLUME_HARD_SPLIT`。

## 6. 建议的下一步（需用户决策）

1. 授权一次有费用上限的真实模型四阶段验收（建议先 1 卷 / 2 场 / 1 次分镜草案）。
2. 决定是否把 `memory` 与 `qa` 在新建项目上默认切到 `on`（当前默认 `shadow`）。
3. 决定是否把「节拍未覆盖」接入落镜入口的硬门禁（当前只阻断剧本确认与 `PROPOSED`）。
