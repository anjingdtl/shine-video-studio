# 收尾建设验收：用户拉取后打开正确工作栈 + AI 一次授权完成整备（2026-11-19）

> 审计基线：`42639bda5baa87cc7d4a577994d37be0efd68850`（本地 main，开发前工作区干净，仅 `.trae/` 未跟踪）。
> 契约依据：`docs/SHINE_AI_COPILOT_V31_TASK_AUTHORIZATION_CONTRACT.md`、`docs/SHINE_COPILOT_V31_ACCEPTANCE.md`、AGENTS.md。
> 原则：一套事实、一个裁决、一个执行者；运行时源码零写入；未实测项标 PARTIAL。

## 1. 交付提交

| 项 | 值 |
|---|---|
| 功能提交（本地 main） | `c80de07`（43 文件，+3960 / −230） |
| 基线父提交 | `42639bda5baa87cc7d4a577994d37be0efd68850` |
| 报告 SHA 补记 | 本行随后续小提交入库（功能提交不含自身 SHA） |
| 推送 | 本轮不推送远端（按授权范围，本地提交） |

## 2. 方向1：启动器旧栈复用与运行身份

**唯一身份实现**：`backend/shine/source_identity.py`（纯 stdlib）。Git 检出 = HEAD + 运行根
（backend/frontend/scripts）的已跟踪 diff + 未跟踪文件内容摘要；分发包 = BUILD_INFO.json
（source_commit/package_version/built_at）；两者皆无 = 显式 unknown。启动器经
`scripts/shine_source_identity.py`（任意解释器可跑）在启动时计算并写入 `processes.json.source_identity`；
API `/health` 与 `/api/v1/system/status` 暴露同一事实（进程内缓存）；supervisor 的 health 与
`supervisor.json`  likewise；顶栏在数据目录旁显示运行代码身份；BLOCKER 报告含「运行代码身份」行。

**两个判定分离**：`update_checkout.ps1` 只裁决「本次 Git 是否快进」（ff-only，脏/分叉/离线不阻断）；
`start_shine.ps1` 在启动锁内裁决「运行栈是否符合本次请求」：身份 digest、data_dir、render_provider、
Worker 形态（-NoWorker 双向）。不符合 → 经唯一停止执行者 `stop_shine.py` 换代；停止失败且旧 API 仍活
→ 具名 throw、归档未决记录、**绝不把旧页面当启动成功交付**。supervisor 在复用路径同样保障
（死亡→启动；活着但跑别的代码→`--supervisor-only`  verified stop 后重启）。`start_dev.ps1` 不再自行
stop（exit-10 冗余停止删除），换代裁决归 start_shine。

### 隔离 Windows checkout 实测（F:\ClaudeWorkSpace\.launcher-accept，独立 SQLite，真实 API/Web/Worker）

| 场景 | 结果 | 证据 |
|---|---|---|
| A 栈运行（基线 42639bd，旧清单无身份）→ 手动 pull 到 B（2e0f5e7）→ 双击 start_dev | PASS | 「running code [unrecorded (older launcher)] … Replacing」；manifest/health 身份 == B；旧 PID 31304/7544/4164 全 GONE |
| 重复启动 | PASS | 「Running code: git:2e0f5e7 (matches this checkout)」+「already running」，PID 不变 |
| 半栈（verified stop 杀 Web）→ 启动 | PASS | 「missing: Web; reconciling」→ 全栈恢复，Web 200 |
| 端口被占（8060 dummy）无 AutoPorts | PASS | exit 1，「requested ports are not free」具名 |
| 端口被占 + AutoPorts | PASS | 自动换 8061，启动成功 |
| 停止失败（篡改 API birth 证书）+ 身份变化 | PASS | stop refused×3 → exit 1「Cannot replace the running stack… old page is NOT a successful start」；旧 API 仍 200（未盲杀）；未决记录归档；恢复证书后正常换代 |
| -NoWorker 栈 → 完整栈请求 | PASS | 「started with -NoWorker; this request needs the full stack」→ 换代，worker 出现 |
| 未提交运行代码修改 | PASS | dirty digest 变化 → 换代，manifest dirty=true |
| 无 Git 分发包（.git 移除 + BUILD_INFO） | PASS | git→package 身份换代，kind=package；恢复 .git 后回到 git 身份 |
| 日常栈真机换代（D:\Shine\data，8000/5173） | PASS | 旧栈（无/旧身份）→ verified stop → npm ci 恢复 → 新代码栈；health 身份 git:42639bd+改动，schema f180f0010007 |

回归：`backend/tests/test_launcher_source_identity.py`（16 例，含真实 pwsh 身份计算与匹配裁决）。

## 3. 方向2：首次诊断与模型继承

- `_repair_action(snapshot, db=db)` 签名不一致（TypeError）已修；最小修复范围单一裁决
  `diagnostics.minimal_repair_scope`：缺件→精确缺件能力；运行合同码（ACTIVE_RUNTIME_CONFIGURATION_CHANGED
  等）→ 只修被失效的视频运行时 h3；无证据→只读 setup_reconcile，**删除无依据 ["h3","image"] 双整备**。
  planner `_setup_plan` 复用同一裁决。
- 失效回读地址 `GET /api/v1/setup/prepare/{task_id}`（不存在）→ `GET /api/v1/setup/summary`；
  新增回归断言 planner 全部 readback 地址都是真实路由。
- 模型继承：tool_loop 在任务开始解析 `profile_for_role_or_inherit` 并 pin，原生 Tool Calling 与
  结构化降级共用同一已选模型（修复降级路径 LLM_NOT_CONFIGURED）；预览/循环/诊断同口径。
- 新会话首条自然语言目标可直接进入授权预览（免先买一次诊断；按需建会话）。
- 旧会话吞新故障：MessageCreate 支持随消息重绑定 page/job/attempt/task/error；前端新故障到达时
  对已恢复会话重绑定并再诊断（不再 `if (restored) return`）。
- GRANT-17 脱敏复现（开发机）：真实受管安装 fixture → 改钉住渲染器文件 →
  runtime INVALID_REQUIRES_REVIEW + verdict/system status 同码 ACTIVE_RUNTIME_CONFIGURATION_CHANGED
  → copilot 诊断推荐与证据精确一致的最小修复。**用户机现场验证仍缺（PARTIAL）**。

回归：`test_copilot_first_diagnosis_v31.py`（8 例）。

## 4. 方向3：一次授权后台执行贯通

- 能力目录：`production_start` 接入 test 组（唯一生产入口，原门禁不变）；删除 _COUNT_COSTS 里
  三个无声明假名；workflow_user_copy 计 profile 成本。预览/菜单/执行同源于 declarations。
- 只读调查工具 `copilot/read_tools.py`：inspect_environment / read_resource_inventory /
  read_node_inventory（真实 live object_info，渲染器不在不为读取而启动）/ read_original_task /
  read_production_run；不建 SupportAction、计入工具预算、越界具名拒绝。
- 持久驱动：循环转录/步骤/无进展/started_at/pending 落 grant 行 `payload.grant.loop`；
  `grants.claim_loop/save_loop/release_loop` 短租约 CAS；Worker tick `_advance_task_grants`
  每拍一个有界模型回合（运行配置阻塞时仍可推进整备型 grant）；API `/run` 同一租约，
  重复点击接回同一执行（LOOP_ALREADY_RUNNING）。关页/重启后前端经
  `/sessions/{id}/task-grant` 接回同一 grant 卡片（轮询 + 继续推进）。
- 委托原任务 → WAITING_EXECUTOR，读回驱动（不空转模型、不重提）；终态读回消费 pending
  （修复 NEEDS_REVIEW 读回死锁）；回执（含 targets/next_step）留痕供恢复后模型行动。
- 模型叙述式 finish（正文「调用 X…」代替工具调用）→ 有成功读取时给有界纠正续行，
  反复叙述仍具名暂停；scope 真实 ID 进入状态摘要（修复模型编造 project slug → SCOPE_EXCEEDED）。
- 多安装歧义：预览返回 candidates + choice_required → 未选定 can_authorize=false，
  authorize 409 INSTALLATION_SELECTION_REQUIRED；选定一次再冻结进 scope。
- PAUSED_USER_DECISION 可由用户显式「继续推进」恢复（审计 SUPPORT_TASK_GRANT_RESUMED）；
  Worker 不自动恢复任何暂停；预算/范围/合同/未知暂停点击不可恢复。
- 真实服务器穿测修复：TaskGrantCard 全部 JSON POST 补 Content-Type（stub 测试未能发现的 422）。

回归：`test_copilot_grant_durable_v31.py`（12 例）、`test_copilot_objective_budget_v31.py`（9 例）、
前端 `taskGrantDurable.test.tsx`（2 例）、`copilotFirstGoal.test.tsx`（2 例）。

## 5. 方向4：成功判定与累计预算

- `grants.settle(reserved=, actual=, step_key=)`：只退还**本步**预留未用部分（reserved-actual≥0），
  累计用量不被单步覆盖；step_key 幂等防重放双退；actual>reserved 不扩边界；委托未报账保留预留。
  execute_under_grant 以原执行者回执（安装计划 download_bytes）结算。
- 目标级权威核验 `copilot/objective_verify.py`：plan_frozen / files_verified / nodes_loadable /
  graph_registered / production_selected（jobs.workflow_profile_id 真实选用）/ sample_completed
  （非 mock PASSED job + artifact 链）/ setup_verdict 分项给结果；目标关键词决定必需里程碑；
  完成判定 = 有原回执 ∧ 必需里程碑全 VERIFIED，否则 OBJECTIVE_NOT_VERIFIED 具名暂停；
  结论落 `payload.objective_verification`，GET 端点回读。

回归：连续两步累计（100+40=140 而非 40）、重放三次不双退、并发 CAS 仅一个成功、撤销拒新预留、
注册型/小样型目标判定、mock artifact 永不满足真实小样、循环端到端 OBJECTIVE_NOT_VERIFIED。

## 6. 方向5：普通用户入口真实验收（开发机，已配置 MiniMax + 受管 ComfyUI）

环境：D:\Shine\data，verdict READY，单实例 managed+adopted
（F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI），h3 READY / image VERIFIED_RENDER / relayvsr READY，
文本模型 MiniMax-M3.1-flash（support_agent 继承 comfyops 路由）。

### 真实浏览器一次授权旅程（grant-dc5f4981f52d4ceeaaa1aa11）

| 步骤 | 事实 |
|---|---|
| 新会话首条目标 → 预览卡 | PASS（修复 Content-Type 后）；范围/上限/风险/绝不执行/模型行完整；安装位置真实 |
| 唯一授权点击 | grant 创建，digest c6f709ed…，预算 200GB/60 次等 |
| 只读调查 | inspect_environment（首次编造 slug 被 SCOPE_EXCEEDED 正确拒绝；纠正后 OK）、read_resource_inventory OK、read_node_inventory OK（live object_info，LoRA 过滤） |
| 写操作经原执行者 | setup_repair(h3+relayvsr, deep_scan, verify_runtime) → DELEGATED，原任务 setup-2afd361ec196，action fac3b08c |
| 原任务推进 | DONE：「服务核验通过，本机配置已激活；已核对 4 个生成方案节点」；**download_bytes=None，installation plans=0 → 零额外下载、零重复安装** |
| WAITING_EXECUTOR 读回 | 项目级 h3 目标 WORKFLOW_NOT_VALIDATED（该项目方案未 live 校验）→ NEEDS_REVIEW 具名暂停（诚实，不假称成功） |
| 恢复后继续 | pending 消费、回执留痕、模型获得回合；模型继续只读调查后叙述式 finish → 有界纠正 → 仍 PAUSED_USER_DECISION（NO_AUTHORITATIVE_EVIDENCE）。共 10 次工具调用（预算 60），累计预算账本一致 |

### 零额外下载 / 零重复安装 / 零额外 Profile（GRANT-18 环境侧）

- 原整备任务 `setup-2afd361ec196` 终态 DONE，`result.download_bytes=None`、`installation_plan_ids=[]`
  → **零额外下载、零重复安装**（已有文件只读核验复用）。
- grant 的 tool_calls/sub_actions 中**无** workflow_graph_register / workflow_user_copy /
  resource_install_plan 调用 → 本次授权**零额外 Profile**（台账 40 条均为历史既有；整备「已核对 4 个
  生成方案节点」是对既有方案的 live 核验，不是新建）。
- 台账直查（只读）：会话窗口（≥2026-11-19T04:50Z）内新建 workflow_profiles=0、
  installation_plans=0、jobs=0。

### 工作栈身份显示（真机 UI）

顶栏实测：`工作台后端在线 / D:\Shine\data / git:42639bd+改动 / 真实制作已就绪`。
截图：`.launcher-accept/evidence-topbar-identity.png`、`.launcher-accept/evidence-grant-preview.png`
（Git 忽略目录，不进安装包）。

### 真实小样与自定义图装配（PARTIAL）

- 模型在本次会话未提出 `production_start`（真实小样）也未装配 LoRA+二采+RelayVSR X2 自定义用户图；
  循环按合同诚实暂停，**未假称成功**。真实 GPU 小样 attempt/artifact 与自定义图注册：**PARTIAL，未运行**。
- 产品侧链路（授权→只读调查→原执行者委托→WAITING_EXECUTOR→读回→具名暂停→显式恢复→模型回合）
  已在真实环境完整证明；模型能否自主走到小样是模型能力边界，不是产品门禁缺陷。
  环境既有质量档（pdd8 加速 / 原生 20 步 / pdd8+放大）与 relayvsr READY 已覆盖目标所需资源。

## 7. 全量回归

- 后端 8 分片（固定 WorkRoot `F:\ClaudeWorkSpace\.pytest-v31`，CODEBUDDY_SAFE_DELETE_ENABLED=0，
  NO_PROXY/no_proxy 置空，PYTEST_DEBUG_TEMPROOT 置空，每片独立 basetemp）：
  - 首轮（12:35，树尚未定稿）6/8 clean：chunk-3 为 /health 契约新增 source_identity 字段
    （断言已纳入新字段）、chunk-5 两例为依赖误删窗口期 typescript 暂缺（环境损伤）；三例单独复跑全绿。
  - 定稿轮（13:21，shards-20261119-132110-042）7/8 clean：chunk-3 唯一失败为新增审计码
    SUPPORT_TASK_GRANT_RESUMED 未登记前端清单（运行中已补 `frontend/src/auditEventCodes.json`，
    该测试单独复跑绿）；其余 7 片 exit 0、零 FAILED/ERROR。
  - chunk-3 同文件集复跑（定稿树，`chunk3-rerun.log`）：**exit 0、零 FAILED/ERROR**（36 模块）。
    合计：定稿轮 7 片 clean + chunk-3 复跑 clean = **等价 8/8 全绿**（284 文件）。
  - 日志留存：`F:\ClaudeWorkSpace\.pytest-v31\shards-20261119-132110-042\chunk-*.log`、
    `chunk3-rerun.log`、`first-run-20261119-123555\`（首轮）。basetemp 已清理。
- 前端：`npx vitest run --no-file-parallelism` **87 文件 / 445 例全绿**；
  `npx tsc --noEmit` 通过；`npm run build` 通过（vite built）。
- Ruff `ruff check shine tests`（backend  cwd）干净；copilot 合同 `--check` 通过（16 写能力）。

## 8. 事故与恢复（透明记录）

隔离验收初期用 junction 复用主仓 node_modules，A 栈启动器因无前端回执执行 npm ci，穿透 junction
删除共享依赖（esbuild.exe 因被日常 vite 占用幸存）。恢复：移除 junction → 日常栈经新启动器换代
（verified stop 释放锁）→ 启动器自身 npm ci 7 秒还原 194 包 → 新代码栈健康。隔离 clone 改为
「junction + 预写依赖回执」使启动器跳过安装。期间全量回归 chunk-5 两例因 typescript 暂缺失败，
恢复后复跑全绿。日常栈用户数据未受影响（独立 SQLite 验收 + 日常栈仅换代重启，空闲时执行）。

## 9. PARTIAL / 未验证边界

- GRANT-17 用户机（2026-11-18 故障机）现场验证：缺该机台账，未执行；开发机脱敏复现已覆盖诊断链。
- 真实 H3 小样出片（GPU attempt/artifact）：见 §6 补记；若最终未产生真实小样，该项 PARTIAL。
- 其他显卡（4070/5090 等）与用户机安装包链路：本轮未验证。
- VLM 正负校准：本轮改了 worker.py（client_review_identity 钉住），旧 revision 按设计失效；
  真实正负校准仍未完成，不得声称已校准。