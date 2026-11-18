# Shine 视频工作台 R5｜基础配置与用户机资源复用 实施报告

> 日期：2026-11-18。分支：`feat/shine-setup-r5-machine-reuse-convergence-20261118`。
> 基线：`origin/main@46c889a`（拉取时最新）。方案：`docs/SHINE_SETUP_R5_THREE_PRINCIPLES_CONVERGENCE_PLAN.md`（提交 `0f3464b`，cherry-pick 为 `bcb0fd0`）。
> 审计：`docs/SHINE_SETUP_R5_BASELINE_AUDIT.md`（提交 `3903977`）。
> 原则：一套事实、一个裁决、一个执行者。本轮全部改动为**归并/删除/兼容修复**，净删代码约 560 行；未新增任何表、状态机、安装器、调度器、资格引擎或缓存。

## 1. 提交清单

| 提交 | 内容 |
| --- | --- |
| `bcb0fd0` | 方案文档（cherry-pick，仅一个文件） |
| `3903977` | R5-0 基线审计文档 |
| `3f75f0f` | R5-1 后端：稳定收编身份、端点可变、回执复用、同源选择、扫描 UNKNOWN、确定性选择（B1–B5/B8） |
| `62804b6` | R5-2 前端：未知≠缺件、第二执行链删除、测试收敛（B6/B7）+ INV-07/10 反例 |
| `40f9781` | INV-03/04 反例补强（历史端口行继承、封条精准失效、多候选只问一次）、Ruff 修正、实施报告 |
| `00ff396` | 同端点多声明按最近显式确认优先（双安装同端口配置死路修复，INV-04） |
| `1fa8f55` | 新审计码登记前端清单（首轮 8 分片唯一真实回归） |
| （本轮后续） | 验收文档与最终全量证据 |

## 2. 修复的真实根因与代码位置

### B1 物理安装身份以端口为主键（INV-03/04/13）

- 根因：`instances.instance_identity(mode, port)` 被外部收编直接用作主键（`adoption.py`）。端口迁移 → 同一物理安装产生第二条行；`_known_port` 读回旧端口；同 root 多行 last-wins 漂移；同端口换 root → 409 死路。
- 修复（`instances.py`）：
  - `adopted_instance_identity(root)`：按物理 root 的稳定身份（`os.path.normcase`  Windows 大小写语义）。
  - `find_adopted_instance(db, root, port)`：单一查找——稳定身份行 → 同 root 历史收编行（保留原 ID，端口匹配优先，其次最小 id）。
  - `find_instance_by_endpoint(db, port, instance_name)`：配置请求按端点找行，歧义返回 None 由调用方具名拒绝。
  - `installation_identity_matches(instance, port)`：六个强制点共用的唯一谓词（端口 uuid 行 OR adopted 且端点一致）。
- 收编（`adoption.py`）：同 root 历史行被继承；端口迁移只更新同一行的 `port` 与 `adoption.service_identity`，写审计 `ADOPTED_INSTALLATION_ENDPOINT_CHANGED`，**字节证据/节点指纹/行 ID 不动**；探测行接管逻辑保留；另一安装的 adopted 行占用同端口身份时，新安装使用稳定 root 身份（不再 409 死路）；受管/假行占用端点仍拒绝。
- 强制点收敛：`runtime_configuration.configuration_identity`、`handoff_startup._installation_evidence`、`resource_resolver.managed_installation_pin`、`deployment.generate_configuration` 全部改用同一谓词/查找；受管实例阶梯（`next_instance_plan`）身份不变。
- 同端点多声明（`00ff396`）：两套安装先后收编在同一端口时，`find_instance_by_endpoint` 与收编候选排序共用同一规则——稳定 root 身份先于历史端口行，同级按最近确认 attempt 时间（`confirmation_sort_key`）排序，完全同序才具名拒绝；用户显式选择仍高于一切自动排序。反例锁定「A(h3)+B(image) 同端口 → 配置指向 B」。
- 兼容与回滚：历史行 ID 一律保留，无 schema 迁移（Alembic check 无漂移）；回滚 = 恢复旧查找函数。旧封条按既有 `comfyui_base_url` 事实精准失效（见 B2/INV-03 反例）。

### B2 RECIPE 幂等跳过不看端点/变体（INV-09）

- 根因：`setup_preparation._step_recipe` 仅比 instance_id 即「沿用现有配置」；端口迁移或候选→基线切换被静默跳过。
- 修复：跳过条件追加端口一致（active identity.comfyui_base_url）与变体一致；`record_candidate`/`activate_runtime` 在 AppSetting value **顶层**携带 `variant_id`（不进 identity、不改配方字节，旧记录缺省按默认基线读，旧封条不受影响）。

### B3 已缓存/已落位字节每轮全量重哈希（INV-02/05）

- 根因：`setup_reconcile.manual_files_reconcile` 与 `_place_relayvsr_served` 用裸 `_hash_file` 判同。
- 修复：改走同一 `verification_receipts.file_digest`（stat+头尾见证 sidecar 复用）。威胁模型不变：同 tick 同长改写仍被见证区分；新复制的临时字节仍全量哈希。反例以「缓存目标禁止裸哈希 + 第三轮必须 reused=True」锁定。

### B4 availability 第二套安装选择（INV-07）

- 根因：`api_setup.variant_availability` 取台账第一条 adopted 行的 root。
- 修复：改读 `resolve_installation` 的选定安装（与 summary 同一事实）。

### B5 扫描截断断言 MISSING（INV-14）

- 根因：`resource_discovery.report` 无候选一律归 missing。
- 修复：覆盖不完整或存在 unreadable 区域时归 `unknown`；仅覆盖完整且无不可读区域才归 missing。活 UI 不消费该桶，合同本身被反例锁定。

### B6 前端未知当缺件（INV-14/§5.3）

- `SetupCenter`：summary 未到达 → 能力文案「正在读取本机文件状态」、体积 null（显示 —）、canPrepare 禁用；presence 只读 availability（删除浏览器扫描计数 variantPresence）；硬件不足×文件已有的诚实表述由 availability+recommendation 两份服务端事实合成；verdict 缺失回退删除。
- `DirectorSetupPanel`：审片分母不再硬编码 13/10，读不到显示「读取中」。
- `DirectorShootPanel`：FALLBACK_OPTIONS 不再伪造 verified:true/supported:true。
- `MachineProgressBanner`：100% 文案读服务端 label。

### B7 死代码第二执行链（INV-16/§7.1）

- 事实：`SetupPanel`（专业模式已下线）不被任何活页面引用，但保留两条浏览器串行链（prepareAutomatically/prepareAll）与直建计划按钮；`ImageCapabilityCard`/`VariantReadinessCard` 同样只被测试引用。
- 删除：prepareAutomatically、prepareAll、createInstallPlan、createInstanceChain 及对应按钮；`VariantReadinessCard.tsx` 整文件（零引用）。
- 归并：审片补装 → `/setup/prepare/execute`（与导演页同一入口）；`ImageCapabilityCard` 收敛为只读诊断（删除独立下载/绑定/启停/验证按钮，保留四层事实显示）。
- 保留：既有计划的批准/执行/整理恢复入口（原批准链合法用途）、单步专业排查、安装进度只读重附着。
- 裁决收敛：modelCards 不再给「已安装并核验」绿灯，只报扫描事实并指向唯一裁决；verdict 缺失回退删除。
- 测试收敛：simpleSetup 重写为唯一执行者合同（9 例）、imageCapability（3 例）、setupCapabilityGates 4070 诚实表述改读服务端 availability。API 端点全部保留，服务端门禁强度不变。

### B8 同 root 多行漂移（并入 B1）

- `setup_summary.candidate_installations`：by_root 按 active 行 > 用户确认端口 > 稳定身份 > 最小 id 确定性收敛；adopted/managed 候选遍历排序后的 by_root。

## 3. 旧链删除清单

| 删除项 | 位置 | 替代 |
| --- | --- | --- |
| 浏览器串行整备链 ×2 | SetupPanel.prepareAutomatically/prepareAll | setup_preparation 持久任务（既有） |
| 直建安装/实例计划按钮 ×3 | SetupPanel.createInstallPlan/createInstanceChain + 按钮 | 制作环境卡一次确认（既有） |
| 审片直建计划 | SetupPanel.installOptionalReviewers 旧实现 | /setup/prepare/execute |
| 出图独立执行入口 ×5 | ImageCapabilityCard registerInstall/bind/start/stop/validate | 整备任务 + 工作台后台管理（既有） |
| 死组件 | VariantReadinessCard.tsx | 无（零引用） |
| 浏览器 presence 计数 | SetupCenter.variantPresence/weightIdsOf | availability 服务端事实 |
| 裸哈希判同 ×3 | setup_reconcile 缓存/候选/落位 | verification_receipts.file_digest |
| 第一条 adopted 行选择 | api_setup.variant_availability | resolve_installation |
| 硬编码分母/verified 回退 | DirectorSetupPanel/DirectorShootPanel | 服务端事实或「读取中」 |

## 4. 身份兼容 / 迁移 / 精准失效 / 回滚

- **无 schema 迁移**：身份收束全部在 payload 与查找层；`Alembic check` 无漂移。历史 ComfyInstance 行、租约、审计、FK 全部保留原 ID。
- **前向**：旧端口主键行按 root 被继承并原地更新端点；新收编使用稳定 root 身份；探测行接管不变。
- **回滚**：恢复旧查找/谓词即可；新身份行对旧代码不可见但不会被旧代码误认（旧代码按端口 uuid 查不到 → 走原拒绝路径，不破坏数据）。
- **精准失效**：端口变化 → `comfyui_base_url` 事实变化 → 旧运行封条失效（INV-03 反例），权重回执不受牵连；变体切换 → RECIPE 重生成；数据目录搬迁 → 沿用 R4 锚定（未改动）；仓库脚本升级 → 沿用 R4 排除（未改动）。

## 5. 安全边界（未放宽）

- SHA-256 钉住、候选显式确认、BLOCKED 拒绝、进程三证、GPU 互斥、Ref2VA 唯一路径、外部 ComfyUI 只读、UNKNOWN 不盲重发：全部保留，反例锁定。
- 回执复用采用既有 stat+头尾见证威胁模型，未以性能为名削弱。
- 本轮**未改** `worker.py` / `preproduction.py` / handoff 客户端 / quality 客户端 → 不触发 `client_review_identity` 重校准义务；历史真实正负校准仍未完成，不得声称已校准。

## 6. 与方案的技术调整（以真实代码为准）

- 方案 §3.1 建议「优先 alias/记录兼容」：实施为稳定 root 身份 + 兼容查找，历史行不重算 ID（比新增 alias 字段更小的改动面）。
- 方案 §7.1「设置→制作环境唯一完整入口」：当前活路径已是 DirectorSetupPanel→SetupCenter；SetupPanel 为下线遗留，按「删除旧链」处理而非改造为第二入口。
- B2 变体身份记在 AppSetting value 顶层而非 identity/recipe：避免改动配方字节导致旧封条整体失效（违反 INV-09）。