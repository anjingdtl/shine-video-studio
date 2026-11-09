# 导演模式全旅程收尾修复报告（基线 `185c6a6`）

日期：2026-11-09  
仓库：`F:\ClaudeWorkSpace\projects\shine-video-studio`  
分支：`fix/director-journey-closeout-20261109`  
审查基线：`185c6a6b2df2852aae7182045caaf335eef59767`（`main` 与 `origin/main` 一致，未推进）  
依据：[对抗审查 J01–J12](ADVERSARIAL_REVIEW_DIRECTOR_JOURNEY_185C6A6_20261109.md)、[本轮建设提示词](LOCAL_AGENT_DIRECTOR_JOURNEY_CLOSEOUT_185C6A6_20261109.md)、`AGENTS.md` 全部现行裁定。

## 0. 结论

J01–J12 的十二个反例全部在代码中处理，旧反例均改写为正向断言；J01、J02、J04–J12 有可重跑的软件证据，J12 的整条浏览器脚本已真实跑通。**本轮没有真机证据**：没有清洁 Windows 新机、没有真实权重下载、没有真实 LLM、没有 MiniMax-H3 出片，也没有人工观感验收。文中所有“通过”都限定在对应的软件层，不能外推为真实出片验收。

四项本轮实际执行的验收：

| 层 | 结果 |
| --- | --- |
| 后端定向 + 完整套件 | 见 §3.1、§3.2 |
| 前端构建与测试 | `npm.cmd run build` 通过；`npm.cmd test` 32 文件 / 201 测试通过 |
| 浏览器 | `scripts/verify_v13_web.py`：`MOCK_VERIFIED`，21 条流程，`api_errors=[]`，退出码 0 |
| Windows 测试包 | 已从干净提交构建并解包验签；8.3 短路径、长路径、中文含空格路径三份清单逐项一致；解包内 `install_once.ps1 -CheckOnly` 退出码 0 |

## 1. 开工核对与边界

- `git status --short --branch` 开工时为 `## main...origin/main`，仅两份未跟踪的审查/提示词文档；`git worktree list` 只有主工作树。基线即 `185c6a6`，`main` 未推进，因此无需逐项判断“是否已被后续 commit 修好”。
- 本轮使用 feature branch、临时端口、隔离 SQLite 与合成素材；没有启动、停止或改写用户已有的 API/Worker/ComfyUI 进程，没有修改用户数据库、模型、素材和历史结果。
- PR #11 已合入，其回归（多模型路由、W6A8 候选、导演包身份）继续运行并通过。
- 没有修改 `client_review_identity` 钉住的 `worker.py` / `preproduction.py` / handoff / quality 文件；但真实 VLM 正负校准本来仍未完成，本报告不声称已校准。

## 2. 逐项修复

### J01 · ZIP 清单路径错位（P1）

- **基线复现**：输出目录为 `C:\Users\ADMINI~1\...` 时，首个清单项变成 `win64/.env.example`，解包验签查找不存在的文件并退出 1。
- **根因**：`Resolve-Path` 可能返回 8.3 短路径，而 `Get-ChildItem` 的 `FullName` 是长路径；按字符串长度裁切根前缀会把两种表示拼错。
- **实现**：`scripts/build_test_package.ps1` 的 `New-FileManifest` 改为逐层遍历目录、用每层自己的子名累加相对路径；清单拒绝绝对路径、`..` 越界和反斜杠项；解包验签再用 `GetFullPath` 确认每一项都在解包根内。
- **证据**：
  - 8.3 短路径：736 个清单项、11,582,859 字节、737 个文件验签通过；解包内 `install_once.ps1 -CheckOnly` 退出码 0。
  - 长路径与 `F:\ClaudeWorkSpace\tmp\闪耀 视频 包\第三 版` 各自构建并验签；三份 `PACKAGE_MANIFEST.json` 的路径列表 SHA-256 均为 `35815a94cd847e33…`，文件数/总字节一致。
  - ZIP 哈希因 `built_at` 时间不同而不同（短 `31dc5518…`、长 `ee08c288…`、中文 `73a01af7…`），清单内容一致。
- **限制**：未在隔离新机解包运行完整安装；只读体检与打包验签已执行。

### J02 · 安装尝试回执与按目标找回（P1）

- **基线复现**：两个作品在同一安装根请求同一组件，第二次响应 `202` 但 `plan_hash` 是第二份计划，详情哈希却是第一份；`/attempts` 对第二份计划返回空数组，原始行也没有 `attempt_id/terminal`。
- **根因**：尝试身份属于“物理目标”，但响应和查询都按请求计划表达；`reused` 用 `stage != QUEUED` 推断，复用一个仍在排队的尝试会被标成“新尝试”。
- **实现**：
  - `queue_attempt` 返回 `(attempt, reused)`；`_queued_response` 同时给出 `plan_id/plan_hash`（尝试自己的 owner）与 `requested_plan_id/requested_plan_hash`。
  - `GET /comfyops/install-plans/{id}/attempts?scope=target` 按规范物理目标返回完整 `attempt_view`（含 `attempt_id/terminal/plan_hash`）；默认 `scope=plan` 保持历史语义。
  - 前端 `setupExecution.ts` 的恢复查找改用目标作用域；浏览器没有本地记忆时回落到服务端回执，并显示尝试真实阶段而不是伪造 `QUEUED`。
- **证据**：`backend/tests/test_installation_persistence.py::test_a_second_plan_shares_the_physical_attempt_and_reads_the_same_receipt`；前端 `npm.cmd test` 通过。
- **限制**：安装 Worker 未在真实网络下执行；测试只到排队/回执层，不声称真实下载。

### J03 · 文本就绪与 H3 安装解耦（P0）

- **基线复现**：两个安装入口先读全量 `/setup/llm-readiness`，`READY` 要求 14 个文本角色全部就绪；新库在下载、检测、复用之前就被 `LLM_SETUP_REQUIRED` 挡住。
- **实现**：
  - `RealProductionWizard` 把文本步骤改为并行 `warn` 分支，不再 `return`；H3 安装、权重检测、复用和激活继续。
  - `SetupPanel.prepareAutomatically` 删除全局抛错，安装完成后在结果里如实附带“文本工种未配置不影响 H3 安装”。
  - 大纲动作只按它真正调用的 `scriptwriter` 拒绝：`409 TEXT_ROLE_NOT_READY`，带 `role/role_label/action`；前端有对应中文文案。连接测试、角色预览入口保留。
- **证据**：`frontend/src/test/realWizard.test.tsx` 改为“LLM 未配置仍安装 H3”；`test_llm_setup_contract.py`、`test_llm_multi_model_routing.py` 通过。
- **限制**：分镜链仍按链路整体检查所需角色；本轮没有把它改成“逐步只查下一步”，因为每一步都可能真实调用对应角色，逐角色拒绝已经具名。

### J04 · 权重核验回执（P0）

- **基线复现**：种入旧 `FILES_VERIFIED_PENDING_RUNTIME`、不匹配 revision/文件清单/记录 SHA，磁盘无对应字节，`/setup/variant-readiness` 仍给 `verified=true`、`FILES_VERIFIED=DONE`；同长错误字节的缓存文件也会被当作证据。
- **实现**：新增 `shine/comfyops/verification_receipts.py`。
  - 回执绑定 `component_id + revision + 每个文件 relative_path/size/sha256 + 实际核验目录 + variant/catalog`；身份是内容哈希。
  - 安装 Worker 成功、原位核验、失败对账三条路径都写回执；已有回执在文件 `size/mtime_ns` 未变时直接复用，不重读；变化后重新哈希。
  - `weight_evidence` 只认“内容身份匹配 + 目录仍逐文件匹配”的回执，或逐字节复核过的缓存；registry 状态单独不再点亮任何层。
  - `_required_components` 仍按工作流图实际加载的文件确定权重，未用的可选 LoRA 不算缺件。
- **证据**：`backend/tests/test_weight_verification_receipts.py`（6 项，真实小文件）覆盖有效复用零重读、同长错字节、错误 revision、旧 registry 误绿、跨变体有效复用、无回执；`test_variant_binding_and_gate.py` 23 项通过。
- **限制**：真实 60 GB 权重的首次哈希耗时未实测；W6A8 仍未下载/未真机验收，保持 `auto_download_allowed=false` 与候选身份。

### J05 · 文本路由钉住与预览映射（P0）

- **基线复现**：`scriptwriter=A` 排入大纲后改路由到 B，预览成功；Worker 重读当前路由，旧任务进入 `NEEDS_REVIEW/STORY_PROVIDER_CHANGED`，A、B 都没被调用。
- **实现**：
  - `LLMGateway` 支持 `pin_role/clear_pins`；大纲/逐段任务按 `model_profile_id` 钉住原模型，配置失效时具名 `STORY_PROVIDER_PIN_INVALID`，保留任务与已完成单元。
  - 创意成稿、四阶段蓝图/分场剧本同样在 claim 后钉住快照。
  - 路由预览把父任务（`story_outline_run`、`story_section_run`、`creative_script_run`、`four_stage_bible_run`、`four_stage_scene_script_run`）映射到实际消费的文字角色；有快照的任务标 `PINNED_MODEL` 可继续，无快照的才是阻断项。
- **证据**：`backend/tests/test_llm_pinned_task_routing.py`（2 项：改路由后仍用原模型、快照失效不换模型）；`test_llm_multi_model_routing.py` 原阻断用例保持通过；创意/四阶段回归通过。
- **限制**：旧配置被编辑（endpoint/key/options）时仍会 `STORY_PROVIDER_CHANGED`，需要用户在原页面安全续办；本轮没有做“自动改派到同能力模型”。

### J06 · 当前剧本与导演包血缘（P0）

- **基线复现**：剧本 A 的确认大纲/分集/镜头，切到剧本 B 并确认 B 大纲后建包 201、确认 200、`startable=true`；包声明 B 的大纲，分集计划和镜头仍是 A。
- **实现**：
  - `plan_basis_blockers` 统一校验当前 `document + confirmed outline` 与分集计划 `source`；`sections` 计划缺来源、来源大纲不一致都在建包前具名拒绝（`EPISODE_PLAN_SCRIPT_CHANGED` 等）。
  - 导演包根清单新增 `episode_plan.source`；`package_readiness` 能发现“包声明 B 剧本 + A 分集”的历史混合，新增 `PACKAGE_SCRIPT_PLAN_MISMATCH`。
  - 旅程第①阶段在有文档时只认当前确认大纲；手工镜头只在无文档项目点亮。第②阶段对来源不一致的计划给出同名阻断，`next_stage` 回到 `plan`。
- **证据**：`backend/tests/test_director_script_basis_closeout.py`（2 项：切 B 后建包 409、历史包仍按 A 可读；清单携带 plan source）；`test_director_long_form.py`、`test_four_stage_reacceptance.py` 回归通过。
- **限制**：`shots` 模式（把已有镜头分组）在已有文档时仍保留兼容语义，因为它没有分集来源可核对；手工分镜的无文档入口保留。

### J07 · 素材采用入口与幂等冲突（P0）

- **基线复现**：分配成功后前端没有调用 `/asset-center/adoptions[/preview]` 的入口；旧确认包永久挡住逐镜采用；同一幂等键换成第二镜仍返回第一镜旧回执，第二镜无绑定。
- **实现**：
  - 采用回执记录规范化请求摘要；同 key 不同内容返回 `409 ADOPTION_IDEMPOTENCY_CONFLICT`，同 key 同内容才重放。
  - `_confirmed_package_for_shot` 只看项目**最新**包；最新包仍是 CONFIRMED 才拦，存在更新 revision 后旧确认包是历史而不是锁。
  - 拍摄中或结果未知的镜头以 `SHOT_PRODUCTION_IN_FLIGHT` 具名阻止，带 `production_job_id/action=RECONCILE_OR_FINISH_CURRENT_TAKE`，不覆盖原尝试。
  - 素材准备区新增「用于这些镜头」：预览列出已绑定/将采用/冲突镜头及 revision，一次确认调用现有 preview + adoption 服务；成功后提示当前导演包需重新生成。
- **证据**：`test_director_assets_closeout.py` 新增 3 项（同 key 换 payload、新 revision 解锁、在拍镜头具名阻止），原采用/撤权用例保持通过；前端构建通过。
- **限制**：真实 Qwen 生图与固定音色调用未执行；采用只覆盖项目素材，全局候选搜索仍走既有入口。

### J08 · 一键开拍、可选槽位与原子应用（P0）

- **基线复现**：Ref2VA 方案声明画面槽，镜头已绑唯一人物，空画面槽让一键开拍报“还缺场景/画面参考图”，零次 Series POST；两镜反例第一镜先写绑定、第二镜缺人物导致整次中止。
- **实现**：
  - 槽位是容量，不是义务；引用义务只来自镜头点名的人物和实际请求。空画面槽只提示“由 H3 自行生成”；点名人物无主体参考则具名阻止；完全没有参考的镜头不再被一键开拍静默送进无参考模式。
  - 一键开拍先在内存中为所有选中镜头做完整计划，任何一镜缺关键参考就整批不提交；确认后把绑定计划随 `POST /production-series` 一次发送。
  - 服务端 `CreateProductionSeries.bindings` 在创建系列同一事务内调用 `set_shot_bindings`；任一步拒绝则整体回滚，不会留下半写绑定。
  - 制作页与导演包页共用按项目保存的方案选择；用户显式选择优先于质量档，刷新/切页/显式 W6A8 不替换。
- **证据**：`backend/tests/test_one_click_start_closeout.py`（3 项：空可选槽可开拍、拒绝不写绑定、点名人物缺主体具名拒绝）；`test_variant_binding_and_gate.py` 与前端 `variantCandidate.test.tsx` 的“用户选择不被替换”通过。
- **限制**：没有真实 H3 调用；音色参考仍按既有可选语义，不自动补。

### J09 · 统一生产预检（P0）

- **基线复现**：Ref2VA 方案无宽高映射；提交 768×1344 的导演包确认成功且 `startable=true`，Run 201，直到批次派发才 `BATCH_DISPATCH_REFUSED / WORKFLOW_SIZE_NOT_MAPPED`。
- **实现**：建包、包就绪、Series、Job 共用 `size_blockers` / `profile_blockers` 的同一判定。建包时带 `settings` 读真实机器事实（候选方案 `require_runtime` 与运行一致），宽高映射缺失在包构建即 `422 WORKFLOW_SIZE_NOT_MAPPED`，并带 `missing_parameters/mapped_parameters`；旧包在 `package_readiness` 也按冻结的 `binding.output` 补充同一阻断。
- **证据**：`backend/tests/test_production_preflight_closeout.py`（2 项：建包与直调 Series 同一 code）；`test_director_long_form.py`、`test_variant_binding_and_gate.py` 通过。
- **限制**：动态条件（真实前镜衍生、运行时状态）仍在派发/Worker 时复核；预检不是一个新的独立 HTTP 端点，而是复用同一组服务端判定。

### J10 · 单镜请求键与 UNKNOWN 保护（P0）

- **基线复现**：普通单镜两次同 body 均 201、Job ID 不同（`useStudio` 未带 `request_key`）；镜头修订把旧 UNKNOWN 任务的 `failure_code` 覆盖为 `SHOT_REVISION_REPLACED`，之后同镜头提交 201。
- **实现**：
  - `useStudio.queueShot` 按项目/镜头 revision/方案/模式/分辨率生成请求签名，首次点击生成 `request_key`，失败/双击/重试复用，成功后清除；新 revision 自然得到新身份。
  - `create_render_job` 的未知提交检查不再排除 `stale` 任务：修订只把旧片移出当前剪辑，不证明外部请求未发出。
  - `revise_shot_record` 对 `UNKNOWN_SUBMISSION_CODES` 保留原 `failure_code` 与 `NEEDS_REVIEW`，只置 `stale`；修订与对账分开表达。
- **证据**：`test_director_journey_closeout.py` 的未知提交用例扩展为“修订后仍保留 UNKNOWN 且仍 409”；原双击/换 key 用例保持通过。
- **限制**：没有真实外部提交响应丢失实验；对账仍依赖既有 Attempt/graph hash/已落盘产物。

### J11 · Scope 与进度一致（P0）

- **基线复现**：同作品包 A 有 Run、包 B 未确认；第 5 阶段显示 B 的草案，第 6/7 阶段却显示 A 的已排队、暂停、取消；`?package_id=B&run_id=A-run` 返回 200 且 scope 同时带 B 和 A 的 Run。
- **实现**：
  - `scope_run` 显式 Run 必须属于所选包，否则 `404 PRODUCTION_RUN_SCOPE_MISMATCH`；只给 Run 时从 Run 推导包，不再回落到最新包。
  - `DirectorProductionRunPanel` 的 run、事件、进度、控制全部从 `journey.scope.run_id` 取；无 Run 的包显示“还没有整部作品的运行记录”，不借别包控制。
- **证据**：`test_director_journey_closeout.py` 新增 A/B 对照（B 无 Run、错配 deep link 404、只给 Run 推导 A 包）；`directorRun.test.tsx` 5 项通过；浏览器新增“未开拍项目无借来的暂停控制 + journey 请求中断后重载恢复”，整条脚本 21 条流程通过。
- **限制**：部分集 Run、历史深链接的播放/下载继续使用既有交付作用域；本轮没有新增多集真实文件对照（无真实出片）。

### J12 · 浏览器回归定位器（P1）

- **基线复现**：`verify_v13_web.py:698` 用旧 placeholder「配置名称」，30 秒超时；实际表单可访问名是「模型配置名称」。
- **实现**：模型管理控件全部改为真实可访问名/语义定位：`模型配置名称`、`服务平台`、`接口地址`、`Model ID`、`回复上限 token`、`思考深度`；先展开「高级参数（可选）」再填，保存后重开「添加模型」再检查 Anthropic 不显示 OpenAI 专属项。业务断言（201、`generation_options`、路由分配）保留。
- **证据**：整条 `scripts/verify_v13_web.py` 真实运行通过：`MOCK_VERIFIED`、21 flows、`api_errors=[]`、退出码 0；新增导演 scope/断网恢复流程在结果中列出。
- **限制**：这是 mock/隔离浏览器验收，不是真实模型或真实出片；Linux CI 需在推送后由远端复跑（本机未推）。

## 3. 软件回归

### 3.1 定向套件

以下 21 个文件共 **289** 项，最终状态运行：**288 passed, 1 skipped**（skip 为 ffmpeg 条件用例）：

`test_director_journey_closeout.py`、`test_director_assets_closeout.py`、`test_installation_persistence.py`、`test_director_setup_contract.py`、`test_new_machine_setup.py`、`test_variant_binding_and_gate.py`、`test_llm_multi_model_routing.py`、`test_llm_setup_contract.py`、`test_director_styles.py`、`test_four_stage_reacceptance.py`、`test_asset_center.py`、`test_director_entries_and_post.py`、`test_production_series.py`、`test_offline_e2e.py`、`test_film_crew.py`、`test_package_entrypoints.py`，以及本轮新增的 `test_weight_verification_receipts.py`、`test_llm_pinned_task_routing.py`、`test_one_click_start_closeout.py`、`test_production_preflight_closeout.py`、`test_director_script_basis_closeout.py`。

### 3.2 完整后端套件

`env PYTHONPATH= backend/.venv/Scripts/python.exe -m pytest -q tests`：收集 **2125** 项，退出码 **0**，运行中出现 1 个跳过标记（ffmpeg 条件用例）。本次运行清除了宿主 Python 注入的 `safe-delete` 中间层（见 §8），这是测试环境差异，不是产品代码放宽。

### 3.3 工程检查

- Ruff：`ruff check shine alembic tests` 全部通过。
- Alembic：空库升级到 `f180b0060001`；`.local-data/shine.db` 的只读副本从 `f170a0030001` 升级到 `f180b0060001 (head)`，原库未被修改。
- 前端：`npm.cmd run build` 通过（仅 Vite 大 chunk 提示）；`npm.cmd test` 32 文件 / 201 测试通过。
- 浏览器：见 J12，21 flows / 0 API errors。

### 3.4 Windows 测试包

- 从最终提交的干净树构建，`-Verify` 解包逐文件 SHA-256 通过；测试包 SHA-256 见 `releases/` 同名 `.sha256`。
- 三路径清单一致（736 项 / 11,582,859 字节 / 路径列表 SHA-256 `35815a94…`）；8.3 短路径、长路径、中文含空格路径都通过。
- 解包内 `scripts/install_once.ps1 -CheckOnly` 在 Windows PowerShell 5.1 下退出码 0；工具链（uv/node/npm/pwsh/ffmpeg/ffprobe）满足固定版本要求。
- **最终包**在报告提交后重建，SHA-256 见 §7。

## 4. Windows 与真实模型证据

| 证据 | 状态 | 说明 |
| --- | --- | --- |
| `CLEAN_WINDOWS_INSTALL` | **未执行** | 本机不是隔离新机/VM；未从 `一键安装并启动.cmd` 完整装工具链、Python/npm 依赖并落到 `#setup`。已做的替代证据：包验签、解包安装器 `-CheckOnly`。 |
| `REAL_DOWNLOAD` / `EXISTING_REUSE` | **未执行** | 未在非系统盘执行真实下载/断点续传；未对真实权重做原位零重复下载。J04 用真实小文件验证了回执语义，不等于真实权重验收。 |
| `REAL_LLM` | **未执行** | 隔离测试只使用 mock/结构化双；没有授权 provider 的真实调用、profile ID、外部结果分类。J05 的钉住/预览已用测试桩验证。 |
| `ASSET_TO_SHOT` | **软件路径通过，真实调用未执行** | 合成图片/声音走分配、预览、逐镜采用与绑定合同；Qwen 生图、固定音色未真实调用。 |
| `REAL_H3` / `MULTI_SHOT_DELIVERY` | **BLOCKED** | 本机没有完成当前 commit 的 H3 权重/受管实例验收，也没有 attempt/artifact/显存/解码证据；W6A8 权重未就位。 |
| `HUMAN_ACCEPTED` | **pending_review** | 没有人工观感验收，不把技术 QC 或建议审片当作人工接受。 |

## 5. 操作者从新机到成片的入口路径

1. 解包后双击 `一键安装并启动.cmd`（ASCII 备用 `SETUP.cmd`）；安装器选非系统盘，完成后落在 `#setup`。
2. `#setup`：检测/复用已有 ComfyUI，按需下载固定权重；文本模型是独立分支，可稍后配置。
3. 新建作品 → `#d/creative` 导入剧本 → 确认大纲（仅需「故事大纲」角色）。
4. `#d/plan` 按剧本场次生成分集 → 确认。
5. `#d/storyboard` 生成/应用分镜；`#d/assets` 上传或从素材中心分配素材。
6. `#d/package` 生成导演包 → 确认；选择生成方案（用户选择优先，候选方案不替换）。
7. `#d/run` 一键开拍：检查每镜人物主体与参考，提交后在页面看真实阶段、事件、暂停/继续；未开拍的包显示“还没有整部作品的运行记录”。
8. `#d/deliver` 查看交付版本、播放/下载；技术 QC、建议审片与人工验收分别记录。

## 6. 剩余限制与下一次复测

- 真实 H3：需要当前 commit 的权重就位、受管实例、GPU 租约与至少一条有对白和无对白的多镜序列；复测入口 `#d/run`，记录 attempt/artifact/lease/解码哈希。
- 真实下载：在可用非系统盘执行一次完整 `install-plan → execute`，记录计划/尝试 ID 与字节账；断网后重开页面应按目标回执回到同一尝试。
- 真实 LLM：配置两条明确路由，分别跑大纲/分镜，记录角色、profile ID、任务与 artifact hash、外部结果分类；不要用全体工种汇总代替。
- 清洁 Windows：在隔离 VM 解包，从唯一入口安装并确认重复启动沿用同一实例；只读体检单独记录。
- VLM/ASR：本轮未改 `client_review_identity` 文件，不触发重校准；但真实正负校准仍未完成，建议审片缺校准时应如实降级为 `not_run/needs_review`，不得声称已校准。
- 浏览器：本机通过；Linux/Windows CI 需在推送后复跑，按实际日志记录平台差异。

## 7. 交付物

- 提交（分支 `fix/director-journey-closeout-20261109`）：
  - `ede5b36` J01 打包清单路径
  - `7ff2b66` J02–J11 后端证据链
  - `75d3d78` J02/J03/J07/J08/J10–J12 前端
  - `2540280` J12 浏览器定位器
  - `4d79536` J11 浏览器 scope/断网回归
  - `35c8b1c` J05 钉住判定收紧
  - `5bb6498` J05 分段任务保留 crew 路由（修复回归）
  - `c9774f8` 本报告与审查文档
  - `d7bf7cc` 前端候选方案夹具类型（最终前端证据）
- 测试包：`releases/ShineVideoStudio-<build-id>-win64.zip` 与同名 `.sha256`；最终包从干净树重建，SHA-256 记录在 `releases/` 的同名文件与交付回复中（报告不内嵌会随自身提交变化的哈希）。
- 可重跑命令：
  - 后端定向：`cd backend && .venv/Scripts/python.exe -m pytest -q <上面的文件列表>`
  - 后端完整：`cd backend && .venv/Scripts/python.exe -m pytest -q tests`
  - 前端：`cd frontend && npm.cmd run build && npm.cmd test`
  - 浏览器：`backend/.venv/Scripts/python.exe scripts/verify_v13_web.py --work-dir F:\ClaudeWorkSpace\tmp\shine-web-verify`
  - 打包：`pwsh -NoProfile -File scripts\build_test_package.ps1 -OutDir <dir> -Verify`

## 8. 最终运行记录

- 完整后端：2125 项收集，退出码 0，1 项 ffmpeg 条件跳过；日志 `/tmp/shine-backend-full3.log`。
- 定向套件：289 项收集，288 passed / 1 skipped，退出码 0；日志 `/tmp/shine-targeted-final.log`。
- 前端：32 文件 / 201 测试通过；`npm.cmd run build` 通过。
- 浏览器：`MOCK_VERIFIED`，21 flows，`api_errors=[]`，退出码 0。
- 最终测试包：提交后从干净树重建，ZIP 路径与 SHA-256 记录在 `releases/` 的同名 `.sha256`；本报告不内嵌会随自身提交变化的哈希。
- 环境说明：宿主注入了 `F://workbuddy//...//shim//sitecustomize.py`，其批量删除保护会在单轮 pytest 超过 50 次临时文件删除时 `SystemExit(1)`；第一次完整运行因此出现安装类用例的假失败。清除 `PYTHONPATH` 后同一批用例全部通过，产品代码未针对该中间层做任何放宽。
