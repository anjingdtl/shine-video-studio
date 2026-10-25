# V1.3 本地集成收尾报告

日期：2026-10-25（Asia/Shanghai）。分支 `feature/v13-local-h3-integration`，基线
`63929f49c98e205776da5e2287cfe368cf87de75`（= `origin/main`）。本轮提交
`fc75693d9aabb44cee92ca40eb97838e1ebd674c`（2026-10-25 17:49:36），随后提交
`2df8aff9f62209b6202bbd839fd914989499d5b2`（2026-10-25 18:01:13）修复 `LLM_FORMAT`
的根因（见 5.1）。同日 11:30（UTC）操作员 `shine-owner` 完成了三个真实镜头的正式
逐项人工审片（见 7.2 与 8.1）；验收证据快照
`.local-acceptance/evidence/acceptance-evidence.json` 于 11:31:09 UTC 采集，其记录的
HEAD 为 `8bf956fc527c9865fdbf779409d8c1d683064f64`。

关联文档：[本地环境验证报告](LOCAL_ENVIRONMENT_VALIDATION.md)、
[真实 H3 验收报告](LOCAL_REAL_H3_ACCEPTANCE.md)、[操作员指南](operator-guide.md)。

证据：`.local-acceptance/evidence/v13-feature-verification.md` / `.json`（46 项能力面
检查）、`.local-acceptance/evidence/acceptance-evidence.json`、
`.local-acceptance/h3run/shine.db`（只读查询）。

---

## 1. 本轮交付摘要

V1.3 云端收尾报告（[V1.3_CLOUD_IMPLEMENTATION_REPORT.md](V1.3_CLOUD_IMPLEMENTATION_REPORT.md)）
交付的是软件，真实设备侧的项全部标为 `PENDING_LOCAL_ENV` / `PENDING_LOCAL_GPU`。本轮在
操作员机器上把这些 `PENDING` 逐项跑过一遍，结论是：

| 面 | 本轮结果 | 类别 |
| --- | --- | --- |
| A 环境中心与 ComfyOps | 读接口全部可用；`execution-context` 与 `publish` 因缺操作员凭据被正确拒绝（403）；本地探测范围未配置（409） | `VERIFIED_LOCAL_ENV` |
| B 工作流能力中心 | 档案、draft、live `object_info` 校验、fake-execute 全部可用；`publish` 需操作员 | `VERIFIED_LOCAL_ENV` |
| C 授权与候选资产 | 权利记录可创建；候选导入因 `LICENSE_NOT_APPROVED` 被契约拒绝（409），提升需操作员 | `VERIFIED_LOCAL_ENV` |
| D 声音工作台 | 档案列表为空；导入因 `LICENSE_NOT_APPROVED` 拒绝；冻结需操作员 | `VERIFIED_LOCAL_ENV` |
| E 专业剧组 | 目录与规则包读取正常；四个角色的真实调用被 `LLM_FORMAT` 拒绝（422），三个角色因上游未产出而未触达 —— 该根因随后被定位并修复（见 5.1），修复后 `scriptwriter` 成功产出 `ScriptBreakdown`，其余角色的复验仍未做 | `BLOCKED / PENDING` |
| F 预制作分析与冻结 | 分析、需求、readiness、gaps 全部可用并正确判定 `BLOCKED`；冻结需操作员 | `VERIFIED_LOCAL_ENV` |
| 真实 H3 生成 | 三个镜头真实渲染通过技术门；A、B 在规则包与 Style Bible v2 之后重渲染 | `VERIFIED_REAL_H3` |
| 交付装配 | 真实 EDL → 成片，`review_state = all_passed` | `VERIFIED_REAL_H3` |
| 恢复与 GPU 调度 | 真机缺陷与收尾审片阶段的缺陷已修复并有回归 | `IMPLEMENTED` / `TESTED_OFFLINE` |
| 正式逐项人工审片 | 2026-10-25 11:30（UTC）由操作员 `shine-owner` 通过真实操作员 API 完成并签署；语义 / 风格 / 终审门与 B 的音频门由**人工**关闭，不是模型覆盖 | `VERIFIED_MANUAL` |

能力面验证脚本共 **46** 项检查，结果分布：

| 结果 | 次数 | 含义 |
| --- | --- | --- |
| `ok` | 27 | 正常返回 |
| `refused_operator` | 6 | 需要人工操作员授权（HTTP 403），属正确产品行为 |
| `refused_contract` | 8 | 产品按业务契约拒绝（HTTP 409 / 422） |
| `failed` | 5 | 失败或因上游被拒而未触达 |

### 1.1 代码变更

相对基线 17 个文件、+3004/−40 行：

| 类别 | 文件 |
| --- | --- |
| 交付装配（新增） | `backend/shine/media/assembly.py`（328 行）、`backend/shine/api_delivery.py`（155 行） |
| 真机缺陷修复 | `backend/shine/worker.py`（+124）、`backend/shine/comfy/client.py`（+73） |
| 工作流档案 | `scripts/build_h3_workflow_profiles.py`（449 行）、`config/workflows/ref2va-pdd8-2img.json`、`ref2va-pdd8-2img-1aud.json`、`ref2va-pdd8-upscale-2img.json` |
| 进程管理 | `scripts/start_shine.ps1`（+84）、`scripts/stop_shine.py`（新增 124 行） |
| 收尾审片阶段 | `scripts/start_shine.ps1`（拒绝占用端口 + 记录真实监听者 `listener_pid`）、`scripts/stop_shine.py`（结束监听者 + 端口仍被占用告警） |
| 回归测试 | `backend/tests/test_comfy_completion_window.py`（2）、`test_gpu_release_confirmation.py`（6）、`test_delivery_assembly.py`（6）、`backend/tests/fake_http.py`（+26/−） |
| 其他 | `backend/shine/main.py`（挂载交付路由）、`scripts/register_ref2va_profiles.py`（+3）、`.gitignore`（+1，排除本地验收目录） |

## 2. A 面：环境中心与 ComfyOps

验收项目 `1f743836-a00c-4787-8343-4f1a24ed9135`，参照档案
`18e2f8d3-ca1d-4f0d-83ae-0bffc1b4b5f2`。

| 检查项 | 方法 | HTTP | 结果 | 说明 |
| --- | --- | --- | --- | --- |
| system/status | `GET /api/v1/system/status` | 200 | ok | `api_online: true`、`worker_online: true`、`h3_connected: true`、`render_mode: comfyui`、`pending_jobs: 0`、`running_jobs: 0`、`failed_jobs: 0`、`blocked_jobs: 5` |
| workers | `GET /api/v1/workers` | 200 | ok | 12 条 worker 记录，`version: shine-worker/3.0` |
| execution-context | `GET /api/v1/comfyops/execution-context` | 403 | refused_operator | `Local operator credential required; Agent identities are not accepted` |
| instances/discover (local) | `POST /api/v1/comfyops/instances/discover` | 409 | refused_contract | `LOCAL_PROBE_SCOPE_NOT_CONFIGURED` |
| inventory/refresh (local) | `POST /api/v1/comfyops/inventory/refresh` | 409 | refused_contract | `LOCAL_PROBE_SCOPE_NOT_CONFIGURED` |
| inventory | `GET /api/v1/comfyops/inventory` | 200 | ok | `[]` |
| instances | `GET /api/v1/comfyops/instances` | 200 | ok | `[]` |
| models | `GET /api/v1/comfyops/models` | 200 | ok | `[]` |
| nodes | `GET /api/v1/comfyops/nodes` | 200 | ok | `[]` |
| resource-status | `GET /api/v1/comfyops/resource-status` | 200 | ok | `capacity_mb: 16384`、`measurement: ESTIMATED_NOT_MEASURED`；租约 `0702da59…` 状态 `RELEASED` |

说明与边界：

- `system/status` 里的 `h3_connected: true` 只表示**渲染端可达**，不表示 H3 模型或电影
  画质通过；`blocked_jobs: 5` 是被规则/连续性/映射阻断的 job，不是崩溃。
- ComfyOps 的本机清单（inventory / instances / models / nodes）为空，是因为这些表记录
  **由本仓库生命周期管理器创建和拥有**的实例与包；本轮使用的是操作员自有的
  `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base` 安装，本轮**没有**把它纳管，也没有改动它。
  `LOCAL_PROBE_SCOPE_NOT_CONFIGURED` 表示探测范围（`SHINE_COMFY_PROBE_ROOTS` /
  `SHINE_COMFY_PROBE_PORTS`）尚未配置，属配置项待办，不是缺陷。
- `resource-status` 的 `measurement: ESTIMATED_NOT_MEASURED` 表示容量是估算值而非实测；
  本轮三条 H3 租约的实测释放过程见第 6 节。

## 3. B 面：工作流能力中心

| 检查项 | 方法 | HTTP | 结果 | 说明 |
| --- | --- | --- | --- | --- |
| 取档案 | `GET /api/v1/workflow-profiles/18e2f8d3-…` | 200 | ok | `name: local-h3-ref2va-pdd8-2img`、图 hash `86a7624e…` |
| 建 draft | `POST /api/v1/workflow-capabilities/drafts` | 200 | ok | 由已登记档案生成，id `429fd489-111e-40f5-ad8b-5a30c3e62b40` |
| 实时 object_info | `GET http://127.0.0.1:8188/object_info` | 200 | ok | **3091** 个节点类 |
| 校验 | `POST /api/v1/workflow-capabilities/{id}/validate` | 200 | ok | 用实时 `object_info` 校验，无缺失节点/输入 |
| fake-execute | `POST /api/v1/workflow-capabilities/{id}/fake-execute` | 200 | ok | 假执行，产出为 mock，不是真实视频 |
| publish | `POST /api/v1/workflow-capabilities/{id}/publish` | 403 | refused_operator | 需要操作员 |
| 类别目录 | `GET /api/v1/workflow-capabilities` | 200 | ok | 含 `H3_REF2VA`、`H3_I2VA`、`H3_L2VA`、`H3_FL2VA`、`H3_T2VA`、`VLM_REVIEW` 等 |

五个已登记档案（两个 V1.2 历史 + 三个本轮新增 PDD8 系列）全部用实时 `object_info`
校验通过，结果统一为 `{"missing_nodes": [], "missing_inputs": []}`；详见
[环境验证报告第 6 节](LOCAL_ENVIRONMENT_VALIDATION.md#6-工作流档案与实时校验)。

**边界**：`fake-execute` 只验证调度与合同，不产生真实视频；`publish` 未执行，因此本轮
**没有**把任何能力草案发布成生产可用分类。

## 4. C / D 面：授权、候选资产与声音工作台

| 检查项 | 方法 | HTTP | 结果 | 说明 |
| --- | --- | --- | --- | --- |
| 权利记录（asset） | `POST /api/v1/preparation/rights` | 200 | ok | `license_id: USER_OWNED_LOCAL_ACCEPTANCE`，状态 `PENDING` |
| 权利记录（voice_design） | `POST /api/v1/preparation/rights` | 200 | ok | 同上 |
| 权利审批 | `POST /api/v1/preparation/rights/{id}/approve` | 403 | refused_operator | 需要操作员 |
| 候选资产导入 | `POST /api/v1/projects/{id}/candidate-assets/import` | 409 | refused_contract | `LICENSE_NOT_APPROVED` |
| 候选 QC | `POST /api/v1/candidate-assets/{id}/qc` | 404 | failed | 无候选身份：上面的导入因权利记录未经人工批准而被拒；操作员闸门在读取候选行之前生效 |
| 候选提升 | `POST /api/v1/candidate-assets/{id}/promote` | 403 | refused_operator | 同上 |
| 候选列表 | `GET /api/v1/projects/{id}/candidate-assets` | 200 | ok | `[]` |
| 音色档案列表 | `GET /api/v1/projects/{id}/voice-profiles` | 200 | ok | `[]` |
| 音色导入 | `POST /api/v1/projects/{id}/voice-profiles/import` | 409 | refused_contract | `LICENSE_NOT_APPROVED` |
| 音色批准并冻结 | `POST /api/v1/voice-profiles/{id}/approve-and-freeze` | 403 | refused_operator | 同上 |

库中留有 4 条权利记录（asset / voice_design 各两条，均 `PENDING`），`candidate_assets`、
`voice_profiles`、`voice_renders`、`preparation_approval_grants` 均为 0 行。

**结论**：AI 可以生成 `CandidateAsset` 行，但**只有经认证的人工授权**才能把候选提升为
`Asset` 主档或冻结音色档案。本轮**没有**执行任何提升或冻结 —— 这正是产品要求的行为，
也正是 V1.3 修订条款要保证的边界。

## 5. E 面：专业剧组

| 检查项 | HTTP | 结果 | 说明 |
| --- | --- | --- | --- |
| 角色目录 `GET /api/v1/film-agents` | 200 | ok | 含 `comfyops`（输出类型 `ComfyOpsProposal`，schema 版本 1.3）等全部角色 |
| 项目规则包 `GET /api/v1/projects/{id}/rulepack` | 200 | ok | `shine-h3-rules/1.1.0`，hash `5990ae18…0bdd28`，状态 `FROZEN` |
| scriptwriter | 422 | refused_contract | `LLM_FORMAT` |
| editor | 422 | refused_contract | `LLM_FORMAT` |
| producer | 422 | refused_contract | `LLM_FORMAT` |
| comfyops | 422 | refused_contract | `LLM_FORMAT` |
| director | — | failed | 未触达：缺上游交接物 `ScriptBreakdown` |
| cinematographer | — | failed | 未触达：缺 `DirectorialPlan` |
| continuity_supervisor | — | failed | 未触达：缺 `DirectorialPlan` + `CameraPlan` |
| sound_director | — | failed | 未触达：缺 `ScriptBreakdown` |
| film-runs `GET /api/v1/projects/{id}/film-runs` | 200 | ok | 5 条 Agent 任务记录（修复后增加到 8 条） |
| film-artifacts `GET /api/v1/projects/{id}/film-artifacts` | 200 | ok | 修复前 `[]`；修复后 1 条 `ScriptBreakdown` |

说明：四个角色确实触达了远端（`remote_text_consent: true`、`is_mock: false`、记录的
`model_profile_id` 为本轮创建的 MiniMax 档案、`model_id` 为 `MiniMax-M3.1-flash`），但
返回内容不符合各自的结构化输出契约，因此被以 `LLM_FORMAT` 拒绝，任务记录状态为
`REJECTED`。三个下游角色没有发出请求，因为它们的必需上游交接物不存在 —— 交接链是
硬约束，不会用猜出来的内容补齐。

### 5.1 `LLM_FORMAT` 的根因与修复（本轮后续提交）

上表是 2026-10-25 17:51:40 那次能力面验证的实测结果。随后定位到根因并修复：

**根因**：剧组的指令写的是"只返回所给的 JSON 契约"，但**契约从未被交给模型**。
`LLMGateway.complete_for_role` 只把 schema 交给服务商做校验，模型只拿到上下文，必须
自己猜答案的形状。Mock 服务商用罐头响应作答，把这个问题掩盖了；真实服务商按上下文
作答，于是过不了 schema 校验，报 `LLM_FORMAT`。

**修复**：提交 `2df8aff9f62209b6202bbd839fd914989499d5b2`（2026-10-25 18:01:13，
`backend/shine/llm/gateway.py`）把 schema 追加到请求里，并放在最后，使不可信上下文
无法把它挤掉。

**修复后的实测**（验收 API 已于 17:53:50 以新代码重启，PID 11652）：

| 项 | 值 |
| --- | --- |
| 角色 | `scriptwriter` |
| HTTP | **201 Created** |
| 任务状态 | `SUCCEEDED`（`error_code: null`），任务 ID `765bd9be-2c24-4705-b9bb-97eaa724c440` |
| 产物 | `6b30cb08-6d9d-4204-b7d1-53f3ffe5d80c`，kind `ScriptBreakdown`，schema `1.1` |
| 产物 SHA-256 | `7cf12ee8445b514df3f0b8194a5e800848923c0ca40bd9a46d550e1d2978ac3e` |
| 审计 | `FILM_PROPOSAL_CREATED`，2026-10-25 18:02:33 |
| 人工批准 | `approved_by: null` —— **尚未**经人工批准 |

产物内容是一份真实的 `ScriptBreakdown`：含 `locked_story_facts`，beat 里引用了本项目
已锁定的场景资产 `4b8364f9-…`、风格参考 `663d7728-…` 与人物资产 `ad0ec279-…`。

**边界**：修复后我只观察到 `scriptwriter` 一次成功调用。`editor`、`producer`、
`comfyops` 在修复后**未**被重新运行；`director`、`cinematographer`、
`continuity_supervisor`、`sound_director` 仍未触达（它们需要上游交接物）。因此上表的
46 项能力面统计仍是修复**前**的状态，剧组整链在修复后的完整复验仍是
`BLOCKED / PENDING`。产物 `approved_by` 为空，说明 Agent 提案没有自动变成人工批准
的成果。

## 6. F 面：预制作分析与冻结

在一个一次性的验证项目上跑完整条预制作链路：

| 步骤 | HTTP | 结果 | 说明 |
| --- | --- | --- | --- |
| 建项目 | 201 | ok | `72c59b42-4a48-46d5-b510-dfbfb2db1823`，`allow_remote_review: false` |
| 上传剧本文档 | 201 | ok | `afd53ee6-5aab-4b33-9faf-d27011f63e52`，sha `06148c4f81c2eddfcfe70555520684fd787c7801ab011d7fda47980090a120a3` |
| 分析 | 200 | ok | 计划 `18f4570a-9d44-4381-bf2e-041b756b3867`，`h3_category: H3_REF2VA` |
| 需求 | 200 | ok | `status: BLOCKED` |
| readiness (offline=false) | 200 | ok | `status: BLOCKED` |
| 冻结 | 403 | refused_operator | 需要操作员 |
| comfyops/gaps | 200 | ok | `readiness: BLOCKED`，列出资产与工作流缺口 |

分析产出的 12 条类型化需求（`asset_requirements`）全部为 `MISSING`，覆盖 character、
costume、voice、scene、prop、storyboard、keyframe、action、dialogue、sfx、music、
subtitle；带依赖关系（例如 costume 依赖 character，storyboard 依赖 character/costume/
scene/prop，dialogue 依赖 voice），并带 `critical` 标记。缺口码是
`ASSET_OR_VOICE_MISSING`。

在验收库中可以看到同样的结构（计划 `383b98aa-51e0-4471-8dc4-d1eb4ed8ad21`，hash
`ea36ce2dd69dca1cdda042e54d21eec9556513b2e31464817e4241929fd31416`，12 条需求全部
`MISSING`）。这是**正确**的结果：分析项目在缺少资产与工作流时必须处于 `BLOCKED`，
`comfyops.preproduction.enforce` 在 Job 创建与 Worker 提交两处都会生效。

**边界**：本轮**没有**冻结任何预制作清单（需要操作员授权），也没有让一个已分析项目
去提交真实 job。

## 7. 恢复、GPU 调度与工程回归

三个只有真实 ComfyUI 才会暴露的缺陷已修复，根因、修复与影响详见
[真实 H3 验收报告第 8 节](LOCAL_REAL_H3_ACCEPTANCE.md#8-本轮发现并修复的缺陷)。
这里只列工程侧证据：

| 修复 | 常量 / 行为 | 回归文件 |
| --- | --- | --- |
| ComfyUI 完成窗口 | `EXTERNAL_MISSING_GRACE_SECONDS = 30`；`RECONCILABLE_FAILURE_CODES = ("COMFY_JOB_NOT_FOUND",)` | `backend/tests/test_comfy_completion_window.py`（2 项） |
| GPU 释放确认 | `ComfyUIClient.MAX_LIVE_TENSOR_BYTES = 512 * 1024 * 1024`；`_modern_memory_accounting()` 按版本分支 | `backend/tests/test_gpu_release_confirmation.py`（6 项） |
| 陈旧租约清扫 | 遍历全部 `ACTIVE` / `RECONCILE_REQUIRED` 租约，按渲染器真实内存状态释放 | 同上 |
| 交付装配 | 来源再校验 + 内容寻址落盘 + `review_state` | `backend/tests/test_delivery_assembly.py`（6 项） |

工程回归（交付提交上亲自重跑）：

```powershell
& 'backend\.venv\Scripts\python.exe' -m pytest backend/tests -q
& 'backend\.venv\Scripts\ruff.exe' check backend/shine backend/alembic backend/tests
npm --prefix frontend run build
npm --prefix frontend audit
```

| 检查 | 结果 |
| --- | --- |
| 后端全量测试 | exit 0，**389 项全部通过**（36 个测试文件） |
| 三个新增回归文件 | 14 项全通过（2 + 6 + 6） |
| Ruff | `All checks passed!`，exit 0 |
| 前端构建 | exit 0，Vite 6.4.5，1590 modules |
| `npm audit` | `found 0 vulnerabilities`，exit 0 |

计数取法：

```powershell
# 全量运行，exit 0
& 'backend\.venv\Scripts\python.exe' -m pytest backend/tests -q

# 收集计数：36 个文件、389 项
& 'backend\.venv\Scripts\python.exe' -m pytest backend/tests --collect-only -q
# tests/test_comfy_completion_window.py: 2
# tests/test_delivery_assembly.py: 6
# tests/test_gpu_release_confirmation.py: 6
```

> 备注：基线在改动前为 375 项（brief 记录），本轮新增 14 项回归后为 **389** 项，
> 375 + 14 = 389，增量与新增测试文件数量一致。

### 7.2 收尾审片阶段新发现并修复的缺陷

2026-10-25 11:30（UTC）的人工审片阶段定位并修复了下面三项。第 3 项与 7.1 表格里的
"陈旧租约清扫"是**同一处修复**，此处只记录收尾轮在两个重渲染 job 上的复验结果。

| # | 缺陷 | 现象 | 根因 | 修复 | 验证 |
| --- | --- | --- | --- | --- | --- |
| 1 | LLM 网关从未把 JSON 契约交给模型 | 剧组四个角色的真实调用被 `LLM_FORMAT` 拒绝（422）；指令要求模型"只返回所给的 JSON 契约"，而契约从未下发 | `LLMGateway.complete_for_role` 只把 schema 交给服务商做校验，模型只拿到上下文，必须自己猜答案的形状；Mock 服务商用罐头响应作答，把问题掩盖了 | 把 schema 追加到请求末尾，使不可信上下文无法把它挤掉（`backend/shine/llm/gateway.py`，提交 `2df8aff`） | 对授权的 MiniMax 端点实跑：`scriptwriter` 返回 **HTTP 201**，产出有效 `ScriptBreakdown`（详见 5.1） |
| 2 | 启动脚本记的是启动器 pid，而不是占用端口的那个 pid | 停止后重启，绑定端口以 `Errno 10048` 失败，而旧服务仍按**旧的操作员凭据**在端口上应答；症状是凭据本身正确，审片却被 HTTP **403** 拒绝 | uv 管理的虚拟环境把基础解释器作为**子进程**重新执行，启动器 pid 与真实监听者 pid 不是同一个；停止时只结束了启动器 | `scripts/start_shine.ps1`：端口被占用时**拒绝启动**并给出停止命令；记录真实监听者 pid（`listener_pid`）到 `processes.json`。`scripts/stop_shine.py`：结束该监听进程，并在端口仍被占用时打印告警 | 重启后自检确认"回答请求的进程"就是本次启动的进程；`Errno 10048` 与"旧凭据 403"不再出现 |
| 3 | 陈旧的独占 GPU 租约可以锁死队列 | 租约清扫只回看"正在被恢复的那些 job"的租约；job 已经结束、租约还在的记录永远不会被回看 | 清扫的查询条件与待恢复 job 的范围耦合 | 清扫遍历**所有** `ACTIVE` / `RECONCILE_REQUIRED` 租约；租约的 job 不再是活动状态且其 attempt 已 `COMPLETED` 时，按渲染器的真实内存状态释放 | 库里五条 H3 租约最终全部为 `RELEASED`，含两个重渲染 job 的 `89899faf…` 与 `3630aaec…` |

第 1 项的完整根因分析、修复与边界见 5.1。第 3 项与上表"陈旧租约清扫"那一行、以及
[真实 H3 验收报告 8.3](LOCAL_REAL_H3_ACCEPTANCE.md#83-陈旧的独占租约可以锁死队列)
是**同一处修复**，不是另一处缺陷；此处列出是为了记录收尾轮在两个重渲染 job 上的
复验结果。

## 8. 操作员边界与剩余操作项

### 8.1 边界机制

以下动作要求服务端签发的操作员授权：资产批准、资产锁定、受保护项目的风格冻结、
候选提升、音色档案冻结、工作流发布、预制作清单冻结、正式逐项审片。

- 操作员身份来自 API 进程环境的 `SHINE_OPERATOR_ID` 与 `SHINE_OPERATOR_TOKEN_SHA256`；
  缺失这两个变量时 API 直接拒绝。
- 本轮实测（对当时唯一的真实镜头 job `ca3754cc-…` 的审片端点；该 job 之后已被重渲染
  取代）：

```powershell
$u='http://127.0.0.1:8100/api/v1/operator/jobs/ca3754cc-099e-41ce-bb5a-5aac121b7793'
Invoke-WebRequest $u -UseBasicParsing                                    # 无凭据
Invoke-WebRequest $u -Headers @{Authorization='Bearer wrong-credential-000000'} -UseBasicParsing
Invoke-WebRequest -Method Post "$u/review" -ContentType 'application/json' -Body '…'
```

实测输出（三次都是 403）：

```
HTTP 403 : {"detail": "Local operator credential required; Agent identities are not accepted"}
HTTP 403 : {"detail": "Local operator credential required; Agent identities are not accepted"}
HTTP 403 : {"detail": "Local operator credential required; Agent identities are not accepted"}
```

  即：无凭据被拒、错误凭据同样被拒、Agent 身份不被接受。
- **审片已执行**（2026-10-25 11:30 UTC）：API 以 `SHINE_OPERATOR_ID = shine-owner` 与
  `SHINE_OPERATOR_TOKEN_SHA256` 配置，操作员用同一份凭据通过
  `POST /api/v1/operator/jobs/{id}/review` 提交了三个镜头的逐项审阅，三次都返回
  HTTP 200（`{"status":"PASSED","result":"passed","source":"manual"}`）。**原始凭据只
  存在于操作员进程内存中**，没有进入仓库、数据库或任何日志；API 侧只保存 SHA-256
  摘要。
- 本轮真实镜头复用的资产审批与 Style Bible 冻结，是**人工在 2026-10-21 做出的**，
  原样复用，本轮未重新签署。
- 除第 8.2 节第 10 项（正式逐项审片）外，本轮没有执行下面第 8.2 节的其它待办。

### 8.2 剩余操作项（按建议顺序）

| # | 动作 | 端点 | 目标 | 请求体要点 |
| --- | --- | --- | --- | --- |
| 1 | 批准权利记录（asset） | `POST /api/v1/preparation/rights/{id}/approve` | `59fd7998-a3bc-438c-bda5-8417c1ba3ffe` | `grant_id`、`snapshot`、`evidence`（`Decision` 必填 `grant_id`/`snapshot`/`evidence`） |
| 2 | 批准权利记录（voice_design） | `POST /api/v1/preparation/rights/{id}/approve` | `c6a9396b-2667-4131-9aee-500edf591031` | 同上 |
| 3 | 重新导入候选资产 | `POST /api/v1/projects/{id}/candidate-assets/import` | 项目 `1f743836-a00c-4787-8343-4f1a24ed9135` | multipart 文件；需权利记录已 `APPROVED` |
| 4 | 候选 QC | `POST /api/v1/candidate-assets/{id}/qc` | 步骤 3 返回的候选 ID | — |
| 5 | 候选提升为主档 | `POST /api/v1/candidate-assets/{id}/promote` | 步骤 3 的候选 ID | 人工授权；保留原始 QC 与来源证据 |
| 6 | 导入音色档案 | `POST /api/v1/projects/{id}/voice-profiles/import` | 项目 `1f743836-…` | multipart；需权利记录已 `APPROVED` |
| 7 | 音色批准并冻结 | `POST /api/v1/voice-profiles/{id}/approve-and-freeze` | 步骤 6 的音色 ID | 人工授权；冻结前必须试听 |
| 8 | 冻结预制作清单 | `POST /api/v1/projects/{id}/preproduction/freeze` | 项目 `72c59b42-4a48-46d5-b510-dfbfb2db1823`（或重新建的项目） | 分析与 readiness 必须先不是 `BLOCKED` |
| 9 | 发布工作流能力 | `POST /api/v1/workflow-capabilities/{id}/publish` | draft `429fd489-111e-40f5-ad8b-5a30c3e62b40` | 人工授权 |
| 10 | **已完成**：正式逐项审片（三个镜头） | `POST /api/v1/operator/jobs/{id}/review` | `ba4bb63d-475a-4802-a469-0c4ce166fc97`（A）、`a2718d14-4a81-4046-8416-e16ce2b8598f`（B）、`ffffe1d8-86e7-4593-acaa-91ff453b6b31`（C） | `snapshot`、`request_id`、`items`（十项分项）、`watched`、`listened`；B 镜头必须 `watched` 且 `listened`。A、B 的**第一版**（`ca3754cc…` / `dee0263c…`）不在此列：它们早于规则包冻结与 Style Bible v2，工作台拒绝为它们签署 |
| 11 | 可选：重新撰写 Style Bible | `PUT /api/v1/projects/{id}/style/draft` → `POST /api/v1/projects/{id}/style/freeze` | 项目 `1f743836-…` | v2 已是英文版；只有操作员更希望自己定稿时才需要 |

顺序约束：1 → 2 必须先于 3 / 6；3 → 4 → 5；6 → 7；8 必须在 5 与 7 之后；
10 与前三项镜头互不依赖，但需先完成观看与试听。

`items` 的十个分项固定为：`identity`、`costume`、`scene_props`、`camera_action`、
`style`、`dialogue`、`voice`、`lip_sync`、`continuity`、`other`，一个不多一个不少；每项
是 `{"status": "PASS|FAIL|NOT_REVIEWED|NOT_APPLICABLE", "evidence": "具体证据"}`。
已审项与不适用项必须写明画面、时间点、实际听到的台词或不适用理由；不适用不能替代必需
检查。任一分项 `FAIL` 或必需项未审，镜头保持 `NEEDS_REVIEW`。`request_id` 必须是
16–64 位的字母数字或连字符且每次审片唯一。

审片前可先取快照与视频：

```powershell
GET /api/v1/operator/jobs/{id}          # 绑定快照与快照摘要
GET /api/v1/operator/jobs/{id}/media    # 重新校验归属与哈希后返回视频
```

### 8.3 本轮已完成的正式逐项人工审片

2026-10-25 11:30（UTC），操作员 `shine-owner` 通过**真实操作员 API** 逐项审阅了三个
当前镜头并签署。三次 `POST /api/v1/operator/jobs/{id}/review` 全部返回 HTTP 200。

| Shot | Job | Artifact | 状态 | `quality_verification` |
| --- | --- | --- | --- | --- |
| A-PDD8-7s | `ba4bb63d-475a-4802-a469-0c4ce166fc97` | `75173459-798f-452c-821b-f839c3c3330f` | `PASSED` | `manual_verified` |
| B-PDD8-7s | `a2718d14-4a81-4046-8416-e16ce2b8598f` | `1b278511-fb66-46c5-8dbd-0c869913fc06` | `PASSED` | `manual_verified` |
| C-PDD8-7s | `ffffe1d8-86e7-4593-acaa-91ff453b6b31` | `c2946fa4-9913-4cf5-a295-41a864217782` | `PASSED` | `manual_verified` |

三条 `OPERATOR_REVIEW_RECORDED` 审计事件：`request_id` 分别为 `op-review-20261025-A`、
`-B`、`-C`；三个镜头的 `watched` 均为 true，`listened` 只有对白镜头 B 为 true ——
A 与 C 的 shot spec 没有对白，它们的 `dialogue` / `voice` / `lip_sync` 记为
`NOT_APPLICABLE`（不是"没看"，而是"没有这项可看"）。

**门禁是追加而不是覆盖**：审片在 worker 写下的第一组 `quality_gates` 之外**追加**了
第二组（语义 / 风格 / 终审，以及 B 的音频门），worker 的原始行原样保留。`final` 为
`passed` 的依据是操作员签了全部必需分项；`technical` 为 `passed` 来自真实的
FFprobe / FFmpeg 实测（`model: ffprobe`，`is_mock: 0`）。这些门**从未**被 VLM 或 ASR
评审过 —— 语义、风格、终审（以及 B 的音频）都是**人工**关闭的。

审片后重新装配了交付样片：artifact `e6b264aa-…`，sha256 `4ce49654…d7fb98e`，
21.895996 s / 525 帧 / 1344x768 / 24 fps，`review_state = all_passed`（三个来源 job
全部 `PASSED`）。先前的成片仍以 `pending_review` 原样留在盘上 —— 交付是内容寻址的。

操作员的完整工作流程见 [操作员指南](operator-guide.md) 第 6.4 节。

## 9. 进程与启动脚本

`scripts/start_shine.ps1` 不再打开三个带 `-NoExit` 的可见 PowerShell 窗口。它把 API、
worker 与 Vite 服务器作为**隐藏进程**启动，各自写独立日志，并把 PID、启动时间、工作
目录与命令行记录到 `<data_dir>/run/processes.json`。

```powershell
.\scripts\start_shine.ps1 -Provider comfyui -ApiPort 8100 -WebPort 5175 `
  -DataDir 'F:\ClaudeWorkSpace\projects\shine-video-studio\.local-acceptance\h3run'
```

收尾审片阶段给这两个脚本补了两处（根因见 7.2 第 2 项）：

- **端口被占用就拒绝启动**。脚本在启动前检查 API 与前端端口是否已被占用；占用时打印
  占用者 pid、给出停止命令并以非零码退出，不再让新进程带着绑定错误（`Errno 10048`）
  死掉、而旧服务继续按旧凭据在端口上应答。
- **记录真正的监听者**。uv 管理的虚拟环境会把基础解释器作为子进程重新执行，启动器
  pid 与占用端口的 pid 不是同一个。脚本因此额外等待并记录 `listener_pid`（前端同理）。

本轮验收实例的清单实测（`.local-acceptance/h3run/run/processes.json`）：

| 名称 | PID | 命令 |
| --- | --- | --- |
| api | 26416 | `python.exe -m uvicorn shine.main:app --host 127.0.0.1 --port 8100` |
| worker | 17620 | `python.exe -m shine.worker` |
| web | 25804 | `node.exe .../vite/bin/vite.js --host 127.0.0.1 --port 5175` |

上表是修复前采集的清单。收尾审片阶段之后，清单里还会多一个 `listener_pid` 字段，
记录真正占用端口的那个进程。

`scripts/stop_shine.py` 只停止该清单里的进程：先对 SQLite 做 WAL 检查点，再终止
进程，**从不**触碰 ComfyUI 或任何无关进程。它还会终止清单里记录的 `listener_pid`
（当它与启动器 pid 不同时），并在端口仍被占用时打印
`[warning] port <n> is still held by pid <n>`，以免把旧服务误认成新服务。

```powershell
uv run --project backend python scripts/stop_shine.py --data-dir "$env:SHINE_DATA_DIR"
```

## 10. 明确未覆盖与已知限制

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 语义 / 风格 / 终审门的**模型**覆盖 | `BLOCKED / PENDING` | 仍然没有 VLM 评审；这三道门由操作员的人工逐项审阅关闭 |
| 音频门的**机器**复核 | `BLOCKED / PENDING` | 未配置自动 ASR；B 的台词与口型由人工试听、逐字核对 |
| 远程媒体评审 | 未启用 | `allow_remote_review = 0`；没有任何图像/音频/视频离开本机；只有剧组的文本提示词在授权下发往 MiniMax 端点 |
| 严格首尾帧锚定 | `PENDING_HARD_CONTINUITY_TEST` | Shot C 只是软参考连续性 |
| 视频参考真实传输 | `SUPPORTED_NODE_UNVERIFIED_TRANSFER` | 节点存在，传输未验证 |
| ComfyOps 本机清单纳管 | 未做 | 本轮使用操作员自有安装，未纳管、未改动 |
| 多模型 GPU 争用 | 未覆盖 | 只跑了一个本地模型家族 |
| PDD4 / Native20 实际出片 | 未跑 | 本轮只用 PDD8 |
| 3D 潜空间超分档案 | 已登记并校验，未渲染 | `local-h3-ref2va-pdd8-upscale-2img` 已用实时 `object_info` 校验通过，但本轮没有用它出片 |
| 远端 CI | 未确认 | `git fetch` 失败（连接重置）；只读 `git ls-remote` 核对远端 `refs/heads/main` 仍是 `63929f4`，未移动，但不对远端 CI 结果做断言 |
| 无人值守全片 | 未验证 | 本轮是人工驱动的三镜头序列 |
| 480x864 历史样本 | 仍拒绝用于正式画质 | 历史结论不改写 |

## 11. 结论分类

| 结论 | 类别 |
| --- | --- |
| A/B/C/D/F 面的读接口、档案、预制作分析链路在真实本机可用，缺授权处按契约正确拒绝 | `VERIFIED_LOCAL_ENV` |
| 五个工作流档案全部用实时 `object_info`（3091 节点类）校验通过 | `VERIFIED_LOCAL_ENV` |
| 权利记录、候选资产、音色档案的人工闸门按设计拒绝未授权提升 | `VERIFIED_LOCAL_ENV` |
| 预制造成本分析产出 12 条类型化需求并正确判定 `BLOCKED`（不静默放行） | `VERIFIED_LOCAL_ENV` |
| 三个真实 H3 镜头与交付样片 | `VERIFIED_REAL_H3` |
| A、B 在规则包冻结与 Style Bible v2 之后重渲染并被签署，第一版 job 与产物原样保留 | `VERIFIED_REAL_H3` |
| 三个真机缺陷（完成窗口、GPU 释放确认、陈旧租约）的修复与 14 项回归 | `IMPLEMENTED` / `TESTED_OFFLINE` |
| 收尾审片阶段的缺陷修复：LLM 网关下发 JSON 契约、启动脚本拒绝占用端口并记录真实监听者、租约清扫复验 | `IMPLEMENTED` / `TESTED_OFFLINE` |
| 交付装配能力、来源再校验、`review_state` 语义 | `IMPLEMENTED` / `TESTED_OFFLINE` |
| 隐藏进程启停与 `processes.json` 清单、`stop_shine.py` 的范围限定 | `IMPLEMENTED` / `TESTED_OFFLINE` |
| 后端全量测试、Ruff、前端构建、`npm audit` | `TESTED_OFFLINE` |
| 剧组四个角色的真实调用因 `LLM_FORMAT` 被拒；根因为 schema 未下发给模型，已在 `2df8aff` 修复，修复后 `scriptwriter` 成功产出 `ScriptBreakdown` | `BLOCKED / PENDING`（其余角色未复验） |
| 操作员 `shine-owner` 通过真实操作员 API 逐项审阅并签署三个镜头（`OPERATOR_REVIEW_RECORDED` × 3） | `VERIFIED_MANUAL` |
| 三个镜头的 semantic / style / final 门与 B 的 audio 门由**人工**关闭，`final = passed` 因为全部必需分项被签署 | `VERIFIED_MANUAL` |
| 交付样片 `review_state = all_passed`，三个来源 job 全部 `PASSED` | `VERIFIED_MANUAL` |
| 语义 / 风格 / 终审门与音频门**没有模型覆盖**，只由人工关闭 | `BLOCKED / PENDING` |
| 候选提升、音色冻结、预制作冻结、工作流发布 | `BLOCKED / PENDING` |
| 严格连续性、视频参考传输、多模型 GPU 争用、远端 CI | `BLOCKED / PENDING` |
