# Shine 视频工作台 R5｜基础配置与用户机资源复用 基线审计（R5-0）

> 状态：只读审计完成，进入建设。审计日期：2026-11-18。
> 代码基线：`origin/main@46c889a0739beefaf81123ec726e1cc72e65d22e`（拉取时最新，与方案制定基线一致，无新增提交）。
> 开发分支：`feat/shine-setup-r5-machine-reuse-convergence-20261118`（方案文档提交 `0f3464b` 已 cherry-pick 为 `bcb0fd0`，仅含方案文档一个文件，已核实）。
> 方案：`docs/SHINE_SETUP_R5_THREE_PRINCIPLES_CONVERGENCE_PLAN.md`。
> 方法：逐文件阅读后端权威模块 + 两路只读子审计（前端消费面 / AI 管家与 API 写入面）+ 既有回归清单核对。所有结论带文件:行号证据；历史报障先核对当前代码再定性，不凭历史报告断言现存 BUG。

## 1. 权威事实 → 裁决 → 执行者 调用图（当前主线）

```
磁盘/外部 ComfyUI ──(只读)──> existing_installation.inspect / discover
                              resource_discovery.advance_scan（有界分批，候选≠核验）
                                        │
                 verification_receipts.file_digest / verify_receipt（stat+头尾见证，sidecar 复用）
                                        │
        setup_reconcile.reconcile_installation ──> register_verified_packages（台账 upsert）
                                        │        └─> adoption.adopt_existing_installation（唯一收编执行者）
                                        │
   ComfyInstance + adoption 证据 + ModelPackage + receipts + AppSetting(choice/scan/active/candidate/seal)
                                        │（只读投影，GET 不哈希大文件/不启动进程/不写库）
                 setup_summary.summary ── capability_report ×5（h3/image/vlm/asr/relayvsr）
                    │      └─ variant_readiness.readiness（五层，按所选变体）
                    │      └─ runtime_configuration.runtime_report / service_evidence_status
                    └─ production_mode.machine_verdict（唯一机器裁决）
                                        │
   GET /setup/summary、GET /setup/verdict ──> 顶栏徽标 / 导演基础配置 / 专家设置 / AI 管家（support/context.py 直读两者）
                                        │
   用户意图 ──> POST /setup/prepare/execute ──> setup_preparation.enqueue（唯一持久整备任务）
                    Worker.run_configured_once 每 tick 一步 advance：
                    SCAN→TOOL_REPAIR→RECONCILE→DOWNLOAD→INSTANCE→TARGET→RECIPE→IMAGE_BIND→PROFILES→VALIDATE→IMAGE_VERIFY→IMAGE_SAMPLES→IMAGE_EDIT_SAMPLES
                    各步委派既有执行者：resource_discovery / environment_repair / setup_reconcile /
                    api_setup.install_plan+queue_install_attempt / instances.execute_operation /
                    deployment.generate_configuration / api_setup._register_project_profiles /
                    supervisor /validate-services（停 Worker→核验→激活→恢复 Worker）/ image_renderer
```

关键约束已在代码中成立：

- `setup_summary.py:1-20` 明示 GET 无写；唯一写是用户显式选择安装 `record_installation_choice`（:67）。
- `production_mode.py:148-187` `machine_verdict` 是唯一裁决合成；`/setup/verdict`（api_setup.py:1238）与 summary.verdict（setup_summary.py:833）同函数。
- `setup_preparation.py:267-289` `_save` 以 `task_id` + CANCELLED/SUPERSEDED fencing，旧 Worker 晚回包被拒。
- AI 管家：`support/context.py:78-87` 直调 `setup_verdict` 与 `summary`；`support/diagnostics.py:197-265` 只转述缺件并生成需授权的 `setup_repair` 意图；`support/actions.py:1731/1764` 分发到 `setup_preparation.enqueue` / `reconcile_installation`；无自算缺件/显存/就绪代码。

## 2. 已收敛能力清单（禁止重复建设）

| 能力 | 证据 | 来源轮次 |
| --- | --- | --- |
| 单一安装选择（active→user_choice→adopted→managed→discovered），持久 CHOICE_KEY，多候选只问一次 | setup_summary.py:271-362 | 2026-11-18 |
| 分组 YAML/多行/base_path/家族别名统一解析；presence/adoption/变体绑定/出图共用 | existing_installation.py:461-515；setup_summary.py:506-513；variant_readiness.py:157-165 | 2026-11-18 |
| 无作品机器级整备与采纳；项目步骤具名跳过 | setup_preparation.py:91-101,675-677,935-937；adoption.py:288 | 2026-11-18 |
| H3/image 共一份物理配方；image 绑定消费同一配方不重写 | setup_preparation.py:854-929；deployment.py:1043-1053（仅 h3 record_candidate+.env） | 2026-11-18 |
| 冷启动先受管 before_prepare 再编译动态图 | worker.py（2026-11-18 验收文档记载） | 2026-11-18 |
| 回执 stat+头尾见证复用；路径绑定回执；跨变体按内容身份找回执 | verification_receipts.py:68-129,252-274,277-341 | J04/2026-11-17 |
| 缺件/未落位一次分类两投影；下载体积只算需外取字节 | setup_summary.py:528-548；api_setup.py:1958-1988 | 2026-11-17 |
| 可选审片不连坐（活工作/配置空闲/下载链三处同口径） | runtime_configuration.py:459-492；setup_preparation.py:199-232,536-560 | 2026-11-15 |
| 能力按清单服务端判定；H3 显存资格只拦 H3 计划 | capability.py；api_setup.py:1002-1084 | PR #23/#24 后续 |
| 配置身份只比机器事实；仓库脚本升级不算环境变化；配方路径锚定 | runtime_configuration.py:45-191 | R4（2026-11-17） |
| 变体五层就绪按依赖顺序；真实出片证明运行链路；机器级核验项目继承 | variant_readiness.py:394-564；api_setup.py:718-727 | 2026-11-09/11-14 |
| 候选不借基线证据；图推导权重（required_component_ids=[] 不恒假） | api_setup.py:770-788；profile_gate.py | 2026-11-09/11-14 |
| 含空格/中文路径脱敏整串归一（不再残缺匹配导致重复哈希） | security.py `_ABSOLUTE_PATH_WHOLE`（2026-11-18 验收记载） | 2026-11-18 |
| Windows 大小写复用不复制 | test_setup_machine_reuse.py:72 | 2026-11-18 |
| 前端活路径只读 summary/verdict，单一 prepare/execute | SetupCenter.tsx:355-411,450-486；SetupEnvironmentStatus.tsx:214-232；DirectorSetupPanel.tsx:154-175 | 2026-11-16/11-17 |
| 检索候选≠核验；截断续扫游标；coverage 披露 | resource_discovery.py:263-401,462-497 | PR #18 后续 |

## 3. 当前代码仍然存在的真实问题（红灯反例目标）

### B1 收编身份以端口为主键 —— 物理安装身份不稳定（方案嫌疑 1；INV-03/04/13）

- `instances.py:78-79` `instance_identity(mode, port)` = uuid5(mode+port)；`adoption.py:374` 外部收编直接用 `instance_identity("local", port)`。
- 后果链（均可写成红灯反例）：
  1. **端口改变 → 同一物理安装产生第二条实例行**。旧行保留旧端口与全部采纳证据；`setup_reconcile._known_port`（setup_reconcile.py:88-108）按 root 读回**旧端口**，重新收编仍落在旧端口；新端口服务无法被同一身份继承。
  2. **同 root 两行 → 选择漂移**。`candidate_installations` 的 `by_root`（setup_summary.py:284）字典覆盖， last-wins，取决于 DB 遍历顺序（INV-04 反例）。
  3. **同端口不同 root → 409 死路**。`adoption.py:381-383` `INSTANCE_IDENTITY_CHANGED`：用户换了一套 ComfyUI 但端口相同，无法收编新安装，除非删历史（方案禁止）。
  4. **variant-availability 取第一条 adopted 行**（api_setup.py:708-713，见 B4），多行时可能拿错 root 做扫描回退。
- 端口耦合的六个强制点：`adoption.py:374`、`deployment.py:621`（受管阶梯，可保留）、`deployment.py:846`（generate_configuration 按端口 uuid 查行）、`handoff_startup.py:569`（_installation_evidence）、`resource_resolver.py:139`（managed_installation_pin）、`runtime_configuration.py:384`（configuration_identity）。
- 修复方向（最小、可回滚、不推翻历史主键）：为**已收编外部安装**引入按物理 root 的稳定身份 `adopted_instance_identity(root)`；单一查找函数按「root 身份行 → 同 root 的旧端口行（含不同端口）→ 同端口探测行」顺序解析，历史行**保留原 ID**；端口是同一行上的可变端点属性（变更写审计，采纳字节证据不动）；六个强制点改为接受「端口 uuid 行 OR adopted 且 payload.port 一致」的同一谓词；受管实例（Shine 自建）身份不变。配套：`by_root` 确定性偏好（active/choice 端口匹配优先）。
- 风险：历史 FK/租约/审计全部引用旧 ID —— 方案是兼容查找而非重算旧 ID，无迁移破坏；新身份行只出现在「历史上从未收编过的 root」。

### B2 RECIPE 步幂等跳过不看端口与变体变化（INV-09 反例）

- `setup_preparation.py:870-876`：active+services_verified+同 instance_id 即「沿用现有配置」。
  - 端口变化（B1 修复后同一 instance_id、新端口）→ 跳过 → 配方仍指旧端口，整备链不会修复端点。
  - 激活的是候选变体（如 W6A8）而本次请求默认 INT8 → `request.variant_id in {None, default}` 成立 → 跳过 → 静默沿用候选配方。
- 修复方向：跳过条件追加「端点一致」（active identity.comfyui_base_url 的端口 == target.port）与「变体一致」（candidate/active 记录携带 variant_id；旧记录无该字段按默认处理，仅当请求为默认时跳过）。variant_id 记在 AppSetting value 顶层（不进 identity，不改 recipe 字节，旧封 seal 不受影响）。

### B3 手动收料与 RelayVSR 落位每次全量重哈希已缓存字节（INV-02/05 反例）

- `setup_reconcile.py:368` `manual_files_reconcile` 对**每个已缓存文件** `_hash_file(target)` 全量读；:373 候选匹配同样裸哈希。整备每轮 RECONCILE 都调用它 → 几十 GiB 缓存被反复全读。
- `setup_reconcile.py:439` `_place_relayvsr_served` 对已存在目标文件全量哈希判同。
- 修复方向：改走同一 `verification_receipts.file_digest`（sidecar stat+头尾见证复用，威胁模型不变：同 tick 同长改写仍被见证区分）。不新建缓存。

### B4 variant-availability 的安装选择是第二套（INV-07 反例）

- `api_setup.py:708-713`：遍历 ComfyInstance 取**第一条** adopted 行的 root，而不是 `resolve_installation` 的选定安装。多安装/多行时与 summary 的安装事实不一致，扫描回退可能读错目录 → 假「本机没有」。
- 修复方向：改读 `resolve_installation(db, settings)["selected"]`（同一事实）；无选定时不做扫描回退。

### B5 检索报告在覆盖不完整时仍断言 MISSING（INV-14 反例）

- `resource_discovery.py:404-433` `report()` 把无候选文件一律归 `missing`，虽然 :441 披露 `coverage_complete`。活 UI（ResourceScanCard）不消费该 missing 桶，但合同本身违反「截断/不可访问只得 UNKNOWN」。
- 修复方向：覆盖不完整或存在 unreadable 区域时，未找到文件归 `unknown`；仅当 coverage_complete 且无 unreadable 时才归 `missing`。

### B6 前端活路径把「读不到」显示成「缺件/未就绪」（INV-14/§5.3 反例）

- `SetupCenter.tsx:375,391-400`：summary 未到达/失败 → `capDownloadBytes` 回退整档体积，能力勾选显示「需要下载 17.3GiB」；`h3Complete/imagePresent` 为 false 被当成「没有」。
- `SetupCenter.tsx:143-148,556-558`：availability 读不到时用自查扫描计数判「本机已有」（第二套 presence）。
- `SetupCenter.tsx:727-729`：verdict 缺失时回退 runtime ACTIVE 文案（第二来源）。
- `DirectorSetupPanel.tsx:269,292`：advisory 读不到时分母硬编码 13/10，显示「0/13」。
- `DirectorShootPanel.tsx:127-138`：策略选项读不到时 FALLBACK 硬编码 `verified:true`（显示层伪造已核验；提交仍由服务端门禁把守，但显示违反 §5.3）。
- `MachineProgressBanner.tsx:50`：100% 时文案为前端常量而非服务端 label（服务端保证 100%=READY/RUNNING，但 RUNNING 时文案不准确）。
- 修复方向：summary/availability 未到达一律显示「读取中/未知」，不回退整档体积、不判「已有/没有」；canPrepare 要求 summary 已到达；FALLBACK verified:false；进度条读服务端 label。

### B7 死代码中的第二执行链与第二裁决（INV-16/§7.1；专业模式已下线）

- `SetupPanel.tsx` **不被任何活页面引用**（App.tsx 只渲染 DirectorSetupPanel；setupNodePackNotes.test.tsx:6 注释明示「专业模式下线」）。其中仍存在：
  - `prepareAutomatically`（:787-879）：浏览器串行链 install-plan→approve→wait→实例阶梯×8→profiles→configuration→services/validate→activate，与持久整备任务并行竞争同一物理目标。
  - `prepareAll`（:620-653）：第二条浏览器串行链（profiles→configuration→validate→activate→startWorker）。
  - `installOptionalReviewers`（:770-785）：直建 install-plan 并浏览器执行，绕过唯一整备入口（DirectorSetupPanel.installAdvisory 已走 prepare/execute）。
  - `buildModelCards`（:223-289）：浏览器按 scan+catalog 自算「已安装并核验」资格（第二套裁决）。
  - :924-926/:932：verdict 缺失回退 runtime ACTIVE 称就绪。
- `ImageCapabilityCard.tsx`、`VariantReadinessCard.tsx` 同样只被测试引用（活路径无引用），自带 install-plan/profiles-validate 按钮。
- `setupExecution.ts` 的 approveAndExecuteSetupPlan 是**既有批准链**（grant→approve→execute+重附着），本身不是第二安装器；问题在于被上述自动链用来从浏览器创建并执行新计划。
- 修复方向：删除 SetupPanel 的三条自动/直建链与对应按钮，保留只读诊断与**既有计划的恢复入口**（批准/执行/整理中断安装——这是原批准链的合法用途）；审片补装改提交 prepare/execute；modelCards 的资格文案改读服务端能力结论或降为扫描事实描述；同步把 simpleSetup 等测试收敛到「唯一执行者」合同（参照 2026-11-18 轮 test_setup_preparation_instance 的收敛方式）。死组件 ImageCapabilityCard/VariantReadinessCard 的独立下载/验证入口一并删除或改为导航到唯一入口。

### B8 candidate_installations 同 root 多行 last-wins（并入 B1 修复）

- `setup_summary.py:284`。B1 修复后不再产生新的重复行；对历史重复行按「active/choice 端口匹配优先」确定性选择，并保留全部历史行。

## 4. 历史报障核对：已修复，不得重复建设

| 历史报障 | 当前代码状态 | 证据 |
| --- | --- | --- |
| PR #23 前端综合就绪误触发 H3 计划连坐图片 | 已修复：活路径按能力事实勾选，服务端 plan_capability 判定 | SetupCenter.tsx:355-411；api_setup.py:1002 |
| PR #24 已核验 bound=false、40GiB 被规划重下 | 已修复：路径绑定回执+consumable；缺件/未落位同源 | verification_receipts.py:277-341；setup_summary.py:528-548 |
| PR #27 出图绑定配方缺失、重复登记 | 已修复：一份物理配方+IMAGE_BIND 消费；register_catalog_packages 内容寻址幂等 | setup_preparation.py:891-929；deployment.py:365-403 |
| 2026-11-16 顶部/能力卡/拍摄页不同裁决 | 已修复：machine_verdict 唯一合成 | production_mode.py:148-187 |
| R4 升级/搬迁 READY 失效 | 已修复：机器事实指纹+脚本排除+锚定 | runtime_configuration.py:45-191；test_runtime_identity_relocation.py |
| 2026-11-17 已核验却报缺 6 文件 | 已修复：presence/receipts/计划同根同源 | test_setup_missing_verdict.py |
| 2026-11-18 含空格路径脱敏残缺→重复哈希 | 已修复：整串绝对路径稳定脱敏 | 2026-11-18 验收文档；security.py |
| 2026-11-18 大小写误判→额外缓存复制 | 已修复 | test_setup_machine_reuse.py:72 |
| supervisor 残留锁 DLL | 已修复：stop_shine 收尾 supervisor | test_supervisor_stop_convergence.py |

## 5. 改动面与风险

| 改动 | 文件 | 风险与回滚 |
| --- | --- | --- |
| B1 稳定收编身份+兼容查找+端点可变 | instances.py、adoption.py、deployment.py、handoff_startup.py、resource_resolver.py、runtime_configuration.py、setup_summary.py、setup_reconcile.py | 历史行 ID 不变；新谓词对旧端口行完全兼容；回滚=恢复旧查找。需覆盖：旧行端口变更、同端口换 root、探测行接管、多行确定性 |
| B2 RECIPE 跳过条件 | setup_preparation.py、runtime_configuration.py（record_candidate/activate 携带 variant_id）、deployment.py | value 顶层新增字段，旧记录缺省按默认；不改 identity/recipe 字节 |
| B3 回执复用 | setup_reconcile.py | 威胁模型不变（stat+见证）；回滚=恢复裸哈希 |
| B4 选择同源 | api_setup.py | 只读投影改读同一 resolver |
| B5 unknown 桶 | resource_discovery.py | 活 UI 不消费 missing 桶；测试断言更新 |
| B6 未知≠缺件 | SetupCenter.tsx、DirectorSetupPanel.tsx、DirectorShootPanel.tsx、MachineProgressBanner.tsx、SetupEnvironmentStatus.tsx | 显示层；门禁仍全在服务端 |
| B7 删除死执行链 | SetupPanel.tsx、ImageCapabilityCard.tsx、VariantReadinessCard.tsx、simpleSetup.test.tsx 等 | API 端点全部保留（批准/恢复链仍是原入口）；测试收敛到唯一执行者合同 |

**不做的事**：不新建 machine_inventory 表、不新建第二缓存/安装器/调度器/资格引擎；不改 GPU 租约、进程三证、Ref2VA 唯一路径、SHA 钉住与候选验收红线；不重写历史台账/风格/成片；不触碰 `worker.py`/`preproduction.py` 的审片身份钉住文件（本轮改动不涉及，若涉及则按 AGENTS.md 重校准义务如实标注 PENDING）。

## 6. INV-01~16 测试落点（现有 + 新增）

| INV | 现有基础 | 本轮新增/加强 |
| --- | --- | --- |
| 01 | test_setup_machine_reuse.py:56 | 端口变更后仍不重下（B1） |
| 02 | test_setup_missing_verdict.py:92 | 手动收料重复执行零全量哈希（B3，计数 file_digest 调用） |
| 03 | test_runtime_identity_relocation.py | 端口变更：回执/采纳证据保留、seal 失效、不认领外部进程（B1/B2） |
| 04 | test_setup_machine_reuse.py:136 | 双候选显式选定后重启/切页不漂移；同 root 历史多行确定性（B1/B8） |
| 05 | test_setup_chain_convergence.py | 重复 reconcile/切页不重复哈希（B3） |
| 06 | test_setup_capability_gates.py、test_setup_machine_reuse.py:165 | 保持；新增候选激活不挡默认整备（B2） |
| 07 | test_setup_missing_verdict.py | summary.verdict == /setup/verdict；availability 与 summary 同安装（B4） |
| 08 | test_service_validation.py、test_runtime_orchestrator.py | 保持：缺节点拒绝、未选模板漂移仅 advisory |
| 09 | test_runtime_identity_relocation.py:149 | 无关模型变化不清空其他证据；变体/端口变化精准失效（B2） |
| 10 | test_setup_preparation_instance.py | 保持：SUPERSEDED/CANCELLED fencing；新增晚回包不复活 |
| 11 | test_existing_comfy_adoption.py | 保持：只读、不写不删不杀 |
| 12 | test_variant_binding_and_gate.py | 保持：候选不借基线证据 |
| 13 | test_setup_machine_reuse.py:72 | root 身份对大小写/空格/中文稳定（B1） |
| 14 | 无 | 扫描截断→unknown（B5）；前端未知≠缺件（B6） |
| 15 | test_offline_e2e.py、fake 隔离 | 保持：mock 不标真实 |
| 16 | test_setup_single_executor（前端） | 专家页唯一执行者合同（B7）；后端整备链唯一写入口审计 |

## 7. 验收边界（预先声明）

- 本机 RTX 5080 16GB；无 RTX 4070 用户机 → L5 一律 PENDING/NOT_RUN。
- 真实 H3 出片（L4）本轮默认不发起（占用显存与时间）；如发起仅在隔离数据目录、资源与 GPU 安全确认后，按 REAL_RENDER_PASS 分级记录。
- 后端全量按 AGENTS.md 固定 8 分片（CODEBUDDY_SAFE_DELETE_ENABLED=0）；前端全量 vitest --no-file-parallelism + tsc + build + Ruff + Alembic check。
- 本轮不改 worker.py/preproduction.py/handoff/quality 客户端 → 不触发 client_review_identity 重校准义务；历史真实正负校准仍未完成，不得声称已校准。