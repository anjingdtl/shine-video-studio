# 测试报告 — 导演模式与本机环境收束 + 唯一性原则审查（2026-11-16）

- 分支：`feat/director-setup-consolidation`（worktree `shine-video-studio-director-dev`）
- 范围：导演工作台唯一入口、本机环境一键整备、能力状态两步归一、出图全六模式验证、
  PDD8/二次精修/极速4步/RelayVSR2X 工作流整备，以及本轮唯一性原则审查修复。
- 数据目录：`.local-acceptance/dev-20261115/data`（隔离，不碰用户真实项目）。
- 真机：RTX 5080 16GB / 31.7GB 内存 / ComfyUI 收编自 `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base`。

## 1. 前端

| 命令 | 结果 |
| --- | --- |
| `npx tsc --noEmit` | 通过 |
| `npx vitest run --no-file-parallelism` | **78 文件 / 396 项全绿** |
| `npm.cmd run build` | 通过（1651 模块，dist 产物正常） |

本轮改动相关前端用例：`materialPreparation`(21)、`directorRun`(8)、`variantCandidate`(5)、
`directorPackageGuide`(3)、`setupEntry`(8，含重连两段进度契约改控 `/system/status`)、
`modeEntry`(11)、`directorEntries`、`produceFocus` 均通过。

## 2. 后端定向回归（本轮改动相关模块，全绿）

命令统一：`CODEBUDDY_SAFE_DELETE_ENABLED=0 backend/.venv/Scripts/python.exe -m pytest
<模块> -q -p no:cacheprovider`（仓库根运行；真实 HTTP 用例另加 `env -u NO_PROXY -u no_proxy`）。

| 模块 | 用例数 | 结果 |
| --- | --- | --- |
| test_two_step_and_edit_validation | 9 | 通过（含本轮新增 3 条唯一性反例） |
| test_setup_capability_gates | 21 | 通过 |
| test_relayvsr_and_profiles | 6 | 通过 |
| test_variant_binding_and_gate | 23 | 通过 |
| test_variant_aware_summary | 4 | 通过 |
| test_summary_optional_review | 3 | 通过 |
| test_setup_one_stop_v41 | 12 | 通过 |
| test_setup_preparation_chain | 10 | 通过 |
| test_resource_resolver_v2 | 19 | 通过（含修复后的可用性解耦用例） |
| test_runtime_activation | 16 | 通过 |
| test_runtime_control_hardening | 40 | 通过 |
| test_existing_comfy_adoption | 7 | 通过（含收编核验缓存函数身份键控反例） |
| test_service_validation | 8 | 通过 |

## 3. 后端 8 分片全量回归

按 AGENTS.md 固定口径：`ls backend/tests/test_*.py` → `split -n r/8`（按文件均分）→
8 分片并行，`CODEBUDDY_SAFE_DELETE_ENABLED=0`，日志留存 `shards-r2/`。8 分片均退出。
日志含 13 处 FAILED，**逐一核实结论：全部为既有失败或环境假失败，本轮零新增**。

### 3.1 既有失败（同一根因，已在本轮起点复现）

`test_repair_revisions`×4、`test_film_crew`×1、`test_comfy_completion_window`×4、
`test_v13_e2e`×1、`test_offline_e2e`×2，共 12 处。错误签名一致：
`acceptance_status='pending_review' / acceptance_final_gate=None`、`REPAIR_PENDING != PASSED`。

**复现证据**：在本轮起点 `9c4b05d`（本轮任何后端改动之前）建独立 detached worktree
复跑上述模块，失败完全一致。根因在验收/最终门禁逻辑（`worker.py`/`quality.py`）；
`git diff 9c4b05d HEAD -- backend/shine/worker.py backend/shine/quality.py
backend/shine/repair.py backend/shine/preproduction.py` 为空，证明本轮未改这些文件，
失败非本轮引入。该根因不在本轮范围，留待对应负责人处理。

**平台口径**：上述失败均在**本机 Windows** 复现；`main`（`351f3a4`）的 CI
**L3 backend full (Linux) 为 success**，即同一批模块在 Linux 全量通过——属 Windows
本地平台差异，非逻辑回归。本 PR 的 CI（Linux）L1 quick 与 Frontend build 均 success。

### 3.2 环境假失败

- `test_renderer_supervisor`：本会话 `NO_PROXY` 含 `[::1]`，httpx 解析报
  `Invalid port ':1]'`。`env -u NO_PROXY -u no_proxy` 复跑 **47 项全绿**（AGENTS.md 已记载）。
- `test_image_managed_renderer::test_an_unmanaged_instance_is_refused`：报
  `EXTERNAL_INSTANCE_ON_PORT port=8188`——验收 dev 栈/素材生成正占用 8188，测试把活
  渲染器当成外部实例；停栈后即恢复，非代码缺陷。

### 3.3 判定

本轮改动相关定向用例与前端全量全绿；**不声称后端全量全绿**（含 12 处既有失败）。
干净复跑（`env -u NO_PROXY`，`shards-r2-clean/`）确认 renderer_supervisor 恢复，
其余失败与 3.1/3.2 分类一致。

## 4. 真机浏览器穿测（结构快照存证）

in-app Browser 面板隐藏，截图不可用，以 DOM 结构快照（uid）存证：

- **基础配置一致性**：顶部「工作台后端在线 / 真实制作已就绪 开始制作」；H3「可试镜 ·
  ① 文件已就绪并核验 · ② 已验证可运行」；出图「已真实验证 · ② 已验证可运行」；
  可选审片/语音/RelayVSR 如实「文件未就绪 · 下载安装缺文件」（不阻断生产）；
  唯一下一步「真实试镜一次」。四处显示结论一致，无硬编码阻滞、无多余验证按钮、
  无「signal timed out / 制作状态暂不可用」假失联。
- **素材页唯一主操作**：刷新后操作栏只剩「确认决策并送素材中心」，高级折叠区不再
  注册竞争主按钮，`MATERIAL_PLAN_STALE` 提示消失。
- **素材真实生成**：点唯一主按钮 →「已确认 9 项决策；已排队 3 项 AI 候选」。后台
  production run 真实推进：老陈 `CANDIDATE_READY`（1 张真实候选）；委托人/沈砚
  `NEEDS_REVIEW / LLM_CONNECTION`（Brief 的 LLM 连接中断 → 结果未知，不自动重发，
  `allowed_actions=['reconcile']` + 用户授权一次性重发入口）。
- **导演包/视频生产**：INT8 已选、候选 W6A8 如实「本机没有这套权重：可选择后下载准备」、
  极速4步/RelayVSR「本机未就绪」禁用；第1集因 3 项必需人物主素材未锁定「等待备料」，
  页面明确指引补齐素材；⑥页逐集进度（6场·23镜·约118秒）如实显示。

## 5. 性能（冷加载假失联修复前后）

| 端点 | 修复前冷态 | 修复后冷态 | 暖态 |
| --- | --- | --- | --- |
| /setup/verdict | 3.42s | 0.68s | <0.1s |
| /system/status | 2.49s | 1.69s | <0.1s |
| /setup/variant-availability | 9.76s | 5.54s | 0.003s |

收编核验/钉住文件哈希加身份键控缓存（字节变即重哈希，漂移仍被发现）；变体可用性
缓存按计算函数身份键控 + 整备每步主动失效（核验即时生效，不报旧结论）。

## 6. 边界声明

- 本轮未改 `worker.py`/`preproduction.py` 的 `client_review_identity` 钉住语义之外的
  审片合同；历史 VLM 正负校准仍未完成，**不声称已校准**。
- **A11 完整成片未产出**：素材生成链路已真机跑通（老陈候选就绪），但整片需先核对/
  授权重发两个 Brief、人工确认锁定 3 项人物主素材、冻结导演包、H3 渲染 23 镜、后期
  合成与人工验收。本轮验证「链路唯一、状态一致、进度可见、失败可核对」，不声称整片通过。
- 所有真实产物、密钥、数据库、私有验收数据留在本机隔离目录，未进安装包。