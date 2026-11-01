# Web 新用户五项缺陷修复报告（2026-11-01）

> 后续独立复验发现的风格批准签发前竞态与全局模板并发重名，已做精确修复，见 [并发修复与验证](WEB_CONCURRENCY_FIX_20261101.md)。以下保留原轮次记录与真实生产边界，不将历史通过数字当作后续版本结果。

适用范围：本轮在审查基线 `ba3301ef7846fb0decadcfa7b41b9132b114cdf2` 上修复本地 Web 新用户链路的五项缺陷（2×P1、3×P2），并按原审查合同补齐长期回归保护。原审查报告：`.local-acceptance/web-audit-20261101-ba3301e/REPORT.md`（历史证据原样保留）。本轮证据目录：`.local-acceptance/web-new-user-fix-20261101-ba3301e/`。

## 结论速览

| 维度 | 结果 |
| --- | --- |
| A. 五项缺陷修复与回归 | **PASS**（后端 8 用例 + 前端 18 用例，修复前可复现原反例、修复后通过） |
| B. 本地工程门禁 | **PASS**（全套件 1307 passed / 1 skipped / 2 warnings；Ruff；TS/Vite；Alembic 新库与旧库副本升级；git diff --check） |
| C. Web 新用户 Mock 全流程 | **MOCK_VERIFIED**（真实浏览器 + 真实 API/SQLite/Worker，全新独立库） |
| D. 当前版本真实本机生产 | **PASS_REAL**（2 镜真实 H3 系列→自动交付→完整解码→Web 实播；边界见下文） |
| E. 干净 Windows 完整安装 | **NOT_RUN**（本轮为已装依赖开发机，不冒充干净机验收） |
| F. 真实素材权属与艺术批准 | **PENDING_HUMAN**（操作员凭据仅为流程性批准，不代替用户批准） |

## 修复内容与验证方式

### 1. [P1] 风格冻结的不是用户屏幕上最终确认的内容

- 根因：`App.tsx` 的 `freezeStyle` 只发送授权信息，从不保存当前表单；服务器冻结已存草稿，与用户屏幕所见不一致。
- 修复：冻结前先 `PUT /style/draft` 保存屏幕快照 → 申请授权（grant 快照绑定保存后的草稿）→ 冻结。授权后草稿再变化会被服务器以 `PLAN_HASH_CHANGED` 拒绝。StylePanel 增加冻结语义说明；失败时表单不丢失。首次填完七项直接冻结（原英文报错路径）现在一步成功。
- 回归：`frontend/src/test/styleFreeze.test.tsx`（4 用例：保存 A 改 B 冻结反例、授权路径顺序与拒绝保留、首冻成功、断网保留输入）。修复前验证：把 App.tsx 换回旧版跑反例用例确实失败（证据见测试文件头注释）。

### 2. [P1] 第二个项目登记默认工作流后仍没有可用模板

- 根因：`/setup/profiles` 按**全局**名称去重而把档案绑定到登记项目；`create_workflow_profile` 也是全局名称唯一。第二个项目收到 ALREADY_REGISTERED（指向第一个项目的档案），自己的列表为空。
- 修复（最小兼容方案）：名称唯一性收敛到与列表可见性一致的规则——同项目内唯一 + 全局档案唯一（SQLite 对 NULL 不去重，全局唯一在 API 层强制）。默认向导按 (project_id, name) 幂等登记，每个项目获得自己的可用副本；A 的既有档案、ID、图与历史任务引用一律不动。同项目同名不同内容仍为 `PROFILE_CONTENT_CHANGED` 硬冲突；生产授权（services.py 的项目归属检查）不变。
- 涉及：`api_setup.py`、`api_comfy.py`、`models.py`、迁移 `f150a0010001_workflow_profile_scope.py`（batch 重建表、复制行、保留 FK）、`scripts/register_ref2va_profiles.py` 无需变更。
- 回归：`backend/tests/test_workflow_profile_scope.py`（8 用例：A/B/C 依次登记与隔离、同项目幂等、同名不同内容拒绝、历史保留、跨项目越权 404、并发登记无冲突副本、旧库副本升级保留档案与任务引用）+ `test_deployment_acceptance_regressions.py`（2 用例保持通过）+ 前端 `profileRefresh.test.tsx`（登记后当前页模板列表即时刷新）。

### 3. [P2] 新交付记录不会自动出现，全局刷新也无效

- 根因：DeliveryPanel 只在"已知存在活动系列"时轮询；板块用 CSS 隐藏保持挂载，进入交付板块与全局刷新都不触发读取。
- 修复：交付面板增加三个独立触发：进入交付板块（`active` prop）、顶部全局刷新（`dataEpoch`）、镜头任务状态变化（jobs 指纹，仅在内容实际变化时触发，不引入恒定轮询）。保留既有"活动系列/近期交付"有界轮询。请求失败显示"交付列表读取失败"横幅，不再伪装成空列表；项目切换由 key 重挂载 + 响应守卫防污染。
- 回归：`frontend/src/test/deliveryRefresh.test.tsx`（5 用例：全局刷新出现、进入板块出现、任务推进触发、失败不伪装空列表、项目切换隔离）。浏览器实测：注入一条**标注为合成的** BLOCKED 订单（隔离库）后，点顶部"刷新全部数据"立即出现（截图 20）。

### 4. [P2] 分镜保存失败后清空用户说明和素材选择

- 等待服务器确认后才清空草稿：`createShot`/`saveBindings` 改用 `withBusyReturn`（异常上报后重新抛出），ShotPanel `await` 结果，失败保留全部输入；保存进行中禁用按钮防重复提交；成功后仍按产品约定清空意图与绑定行。
- 服务端 422 细节翻译为字段级中文（`api.ts describeDetail`：时长/镜头意图等标签 + 原始信息辅助），分镜表单内联显示、不吞异常。
- 回归：`frontend/src/test/shotDraft.test.tsx（4 用例）`：16 秒客户端预检、服务端 422（对白越界）保留输入、修正后成功创建才清空、请求期间防重复提交（一次点击一个镜头）。
- 浏览器实测：16 秒保存 → 行内"时长需在 4–15 秒之间（当前：16）"、输入保留（截图 11）；修正为 6 秒 + 绑定素材后成功创建（截图 12）。

### 5. [P2] 空库的新机设置入口被创建项目挡住

- 无项目时渲染完整设置板块（环境检查、上游可达性、部署清单、安装目录、偏好——均不依赖项目）；依赖项目的按钮禁用并给出文字说明；同一视图内保留创建第一个项目的卡片与"先去新机设置"引导；`#setup` 深链接直接进入。创建项目后自动回到设置板块继续向导。
- 回归：`frontend/src/test/setupEntry.test.tsx`（4 用例：空库普通入口、依赖项说明、建首项目后继续、已有项目完整四板块）。
- 浏览器实测：空库 `/` 与向导入口可见（截图 01/02），环境检查真实加载（RTX 5080/磁盘/工具链达标），创建项目 A 后停留在设置板块继续。

## 工程门禁（本轮实测）

| 检查 | 结果 |
| --- | --- |
| 新增回归 | 后端 8 + 前端 18 用例全绿；反例在修复前可复现（App.tsx 旧版验证） |
| 五个命名套件 | offline_e2e + film_crew + v13_preparation + v13_migrations + v13_e2e：95 passed |
| 完整后端套件 | **1307 passed / 1 skipped / 2 warnings**，exit 0（唯一 skip：`test_real_managed_startup.py:85`，需 `SHINE_REAL_COMFY_PROBE=1` 与本机 ComfyUI） |
| Ruff | All checks passed |
| TypeScript/Vite | `tsc -b` + `vite build` 通过（18 个前端测试 + 构建） |
| Alembic | 新库升级到 head `f150a0010001`（隔离库）；旧库副本升级保留档案/任务引用（回归测试断言） |
| git diff --check | 通过（仅 CRLF 提示） |
| 前端依赖 | vitest 2.1.9 / RTL 16.1.0 / user-event 14.5.2 / jest-dom 6.6.3 / jsdom 25.0.1，全部 `--save-exact`，lockfile 一致 |

注：本会话 shell 的 `PYTHONUTF8=1` 会让 `test_bootstrap_entry.py` 的两个 PowerShell 子进程测试因 GBK 解码失败——这是会话环境变量与 PowerShell 5.1 控制台编码的交互，清空变量后全部通过，与五项修复无关；已按干净环境记录门禁结果。

## Web 新用户 Mock 全流程（真实浏览器 + 真实后端，MOCK_VERIFIED）

独立数据目录与端口（API 8033 / Web 5199，操作员凭据为本轮生成的隔离身份），全新库自动迁移到 head。浏览器实测步骤：空库首次入口 → 新机设置（无项目可进入）→ 创建项目 A → 素材审批/锁定 → 保存 A 改 B 直接冻结（原反例，现在冻结内容=屏幕内容，API 交叉核对哈希与快照）→ 非法时长 16 秒（输入保留 + 字段级错误）→ 修正后创建分镜并绑定素材 → H3 提示词预览引用冻结版本 → 加入 Mock 队列、常驻 Worker 完成并显示明确演示标记（`simulated: true`、`acceptance: mock`）→ 交付板块进入/全局刷新/局部刷新均有效（合成 BLOCKED 订单**仅用于刷新机制回归**，已在证据中标注）→ 项目 B/C 经向导登记默认工作流 → 模板下拉即时出现 → A/B 档案隔离（ID 不相交）→ 项目切换/浏览器后退/深链接/四板块导航正常。
特别说明：本轮按用户要求把侧边栏导航框体拉伸到底部（`.nav-groups` 弹性占满、移除 spacer、分组均分高度），构建与 18 个前端测试复验通过。

## 真实生产验收（真实 H3 + 受管 ComfyUI）— PASS_REAL

- 本机已有真实 H3 环境（`F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base`：INT8 ConvRot 权重 20.9GB、Qwen 编码器、RTX 5080、443GB 可用磁盘）。按 LOCAL_TECH_FINAL_CLOSURE_V3 的文档化流程执行 `scripts/run_real_closure_v3.ps1 -RunName web-fix-20261101-b -ComfyRoot "F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base" -Shots 2 -TimeoutMinutes 210`。
- 结果（2026-11-01 22:07）：五 Agent 5/5 SUCCEEDED → 浏览器内人类确认分镜 → 系列 2 镜 `RUNNING→COMPLETED (2/2)`（同一受管渲染器串行、租约全程持有、结束释放，端口与 GPU 基线核验）→ 自动 EDL（`edl_source=frozen_edit_intent`）→ 自动 FFmpeg 装配 → 交付订单 `DELIVERED` → 下载 MP4 SHA-256 匹配（`dd581dce…`，14.6s，1344×768）→ **FFprobe 元数据 + 完整解码均通过** → Web 播放器实际播放（readyState 4）→ 栈按出生身份停止。
- 每镜尝试 1 次，`submit_intent` 在外部调用前持久化，真实外部任务 ID（`556b9437…`/`634e0b38…`）；五道质量门全部 real 记录（镜 2 audio 门 failed 按当前产品策略为 advisory 上报，不阻断；final gate needs_review → 成片 `pending_review` 如实呈现，未改称 accepted）。
- 第一次尝试在五 Agent 阶段失败（`LLM_CONNECTION`：api.minimax.cn TLS 握手瞬时超时 + 一次模型输出合同拒绝）；诊断确认网络恢复后做了一次有依据的重试，**未发生任何生产提交的盲目重投**（失败发生在创建镜头之前）。诊断与结果记录：`real-run/01-first-attempt-diagnosis.txt`、`real-run/02-second-attempt-result.txt`。
- 边界（如实声明）：现有模型目录复用（非干净机 60.8GB 从零下载）；操作员凭据=流程性批准；**真实素材权属与最终艺术批准保持 PENDING_HUMAN**；pending_review 状态如实展示，不改称 accepted。

## 剩余边界（集中说明一次）

1. **干净 Windows 完整安装**（60.8GB 从零下载、干净机首启）本轮 NOT_RUN——本轮是已装依赖的开发机验收。
2. **真实素材权属与艺术批准** PENDING_HUMAN——本轮真实运行使用的素材为既有验收库中的主资产，权属与艺术质量批准需用户完成。
3. `test_real_managed_startup.py:85` 真实 ComfyUI 探测保持 SKIP（需 `SHINE_REAL_COMFY_PROBE=1`）。
4. 前端无独立 lint 脚本（未虚构 `npm run lint` 通过）；本轮新增的测试基建为最小集（vitest+RTL，固定版本）。
