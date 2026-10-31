# LOCAL_TECH_FINAL_CLOSURE_V3 · 最终收官报告

> **最新本地修复（2026-10-31，第二轮复审）**：在 `81a92f3` 上发现的“丢失租约仍覆盖 EDL 元数据”和“64 个占用订单遮挡可执行订单”已直接修复。被测代码为 `81a92f3` 加本轮工作树修复，正式新增 3 项回归；定向 23 passed，全量 **1178 passed / 1 skipped / 2 warnings**。本轮已执行的本地工程检查 PASS，真实 ComfyUI 探测 SKIPPED；影片 `pending_review` 与人工批准边界保持。详见 [本轮修复与验证](LOCAL_DELIVERY_FENCING_FIX_20261031.md)。以下保留各历史轮次记录；本轮按用户指令仅做本地提交，未推送。

> 本报告是《Shine视频工作台》本地技术收官（任务一：自动 EDL + 自动装配；任务二：完整剧本 → 五真实 Agent 分镜；任务三：最终版真实端到端复验）的最终验收结论。
> 记录时间：2026-10-31（本地）。被测功能提交：`fd7ebb0`（基线 `09f17c6`）。
> 所有真实运行的证据根目录：`.local-acceptance/final-closure-v3/`（该目录不入 Git）。
>
> **追加轮次（2026-10-31，同日）**：独立审查（基线 `d8ae267`）发现两项代码缺陷与一处证据索引自引用，本轮已全部关闭；被测代码 = `d8ae267` + 未提交工作树修复（3 个文件），全量后端套件 **1175 passed / 1 skipped**，详见 §0 与 `.local-acceptance/final-closure-v4/CLOSURE_ROUND.md`。本轮修复已于同日按用户指令提交并推送至 `origin/main`。

## 0. 追加轮次（2026-10-31 独立审查关闭，同日）

独立审查（`.local-acceptance/audit-20261031-d8ae267/`，审查基线 `d8ae267`）复现了两项代码缺陷与一处证据索引自引用。本轮已按"复现 → 最小修复 → 回归 → 证据"全部关闭：

| 项 | 结论 |
|---|---|
| P1 忙等 | **已修复**：`run_pending_deliveries` 改为有限调度轮（快照 `id+state_version`，每单至多尝试一次，按 `state_version` 变化计推进）；未到期装配不再阻塞调度，其他订单/序列/普通任务不被饿死；正常装配、有界重试、崩溃恢复、幂等语义保留 |
| P2 双领取 | **已修复**：`_claim_order` 与四类写回（完成/阻断/失败/重试）全部改为数据库原子条件 UPDATE，按受影响行数判定；领取与写回均以 `(owner, state_version)` 令牌校验所有权，失去所有权的 Worker 不能覆盖新 owner 或终态；无迁移 |
| 证据索引自引用 | **已修复**：`scripts/build_evidence_index.py` 按路径排除输出自身；实测 972 项（旧 969 含旧索引自身；+4 为审查轮 SQLite WAL 只读检查产生的 `-wal/-shm` 旁挂文件）；独立校验器两轮 PASS（972/972 匹配，`self_listed=false`） |
| 验证门禁 | 反例 2 passed；交付套件 16 passed（含 5 项新长期回归）；offline_e2e+film_crew 32 passed；v13 三件套 63 passed；全量 **1175 passed / 1 skipped / 2 warnings**；Ruff clean；Alembic 新库 head=`f140a0030001`（12 迁移链完整）；`uv sync --locked`（Resolved 42 / Checked 41）；npm ci 0 漏洞 + TS/Vite 构建通过；verify_local_stack / verify_stack_operations PASS；verify_v13_web=MOCK_VERIFIED（api_errors=[]）；`git diff --check` 干净；端口/进程释放收据 PASS |
| 唯一 skip | `test_real_managed_startup.py:85` — 需 `SHINE_REAL_COMFY_PROBE=1` 且本地 ComfyUI；本轮按任务边界不启动真实 ComfyUI，如实记 SKIPPED |
| 被测代码 | `d8ae267` + 未提交工作树修复（`backend/shine/media/delivery_engine.py`、`backend/tests/test_delivery_orders.py`、`scripts/build_evidence_index.py`）；修复已提交并推送至 `origin/main`（用户 2026-10-31 指令） |
| 审计记录 | `.local-acceptance(审查目录)/`（反例、审查日志、已存复核 JSON）+ `.local-acceptance/final-closure-v4/CLOSURE_ROUND.md` 与全部命令日志 |
| 真实生产维持原判 | run-m `all_passed`；portrait-h `pending_review`（首镜 semantic/style/final=needs_review）。建议性审片是用户已定调的产品策略；真实影片质量与用户批准边界不变（§11） |

---

## 1. 最终结论

真实用户从**完整剧本**开始、一次点击生产，系统自行完成分镜、真实 H3 生成、质检与成片交付——这条链路在本地真实环境**已被证明**：

| 规格 | 运行 | 结论 |
|---|---|---|
| 横屏 1344×768 | `20261031-run-m` | **全链 PASS**（五真实 Agent → 3 镜真实 H3 全 PASSED → 产品自动生成 EDL → 自动 FFmpeg 装配 → Web 实播） |
| 竖屏 768×1344（正式规格） | `20261031-final-portrait-h` | **全链 PASS**（剧本 → 五真实 Agent → apply → 序列 → 3 镜真实 H3 → 自动 EDL → 自动装配 → Web 实播）；首镜小模型审片 needs_review 上报人工，成片如实为 `pending_review`（§4.2）；f/g 是它的失败前轮（§4.3） |

本轮（任务三）在收官过程中发现并修复了 **5 类真实缺陷**（§3），其中三类曾直接阻断生产链路；修复后全部离线回归在最终功能 SHA `fd7ebb0` 上通过，横竖屏两个规格的真实全链验收均 **PASS**（§4）。

判定口径与边界：
- **PASS_SOFTWARE**：离线软件测试（不含真实模型生成）。
- **PASS_REAL**：本机真实模型（MiniMax-M3.1-Flash 五个 Agent、真实 MiniMax-H3 Ref2VA、真实 Qwen3-VL/ASR 审片、真实 FFmpeg 装配、真实浏览器实播）。
- GitHub Actions = **WAIVED_EXTERNAL_BILLING**（不计入本地技术判定）。
- `RIGHTS_APPROVAL / ARTISTIC_REVIEW / FINAL_FILM_APPROVAL` = **PENDING_HUMAN**。

---

## 2. 最终验收矩阵（16 项）

| # | 条件 | 状态 | 依据 |
|---|---|---|---|
| 1 | 完整剧本一次点击进入生产 | **PASS_REAL** | 浏览器内导入 874 字剧本 → 五 Agent 链 → 人工 apply → 序列启动 → 3 镜真实 H3 → 自动 EDL → 自动装配 → Web 实播（§4.1） |
| 2 | 五个真实 Agent 逐角色调用（非 mock） | **PASS_REAL** | `agent_task_records` 5/5 SUCCEEDED，`model_id=MiniMax-M3.1-Flash`，`is_mock=false`（portrait-h 与 run-m 两处一致） |
| 3 | 缺少主资产的角色被明确阻断 | **PASS_REAL** | 负例：`BLOCKED_MISSING_MASTERS`，缺清单精确（§5） |
| 4 | 角色之间的矛盾由仲裁层按官方准则裁决 | **PASS_SOFTWARE** | 4 项新回归覆盖（逐条裁决、引用不存在规则不放行、冲突仍在则阻断、无矛盾不调用）；**真实运行未触发仲裁**——合同强化后场记未再提出误判（§3.3–3.4） |
| 5 | 参考集纪律由服务端机器事实保证 | **PASS_SOFTWARE** | `shot_reference_policy` + 2 项回归（§3.3） |
| 6 | 小模型自审只做一轮、上报人工、绝不阻断自主成片 | **PASS_REAL** | 竖屏第一镜：semantic/style = needs_review 但镜头 PASSED，成片 `review_state=pending_review`（§3.5、§4.2） |
| 7 | 确定性门禁保持硬阻断 | **PASS_SOFTWARE** | technical QC / 编译提示词 / 已批准主资产 / 租约纪律均不因建议性审片而放行（§3.5） |
| 8 | 不确定的 LLM 结果绝不重放、绝不伪装成功 | **PASS_SOFTWARE** | provider 层仅重放"连接未建立"与"可重试状态"；3 项新回归（§3.1） |
| 9 | 真实 H3 生产（独占租约，受管渲染器） | **PASS_REAL** | 横竖屏**各 3/3 PASSED**，逐镜五门禁留痕（§4.1） |
| 10 | 产品自动生成 EDL（来源=冻结剪辑意图） | **PASS_REAL** | 横竖屏均验证：订单 `edl_source=frozen_edit_intent`，镜头顺序 == 序列 position 顺序，脚本零次构造 EDL（§4.1） |
| 11 | 产品自动 FFmpeg 装配成片并注册 | **PASS_REAL** | 竖屏 sha256 `471e4315…`，13 156 347 字节，768×1344 / 24 fps / 21.896 s / 525 帧，逐帧解码通过（§4.1） |
| 12 | Web 实播成片 | **PASS_REAL** | 竖屏：`readyState=4`、`currentTime=3.920042`、`duration=21.895996`（§4.1） |
| 13 | 官方 H3 规范一致性（哈希/段落/标签族） | **PASS_SOFTWARE** | 三个官方文件 SHA-256 与锁定值逐字节一致；六段顺序与标签族一致（§6） |
| 14 | 终版 SHA 全量命令清单 | **PASS_SOFTWARE** | `fd7ebb0` 上 uv/pytest(1170 passed)/ruff/npm×2/verify×2 全绿（§7）；追加轮次在 `d8ae267`+未提交修复上复验为 **1175 passed / 1 skipped**（§0、§7） |
| 15 | 资源释放（端口/进程/GPU）与证据可审计（逐文件 SHA-256 索引） | **PASS_REAL** / **PASS_SOFTWARE** | 释放收据 PASS：5 端口无监听、无残留进程、GPU 回基线（§8）；`evidence-index.json` 追加轮次修复自引用后按实测 **972** 项（§9） |
| 16 | 证据可审计（逐文件 SHA-256 索引） | **PASS_SOFTWARE** | `evidence-index.json`（§9，追加轮次修复自引用后为 972 项，两次独立全量校验 PASS） |

---

## 3. 本轮发现并修复的缺陷

### 3.1 不确定的 LLM 结果曾被 provider 层重放（红线违反）

- **现象**：`20261031-final-portrait-d` 中，场记角色调用在上游约 300s 切断连接后失败（`LLM_CONNECTION`，`external_outcome=UNKNOWN`）；但 provider 客户端在**同一请求上重放了 2 次**，把一次 5 分钟的未知结果拉长成 10 分钟。
- **根因**：`shine/llm/openai_compatible.py` 与 `anthropic.py` 把读超时与协议错误都当作可重试错误。而调度器 `shine/agents/execution.py` 早已把 `LLM_TIMEOUT/LLM_CONNECTION/LLM_SERVER` 记为 `external_outcome=UNKNOWN` 且 `automatic_replay=false`——两层语义互相矛盾，违反"不确定的 LLM 结果绝不重试"。
- **修复**：只有**连接未建立**（`ConnectTimeout`/`PoolTimeout`/`ConnectError`，请求根本没送出）与**显式可重试状态**（429/5xx）才可重放；读超时与"请求已发出但连接断裂"立即上报，不再重放。
- **回归**：新增 3 项（读超时不重放 / 断链不重放 / 连接未建立才重放）。

### 3.2 UI 与验收脚本对"被拒绝"缺少出口

- **现象**：`portrait-d` 被场记拒绝后，验收脚本空等 30 分钟并报 Playwright 超时，**丢失了真实原因**（`continuity_supervisor: LLM_CONNECTION`）。
- **根因**：面板只在 `.blocked-callout` 里给出原因，状态行 `<p role=status>` 为空；脚本只等状态行或步骤列表。
- **修复**：面板对**每一种结果**都写状态行（成功/需复核/缺主资产/被拒绝）；验收脚本改为观察"步骤列表 / 状态行 / 拒绝卡片"三者之一即判定，并把场记逐角色结果写入证据 `crew_runs`。

### 3.3 参考集纪律是"事实"而非"意见"（两份 Agent 合同互相矛盾）

- **现象**：`20261031-final-portrait-e` 五 Agent 链 5/5 成功，但 `apply-camera` 返回 422 `CONTINUITY_REVIEW_REQUIRED`：场记认为第 3 镜"漏绑 FRAME_A_LAST 参考资产"。
- **根因**：导演合同明文规定"绝不把 style / last-frame 参考图列为镜头参考"，而场记把这一**刻意排除**读成了**遗漏**。横屏 run-m 结构完全相同却未触发——纯属模型随机性。
- **修复**：服务端新增 `shot_reference_policy`，把参考集策略与**逐镜合规判定**作为机器事实交给场记；场记合同明确"合规镜头没有缺失参考"、"soft `last_frame` 是与前一镜的软关系，不需要参考图"。
- **回归**：新增 2 项（合规镜头被判定为 policy_compliant 且排除项被列明 / 缺音色主资产的镜头被判定为不合规）。

### 3.4  crew 需要一个仲裁层（用户指令落地）

- **问题**：角色之间判断矛盾时，此前只有"整条链重跑"或"永久阻断"两条路，浪费算力。
- **修复**：新增 **`ContinuityArbitration`** 合同与 `continuity_arbitration` 工具：
  - 只有**拥有镜头的导演**可以仲裁，其他角色被 `ROLE_OR_TOOL_DENIED` 拒绝；
  - 仲裁**只依据已锁定规则包 `shine-h3-rules/1.1.0` 的官方准则**（`rule_id` 必须存在于规则包，否则 `ARBITRATION_UNKNOWN_RULE`）；
  - **每条 issue 必须被逐条裁决**（按索引覆盖，缺一条即 `ARBITRATION_ISSUE_COVERAGE`）；服务端绑定权威 issue 文本，模型的复述无法让裁决脱离它所裁决的 issue；
  - 仍存在冲突 → 保持阻断，交人工；全部判为不构成冲突 → 分镜可进入人工 apply（裁决随分镜同行，界面展示每条裁决）。
- **回归**：新增 4 项（仲裁放行并可 apply / 冲突仍在则阻断 / 引用不存在规则则不放行 / 无矛盾时不调用仲裁）。

### 3.5 小模型自审：只做一轮、上报人工、绝不阻断自主生成

- **问题**：本地 4B 审片模型（Qwen3-VL-4B / Qwen3-ASR-0.6B）的检查精度有限，`portrait-f` 与 `portrait-g` 两轮都因它返回**自相矛盾**的结论（`style_input_checks` 声明某风格字段"不一致"却不给对应 finding）而把整条生产链停在最后一道门禁、升级为人工对账。
- **决策（用户明确）**：小模型自审**只做一轮**，结果按提示**上报人工复核**，但**绝对不能影响整个项目的自主生成影片流程**。
- **实现**：
  1. **绑定而非丢弃**：审片模型自己的话被服务端绑定成合规证据——它声明的冲突会被补成对应 finding，整体 verdict 不能再是 passed；它没评估的风格字段记为 `uncertain` 而绝非 `consistent`；**绝不**为要一个更好的答复而再跑一次模型。
  2. **完整但不可用的答复 = 已发生**：答复到达却不符合合同，交接层以"已发生但不可用"封存（保留隔离收据与失败分类），于是阶段能收尾、渲染器与 GPU 租约能释放、原因能进入人工复核。
  3. **建议性门禁**：semantic / style / audio 三道门禁对生产流是**建议性**的——它们的真实状态与 findings 被完整记录并上报人工复核，但**不阻断**自主成片，也**不为模型意见消耗一次返工**。
  4. **确定性门禁保持硬阻断**：技术 QC（解码/时长/音轨）、编译后的提示词与规则包校验、已批准主资产绑定、资源与租约纪律——一律不可绕过。
  5. **不确定的结果仍然阻断**：答复根本没到（超时/断链）属于未知结果，按红线不重放、不伪装，继续等待人工对账。
- **可逆**：`QualityPolicy.advisory_model_reviews` 默认开启；项目把它置为 `false` 即恢复"模型审片决定生产"的严格模式（既有回归已在严格模式下保留原断言）。
- **回归**：新增 5 项（策略显式且可逆 / 建议性审片不阻断生产 / 确定性门禁仍阻断 / 干净审片仍是干净通过 / 未发生的审片不算模型意见），既有门禁与返工回归在严格模式下原样保留。

### 3.6 验收配置：给 crew 调用设定界限

- 上游模型 `MiniMax-M3.1-Flash` 的思考深度**默认为最高档**，且思考 token 计入输出上限；一次 crew 调用因此可能超过上游连接窗口。
- 种子脚本现在显式设定 `max_completion_tokens=12288`、`reasoning_effort=medium`（产品自带配置面 `generation_options`），并用 `scripts/probe_llm_generation_options.py` 先探测上游是否真的接受（实测四组参数均 HTTP 200 / `finish_reason=stop`）。

---

## 4. 竖屏真实验收（768×1344，正式规格）

**最终结果：`20261031-final-portrait-h` 全链 PASS**（被测功能 SHA `fd7ebb0`）。

### 4.1 全链事实

| 步骤 | 结果 | 证据 |
|---|---|---|
| 浏览器内导入 874 字完整剧本 | ✅ | `STEP script_imported {'chars': 874}` |
| 五个真实 Agent 逐角色调用 | ✅ 5/5 SUCCEEDED | `model_id=MiniMax-M3.1-Flash`，`is_mock=false`；编剧/摄影各有 1 次**确定性拒绝** → 有界重试 → 成功 |
| 场记报告 | ✅ 干净 | `issues=[]`、`proceed_to_preflight`，sha `6bfbca98aeaa`（与横屏 run-m 同一结论） |
| 人工 apply 创建镜头 | ✅ 3 镜 | `STEP storyboard_applied {'shot_count': 3}` |
| 冻结剪辑意图绑定序列 | ✅ | edit intent sha `29aa6d7208d1…` |
| 常驻 Worker 认领 + 真实 H3 三镜 | ✅ **3/3 PASSED** | series `744191d8…`，provider job `d0e46dea…` / `ec4b050a…` / `a1bc42eb…` |
| 产品自动生成 EDL | ✅ | 订单 `5dba57db…` v1，`edl_source=frozen_edit_intent`，镜头顺序 == 序列 position 顺序（`STEP auto_edl_order_matches_series`） |
| 产品自动 FFmpeg 装配成片 | ✅ | 产物 `6d26556a…`，sha256 `471e4315135668e6c461316f565e5fa694acfde7e62ff906195af61b57594f31`，13 156 347 字节，**768×1344 / 24 fps / 21.896 s / 525 帧 / h264+aac**，逐帧解码通过 |
| Web 实播 | ✅ | `readyState=4`、`currentTime=3.920042`、`duration=21.895996` |
| 脚本纪律 | ✅ | 验收脚本**零次**构造 EDL、**零次**调用 `POST /deliveries` |
| 退出码 | ✅ | `ORCHESTRATOR_EXIT=0` |

### 4.2 小模型自审在真实运行中的表现

第一镜的本地 VLM 判定 `editing_rhythm` **不确定**，产品按 §3.5 处理：

| 门禁 | 状态 | 模型 | 建议性 | 说明 |
|---|---|---|---|---|
| technical | passed | ffprobe | — | 确定性门禁，硬通过 |
| semantic | needs_review | local-qwen3-vl-4b (conf 0.85) | ✅ | `STYLE_INPUT_EDITING_RHYTHM_UNCERTAIN` |
| audio | passed | local-qwen3-asr-0.6b | ✅ | 真实转写 |
| style | needs_review | local-qwen3-vl-4b (conf 0.85) | ✅ | 同上 |
| final | needs_review | — | — | `advisory_pending_human_review=['semantic','style']` |

- 该镜**仍然 PASSED**：小模型的不确定性被记录并上报人工复核，**没有阻断**自主成片，也没有为模型意见消耗一次返工。
- 第二、三镜五门禁全 `passed`。
- 成片的 `review_state` 如实为 **`pending_review`**（而不是谎称 `all_passed`）——模型说了什么，就记什么。

### 4.3 此前失败轮次（逐轮原因，均已在最终版修复）

| 轮次 | 阻断点 | 处置 |
|---|---|---|
| `final-portrait-d` | 场记调用上游 300s 断链 → `LLM_CONNECTION`；provider 层还重放了 2 次 | 修：不确定结果不重放（§3.1） |
| `final-portrait-e` | 场记把"刻意排除参考图"读成"遗漏" → `CONTINUITY_REVIEW_REQUIRED`，apply 被拒 | 修：参考集策略成为服务端机器事实（§3.3） |
| `final-portrait-f` | 本地 VLM 结论自相矛盾 → 合同违规 → 受管交接判为"审片缺失" → 序列停摆 | 修：绑定自话 + 封存为已发生 + 建议性门禁（§3.5） |
| `final-portrait-g` | 同上（第二例） | 同上 |
| `final-portrait-h` | — | **全链 PASS** |

### 4.4 横屏对照（同代码、同链路）

`20261031-run-m`：3 镜真实 H3 全 PASSED → 产品自动生成 EDL（`edl_source=frozen_edit_intent`）→ 自动 FFmpeg 装配 → 成片 sha256 `b0f7e7765c17b38fa9847c55125a827a91fd208a83903bbfddadc7e15ec7e1d6`，21.896 s，1344×768，24 fps，逐帧解码通过 → Web 实播 readyState=4、currentTime=3.92 s。

---

## 5. 真实负例：引用无主资产角色的剧本

对**运行中的真实栈**（最终竖屏运行 `20261031-final-portrait-h` 的数据目录：4 个已批准并锁定的主资产、冻结风格、已绑定路由的真实 MiniMax provider）提交一份引用 `CHAR_CHENMO`（无任何主资产）的完整剧本：

| 断言 | 结果 |
|---|---|
| HTTP 状态 | 201（链正常执行到覆盖检查） |
| `status` | **BLOCKED_MISSING_MASTERS** |
| `missing_characters` | **["CHAR_CHENMO"]**（精确） |
| 项目已批准角色主资产 | ["CHAR_LIANG"] —— **未被误报为缺失** |
| 缺清单中的角色是否都出现在剧本里 | 是（脚本自校验） |
| 链是否继续 | 否：只完成编剧一步，导演/摄影/场记/剪辑**未创建任何产物** |
| 是否自动创建资产 | 否：产品从不代替用户生成主资产 |
| 真实耗时 | 30.6 s（含一次真实 MiniMax 编剧调用） |

证据：`.local-acceptance/final-closure-v3/20261031-final-portrait-h/negative-missing-masters/`
（`negative-missing-masters-response.json` = 断言与响应原文；`negative-missing-masters-script.md` = 被拒剧本；`start-stack.log` / `stop-stack.log` = 栈起停；`negative-run.log` = 判定输出）。

补充：同一负例在更早的竖屏数据目录上曾先被两条**产品设计**的规则挡住，均未绕过：
1. 残留的 H3 独占租约处于 `RECONCILE_REQUIRED`（上一轮渲染异常终止）→ 未对账资源保持阻塞 → Agent 阶段拿不到 GPU 阶段位（这正是"过期资源在人工对账前保持阻塞"的实现）。
2. 同一请求在上一次尝试中留下 `NEEDS_REVIEW` 记录 → `AGENT_REQUEST_RECONCILIATION_REQUIRED`（不确定结果不自动重放）。

---

## 6. 官方 H3 规范一致性核对

| 检查 | 结果 |
|---|---|
| `skills/h3-prompt-writing/SKILL.md` | SHA-256 `a7000443588ca3f145e3b3fd8900f14e0325dc460bd811268fac89a9dc8e56d0` **与锁定值一致** |
| `references/base-en.txt` | SHA-256 `2cfebc096a6e08370f288d468d90b60f7f9bcb938f94bf090816e910e48e75fc` **与锁定值一致** |
| `references/ref-en.txt` | SHA-256 `1e574f356716ad55612247ffb7bbccbcdb484ad96599d63c7dca1af186b1fab7` **与锁定值一致** |
| Ref2VA 六段顺序 | `subject_definitions → summary → retention_analysis → detailed_description → overall_soundscape → non_diegetic_music` **一致** |
| Base 三段顺序 | `integrated_multimodal_description → overall_soundscape → non_diegetic_music` **一致** |
| 参考标签族 | `<Subject N>` / `<Picture N>` / `<Video N>` / `<Audio N>` **一致** |

核对脚本：`scripts/verify_h3_rulebook_pin.py`（对本地官方技能副本重算哈希并比对编译器常量）；证据：`h3-rulebook-pin.json`。

结论：收官建设**未偏离官方约束**——规则包仍锁定在 `h3-prompt-writing@d21241f0a4b3`，官方源文件未被静默更新（规则包校验在 `UNSUPPORTED_RULEPACK` 时直接拒绝）。

---

## 7. 终版 SHA 全量命令清单（`fd7ebb0`）

| 命令 | 结果 |
|---|---|
| `uv sync --project backend --locked` | exit 0（Resolved 42 / Checked 41） |
| `pytest backend/tests -q -rA` | **exit 0，1170 passed / 1 skipped / 0 failed**（skip 为既有 `SHINE_REAL_COMFY_PROBE` 门控；新增 5 项建议性审片回归） |
| `ruff check backend/shine backend/alembic backend/tests scripts` | `All checks passed!`，exit 0 |
| `npm ci --prefix frontend` | exit 0，`found 0 vulnerabilities` |
| `npm --prefix frontend run build` | exit 0，`tsc -b && vite build`，1594 modules，dist 327.31 kB |
| `scripts/verify_local_stack.ps1` | `Local stack verification passed.`（mock 模式，从不生成视频） |
| `scripts/verify_stack_operations.ps1` | `PASS: custom proxy, NoWorker, duplicate refusal, Worker restart and verified shutdown` |
| Alembic | 新库 + 历史库升级 + 降级再升级由 `test_alembic.py` / `test_migrations_windows.py` / `test_v13_migrations.py` 覆盖，head = `f140a0030001` |

输出留档：`F:/ClaudeWorkSpace/tmp_v3/advisory_suite6.txt`（全量 pytest）、`ruff_*.txt`、`verify_local_stack_fd7ebb0.txt`、`verify_stack_operations_fd7ebb0.txt`。

**追加轮次（d8ae267 + 未提交修复）复验**：全量 pytest **1175 passed / 1 skipped / 2 warnings**（唯一 skip = SHINE_REAL_COMFY_PROBE 门控的真实 ComfyUI 启动探测，本轮按任务边界不启动真实 ComfyUI，如实记 SKIPPED）；Ruff clean；`uv sync --project backend --locked`（Resolved 42 / Checked 41）；npm ci（0 漏洞）+ TS/Vite 构建（327.31 kB）；verify_local_stack.ps1（-ApiPort 18123）与 verify_stack_operations.ps1（-ApiPort 18128 / -WebPort 15188）均 PASS；verify_v13_web.py = MOCK_VERIFIED（api_errors=[]）；Alembic 新库 upgrade head 至 f140a0030001（12 迁移链完整）。命令与退出码全表见 `.local-acceptance/final-closure-v4/CLOSURE_ROUND.md` §3。

---

## 8. 资源释放核验

停栈后立即采集（`scripts/capture_release_evidence.ps1`，收据落盘为 JSON + 文本 + 渲染 PNG，不截取桌面）：

| 检查 | 结果 |
|---|---|
| 端口 8140 / 5183 / 8188 / 8116 / 8117 | **全部无监听** |
| 本项目残留 python / node 进程 | **无** |
| GPU | `GPU-f3fda412-dabc-0f4a-c8e0-9fd69902205b`，16303 MiB 总量，**1286 MiB 已用**、14692 MiB 空闲、0% 利用率 —— 回到空载基线（本机空载实测 983–1633 MiB） |
| 判定 | **PASS** |

证据：`.local-acceptance/final-closure-v3/20261031-final-portrait-h/release/`
（`release-evidence.json`、`ports-after-stop.txt`、`processes-after-stop.txt`、`gpu-after-stop.txt`、`release-receipt.png`、`stop-stack.log`）。

---

## 9. 证据索引与路径

`.local-acceptance/final-closure-v3/evidence-index.json`：修复自引用后按实测 **972 个文件、632 805 500 字节**，逐文件记录 `path` / `bytes` / `sha256`（生成器：`scripts/build_evidence_index.py`，已按路径排除索引自身；独立校验器两轮 PASS）。与旧报告 969 的差异 = -1 旧索引自引用 +4 个审查轮 SQLite WAL 只读检查产生的 -wal/-shm 旁挂文件（媒体零改动）。

| 路径 | 内容 |
|---|---|
| `20261031-final-portrait-h/` | **竖屏全链 PASS**：五 Agent → 3 镜真实 H3 → 自动 EDL → 自动装配 21.896 s / 768×1344 → Web 实播；另含负例与释放收据 |
| `20261031-final-portrait-f/`、`-g/` | 竖屏失败轮次：逐轮阻断点与修复见 §4.3（f/g 各含一次真实竖屏产物） |
| `20261031-final-portrait-d/`、`-e/`、`final-portrait/`、`final-portrait-b/`、`final-portrait-c/` | 收官过程中的失败轮次，逐轮原因见 §3 与本报告 §12 |
| `20261031-run-a` … `run-l` | 前序排障轮次 |
| `closure-script-v3.md`、`negative-missing-masters-script.md` | 正/负例剧本 |
| `evidence-index.json` | 逐文件 SHA-256 索引 |

所有证据目录、数据库、媒体与密钥均在 `.gitignore` 覆盖范围内（`/.local-acceptance/`、`/.local-data/`、`*.db`、`*.mp4`、`*.wav`、`secrets/`），`git ls-files .local-acceptance .local-data` 返回 0 个文件。

---

## 10. Git SHA 与推送核对

| 项 | 值 |
|---|---|
| 功能提交（被测代码） | `fd7ebb0` — *Close the loop: automatic delivery and script-driven storyboarding* |
| 基线 | `09f17c6` — *Record the final local technical acceptance and its matrix* |
| 文档提交 | 本报告与交接文档（进度 + 测试记录）同属其上 |
| 最终报告 | 本文件，提交于交接文档提交之上 |
| 真实竖屏运行所依据的功能提交 | `fd7ebb0`（与最终功能提交一致） |
| 横屏全链 PASS 所依据的功能提交 | `a403a62`（交付与分镜主路径；该路径代码在 `fd7ebb0` 未变） |
| 适配器身份 | `d182268e70b4d57be695f01f0328c44df05b90480398777829f53785e301a93a` |
| 本轮修复 | 已提交并推送至 `origin/main`：delivery_engine.py / test_delivery_orders.py / build_evidence_index.py 共 3 文件 361+/109-；`git diff --check` 干净 |
| GPU UUID | `GPU-f3fda412-dabc-0f4a-c8e0-9fd69902205b` |

推送核对：`git rev-parse HEAD` 与 `git rev-parse origin/main` 在推送后一致（推送前后各核对一次）。

**安全核对**：`.local-acceptance/`、`.local-data/`、`*.db`、`*.mp4`、`*.wav`、`secrets/` 均在 `.gitignore` 覆盖内；`git ls-files .local-acceptance .local-data` 返回 0 个文件；本次提交不含任何媒体、数据库、密钥或证据文件。LLM 密钥只存在于 DPAPI 密文（`data/secrets/*.secret`），种子脚本在进程内解封后经环回 HTTP 注入，**不打印、不入库、不入证据**。

---

## 11. 豁免与人工边界（不计入本地技术判定）

| 项 | 状态 | 说明 |
|---|---|---|
| GitHub Actions | **WAIVED_EXTERNAL_BILLING** | 账户额度阻断；本轮**未**修复计费、**未**重跑托管 CI、**未**为托管 CI 改动生产代码；**不声称**远端 CI 通过 |
| 权属确认 RIGHTS_APPROVAL | **PENDING_HUMAN** | 正式资产版权与授权由用户确认；本轮沿用历史已批准资产，哈希未变 |
| 艺术质量确认 ARTISTIC_REVIEW | **PENDING_HUMAN** | 成片表演、镜头语言与声音设计的观感确认 |
| 最终影片确认 FINAL_FILM_APPROVAL | **PENDING_HUMAN** | 是否接受当前交付版本 |
| 字幕文本定稿 | **PENDING_HUMAN** | 字幕层可修订可导出，文案需人工定稿并批准 |
| 硬帧锚定 / 视频参考连续性 | **NOT_RUN** | 本轮只如实标识软连续性（`last_frame`），未宣称这两项通过 |
| 真实人工审批 | **PENDING_HUMAN** | 本轮"批准"由操作凭据驱动的自动化流程执行，证明流程与技术链路，**不等于**用户对版权或艺术质量的实际批准 |

---

## 12. 已知风险与未覆盖项

1. **上游长尾**：托管推理服务在观测窗口内出现过单次调用 9 分钟的慢响应与一次 300s 断链。产品已按红线**不重放不确定结果**（一次未知结果 = 一次 `NEEDS_REVIEW` + 人工对账），因此极端情况下需要人工介入或重跑；这比"重放到成功"更诚实，但可用性代价真实存在。
2. **本地审片模型的自洽性**：本地 Qwen3-VL-4B 在竖屏两轮真实运行中均返回过**自相矛盾**的结论（`style_input_checks` 声明某风格字段"不一致"却不给对应 finding）；横屏 run-m 的 3 镜审片全部自洽，说明这与素材/文案相关而非偶发。**已按用户决策处理**（§3.5）：小模型自审只做一轮、结果绑定成可上报的证据、按提示进入人工复核，**不再阻断自主成片**；不确定的结果（答复未到）仍按红线阻断并等待人工对账。
3. **仲裁者是计划作者**：导演既拥有镜头又要仲裁场记意见，存在自证偏向。缓解手段是：裁决必须引用规则包中**可验证存在**的规则 id、逐条裁决、由**人工 apply** 才生效；规则包只有 9 条准则，覆盖不到"故事品味"层面，因此产品**不**把仲裁扩展到艺术判断。
4. **两道放宽都只作用在"判断"上，不作用在"事实"上**：仲裁只覆盖角色之间的连续性冲突；建议性审片只覆盖模型自己的结论。资产缺失（`BLOCKED_MISSING_MASTERS`）、H3 编译与规则包校验、技术 QC（解码/时长/音轨）、已批准主资产绑定、租约与模型释放**一律不可绕过**，也不因模型意见而放行。
5. **未覆盖**：多机并发集群与网络分区、硬帧锚定、字幕文案定稿、托管 CI。

---

## 13. 复现命令

```powershell
# 竖屏全链真实验收（全新 RunName，一次只跑一个栈）
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_real_closure_v3.ps1 `
  -RunName <new-run-name> -Profile local-h3-ref2va-pdd8-2img-1aud-portrait -TimeoutMinutes 210

# 横屏全链真实验收
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_real_closure_v3.ps1 `
  -RunName <new-run-name> -Profile local-h3-ref2va-pdd8-2img-1aud -TimeoutMinutes 210

# 真实负例（无主资产角色 → BLOCKED_MISSING_MASTERS）
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_negative_missing_masters.ps1 -RunName <run>

# 官方规则锁定核对
backend\.venv\Scripts\python.exe scripts/verify_h3_rulebook_pin.py --skill-dir <official-skill-dir>

# 资源释放收据
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/capture_release_evidence.ps1 -OutDir <dir> -DataDir <run>/data

# 证据索引
backend\.venv\Scripts\python.exe scripts/build_evidence_index.py --root .local-acceptance/final-closure-v3 `
  --out .local-acceptance/final-closure-v3/evidence-index.json
```

---

## 14. 签核

| 角色 | 状态 | 说明 |
|---|---|---|
| 首席架构师 / 最终验收负责人 | 本报告出具 | 本地技术收官判定：**横屏与竖屏均真实全链 PASS**（剧本 → 五真实 Agent → 3 镜真实 H3 → 自动 EDL → 自动装配 → Web 实播）；5 类缺陷已修复并在最终 SHA `fd7ebb0` 上全量回归（1170 passed）；追加轮次（同日独立审查）关闭两项代码缺陷与索引自引用，`d8ae267`+未提交修复上全量回归 **1175 passed / 1 skipped**（§0） |
| 用户（权属 / 艺术 / 最终影片） | **PENDING_HUMAN** | 见 §11 |
| 小模型自审的门禁权重 | **已决（用户定调）** | 一轮、结果上报人工复核、**不阻断**自主成片；实现见 §3.5，真实运行表现见 §4.2 |
| GitHub Actions | **WAIVED_EXTERNAL_BILLING** | 见 §11 |

本报告不宣称：远端 CI 通过、硬帧锚定通过、托管 CI 计费恢复、用户已完成版权或艺术审批、小模型自审的结论等于人工复核的结论。
