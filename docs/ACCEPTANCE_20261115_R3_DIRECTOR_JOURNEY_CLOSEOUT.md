# R3 质检验收报告：导演旅程 R1/R2 修复收口

- 报告文件：`docs/ACCEPTANCE_20261115_R3_DIRECTOR_JOURNEY_CLOSEOUT.md`
- 质检员：质检员（bot_d5c8166508ea）；**全程只读，未修改任何产品代码**
- 日期：2026-11-15（本版为第二版：补 Git Bash 官方口径后端全量 + 复核不一致条目）
- 证据目录：`.local-acceptance/r3-20261115-qa/`（Git 忽略，不进安装包）

---

## 0. 基线与执行口径（先声明）

**基线**：`main@afaef8a`「docs(test): 补 R3 质检结论与退回项（4C 节）」，相对 `origin/main` 为 +1
（`origin/main` = 63f50be）。R2 修复 `66caf31 / 476c962 / 46a4a72` 已随 PR #30/#31 合入主干。
**`63f50be → afaef8a` 只改了 `docs/TEST_REPORT_20261115_DIRECTOR_JOURNEY_R1.md`（+71 行）**，
产品代码零变化 → 两版基线的回归结论可互相引用。

**为避免掺入未提交噪声，后端全量在 afaef8a 的干净 worktree 上执行**：

- 干净树：`F:\ClaudeWorkSpace\projects\.qa-r3-clean-20261115`（`git worktree add --detach`）
- 理由：主工作树当时有 4 个在途改动（`backend/shine/asset_center/service.py`、`frontend/src/api.ts`、
  `frontend/src/asset-center/AssetsWorkspace.tsx`、`frontend/src/test/assetsWorkspace.test.tsx`、
  `frontend/src/test/apiError.test.ts`），其中后端那份含 `if False and content_existing is not None:`
  ——明显是未完成的临时屏蔽。在脏树上跑会把在途噪声算进验收结论。
- **不变量**：本次回归期间主工作树的这些文件仍在被修改（`service.py` mtime 02:48:31，
  即我的分片运行期间），干净树不受影响。
- **未执行 `npm ci` / `npm install`**（红线）。

**诚实边界**：本机 `GET /api/v1/system/status` → `h3_connected=false`、`render_mode=mock`、
`production_mode={mode: DEMO, runtime_status: NOT_ACTIVATED}`。
本报告全部结论为**离线 / mock** 结论，**不构成真实 H3 验收、不构成成片验收**。

---

## 1. 后端全量：8 分片并行（AGENTS.md 官方口径，Git Bash）

### 1.1 执行命令（逐字）

```
# Git Bash: C:/Program Files/Git/bin/bash.exe
# 脚本：.local-acceptance/r3-20261115-qa/run_backend_shards_gitbash.sh
cd /f/ClaudeWorkSpace/projects/.qa-r3-clean-20261115
export CODEBUDDY_SAFE_DELETE_ENABLED=0
ls backend/tests/test_*.py > /tmp/r3-all-tests.txt
split -n r/8 /tmp/r3-all-tests.txt /tmp/r3-chunk-
for f in /tmp/r3-chunk-*; do
  ( files=$(tr '\n' ' ' < "$f")
    "$PY" -m pytest $files -q -p no:cacheprovider > "$OUT/$(basename $f).log" 2>&1
    rc=$?; printf '%s\t%s\t%s\n' "$(basename $f)" "$rc" "$(wc -l < $f)" >> "$OUT/shard-exit-codes.tsv" ) &
done
wait
grep -H "^FAILED\|^ERROR" "$OUT"/r3-chunk-*.log
```

- 解释器复用主仓库 `backend/.venv/Scripts/python.exe`（worktree 内不复制虚拟环境）。
- `split -n r/8` 为 GNU coreutils 8.32 的 **round-robin 按文件分配**，共 236 个 `test_*.py`。

### 1.2 逐片退出码表（判定口径：8 片退出码全 0 且各片日志无 FAILED/ERROR 行）

| 分片 | 文件数 | 退出码 | FAILED/ERROR 行数 | 判定 |
| --- | --- | --- | --- | --- |
| r3-chunk-aa | 30 | **0** | 0 | 绿 |
| r3-chunk-ab | 30 | **1** | 1 | 红 |
| r3-chunk-ac | 30 | **0** | 0 | 绿 |
| r3-chunk-ad | 30 | **1** | 17 | 红 |
| r3-chunk-ae | 29 | **0** | 0 | 绿 |
| r3-chunk-af | 29 | **0** | 0 | 绿 |
| r3-chunk-ag | 29 | **1** | 7 | 红 |
| r3-chunk-ah | 29 | **1** | 8 | 红 |
| **合计** | **236** | **4 片非 0** | **33** | **不满足全量门禁** |

原始表：`backend-shards-gitbash/shard-exit-codes.tsv`、`shard-files.tsv`；
日志：`backend-shards-gitbash/r3-chunk-{aa..ah}.log`。

### 1.3 自检：一次由我自己的隔离方式造成的伪失败（已剔除，不计入产品缺陷）

首跑时 r3-chunk-ab 有 **3** 条失败，多出的 2 条是：

```
FAILED backend/tests/test_director_setup_contract.py::test_shipped_service_uses_real_install_contract
FAILED backend/tests/test_director_setup_contract.py::test_shipped_service_uses_real_instance_approval_and_state_machine
E  Error [ERR_MODULE_NOT_FOUND]: Cannot find module
   'F:\...\.qa-r3-clean-20261115\frontend\node_modules\typescript\lib\typescript.js'
   imported from ...\scripts\setup_contract_bridge.mjs
```

**根因是干净 worktree 里没有 `frontend/node_modules`**（node 桥接脚本要读 typescript），不是产品缺陷。
三条独立证据：

1. 早先在**主树**跑的同一文件是绿的：`backend-shards/chunk-1.log` 有
   `[shine-module] 10.544s phases=36 cases=12 backend/tests/test_director_setup_contract.py`，无 FAILED/ERROR 行；
2. 在**主树**定向复跑该文件：`12 passed`，**退出码 0**；
3. 给 worktree 建 `frontend/node_modules` **目录联接**（指向主仓库同一目录，不复制、不重装、不改共享内容）
   后，按同一文件集合、同一 Git Bash 口径**重跑 r3-chunk-ab**：
   **只剩 1 条失败**（即下面的 `NODE_PACK_DIRECTORY_MISSING`），
   `test_director_setup_contract.py` 12 项全过。日志 `r3-chunk-ab-rerun.log`。

→ **产品级失败条数 = 33**，与 1.2 表一致。红片判定不变（chunk-ab/ad/ag/ah 四片退出码非 0）。

### 1.4 失败项清单（33 条，全部同一根因）

| 分片 | 失败文件 | 条数 |
| --- | --- | --- |
| r3-chunk-ab | `test_deployment_acceptance_regressions.py::test_generated_asr_manifest_matches_existing_executor_contract` | 1 |
| r3-chunk-ad | `test_deployment_storage.py::test_managed_renderer_and_review_launches_override_ambient_compiler_caches` | 1 |
| r3-chunk-ad | `test_runtime_activation.py`（全组） | 16 |
| r3-chunk-ag | `test_service_validation.py` | 6 |
| r3-chunk-ag | `test_managed_run_binding.py::test_activation_requires_the_installation_binding` | 1 |
| r3-chunk-ah | `test_new_machine_setup.py::test_configuration_generation_and_recipe_validation` | 1 |
| r3-chunk-ah | `test_production_mode.py`（全组） | 7 |

错误码统计（8 个分片日志合计）：**`NODE_PACK_DIRECTORY_MISSING` × 33**，
`EXISTING_COMFY_ADOPTION` × 1（伴随出现），无第二种根因。

根因原文：

```
409 NODE_PACK_DIRECTORY_MISSING
missing: ComfyUI-KJNodes, comfyui-videohelpersuite, ComfyUI-MiniMax-H3-PDD-Acc,
         ComfyUI-SolAttn_triton, Comfyui_Minimax_h3_latent_Upscaler
message: 这些节点包在安装的 custom_nodes 里找不到对应目录；请先在「安装与下载」补装节点，再重新生成运行配置。
```

抛出点：`backend/shine/comfyops/deployment.py:894`（`resolve_node_pack_dirs` 报缺包且 `capability == 'h3'`）。
绝大多数失败是用例在 fixture 阶段就被 409 拦下，**根本没跑起来**。

另：`r3-chunk-af.log` 里 1 条 `UnicodeDecodeError('gbk')` 出自 subprocess 读线程的 `ResourceWarning`，
该片退出码 0，**是警告不是失败**。

### 1.5 归属：基线自带，不是 R1/R2 导演线修复造成的

- 引入提交：`d043a11`「fix(setup): 工作栈生命周期与全流程依赖一键整备」（PR #29 / `333f75f`）。
- `git merge-base --is-ancestor d043a11 476c962` → **true**，早于 R1/R2 三个提交
  `66caf31 / 46a4a72 / 476c962`。
- **基线对照实验**：临时 worktree checkout 到 `d043a11^`（= `1e05bfa`），
  用**同一条命令跑同样 8 个文件** → **退出码 0，全绿**（日志 `backend-baseline-pre-gate.log`）。

| 同样 8 个文件 | 基线 `1e05bfa`（门禁前） | 当前 `afaef8a` |
| --- | --- | --- |
| 退出码 | **0（全绿）** | 1（33 条失败） |

→ 导演线 R1/R2 修复**不需要返工**；这是当前主干的既有红，须由修复师单独修。

### 1.6 复现方式（交给修复师直接跑）

```
$env:CODEBUDDY_SAFE_DELETE_ENABLED='0'
cd F:\ClaudeWorkSpace\projects\shine-video-studio
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_runtime_activation.py \
  backend/tests/test_service_validation.py backend/tests/test_managed_run_binding.py \
  backend/tests/test_production_mode.py backend/tests/test_deployment_storage.py \
  backend/tests/test_deployment_acceptance_regressions.py backend/tests/test_new_machine_setup.py \
  backend/tests/test_owned_process_group.py -q -p no:cacheprovider
```

单进程隔离复跑：**退出码 1，33 条失败**，全部为上面的 409（日志 `backend-failing-files-rerun.log`）。

### 1.7 修法硬约束（不得越界）

1. **不得删门禁、不得放宽生产路径**。真实安装必须具备这些节点包的生产语义要保留。
2. 真正的问题：**夹具 / 离线 / 测试环境没有这些 custom_nodes 目录时被硬拦**。
3. 正确方向：把门禁做成「离线 / 夹具场景可声明节点包状态」，或「离线模式降级为可诊断提示而非硬 409」；
   任何靠删断言 / 跳过模块 / 改期望值换变绿的做法一律判不合格退回。
4. 必须带定向回归：上述 8 个文件 + 1.8 的 7 个文件。

### 1.8 额外必跑：worker / 质量门 / 修复引擎 7 个文件

**触发判定**：本次 R1/R2 改动**未触及** `client_review_identity` 钉住的文件
（`git diff --stat main..HEAD -- backend/shine/worker.py backend/shine/preproduction.py
backend/shine/comfyops/handoff* backend/shine/agents/quality* backend/shine/quality*` → **空**），
但该分支**确实改了后端**（`backend/shine/director/journey.py`、`backend/shine/comfyops/*`、
`scripts/managed_comfy_bootstrap.py`、`scripts/shine_supervisor.py`），故按「宁可多跑」执行全部 7 个文件。

```
$env:CODEBUDDY_SAFE_DELETE_ENABLED='0'
cd F:\ClaudeWorkSpace\projects\shine-video-studio
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_offline_e2e.py \
  backend/tests/test_comfy_completion_window.py backend/tests/test_production_series.py \
  backend/tests/test_advisory_queue.py backend/tests/test_comfy_handoff_journal.py \
  backend/tests/test_v13_preparation.py backend/tests/test_h3_compiler.py -q -p no:cacheprovider
```

**结果**：7 modules，`[shine-tier] total=194 selected=194`，**退出码 0**，无 FAILED/ERROR。
逐文件 cases：`test_v13_preparation` 69 / `test_comfy_handoff_journal` 38 / `test_production_series` 33 /
`test_comfy_completion_window` 19 / `test_advisory_queue` 7 / `test_offline_e2e` 2 / `test_h3_compiler` 26。
日志：`backend-worker-quality-extra.log`

> 说明：本轮 `test_v13_preparation.py` **全过**（69 cases、退出码 0）。此前某份交接件把
> `ValueError: invalid literal for int() with base 10: ''` 归到 `test_v13_preparation.py:54`，
> 实际出处是 `backend/tests/test_owned_process_group.py:29` 的
> `child_pid = int(marker.read_text())`——8 分片并行负载下会读到子进程尚未写入的空 marker，
> 单进程隔离复跑通过。这是**测试侧竞态**，会让「8 分片全绿」出现假红，建议一并改为等待 marker 非空。

---

## 2. 前端全量（已具备有效证据，本轮未重跑）

**结论先行**：前端全量**已经跑过且结果有效**，时间线证明未被 A1 在途改动污染。
按总控要求「A1 提交前不再跑」，本轮**没有重跑**；下表是既有证据的溯源。

| 项 | 结果 |
| --- | --- |
| `npm.cmd run build`（`tsc -b && vite build`） | 1653 modules transformed，✓ built in 1.79s，**退出码 0** |
| `npm.cmd test -- --no-file-parallelism` | **69 files / 373 tests passed，0 failed**，Duration 146.04s，**退出码 0** |
| 定向 `directorBlockerNavigation` + `directorModuleGuide` | **2 files / 6 tests passed，退出码 0** |

**未被 A1 污染的时间线证据**：

| 事件 | 时刻 |
| --- | --- |
| 我跑前端 build + vitest 全量 | 02:19:xx – **02:22:30** |
| A1 在途改动 `frontend/src/asset-center/AssetsWorkspace.tsx` 首次 mtime | **02:27:02**（晚于我的运行结束 4.5 分钟） |
| `frontend/src/test/assetsWorkspace.test.tsx` mtime | 02:29:57 |
| `frontend/src/api.ts` / `apiError.test.ts` / `backend/.../service.py` mtime | 02:47:31 / 02:48:07 / 02:48:31 |

且 `63f50be → afaef8a` 只改了 docs，前端代码逐字节一致。
→ 我这次的前端证据对应的是**已提交基线**，不含任何 A1 未提交噪声。

**待办**：收到总控「A1 已提交」通知后，我会按同一口径在合并后的主干上重跑
`npm.cmd run build` 与 `npm.cmd test -- --no-file-parallelism`，并把新计数补进本报告。

日志：`frontend-full-vitest.log`

---

## 3. 只读复核：R1 报告「已修复」结论 vs 代码（抽查 D1 / D3 / D2·D4 映射与自环 / D5）

> 本轮按纪律**只读代码**，不去碰 8010/8020/5180/5190/8000 上的任何实例。

### 3.1 一致条目（抽查通过，结论与代码逐字吻合）

| 缺陷 | 代码依据 | 判定 |
| --- | --- | --- |
| **D1** 禁用理由与出口 | `OutlineConfirmationPanel.tsx:110-117` 三态化（`modelMissing` / `consentMissing`），理由 = 「编剧角色还没有配置模型，无法提取大纲」；`:228-234` 必做块 `what` = 「先给「编剧」角色配置一个文本模型。」、`why` = 「总体大纲由编剧角色调用模型提取。角色没有模型时，授权与提取都不会真正发生，按多少次授权也解不开主按钮。」、出口 label = 「去配置文本模型」 | ✅ 一致 |
| **D3** 必做动作块 | `errorActions.ts` 收录 `STORYBOARD_STYLE_NOT_FROZEN`（why 与报告逐字一致）、`SCRIPT_OUTLINE_NOT_CONFIRMED`、`EPISODE_PLAN_NOT_MATERIALIZED`；`StreamlinePanels.tsx:240/483/848` 三处 `.form-error` 后均接 `RequiredActionNotice` | ✅ 一致 |
| **D2/D4 映射与自环** | `errorActions.ts`：`EPISODE_PLAN_NOT_CONFIRMED.action.target='plan-stage'` + `selfLoopAction.target='creative-stage'`；`App.tsx`：`case 'plan-stage' → 'assets'`、`case 'creative-stage' → 'creative'`，自环分支回退渲染 `selfLoopAction`；映射单测 `target === 'plan-stage'` 且 `!== 'package-stage'` **保留未放宽** | ✅ 一致 |
| **D4 后端** | `backend/shine/director/journey.py:342-343` summary = 「已有剧本「{filename}」，待整理大纲」；`:359-360` blockers = `SCRIPT_OUTLINE_NOT_CONFIRMED` + 「已有剧本「…」，先整理并确认总体大纲。」；summary 与 blockers 说同一件事 | ✅ 一致 |
| **D5** 文案 | `JourneyRail.tsx:138-139` `bridgeHint = next ? '接下来：X' : atLastStage ? '已经是最后一个阶段' : '没有下一阶段'`；全仓已无「全部阶段已完成」 | ✅ 一致 |
| **D6** 七页 .module-guide | `App.tsx:677` 单点 `<ModuleGuide what={desc} why={DIRECTOR_STAGE_WHY[kicker]} />`，覆盖导演七阶段（实测 7/7 页可见且含「这一步要做什么 / 为什么必须做」） | ✅ 一致 |
| **D8** #d/run 右卡 | `DirectorShootPanel.tsx:302-304` `secondary` = `[{ label: '去导演包选择要拍的集', onClick: () => onNavigate('package') }]` | ✅ 一致 |

### 3.2 不一致条目（写入本节）

**不一致 1【D7 · 报告表述过宽，代码仍留该文案】P3**
R1 报告 4B 写「全页已无『确认拍摄方案并开始真实制作』这个不存在的按钮」。
代码 `DirectorShootPanel.tsx:445` **仍有字面文案**「确认拍摄方案并开始真实制作」（`!railPresent` 分支的底部按钮）。
但 `useRailPresent()`（`StageRail.tsx:24-26`）仅在面板被单独渲染（注释明说是测试回退路径）时为假；
真实生产布局有右栏时该按钮不渲染，指引与右栏按钮名同取动态 `startLabel`（`:288-289`、`:341-342`）。
→ **真实用户可见路径无影响**；但报告的「全页已无」不准确，且若将来出现无右栏布局，指引会再次指向不存在的按钮名。

**不一致 2【D5 · 行号漂移】P3**
R1 报告 3 节表格与 4B 把根因位置写作 `JourneyRail.tsx:144`；当前代码修复点在 **138–139 行**（`bridgeHint`），
144 行已是 `return (`。结论不受影响，仅引述过期。

**不一致 3【D2 · 状态字样过期】P3**
R1 报告 4B 表格把 D2 标为「**部分已修**」，但同一节的「裁定第 3 条落地复验（02:12–02:13）」已写明该缺口
已修复并实测通过。实际三条裁定（映射正确 + 同页不自环 + 同页改指上游前置）**全部成立**。属表格字样未同步。

**不一致 4【记录归属混写】P2（需总控裁定）**
R1 报告（测评员产出）现含 **R3 质检结论与质检截图**（`01-d-assets-rail-upstream.png` 等），
由总控以 `afaef8a`「docs(test): 补 R3 质检结论与退回项（4C 节）」提交。
按团队分工，测评报告归测评员、质检结论的正式载体是 `docs/ACCEPTANCE_*`；
同一批截图与结论同时出现在两份文档，会让「这条结论是谁出的」不可辨。**请总控裁定是否保留。**

**不一致 5【D1 · 文案细节】P3**
R1 4B 写「去配置文本模型 ›」；代码按钮名为「去配置文本模型」（无 ›），
必做块 `what` 为「先给「编剧」角色配置一个文本模型。」。结论不变。

### 3.3 素材线 R1 报告复核（`docs/TEST_REPORT_20261115_ASSET_IMAGE_JOURNEY_r1-20261115-assets.md`，已提交 842a247）

| 缺陷 | 报告结论 | 代码核对 | 判定 |
| --- | --- | --- | --- |
| **A1【P1】** 直接进入项目分配页时分配必然失败 | 已修复并复测通过（02:3x） | 修复提交 `4c38d77`「项目分配页在作品列表异步到达后回填目标作品」（`AssetsWorkspace.tsx` +12/−1、`assetsWorkspace.test.tsx` +74）。**根因是真的**：作品列表异步到达时，受控 `select` 在没有匹配 option 时会显示浏览器回落的第一项、而 state 仍是空串 → 用户看着「已选」却永远分配失败。修复在 `projects` 到达后回填 `targetProject`，并补了「请选择作品」空选项。回归断言严格且覆盖了「用户手动切换后不被弹回第一项」 | ✅ **一致**（真修复，非藏症状） |
| **A6【P1】** 「分配到作品」在项目已有同内容素材时服务端 500、界面裸英文 | 新增缺陷，建议后端幂等/具名拒绝 + 前端错误兜底两层 | **修复代码存在但被短路关闭**：工作区 `backend/shine/asset_center/service.py` 里新写的去重分支被 `if False and content_existing is not None:` 关闭，而 `content_existing` 的 select 仍无条件执行（一次无用查询）。且该文件**尚未提交**；后端回归 `backend/tests/test_asset_assignment_duplicate.py` 仍为未跟踪 | ❌ **A6 未修复**，不能记为已修 |
| A2–A5 | 未修 | 未见对应改动 | ❌ 未收口 |

**对报告本身的两点肯定**：§7.2 美术师主动更正了 R1 期间「经 `#d/projects` 进入能成功」的错误结论
（当时用「发出请求即算成功」判定，漏掉了界面上的裸英文 `500 Internal Server Error`，而台账实为 `{"items":[]}`），
并明确「分配链路当时从未成功过」；§1 推进表与 §7 对 A6 的标注一致，未把 A6 粉饰成已修。

**我的复核意见**：A1 可记为已修（待合入 main 后随全量回归一起复验）；**A6 仍是未修复的 P1**，
且 `if False` 这种短路式改法在本轮不能被当成修复提交——它让新写的正确逻辑变成死代码，
同时留下一条无用的 select。修好 A6 后应有 `test_asset_assignment_duplicate.py` 的后端定向回归。

### 3.4 门禁语义修复预审（修复师在途改动，未提交，只读核对）

**审的是工作区未提交内容**：`backend/shine/comfyops/deployment.py`、`backend/shine/config.py`、
`backend/tests/conftest.py`（已改）+ 未跟踪的 `backend/tests/test_node_pack_gate_semantics.py`。

**方向判定：符合「生产语义保留、禁止为变绿删门禁」的要求。**

| 点 | 核对结果 |
| --- | --- |
| 生产默认仍拦 | `Settings.node_pack_gate` 默认 `"enforce"`，缺节点包且 `capability=='h3'` 时**仍抛 409 NODE_PACK_DIRECTORY_MISSING**（`deployment.py:902-903`） |
| 降级显式声明 | 只有 `node_pack_gate == "diagnose"` 才降级；夹具在 `conftest.py:449` **显式**声明 `node_pack_gate="diagnose"`，不是全局默认 |
| 降级不是静默通过 | 缺失被写进 `node_pack_notes`（含 code / missing / message），并同时落到 renderer payload（`deployment.py:929`）与配置响应（`:1032`） |
| 双向锁死 | `test_node_pack_gate_semantics.py` 两条用例分别断言：`enforce` → 409 且 code/missing 非空；`diagnose` → 200 且 `node_pack_notes` 非空、code 正确、missing 非空、message 含 `custom_nodes` |

**两点必须补，否则「可诊断提示」对用户等于「静默通过」**：

1. **`node_pack_notes` 没有任何消费方**。全仓检索：后端只有写入点（`deployment.py`），
   **前端 0 命中**，readiness / 服务校验 / UI 也没有读它。
   → 降级后配置生成成功，但「缺哪 5 个节点包、渲染器会禁用全部自定义节点」这条事实
   **用户看不到**，与团队「原始错误码不是屏上唯一信息」「必做动作块」口径有落差。
   **建议**：把 `node_pack_notes` 接进服务校验/readiness 的用户可见文本（缺哪些包 + 下一步「去安装与下载」补装）。
2. **`diagnose` 无条件生效于任何环境**。`SHINE_NODE_PACK_GATE=diagnose` 是裸环境变量，
   **没有**与 `offline_simulation`、也没有与「实例是否为合成/测试根」绑定；
   而 `enforce` 之外的任何值都会走降级分支（`!= "diagnose"` 才拦，等于**只有恰好等于 diagnose 才降级**，这点是对的）。
   → 真实机器上一旦误设该环境变量，生产门禁会被静默关掉。
   **建议**：`diagnose` 只在明确的离线/夹具上下文生效（例如与 `offline_simulation` 绑定，
   或在真实安装 `is_mock=False` 上**拒绝** diagnose 模式——具名拒绝，而不是静默放行）。

---

## 4. 未验证 / 待真机 H3 验证（写「未运行」，不得写成「已通过」）

| 项 | 状态 | 原因 |
| --- | --- | --- |
| 真实 H3 渲染 / 真实出图 / 成片验收 | **未运行** | h3_connected=false、production_mode=BLOCKED |
| VLM 正负校准 | **未做** | 本分支未改 `client_review_identity` 钉住文件，**不触发**重校准义务；但历史正负校准本就没做过，**不得声称已校准** |
| ① 大纲提取与确认 | **未运行** | 本机无可用文本模型 |
| ② 分集确认 | **未运行** | 同上 |
| ③ 分镜草案与应用 | **未运行** | 同上 |
| ④ `EPISODE_PLAN_NOT_MATERIALIZED` 双台账复现 | **未运行** | 同上 |
| ⑤ 一键开拍空槽位口径 | **未运行** | 同上 |
| ⑥ 幂等键冲突 | **未运行** | 同上 |
| Windows 安装包 `build_test_package.ps1 -Verify` + 干净机安装 | **未运行** | 本轮不做发布验收 |
| 启动器四条验收入口（干净重启 / 端口被占 / 半启动残栈 / 停止） | **未运行** | 本轮只读复验，未起停实例 |
| 前端全量在 A1 合入后的复跑 | **未运行** | 等总控通知「A1 已提交」 |

六项「未运行」的原因（团队记忆 `shine.r1.notrun`）：本机没有任何可用文本模型——
产品以 `REAL_LLM_PROVIDER_REQUIRED` 拒绝 mock 文本 profile，生产应用不装配 `mock_llm_responder`，
也无 Ollama 或本地 OpenAI 兼容端点。

---

## 5. 结论

### 5.1 分层结论

| 分类 | 内容 |
| --- | --- |
| **已实现 + 离线已验证** | 裁定第 3 条、D1、D3、D4、D5、D6、D7、D8、启动器 stdin 修复 —— 定向 2/6、前端 build + 全量 69/373、浏览器实景 19/19 全绿 |
| **待真机 H3 验证** | 真实 H3 / 出图 / 成片、VLM 校准、六项文本模型链、Windows 包、启动器四条入口、A1 合入后的前端复跑 —— 全部**未运行** |
| **阻塞** | BLOCKER-1 后端 8 分片 4 片红（33 条，基线自带）；BLOCKER-2 素材线 A1–A5 未收口 |
| **待总控裁定** | 不一致 4：R1 报告 4C 节混入质检结论与截图 |

### 5.2 总判定：**FAIL（不通过，需退回）**

**理由**：

1. **R1/R2 导演线修复本身全绿**，抽查 D1/D3/D2·D4/D5/D6/D8 与代码逐字吻合，无过门禁式改动，无用户可见缺陷。
2. **但收口基线 `main@afaef8a` 的后端全量 8 分片有 4 片退出码非 0（33 条 FAILED/ERROR）**，
   AGENTS.md 的全量回归门禁未满足 → **不能签发整体通过**。
3. 该红的归属已用基线对照实验查实：**不是 R1/R2 导演线修复造成的**（门禁来自更早的 PR #29，
   在 `d043a11^` 上同样 8 个文件退出码 0 全绿），但它是当前主干的既有红，必须单独修完。
4. 素材线 A1–A5 同样未收口。

**退回归口**：修复师（bot_ffa031e389fd）　**同步总控**：Chief（bot_a48efeacd5bc）
**复现方式**：见 §1.6。

---

## 6. 证据清单

| 内容 | 路径（相对仓库根） |
| --- | --- |
| **后端 8 分片日志（Git Bash 官方口径）** | `.local-acceptance/r3-20261115-qa/backend-shards-gitbash/r3-chunk-{aa..ah}.log` |
| **逐片退出码表** | `.local-acceptance/r3-20261115-qa/backend-shards-gitbash/shard-exit-codes.tsv` |
| 每片文件清单 | `.local-acceptance/r3-20261115-qa/backend-shards-gitbash/shard-files.tsv` |
| 分片脚本（Git Bash 版） | `.local-acceptance/r3-20261115-qa/run_backend_shards_gitbash.sh` |
| chunk-ab 联接后重跑（剔除伪失败） | `.local-acceptance/r3-20261115-qa/backend-shards-gitbash/r3-chunk-ab-rerun.log` |
| 首轮（主树 PowerShell 口径）分片日志 | `.local-acceptance/r3-20261115-qa/backend-shards/chunk-{0..7}.log` |
| 首轮退出码表 | `.local-acceptance/r3-20261115-qa/backend-shards/shard-exit-codes.json` |
| 失败文件单进程隔离复跑 | `.local-acceptance/r3-20261115-qa/backend-failing-files-rerun.log` |
| **门禁前基线对照（对照组）** | `.local-acceptance/r3-20261115-qa/backend-baseline-pre-gate.log` |
| worker/质量门 7 文件加跑 | `.local-acceptance/r3-20261115-qa/backend-worker-quality-extra.log` |
| 前端全量 vitest | `.local-acceptance/r3-20261115-qa/frontend-full-vitest.log` |
| 浏览器复验脚本 / 结果 JSON / 截图 ×6 | `.local-acceptance/r3-20261115-qa/verify_director_r3.py`、`verify_director_r3.json`、`shots/` |
| 投递兜底文本 | `.local-acceptance/r3-20261115-qa/DELIVER-0{1,2,3}-*.txt` |
| 测评报告（导演线） | `docs/TEST_REPORT_20261115_DIRECTOR_JOURNEY_R1.md`（含 4B/4C 节） |
| 测评报告（素材线，未入库） | `docs/TEST_REPORT_20261115_ASSET_IMAGE_JOURNEY_r1-20261115-assets.md` |

---

## 7. 纪律与边界声明

- **全程只读**：未修改任何产品代码；未新增/删除/改动任何产品决策；未提交权重、成片、密钥或数据库。
- **未执行 `npm ci` / `npm install`**（任何目录均未执行）。worktree 内只用**目录联接**引用既有 node_modules。
- **未启动、停止或抢占任何实例**：本轮未访问 8010/8020/5180/5190/8000 上的实例；8030/5200 未使用；
  未使用 `scripts/start_shine.ps1`。
- 主工作树的在途改动（`backend/shine/asset_center/service.py`、`frontend/src/api.ts`、
  `frontend/src/asset-center/AssetsWorkspace.tsx`、其两个测试）**未被本轮触碰**，仍在途。
- 临时 worktree `F:\ClaudeWorkSpace\projects\.qa-r3-clean-20261115` 的 git 登记已移除，
  但**目录删除被本机安全策略拦截**（永久删除被拒），请总控/用户手工清理；
  另有上一轮遗留目录 `F:\ClaudeWorkSpace\projects\.qa-base-20261115` 同此。
- 诚实边界：本机 `h3_connected=false`、`production_mode=BLOCKED`；
  本报告全部结论为**离线 / mock** 结论，**不构成真实 H3 验收、不构成成片验收**。
