# Shine 视频工作台 · 最新 main 主线最终工程验收报告（2026-11-14）

> 验收性质：工程收尾 + 全量回归 + 分级真机验收。本报告区分
> `ENGINEERING_PASS` / `RTX5080_HOST_PASS` / `RTX4070_EXEMPT` / `PRODUCTION_READY`，
> 不以工程绿灯冒充未验证硬件的生产许可。

## 0. 结论摘要

| 维度 | 结论 |
| --- | --- |
| 最新主线已知 CI 故障（L3 browser E2E） | **已修复**，本地隔离 E2E 全绿（21 条流程，`api_errors=[]`） |
| 后端全量（8 分片） | **PASS**：8/8 分片 exit 0，160 模块，零 FAILED/ERROR（最终口径复跑同绿） |
| 前端全量 / tsc / build | **PASS**：52 文件 299 用例；tsc 0；build 0 |
| Ruff / Alembic | **PASS**：`ruff check shine tests` 全过；head `f180d0010001`，无 drift |
| 大纲版本 / 三档节奏 / 登场人物 | **PASS**（定向 + 全量 + 真实 MiniMax 旅程） |
| 资源自整备 V2 / 下载链 / 变体启动配方 | **ENGINEERING_PASS**；W6A8 生产门禁保持关闭 |
| ComfyUI 统一连接与安全恢复 | **ENGINEERING_PASS**；5080 真实连接/节点核验/出片见 §5 |
| RTX 5080 真实生产 | **RTX5080_HOST_PASS**：真实渲染器节点核验 4 方案、真实 H3 出片 PASSED（technical 过、semantic/final 诚实待人工）、GPU 回基线、租约释放（§5） |
| 页面安全重启后台 | **PASS**：常驻监督+空闲门禁+Origin/标记契约，真实重启端到端生效（§2.7） |
| RTX 4070 真机 | **EXEMPT_HARDWARE_UNAVAILABLE**（§6） |
| W6A8 / RelayVSR / VLM 校准 | 保持真实阻断，见 §7 |

## 1. 验收基准与隔离

- 远端基准 main SHA：`1c7cfd3205724fdb6514cdb602a459e7d545b869`
  （`feat: 大纲生成三档剧情节奏 + 登场人物介绍`）。fetch 后确认 main 未再前进。
- 收尾 worktree：`F:/ClaudeWorkSpace/projects/shine-main-closeout-20261114`，
  分支 `closeout/main-final-engineering-20261114`（自 origin/main 新建）。
  未触碰其他 Agent 的 worktree，未触碰 PR #17（Launcher，OPEN）的并行开发。
- PR #16（H3 资源自整备 V2.0 最终收尾）已合并（`fd176f1`）；其成果本轮只核查不重做。
- 基准 CI run <https://github.com/anjingdtl/shine-video-studio/actions/runs/43550015947>：
  L1 backend quick ✅、L3 backend full (Linux) ✅、Frontend build ✅、
  L2 backend integration ⏭️ skipped（push 事件按 workflow 规则不跑 L2）、
  L3 backend full (Windows) 见 §4、**L3 browser E2E ❌（本轮修复对象）**。

## 2. 本轮修复与根因

### 2.1 P0 · 浏览器 E2E strict mode violation（CI 唯一已知故障）

根因：导演模式七阶段页面**保持挂载**（`hidden` 切换），每页各有一个
`.action-rail`（`aria-label="本阶段操作"`）。`scripts/verify_v13_web.py` 用全局
`.action-rail` 断言，解析到 7 个元素触发 strict mode violation。

修复（不删断言、不用 `.first()` 掩盖）：
- 作用域锁定当前活动阶段容器：`page.locator("#creative").get_by_label("本阶段操作")`；
- 增加可见性断言、按钮唯一性断言（`to_have_count(1)`）；
- 按钮若合法禁用（隔离 fixture 无文字授权），必须显示具名原因
  （`.rail-disabled-reason`），不允许静默死按钮。

### 2.2 P0 · E2E 与收敛后产品行为对齐（同一次 CI 故障链的后续断言）

修好首断言后逐段实跑暴露的其余失配，全部按**真实产品行为**调整，断言只增不减：

| 位置 | 旧断言/路径 | 现产品行为 | 调整 |
| --- | --- | --- | --- |
| 风格冻结 | `#d/assets` 找 `#style` | 风格面板在专业生产板块；导演统筹页经分镜书一次确认分集+风格 | 改到 `#v/production` 冻结 |
| 制片准备证据 | 「人工核验 / 试听 / 权利证据」 | 收敛为「核验证据」（仅视图 0–2） | 改标签；工厂/语音视图不再有无控件的填值步骤（共享证据仍随每次审批提交） |
| 风险核对 | 「我已核对服务器配置的范围、来源、许可和具体操作风险」 | 「我已核对本机维护的路径范围和操作内容」（local 模式视图 0–2） | 改标签 |
| 真实生成确认 | 原生 `window.confirm` | 两击武装按钮（`.queue-confirm`：取消零提交、确认恰一提交） | 重写为武装按钮断言，保留模式/方案 payload 断言 |
| 工种远端同意 | 每次调用复选框 | 项目级一次同意 + 请求携带 `allow_remote_text` | 断言同意披露可见 + payload 标志 |
| LLM 管理 | `#settings .llm-add-model` | 移入 `#setup` 制作环境区；模型卡编辑收进「设置」折叠 | 改容器 + 先开折叠 |
| run-scope（J11） | `#d/run` | 运行范围/后期面板在 `#d/deliver`（成片中心） | 改导航 |

### 2.3 P0 · 真实旅程发现的产品缺陷：操作栏 stale closure（5 处）

`useStageRail` 仅在 deps 变化时重新注册动作；多个面板的 deps 未覆盖动作闭包
全部状态，导致**页面级主操作按钮使用注册时的旧状态**。真实旅程实证：统筹页把
集数改为 1、秒数改为 30 后点操作栏「生成分镜书」，服务端收到的仍是默认
（`episode_count=None/target=60`，产出 2 集草案）。

修复（补齐 deps，不改架构）：
- `StreamlinePanels.tsx` storyboard-book：+`episodeCount, targetSeconds`
- `OutlineConfirmationPanel.tsx`：+`pacing`（切换节奏后操作栏提取按钮曾持旧节奏）
- `DirectorShootPanel.tsx`：+`variantId, acceleratorId, secondPass, upscale`
- `ProductionSeriesPanel.tsx`：+`quality, profileChosenByUser, gates, shots,
  busyShotIds, framePreset, customWidth, customHeight, clipSeconds,
  ref2vaProfiles, mode, system`
- `MaterialPreparationPanel.tsx`：+`state, selected, bindings, strategy`

回归：`frontend/src/test/storyboardTone.test.tsx` 新增「操作栏携带当前集数与秒数」
用例，并在**无修复时确证变红**（收到旧默认 2/60）、有修复时绿。

### 2.4 P0 · 变体级启动配方合同（§5.3 工程交付）

现状缺陷：`deployment.generate_configuration` 永远使用 INT8 默认门槛，对变体-blind；
W6A8 候选若走到配置生成会 silently 借用基线 12000/8192 门槛。

新增 `backend/shine/comfyops/variant_recipe.py`（配置合同，不是权威）：
- 从变体自己的 manifest/catalog/工作流图构建合同：四权重模型栈（role/component/
  sha/size/revision）、图身份（`graph_sha256` 与 manifest 比对）、节点依赖
  （`required_nodes` + class_type 集合）、运行时 ABI（python/torch/cuda 声明）、
  架构要求、内存事实（unknown 保持 unknown）、启动参数结构合法性
  （loopback 不变量、已知 flag 集合、reserve-vram 非负、fast 模式已知）；
- `admission.startup_determined`：任一渲染期门槛 unknown → `CANDIDATE_CONTRACT`
  + 具名 blocker `VARIANT_STARTUP_THRESHOLDS_UNKNOWN` + 缺失上游证据清单；
- `contract_sha256` 覆盖全部声明事实（改 manifest/图/门槛即换身份）；
- 集成：`generate_configuration(..., variant_id=None)` 默认路径**字节级不变**；
  显式变体在**事务前**解析合同，未确定即 409
  `VARIANT_STARTUP_CONTRACT_NOT_DETERMINED`（不写 recipe、不写 candidate）；
- 新只读端点 `GET /api/v1/setup/variant-startup-contract?variant_id=`；
- 真实激活/生产门禁原封不动（`runtime_configuration` / `profile_gate` /
  `strategy_blockers`），合同不构成生产许可。

回归：`backend/tests/test_variant_startup_recipe.py` 9 项（四权重闭包、图身份、
unknown 具名拒绝、基线门槛 12000/8192、身份绑定、未知变体 404、参数合法性、
API 拒绝且不落盘、默认路径不变、只读端点）。

### 2.5 P0 · 大纲创作能力回归补充

- `test_director_streamline.py`：版本链用例补「确认 v2 后单一依据切换、v1 保留为
  历史（approved 不变、confirmed 转 false）、`basis.outline_for_document` 解析到 v2」；
  新增「编辑登场人物介绍不丢 entity_id/stable_identity」纯函数用例。
- `test_story_preparation.py`：节奏用例补「省略字段=balanced 默认且身份独立」、
  「三档节奏下 production_sections / locked_facts / 人物 entity_id 完全一致」
  （节奏只塑造叙事，不碰事实与编号）。

### 2.6 P1 · 分镜重试与 UNKNOWN 授权重发的衔接缺陷（真实旅程发现）

真实旅程中摄影工种调用传输中断（`finish_reason=length`、部分正文、
`external_outcome=UNKNOWN`）。产品正确具名拒绝、不自动重发；但「核对→授权一次
重发→分集重试」链路有两处缺陷，已修：
- `director/streamline.py` resume 分支原先对非 mock 失败任务**无条件**走 one-call
  replay 授权，未区分 UNKNOWN/已授权重发，导致授权重发后重试被
  `STORYBOARD_REPLAY_TARGET_NOT_RESPONSE_RECEIVED` 拒绝。现按结果分流：
  `RESPONSE_RECEIVED`+REJECTED/FAILED→replay 授权；
  `USER_AUTHORIZED_NEW_REQUEST`/`NOT_SENT_PROVEN`→一次新调用（不 replay 旧行）；
  未核对 UNKNOWN→具名 `STORYBOARD_UNKNOWN_REQUIRES_RECONCILIATION`，绝不静默新调用。
- 步骤抛门禁异常时原先丢失 `blocked_episodes` 标记，使运行 stranded 在
  NEEDS_REVIEW 且无重试入口。现异常路径保留 blocked 标记与 failed_unit/role/task。
- 回归：`test_supervisor_restart.py` 之外，分镜重试衔接以真实旅程实证
  （核对只读→授权一次→重试新调用）；既有 `test_blocked_episode_retry_resumes_and_finishes`
  保持绿。

### 2.7 P1 · 重连无效时页面安全重启后台（用户裁定本轮实现）

架构事实：页面由后台进程服务，后台死掉后无法经 API 重启自己。新增常驻环回监督
进程 `scripts/shine_supervisor.py`（启动器幂等拉起，记录于 `run/supervisor.json`，
**不进** `processes.json`，故 `stop_shine.py` 不会误杀）：
- 仅绑 127.0.0.1；跨站 `Origin` 拒绝（防远程页面 CSRF 重启）；变更端点要求
  `x-shine-local-action` 标记；
- 重启前过产品空闲门禁 `require_idle_configuration`：有活 job/series/GPU 租约/
  安装尝试时具名 `STACK_BUSY` 拒绝，绝不静默杀在途渲染；
- 停止/启动复用身份核验的 `stop_shine.py` 与幂等 `start_shine.ps1`，不引入 PID-only kill；
- 页内「重启后台」按钮（重连横幅）显示进度与具名拒绝，成功后自动重连。
- 回归：后端 `test_supervisor_restart.py`（环回/Origin/标记/空闲拒绝/状态机，4 项）、
  前端 `supervisorRestart.test.ts`（端口/标记/状态，3 项）。活体验证：health ok、
  无标记 403、跨站 403。

## 3. 全量回归结果（对应最终源码）

### 3.1 后端 8 分片（AGENTS.md 固定纪律）

命令（仓库根、`CODEBUDDY_SAFE_DELETE_ENABLED=0`、`env -u NO_PROXY -u no_proxy`、
按文件均分 `split -n r/8`）：

```bash
ls backend/tests/test_*.py > /tmp/closeout-all-tests.txt
split -n r/8 /tmp/closeout-all-tests.txt /tmp/closeout-chunk-
env -u NO_PROXY -u no_proxy CODEBUDDY_SAFE_DELETE_ENABLED=0 bash -c \
  'for f in /tmp/closeout-chunk-*; do (backend/.venv/Scripts/python.exe -m pytest \
   $(tr "\n" " " < "$f") -q -p no:cacheprovider > "$f.log" 2>&1; \
   echo "$f exit=$?" >> /tmp/closeout-shards-exit.txt) & done; wait'
```

结果：**8/8 分片 exit 0**；`grep -H "^FAILED\|^ERROR"` 无输出；
160 个模块摘要。最终源码口径复跑（含 §2.6/§2.7 后端改动与新增回归）：
`/tmp/co2-chunk-*.log`、`/tmp/co2-exit.txt` 同样 8/8 exit 0、零 FAILED/ERROR。

### 3.2 前端

- `npx vitest run --no-file-parallelism`：**52 文件 / 299 用例全过**（含新增回归）
- `npx tsc --noEmit`：exit 0
- `npm run build`：exit 0
- 日志：`.local-acceptance/fe-vitest4.log` / `fe-tsc4.log` / `fe-build4.log`
  （最终源码口径，含 §2.3/§2.7 前端改动与重连横幅测试对齐）。

### 3.3 Ruff / Alembic

- `backend/.venv/Scripts/python.exe -m ruff check shine tests`：All checks passed
- CI 同口径 `ruff check scripts/verify_v13_web.py`（仓库根）：All checks passed
- Alembic 全新库 `upgrade head` → `f180d0010001`；`alembic check`：
  No new upgrade operations detected（无 drift）

### 3.4 隔离浏览器 E2E（CI L3 同命令）

```bash
uv run --project backend --frozen --with playwright==1.62.0 \
  python scripts/verify_v13_web.py --work-dir .local-acceptance/e2e-work \
  --browser-path <chromium> --screenshot .local-acceptance/e2e-work/qa.png
```

结果：exit 0，回执 `status=MOCK_VERIFIED`、21 条 flows、`api_errors=[]`；
截图 `qa.png` / `qa-native.png`。日志 `.local-acceptance/e2e-work/run7.log`。
**最终源码口径复跑**（含 §2.3 deps 修复后）：`run8.log` 同样 exit 0、
`MOCK_VERIFIED`、`api_errors=[]`，截图 `qa-final.png`。

## 4. GitHub CI

- 基准 run 43550015947（main@1c7cfd3）：见 §1；L3 browser E2E 失败已由 §2.1/2.2 修复。
- 触发规则（`.github/workflows/ci.yml`）：PR 事件跑 L1+L2+Frontend；
  **L3（Linux/Windows/E2E）仅 main push 或手动 full**。因此 PR 全绿不代表 L3 已过；
  本轮以本地隔离 E2E 作同等检查，并在合并后复核 main 完整 CI。
- 收尾 PR CI：见 §10（创建后回填）。
- 合并后 main CI：见 §10（回填）。

## 5. RTX 5080 真实验收（受控窗口 · 隔离测试作品）

环境：RTX 5080 16GB；开发栈以**收尾 worktree 代码**启动，数据目录为既有真实台账
`.local-acceptance/witch-main-20261111`（启动前只读核查：无 QUEUED/RUNNING job、
无活跃 series/run、12694 条租约全 RELEASED → Worker 不会恢复任何用户未完成工作）。
测试作品：`收尾真实短镜验证1114`（`523c5d98-9851-4d79-a916-f7e867bf6582`），
不触碰正式《放开那个女巫》项目的镜头与产物。

- **A 创意与剧本（真实 MiniMax M3.1-flash）**：导入合成剧本 → 项目级文字同意 →
  紧凑节奏 → 真实提取大纲（任务 100%）→ 正文含 `## 登场人物`（老周/客人介绍
  忠于原文，未说明处如实写「未说明」）→ 确认。截图 `a-outline-ready.png` /
  `a-outline-confirmed.png`。回执 `phase_a.log`：PASS、`api_errors=[]`。
- **B 剧本统筹**：分镜书（1 集 / 30 秒 / 动画）一次确认分集+风格；版本链如实
  （草稿 v1 挂起不顶掉确认依据，本轮确认 v2）。截图 `b-book-confirmed.png`。
- **C 镜头拆解**：真实五工种链逐场拆镜。首轮含标题碎片的合成剧本产生 31 字符退化
  「场次」，MiniMax 在 32K 输出预算内耗尽 reasoning（`finish_reason=length`、正文 0
  字符），产品按合同 `LLM_FORMAT` 具名拒绝、带纠错说明有界重试后 `NEEDS_REVIEW`
  （行为正确）。换干净剧本后编剧/导演工种真实通过；摄影工种一次传输中断
  （`external_outcome=UNKNOWN`）→ 核对只读 → 用户授权一次重发 → 分集重试衔接
  （§2.6 修复后）可继续。**crew 链整集落镜的正路径完成度受 provider 截断行为影响，
  本轮以产品 documented 手工镜头 fallback 完成真实出片**（见 E），不伪造整链 PASS。
- **D 拍摄素材准备**：上传既有真实成片的 1 秒代表帧（本地字节，无外传）并
  「确认使用」为老周主素材（`entity_id=老周`，approved+locked）。
  截图 `d-master-confirmed.png`；回执 `phase_d.log`：PASS、`api_errors=[]`。
- **E 视频生产（真实 H3）**：`POST /setup/profiles` 登记 4 方案 →
  `POST /setup/profiles/validate` **真实受管渲染器节点核验 4/4 通过**（启动受管
  ComfyUI、读 object_info、停止）。手工镜头（英文 story_beat+prompt_style）+
  主素材 subject 绑定 → `POST /production-series`（INT8 Ref2VA pdd8，basic 自审
  1344×768）。门禁正确性实证：中文手工镜头被 `H3_ENGLISH` 拒绝；声明画幅与方案
  实际输出不符被 `BASIC_TECHNICAL_FAILED` 拒绝；修正后过门禁真实渲染。
- **F 出片核验**：job `c8efadc7…` **PASSED**；technical 门禁 passed；semantic/final
  `needs_review`（VLM/ASR 路由未配置 → 诚实待人工验收，**不声称 accepted**）。
  artifact `…_004镜_未分集.mp4`：SHA-256 与字节数匹配、h264 1344×768@24fps + AAC、
  `ffmpeg -f null -` 全解码 exit 0 无错误；租约全 RELEASED；GPU 回基线 ~1.8GB
  （渲染期 ~15.4GB，收尾释放，无孤儿渲染器）。回执 `phase_f_receipt.json`。
- **监督进程真实重启端到端**：`POST /restart-stack`（带本地标记）→ 监督进程执行
  身份核验停止+幂等启动 → API/Web 恢复健康、页面可重连（§2.7）。

历史证据引用（本轮未重跑者标 NOT_RUN）：R7/R8 的 64 镜 4 集技术交付
（`docs/PROGRESS_20261107_DIRECTOR_JOURNEY_R7.md` §9）仍为历史事实，不作为本轮结论。

## 6. RTX 4070 硬件豁免（EXEMPT_HARDWARE_UNAVAILABLE）

本机为 RTX 5080 16GB，无 RTX 4070 12GB。以下项**豁免真机**，不参与工程 PASS 计算：
- 4070 实际出片；4070 SM8.9 SageAttention 真实推理；4070 W6A8 真正出片。
工程兼容性以 Mock、配置合同、节点契约、硬件事实模拟与异常注入覆盖
（`test_variant_binding_and_gate`、`test_resource_resolver_v2`、`test_graph_compiler`、
`test_install_plan_qualification`、`test_installer_redirects`、`test_installation_persistence` 等）。
**不得**将模拟结果宣称为 4070 真机兼容 PASS。

## 7. 未完成外部阻断项（保持真实状态）

- **W6A8 候选**：官方未公布渲染期显存/内存门槛 → 启动合同 `CANDIDATE_CONTRACT`、
  `VARIANT_STARTUP_THRESHOLDS_UNKNOWN`；受管激活/试镜/生产门禁保持关闭
  （`MODEL_VARIANT_NOT_ACCEPTED` 等）。缺失上游证据清单见合同
  `missing_upstream_evidence`（真机峰值、兼容说明、验收产物等）。
- **RelayVSR X2**：权重 SHA 与可信节点版本未钉 → `RELAYVSR_WEIGHTS_NOT_PINNED`；
  依赖解析/错误提示/不兼容拒绝的工程测试通过，真实超分 NOT_RUN。
- **VLM 正负校准**：从未执行；本轮未改 `worker.py`/`preproduction.py`，不触发新的
  重校准义务，但旧校准缺失事实不变，不得声称已校准。
- **W6A8 受管启动配置**：requirements 全 unknown，不猜测门槛；真机验收前 BLOCKED。

## 8. 安全回归结论

- UNKNOWN 不重发/不替代、一次性授权、候选晋升门禁、风格冻结、云同意、
  不确定提交对账：相关模块（`test_story_outline_unblock`、`test_film_unknown_outcomes`、
  `test_advisory_queue`、`test_production_series`、`test_security_windows` 等）全绿。
- 环回-only、不盲杀未知进程、不删启动记录、租约不丢弃：
  `test_managed_process_release`、`test_comfy_recovery`、`test_gpu_release_confirmation`、
  `test_runtime_orchestrator` 全绿。
- 输出目录迁移：缺失文件如实报告跳过、存在文件逐字节核验、不覆盖、可回滚
  （`test_output_directory` 10 项全绿）。
- 本轮未提交模型权重、生成视频、项目数据库、`.env`/密钥；证据均在 Git 忽略目录。

## 9. 真实用户旅程验收（导演模式）

逐阶段主操作唯一、页面切换不丢状态、旧草稿不覆盖已确认大纲、候选可准备但
不能绕过开拍闸门、断线可诊断（J11 恢复断言在 E2E 与旅程均覆盖）。
旅程截图与回执见 §5 与 `.local-acceptance/journey/`。
发现的 stale-closure 缺陷已修并加回归（§2.3）——这正是「所有实际任务与 UI 显示一致」
要求的实证修复。
另观察（非缺陷、不改产品）：多文档窗口期——选中尚未提取大纲的新剧本时，面板正文
展示项目当前确认依据（旧剧本大纲），操作栏主操作文案为「补充锁定事实后重新提取大纲」，
但其动作仍是对**所选文档**提取；新大纲产出后视图即切换到较新版本并出现确认入口。
依据切换语义（草稿不顶掉确认依据）全程正确。

## 10. PR 与合并风险

- 收尾 PR：<https://github.com/anjingdtl/shine-video-studio/pull/19>
  （分支 `closeout/main-final-engineering-20261114`）。PR CI：Frontend build ✅、
  L1/L2 见 run 43562877511；L3（Linux/Windows/E2E）按规则在 PR 事件跳过，
  以本地隔离 E2E 作同等检查，合并后复核 main 完整 CI。
- 改动面：E2E 脚本对齐、5 个前端面板 deps 补齐 + 回归、后端分镜 resume/异常路径
  修复、变体启动合同（新模块 + 显式 variant_id 门禁 + 只读端点）+ 9 回归、
  常驻监督进程 + 页面重启按钮 + 7 回归、大纲回归补充、验收报告。
- 风险评估：默认路径行为不变（INT8 配置生成字节级不变、无 variant_id 调用不变）；
  前端 deps 补齐为行为修正（操作栏与面板状态同步），有红/绿对照回归；
  新门禁只作用于显式选择变体的配置生成，且发生在写事务之前；监督进程仅环回、
  空闲门禁拒绝活工作、不引入 PID-only kill、独立记录不被 stop_shine 误杀。
- 合并方式：PR 合并入 main 后推送 main（所有者权限，ruleset 允许）；不 force push。

## 附录 · 证据目录（Git 忽略，不进安装包）

- `.local-acceptance/e2e-work/`：隔离 E2E 日志与截图
- `.local-acceptance/journey/`：真实旅程脚本、截图、回执
- `.local-acceptance/frontend-*.log`、`.local-acceptance/outline-regression.log`
- `/tmp/closeout-chunk-*.log`、`/tmp/closeout-shards-exit.txt`：8 分片原始日志