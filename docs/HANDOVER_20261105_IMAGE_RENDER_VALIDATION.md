# 交接提示词：真实出图验证与剩余缺口（2026-11-05）

> 这份文件是**给下一个 agent 的作业说明**，可以直接整段粘给它。
> 它假定你在同一个仓库、同一台机器上继续；每条结论都有文件或命令可复核。

---

你接手的是 `anjingdtl/shine-video-studio`（私有仓库）的"导演 AI 素材准备"收尾工作。
总体目标没变：**以全 Pass 为目标，继续修复存在缺口**，直到计划
`docs/DIRECTOR_AI_MATERIAL_PREPARATION_PLAN_20261104.md` 里剩下的条目都有真实证据。

## 1. 当前状态（务必先复核，再动手）

| 项 | 值 |
| --- | --- |
| 仓库 | `F:\ClaudeWorkSpace\projects\shine-video-studio`，分支 `main`，工作区干净 |
| HEAD | `718b960 docs(material): record the managed image renderer round` |
| 远端 | `origin/main` 已同步（`5c4842f..718b960` 已推送） |
| 本机 | Windows 11 / Python 3.12.13 / RTX 5080（16303 MiB 总量，15030 MiB 空闲） |
| 既有 ComfyUI | `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI`（含 `custom_nodes/shine_comfy_handoff`） |
| 权重 | Qwen-Image-2.1 三份齐备（16.10 GiB）；H3 权重齐备（含 `clip/`、`loras/`、`pdd_acc/`、`vae/MiniMax-H3(1)/` 子目录） |

本轮之前 16 个提交也只在本地，本轮已一并推送；**不要再重复推送历史**。

## 2. 必须先读的规则（`AGENTS.md`）

按顺序读：`AGENTS.md` 全文 → `docs/ACCEPTANCE_20261105_MANAGED_IMAGE_RENDERER.md`（本轮证据）
→ `docs/PROGRESS_DIRECTOR_AI_MATERIAL_PREPARATION.md`（进度与下一步）→
`docs/ADVERSARIAL_REVIEW_MATERIAL_PREPARATION_20261105.md`（前一轮对抗性复核）。

最容易被违反的几条：

- 受管渲染器属于工作台：**禁止用脚本或手工启动 ComfyUI**，必须走
  `shine/comfyops/image_renderer.py` 这条受管路径，否则会留下工作台无法证明归属、
  也无法释放的孤儿渲染器。
- 真实出图只认"工作台自己拥有、且本次真的启动"的渲染器：台账字段
  `renderer_owned`。替身渲染器的字节只能证明链路可用，晋升出来的素材一律
  `offline_only`。
- 禁止把"路由返回 202""离线测试通过""权重已就位"当成真实出图验收。
- 报告要分层：IMPLEMENTED / OFFLINE_VERIFIED / REAL_QWEN_VERIFIED /
  REAL_H3_VERIFIED / HUMAN_REVIEW / WINDOWS_AND_PACKAGE / BLOCKED。
- 用户自己的 4 个未跟踪文件（`.zcode/`、`docs/DIRECTOR_AI_MATERIAL_PREPARATION_PLAN_20261104.md`、
  `docs/H3_MV_MANSIONS_15S_T2VA_SCRIPT.md`、`docs/LOCAL_AGENT_MATERIAL_PREPARATION_PROMPT_20261104.md`）
  **不得删除、覆盖或纳入提交**。
- 提交按明确路径 `git add`，不要 `git add -A`；安装包只能从**已提交树**构建
  （`pwsh -File scripts\build_test_package.ps1 -OutDir releases -Verify`）。

## 3. 已经做完并验证过的（不要重做）

- 素材导演链（A–D 阶段）、四层出图就绪度、素材完成判定、出图能力卡、受管实例收编与检测。
- **本轮新增**：受管出图渲染器（`backend/shine/comfyops/image_renderer.py`）、
  `POST /setup/image-runtime/start` 与 `/stop`、就绪报告里的 `renderer` 段、
  `renderer_owned` 证据分级、GPU 租约优先的执行顺序。
- 本轮验证：定向后端回归 **136 passed（EXIT=0）**；前端 **18 文件 / 130 测试通过**；
  `npm.cmd run build` 成功；Ruff **All checks passed**。

## 4. 还没做的（按优先级）

### P0 —— 真实三类小样出图（本轮唯一的功能缺口）

必须**用受管路径**，不能手工起 ComfyUI。步骤（每一步都要留下请求/响应作为证据）：

1. `GET /api/v1/comfyops/execution-context` → 取 `configuration_hash`。
2. `POST /api/v1/preparation/approval-grants`（`action=scope_configure`，
   `target_id=project_id`，**必须带 `context`**，否则 422 `EXECUTION_CONTEXT_REQUIRED_ONLY_FOR_VOICE`）。
3. `POST /api/v1/comfyops/scope`（roots/ports/previous_hash/grant_id/snapshot）；
   哈希变了会返回 `PLAN_HASH_CHANGED`，**重读哈希再试**，不要绕过。
4. `POST /api/v1/setup/existing-installations/inspect` → snapshot。
5. `POST /api/v1/setup/existing-installations/verify` → 逐字节核验 H3 权重（耗时几分钟，耐心等，不要并发重复提交）。
6. `POST /api/v1/setup/existing-installations/adopt` → 拿到受管 instance_id。
7. `POST /api/v1/setup/configuration` → 生成受管配方（`comfyops/setup/managed_recipe.json`）。
8. `POST /api/v1/setup/image-runtime` → 绑定 instance_id / base_url=`http://127.0.0.1:8188` /
   model_root=既有 ComfyUI 的 `models` 目录。
9. `POST /api/v1/setup/image-runtime/start` → **受管启动**（队列先关闭后放行）。
10. `GET /api/v1/setup/image-readiness` → 第三层应变成 `GRAPH_VERIFIED`（活体 `object_info` 校验）。
11. `POST /api/v1/setup/image-runtime/validate`（需一条**已批准的 asset 用途权利记录**）→ 三类小样。
12. Worker 渲染 → 取回声明输出节点 → 字节校验 → 技术质检 → 第四层由台账推导。
13. 收尾：`POST /api/v1/setup/image-runtime/stop` 或 `POST /api/v1/setup/managed-processes/release`
    回收显存。**不要留下占显存的渲染器。**

> 驱动脚本在本机 `.local-acceptance/real_image_validation.py`（被 gitignore，克隆后没有；
> 步骤已完整写在上一步里，可自行重建）。

**先决条件**：现在跑着的工作台（API 8133 / Web 5193，数据目录 `.local-acceptance/live-browser`）
是**本轮提交之前**启动的，**没有** `/setup/image-runtime/start` 与 `/stop`。
必须先用新代码重启：

```powershell
backend/.venv/Scripts/python.exe scripts/stop_shine.py --data-dir .local-acceptance/live-browser
# 再用产品启动器起在同样的端口，带上管理员扫描白名单：
$env:SHINE_COMFY_PROBE_ROOTS = '["F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI"]'
$env:SHINE_COMFY_PROBE_PORTS = '[8188]'
pwsh -File scripts/start_shine.ps1 -ApiPort 8133 -WebPort 5193 -DataDir .local-acceptance/live-browser
```

### P1 —— 浏览器 E2E 闸门仍红

`scripts/verify_v13_web.py` 本轮修完两段（实例生命周期段"批准后必须执行"、
单镜头提交段对齐当前界面 + 单一可切换弹窗策略），脚本推进到更靠后的位置后**仍有一处待定位**。
跑法：

```powershell
$env:TEMP = 'F:\ClaudeWorkSpace\projects\shine-video-studio\.local-acceptance\e2e-tmp'
$env:TMP  = $env:TEMP
uv run --project backend --with playwright==1.62.0 python scripts/verify_v13_web.py --screenshot .local-acceptance/e2e-shot.png
```

（TEMP/TMP 必须指向非系统盘，否则 `CHOOSE_DEPLOYMENT_DIRECTORY_OUTSIDE_SYSTEM_DRIVE`。）
改法原则：按**当前界面**重写断言，不要放宽断言，也不要恢复界面上已不存在的按钮。

### P2 —— 全量回归与安装包

- 全量后端：`uv run --project backend --locked pytest -c backend/pyproject.toml backend/tests`
  （上一轮基线 1648 例 EXIT=0；本轮**被中止**，不能引用为本轮结论）。
- `uv run --project backend --locked ruff check backend/shine backend/alembic backend/tests scripts/verify_v13_web.py`
- Alembic：本轮**没有新增迁移**，head 仍是 `f170a0010001`；用隔离数据目录跑
  `alembic upgrade head` + `alembic check` 确认无漂移。
- 前端：`npm.cmd test`、`npm.cmd run build`。
- 安装包：`pwsh -File scripts/build_test_package.ps1 -OutDir releases -Verify`，
  只能从**最终已提交树**构建，保留旧包，不打包权重/数据库/密钥/生成媒体。

### P3 —— 只能由人完成的环节

候选素材的选用与晋升、构图与美术评审、真实 H3 引用新素材。
这些要做的是：把界面上的证据摆清楚，让用户能判断，并如实记录"待人工评审"，
**不要替用户下结论**。

## 5. 已知陷阱

- `POST /comfyops/scope` 的 `previous_hash` 必须在**签发授权前**读取；
  授权签发后配置哈希会变，报 `PLAN_HASH_CHANGED` 是保护，不是 bug。
- 出图运行时只接受**受管实例**；绑非受管实例会得到
  `IMAGE_RUNTIME_INSTANCE_NOT_MANAGED`。
- 第四层就绪度要求 attempt 的 `renderer_owned` 为真；用替身渲染器测出来的成功
  **不会**解锁该层，这是设计如此。
- 图片项在共享 GPU 租约内启动渲染器：租约被视频系列占着时是"等待"，不是失败。
- 下载/校验大文件后必须逐文件核对**字节数 + SHA-256**；
  停止下载要杀父进程（脚本里的重试循环会在 curl 被杀后重新拉起）。
- `docs/upstream-audit.md` 与实现要保持一致（本轮已把"三个必需权重 16.10 GiB"
  与"替身渲染器不是真机"写清楚）。

## 6. 交付纪律

- 每个节点改完就跑后端相关批次 + 前端 build/lint，并报告**确切命令与失败**。
- 提交按节点拆分，提交信息写清"为什么"，不写"已完成"式空话。
- 收尾时更新 `docs/PROGRESS_DIRECTOR_AI_MATERIAL_PREPARATION.md` 与
  `AGENTS.md`（把这一轮的新规则固化下来），并在 `docs/` 留一份验收记录。
- 最终报告按分层给出结论，**没做的就说没做**。
