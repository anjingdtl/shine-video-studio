# R3 终版质检验收报告：门禁语义修复 + 素材线 A1/A6

- 报告文件：`docs/ACCEPTANCE_20261115_R3_FINAL_GATE_AND_ASSET.md`
- 质检员：质检员（bot_d5c8166508ea）；**全程只读，未修改任何产品代码**
- 日期：2026-11-15
- 数据目录：`.local-acceptance/r3-final-20261115-qa/`（Git 忽略，不进安装包）

---

## 0. 基线与被测树

**被测树（候选树）**：`e35fd94` = `fix/node-pack-gate-offline-fixture-20261115`（门禁语义，4 提交）
合并 `chore/asset-assign-a1-20261115`（素材线 A1 + A6 复测 + 报告）。

| 组成 | 提交 |
| --- | --- |
| 门禁：enforce/diagnose 双模式 | `9e34736` |
| 门禁：并行 `int('')` 竞态修复 | `37f10db` |
| 门禁：诊断接入 readiness 层 + 门禁不得被环境变量降级 | `28e4977` |
| 门禁：诊断在向导与基础配置面板可见 | `1ae4669` |
| 素材线：A1 修复 | `4c38d77` |
| 素材线：R2 第二轮复测（A6 复测通过、A7 合并缺口型回归） | `f6f301c` |

**为什么在候选树跑而不在 main**：门禁 PR 与素材线 PR **当时都尚未合入 main**（`merge-base --is-ancestor` 对两者均为 False，
main 仍为 `afaef8a`）。本报告是**候选树**上的终版回归结论；**合入 main 后需在主干上再跑一次**才算最终签发。

---

## 1. 环境取证（回归可信度的前提）

取证脚本：`.local-acceptance/r3-final-20261115-qa/env_forensics.py`（输出 `env-forensics.json` 与 `env-forensics-candidate.json`）

| 项 | 值 | 判定 |
| --- | --- | --- |
| `sys.executable` | `F:\ClaudeWorkSpace\projects\shine-video-studio\backend\.venv\Scripts\python.exe` | ✅ 本仓库 venv |
| `sys.prefix` | 同上 | ✅ |
| `sys.base_prefix` | `C:\Users\Administrator\AppData\Roaming\uv\python\cpython-3.12-windows-x86_64-none` | ✅ 基础解释器，无 venv |
| `PYTHONPATH` | **null** | ✅ 未设置 |
| `PYTHONHOME` | null | ✅ |
| `sys.path[:8]` | 脚本目录 → uv 基础解释器 4 项 → **本仓库 venv** → **本仓库 venv site-packages** → **本仓库 backend** | ✅ 全部归属本仓库 |
| `sys.path` 总长 | **8** | ✅ 无多余项 |
| `PATH` | 无任何其他 checkout 的 venv/Scripts | ✅ |
| site-packages `.pth` | 仅 `_editable_impl_shine_video_studio.pth`（指向本仓库 backend）与 `_virtualenv.pth` | ✅ 无跨 checkout 注入 |
| `foreign_checkout_paths_in_sys_path` | **[]**（`clean: true`） | ✅ |

**结论：本轮后端回归环境干净，无其他 checkout 的 venv/.pth 污染。**
因此修复师报告的「`shine-multiepisode-autopilot-20261114\backend\.venv` 混进 sys.path 导致
`test_v13_preparation.py` 11 个用例红」是**他那边的环境问题，不是产品缺陷**；本机同文件 69 个用例全绿（见 §3）。

---

## 2. 后端全量：8 分片并行（AGENTS.md 官方口径）

命令（Git Bash `C:/Program Files/Git/bin/bash.exe`，脚本 `run_backend_shards_final.sh`）：

```
export CODEBUDDY_SAFE_DELETE_ENABLED=0
ls backend/tests/test_*.py > /tmp/final-tests.txt      # 238 个文件
split -n r/8 /tmp/final-tests.txt /tmp/final-chunk-    # 按文件 round-robin 均分
for f in /tmp/final-chunk-*; do
  ( "$PY" -m pytest $(tr '\n' ' ' < "$f") -q -p no:cacheprovider > "$OUT/$b.log" 2>&1; ... ) &
done
wait
grep -H "^FAILED\|^ERROR" "$OUT"/final-chunk-*.log
```

| 分片 | 文件数 | 退出码 | FAILED/ERROR |
| --- | --- | --- | --- |
| final-chunk-aa | 30 | **0** | 0 |
| final-chunk-ab | 30 | **0** | 0 |
| final-chunk-ac | 30 | **0** | 0 |
| final-chunk-ad | 30 | **0** | 0 |
| final-chunk-ae | 30 | **0** | 0 |
| final-chunk-af | 30 | **0** | 0 |
| final-chunk-ag | 29 | **0** | 0 |
| final-chunk-ah | 29 | **0** | 0 |
| **合计** | **238** | **全部 0** | **0（扫描无任何 FAILED/ERROR 行）** |

**判定：后端全量通过**（口径＝8 片退出码全 0 且各片日志无 FAILED/ERROR 行，未使用 pytest 汇总行判定）。
日志：`backend-shards/final-chunk-{aa..ah}.log`；退出码表：`backend-shards/shard-exit-codes.tsv`。

**对比上一轮**：同一口径在 `afaef8a` 上是 4 片红、33 条 `NODE_PACK_DIRECTORY_MISSING`；门禁修复后**归零**。

---

## 3. 额外必跑：worker / 质量门 / 修复引擎 7 个文件

**触发判定**：本轮改动**未触及** `client_review_identity` 钉住文件
（`git diff --stat afaef8a <门禁分支> -- backend/shine/worker.py backend/shine/preproduction.py
backend/shine/comfyops/handoff backend/shine/agents backend/shine/quality` → **空**），
故严格意义上不触发；但按总控指定 `test_v13_preparation.py` 为重点，7 个文件全部补跑。

```
$env:CODEBUDDY_SAFE_DELETE_ENABLED='0'
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_offline_e2e.py \
  backend/tests/test_comfy_completion_window.py backend/tests/test_production_series.py \
  backend/tests/test_advisory_queue.py backend/tests/test_comfy_handoff_journal.py \
  backend/tests/test_v13_preparation.py backend/tests/test_h3_compiler.py -q -p no:cacheprovider
```

**结果**：7 modules，`[shine-tier] total=194 selected=194`，**退出码 0**，零 FAILED/ERROR。

| 文件 | cases |
| --- | --- |
| `test_v13_preparation.py` | **69（全过）** |
| `test_comfy_handoff_journal.py` | 38 |
| `test_production_series.py` | 33 |
| `test_h3_compiler.py` | 26 |
| `test_comfy_completion_window.py` | 19 |
| `test_advisory_queue.py` | 7 |
| `test_offline_e2e.py` | 2 |

---

## 4. 前端全量

| 命令 | 结果 | 退出码 |
| --- | --- | --- |
| `npm.cmd run build`（`tsc -b && vite build`） | 1653 modules，✓ built in 1.79s | **0** |
| `npm.cmd test -- --no-file-parallelism` | **Test Files 69 passed / 1 failed（70）**；**Tests 375 passed / 1 failed（376）** | **1** |

**失败文件**：`src/test/setupNodePackNotes.test.tsx`（门禁分支最新提交 `1ae4669` 新增）。
日志：`frontend-final-vitest.log`

### 4.1 失败根因：两层，第一层是测试桩，第二层是**真实产品缺陷**

**第一层 · 测试桩不足（测试缺陷）**
该文件只桩了 6 个接口（system/status、setup/readiness、setup/profiles、setup/runtime、setup/services/validate、setup/configuration），
且把 `setup/readiness` 覆盖成 `{ layers: [] }`（缺 `L2_files_verified`）。
向导因此在 models 阶段就 `stopWith('models', '模型清单核对失败：…')` 提前返回，
**根本走不到 `/setup/configuration`**，note 自然不会出现。
既有 `realWizard.test.tsx` 的 `fullPath()` 已有完整 happy-path 桩（install-plan / next-instance-plan / 各 approve / 各 execute / installation-attempts），
新测试应复用它。

**第二层 · 真实产品缺陷（关键）**
我用**完整 happy-path 桩**写了探针（候选树内 `src/test/r3qaNodePackDiag.test.tsx`）复跑，实测：

```
DIAG configuration_calls=1          ← /setup/configuration 确实被调用
DIAG body_has_KJNodes=false         ← 屏上没有节点包名
DIAG body_has_install_hint=false    ← 屏上没有「安装与下载」提示
DIAG stages_text=… 登记生成方案并验证本机服务 **H3 服务与工作流已验证**。…
DIAG result_text=真实制作已准备好。现在可以返回作品，开始生成影片。
```

**覆盖点（已钉到行）**：`frontend/src/components/RealProductionWizard.tsx`

- `:601-605` 设置降级提示：`setStage('workflows', { status: 'warn', note: '运行配置已生成，但 N 个节点包没有装（…）；本次运行会把它们禁用，请到「安装与下载」补装后再重新生成运行配置。' })`
- `:636` **覆盖**：`setStage('workflows', { status: 'running', note: '正在检查本机 H3 服务与工作流…' })`
- `:647` **再次覆盖**：`setStage('workflows', { status: 'ok', note: 'H3 服务与工作流已验证。…' })`

→ 降级诊断在**同一次向导运行内被后续两次 setStage 覆盖**，屏上最终什么都不剩。
**结论：「降级后用户可见」这条能力目前不成立——降级对用户仍然是静默的。**
（`SetupPanel.tsx:553-558` 的回显路径是同步函数内的返回值，不受此覆盖影响；
但向导这条路径是用户真正会走的那条。）

---

## 5. 只读复核

### 5.1 新增复核 (a)：`node_pack_notes` 是否有用户可见消费方？—— **未成立**

- 数据链路**已接通**：后端 `deployment.py:929/1032` 写入 → `deployment.py:1182-1199` 进入 readiness **L3 层** `notes`；
  前端 `RealProductionWizard.tsx:591-606`、`SetupPanel.tsx:553-558` 都读取并拼装文案。
- 但**用户看不到**：见 §4.1 第二层——向导路径上提示被 `:636`/`:647` 覆盖。
- → 记为**不一致条目**（不是「过门禁式」改动，但确实等于「降级静默通过」）。
- 修法方向：降级提示必须**独立于阶段提示**承载（例如独立的状态条/警示行，或把 note 合并进后续阶段文案），
  并让 `setupNodePackNotes.test.tsx` 用完整 happy-path 桩锁住它。

### 5.2 新增复核 (b)：`diagnose` 是否真的无法在生产被打开？—— **成立**

| 检查点 | 代码 | 判定 |
| --- | --- | --- |
| 默认值 | `config.py:57` `node_pack_gate: str = "enforce"` | ✅ 生产默认拦 |
| 环境变量 | `config.py:111-113`：**刻意不从环境读取**——「a machine-wide variable could silently turn the production gate off」 | ✅ 机器级 env 无法关掉生产门禁 |
| 判定条件 | `deployment.py:902` `if getattr(settings, "node_pack_gate", "enforce") != "diagnose": raise HTTPException(409, …)` | ✅ 只有**恰好** `"diagnose"` 才降级，其它任何值都 enforce |
| 谁能设 | 只有进程内显式设置（`config.py:55-56` 注释：仅离线测试夹具） | ✅ 生产路径不可绕过 |

### 5.3 沿用复核（D1 / D3 / D2·D4 映射与自环 / D5）—— 与代码一致

门禁分支与素材线分支**均未触碰**这些文件，上一轮逐条核对结论继续成立：
D1（`OutlineConfirmationPanel.tsx:110-117/228-234` 三态化 + 真实阻塞理由 + 「去配置文本模型」出口）、
D3（`errorActions.ts` 三码齐备 + `StreamlinePanels.tsx:240/483/848` 必做块）、
D2/D4（`plan-stage` 映射 + `selfLoopAction=creative-stage` + `App.tsx` 自环回退，映射单测保留未放宽）、
D5（`JourneyRail.tsx:138-139`）。浏览器实景 19/19 与定向 2 files/6 tests 的证据见上一版报告。

---

## 6. 结论

### 6.1 四类结论

**已实现**
- 节点包门禁双模式：`enforce`（生产默认，缺包仍 409）/ `diagnose`（仅进程内显式设置，降级为可诊断提示）
- `diagnose` **不可被环境变量绕过**，生产路径不可绕过 ✅
- 降级缺失记入 `node_pack_notes`，并进入 readiness **L3 层**
- 并行下 `int('')` 竞态修复（`37f10db`）
- 素材线 A1（项目分配页回填目标作品）、A6（重复登记具名拒绝）

**离线已验证**
- 后端全量 8 分片：**238 文件、8 片退出码全 0、零 FAILED/ERROR**
- worker/质量门/修复引擎 7 文件：194 tests，退出码 0（含 `test_v13_preparation.py` 69 cases 全过）
- 前端 `npm.cmd run build`：退出码 0
- 环境取证干净，无跨 checkout 污染

**待真机 H3 验证（未运行）**
- 真实 H3 渲染 / 真实出图 / 成片验收
- VLM 正负校准（本轮未改 `client_review_identity` 钉住文件，不触发重校准义务；历史校准仍未做，**不得声称已校准**）
- 六项文本模型链：① 大纲提取与确认 ② 分集确认 ③ 分镜草案与应用
  ④ `EPISODE_PLAN_NOT_MATERIALIZED` 双台账复现 ⑤ 一键开拍空槽位口径 ⑥ 幂等键冲突
- Windows 安装包 `build_test_package.ps1 -Verify` + 干净机安装；启动器四条验收入口

**阻塞**
1. **前端全量红**：`setupNodePackNotes.test.tsx` 失败（375 passed / 1 failed）。
2. **真实产品缺陷**：向导路径上节点包降级提示被 `RealProductionWizard.tsx:636/647` 覆盖，
   **用户看不到「缺哪几个节点包、渲染器会禁用它们」** → 降级对用户仍等于静默通过。
   （对应总控要求复核的 (a) 项：**不成立**）

### 6.2 总判定：**FAIL（不通过，需退回）**

**理由**：

1. 后端全量已全绿（8 片退出码全 0、零 FAILED/ERROR），此前 33 条 `NODE_PACK_DIRECTORY_MISSING` 归零，
   环境取证证明本轮结论不受跨 checkout 污染影响 —— **后端这一侧通过**。
2. 前端全量 `npm.cmd test -- --no-file-parallelism` **退出码 1**（1 文件 / 1 用例失败），
   AGENTS.md 的前端全量门禁未满足 → **不能签发整体通过**。
3. 且该失败指向一个**真实产品缺陷**（降级提示在向导内被覆盖，用户看不到），不是单纯的测试桩问题，
   必须由修复师修产品代码 + 修测试桩后重跑。
4. 另：本报告的结论取自**候选树**（两个 PR 当时均未合入 main）。合入 main 后需在主干上重跑
   后端 8 分片 + 前端 build/全量 vitest，作为最终签发依据。

**退回对象**：修复师（bot_ffa031e389fd）　**同步总控**：Chief（bot_a48efeacd5bc）

---

## 7. 证据清单

| 内容 | 路径 |
| --- | --- |
| 环境取证（脚本 / 输出） | `.local-acceptance/r3-final-20261115-qa/env_forensics.py`、`env-forensics.json`、`env-forensics-candidate.json` |
| 后端 8 分片日志 | `.local-acceptance/r3-final-20261115-qa/backend-shards/final-chunk-{aa..ah}.log` |
| 逐片退出码表 / 文件清单 | `.../backend-shards/shard-exit-codes.tsv`、`shard-files.tsv` |
| 分片脚本 | `.../run_backend_shards_final.sh` |
| worker/质量门 7 文件 | `.../backend-worker-quality-extra.log` |
| 前端全量 vitest | `.../frontend-final-vitest.log` |
| 诊断探针（证明覆盖缺陷） | 候选树 `frontend/src/test/r3qaNodePackDiag.test.tsx`（我的只读探针，未进产品仓库） |
| 失败测试本体 | `frontend/src/test/setupNodePackNotes.test.tsx`（门禁分支新增） |
| 上一轮浏览器实景与定向证据 | `.local-acceptance/r3-20261115-qa/`（19/19 截图、verify_director_r3.json） |

---

## 8. 纪律与边界

- **全程只读**：未修改任何产品代码；探针测试只写在我自己的候选 worktree 内，未进产品仓库。
- **未执行 `npm ci` / `npm install`**（任何目录均未执行）。
- **未启动、停止或抢占任何实例**：未访问 8010/8020/5180/5190/8000 上的实例；8030/5200 未使用；
  未使用 `scripts/start_shine.ps1`。
- **共享 `frontend/node_modules` 当前是一个 Junction**，指向
  `.local-acceptance/r1-20261115-assets/wt-a1/frontend/node_modules`（另有一份
  `frontend/node_modules.broken-20261115/` 留在仓库内未跟踪）。两者都在 `frontend/` 下，
  会被打包/扫描扫到，建议由负责人清理并恢复为常规目录。
- 遗留 worktree 目录（git 登记已移除，但**永久删除被本机安全策略拦截**，需人工清理）：
  `F:\ClaudeWorkSpace\projects\.qa-base-20261115`、`.qa-r3-clean-20261115`、`.qa-candidate-20261115`。
- 诚实边界：本机 `h3_connected=false`、`production_mode=BLOCKED`；
  本报告全部结论为**离线 / mock** 结论，**不构成真实 H3 验收、不构成成片验收**。
