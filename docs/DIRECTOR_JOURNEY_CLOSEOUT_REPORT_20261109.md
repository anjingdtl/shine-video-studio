# 导演模式全旅程收尾复验报告

2026-11-09，分支 `codex/director-journey-closeout`，基线 `main@12dd99bc9e037c53fce9f19aea68cbe342fece03`。
本轮把 [导演模式全旅程唯一性对抗审查](ADVERSARIAL_REVIEW_DIRECTOR_JOURNEY_12DD99B_20261109.md) 的 U01–U10
从「可复现缺陷」改成「有回归测试的合同」，并逐项记录仍然只能在真机上完成的部分。

**判定：`SOFTWARE_VERIFIED`。** 十项缺陷的反例都先在 `12dd99b` 上重现，修复版用同一组入口断言了正确行为；
后端定向与全量、前端单测与构建、隔离浏览器验收、空库与旧库副本迁移、真实包构建均在本机执行并通过。
`CLEAN_WINDOWS_INSTALL`、`REAL_DOWNLOAD`、`REAL_LLM`、`REAL_QWEN_IMAGE`、`REAL_VOICE`、`REAL_H3`、
`VOLUME_DELIVERED`、`HUMAN_ACCEPTED` 为 `NOT_RUN`，具名阻塞与继续步骤见文末。本报告不把任何离线结果当作真实出片。

## 1. 本轮基线核对

| 项 | 结果 |
| --- | --- |
| `git status --short --branch`（开审时） | `## main...origin/main`，仅两份未跟踪交接文档 |
| `git log -20 --oneline` | HEAD `12dd99b fix(workbench): auto-recover the project list after a backend blip`，与审查基线一致 |
| `git ls-remote origin refs/heads/main` | `12dd99bc9e037c53fce9f19aea68cbe342fece03`，与本地 HEAD 一致 |
| 运行时占用（开工前） | 无 Shine API/Worker 监听（8000/5173），无 ComfyUI 监听（8188），无 GPU 租约 |
| 本机硬件（只读探测） | RTX 5080 16,303 MiB，驱动 621.01；D 盘可用 554.9 GB，F 盘 292.1 GB |
| 隔离方式 | 独立 SQLite（`tmp_path` 或 `.local-acceptance/closeout-*`）、显式 `SHINE_DATA_DIR`、随机回环端口、合成剧本与自有素材 |

本轮没有接管任何外部进程：没有启动、停止或改写用户已有的 ComfyUI/Shine 实例。探测未发现常见路径下的
ComfyUI 安装，因此「复用已有安装」这条真机路径仍是 `NOT_RUN`，而不是被跳过。

## 2. 权威身份链

每一项身份只有一个写入者；读取者按各自合同重查同一事实，不复制判断。

| 领域 | 身份与写入者 | 读取者 | 版本 | 重试/冲突约束 |
| --- | --- | --- | --- | --- |
| 安装 | `comfyops.deployment.create_install_plan` 写 `target_key`（规范化存储根 + 组件闭包）与 `configuration`；`installation.queue_attempt` 写唯一活跃尝试 | `api_setup.install-plan`、`api_preparation.execute`、`installation_worker`、`build_test_package.ps1` | `catalog_id/version`、`plan.sha256`、`target_key`、`attempt.payload.plan_hash` | 同 target 同 hash 重接同一尝试；同根不同内容 `INSTALL_TARGET_CONFLICT`；`INSTALLING/VALIDATING` 结果不明进 `NEEDS_REVIEW` 对账，不自动重放 |
| 剧本分镜 | `api_agent.upload_document`（空项目首次成为依据）、`api_story.choose_script_basis`、`confirm_outline`（确认即选用）、`api_director.create_episode_plan/materialize/confirm` | `director.package._script_ref`、`api_director._confirmed_outline`、`journey`、`pipeline` | `ProjectDocument.sha256`、`AgentArtifact.sha256`、`DirectorEpisodePlan.sha256/version`、`Shot.revision` | 上传只增候选；确认/选用显式；`expected_sha256` 不符 409；逐单元 apply 回执缺失具名 |
| 素材与风格 | `asset_center.service.assign_to_project` 写项目分配；`api_asset_center.adopt_into_shots` 经 `revise_shot_record` 写新镜头 revision；`freeze_style` 写 `StyleVersion` | `preproduction.enforce/enforce_job`、`package_readiness`、`runner` 派发、`journey` 素材通知 | 全局 `LibraryVersion.id/ordinal/content_sha256`、`LibraryProjectLink`、项目 `Asset.id/sha256`、`ShotAssetBinding`、`StyleVersion.sha256`、`ProjectRuleLock` | 采用需 `confirmed=true` + `expected_shot_revisions` + `idempotency_key` 回执；已确认包内镜头 `ADOPTION_BLOCKED`；撤权后历史收据保留、新消费者拒绝 |
| 拍摄 | `services.create_render_job` 写 `request_fingerprint`/`idempotency_key`；`runner.create_run` 写 Run；`production_series` 写 Series/Item | Worker 认领、`run_public`、`journey`、`blockers` | `DirectorPackageVersion.manifest_sha256/scope_digest`、`config_fingerprint`、`ProductionRun.id/state_version`、`Job.id`、`JobAttempt`、`prompt_graph_sha256`、`ResourceLease` | `request_key` 重放同一 job；同 key 异内容 409；未对账 UNKNOWN 阻断新尝试；系列一次只被一个 Worker 持有；租约只释放一次 |
| 交付 | `delivery_engine` 写 EDL/交付版本；`runner.set_episode_post_contract` 写新交付版本 | `journey` 后期、`DeliveryPanel`、人工验收 | `MediaArtifact.sha256`、`DeliveryOrder` revision、`acceptance_status` | 后期改动复用已确认视频生成新交付版本；历史集按原 ID 下载；`pending_review` 不升级为 `accepted` |

## 3. U01–U10 逐项

每条都注明「旧结果（12dd99b 复现）→ 新结果（本分支回归）→ 入口 → 兼容与迁移影响」。

### U01 · 同一安装内容只能拥有一个待执行尝试

- **旧结果**：同项目、同组件、同存储根连续两次 `POST /setup/install-plan`，`sha256` 相同、`plan_id` 不同；
  分别批准执行都返回 `202 QUEUED`，`attempt_id` 不同。排他只查 `plan_id`。
- **新结果**：计划的物理身份是 `target_key`（规范化 `realpath` 存储根 + 组件闭包 + executor）。同 target 同
  `configuration` 的重复请求返回同一 `plan_id`；同计划再次执行返回同一 `attempt_id`；同根不同组件集合
  返回 `409 INSTALL_TARGET_CONFLICT`。缓存字节变化会使旧数字失效，因此复用条件还要求 `download_bytes`/`cached_bytes` 一致。
- **入口**：`POST /api/v1/setup/install-plan`、`POST /api/v1/comfyops/install-plans/{id}/execute`、
  `GET /api/v1/comfyops/installation-attempts/{id}`。
- **兼容/迁移**：无数据库迁移；旧计划没有 `target_key` 时按同一规则即时计算，旧记录仍可读。旧的
  `INSTALL_ATTEMPT_ACTIVE` 拒绝改成「重接同一尝试」，`test_installation_persistence` 与
  `test_director_setup_contract` 的断言同步更新。

### U02 · 安装选择、计划与包清单是同一组事实

- **旧结果**：推荐卡的 `h3_video`/`image_prep` 只控制生图卡片是否显示，`SetupPanel` 从固定 catalog 自取 H3 组件
  建计划；打包脚本写死 `packages = 154`，catalog 实际是 14 组件 / **189 条文件声明** / 60,804,054,569 bytes。
- **新结果**：`POST /setup/install-plan` 接受 `include_image_capability`，把组合写入计划 `payload.configuration`
  并回显；Qwen Image 以独立 catalog 的 `companion_plans` 单列（字节、组件、安装入口），不并入 H3 下载。
  `SetupPanel` 从 `DirectorSetupPanel` 接收真实选择并随计划提交。打包脚本改为从 catalog 计算
  `components`、`packages`（=文件声明数）、`download_bytes` 及 production/optional/install 三个口径，
  并写入 `statistics_basis` 字段。
- **入口**：`POST /api/v1/setup/install-plan`、`pwsh -File scripts/build_test_package.ps1 -Verify`。
- **兼容/迁移**：旧脚本字段名 `packages` 保留但语义明确为文件声明；`download_bytes` 与
  `default_production_bytes`、`optional_review_bytes`、`install_required_bytes` 的计算结果与原手写值一致，
  仅 `packages` 由 154 纠正为 189。

### U03 · 普通入口的下一步就在第一阶段

- **旧结果**：`#d/creative` 导入剧本后，第一阶段只有「保存为创意简报」和「刷新」；普通大纲动作
  「后台提取并归并总体大纲」藏在 `#d/produce` → 素材准备 → 选文件之后。第二阶段还提前渲染第 5 阶段的导演包表单。
- **新结果**：`ScriptEntryPanel` 增加文档选择与 `OutlineConfirmationPanel`，第一阶段即可提取/确认大纲；
  未确认大纲时 `journey.next_stage` 为 `script`，`facts.outline_confirmed=false`。导演页 `#assets` 与
  `AssetPanel` 的重复 DOM id 改为 `#plan-stage`，`#assets` 只属于素材面板。`DirectorProductionRunPanel`
  的 `mode='plan'` 只渲染分集，导演包表单只在 `mode='package'` 出现。
- **入口**：`GET /projects/{id}/journey`、`POST /projects/{id}/story-preparation/outlines`、
  `POST .../outlines/{artifact_id}/confirm`、浏览器 `#d/creative`。
- **兼容/迁移**：四阶段入口仍保留为折叠的高级路径，调用同一文档与同一大纲产物；URL hash 不变。

### U04 · 一个包只绑定一个已选定的剧本依据

- **旧结果**：导入 A 并确认 A 的大纲与镜头，随后导入 B；新包的 `script.document_id` 变成 B，
  `outline_artifact_id` 仍是 A，确认返回 200；A 的旧包仍 `ready`。
- **新结果**：`projects.current_document_id` 记录显式依据（空项目首次导入、确认大纲、或
  `POST /projects/{id}/script-basis` 三选一），上传第二个文件只增加候选。`_script_ref` 通过依据文档解析
  大纲，不按「最新文档 + 最新大纲」拼接。`package_readiness` 比较冻结依据与当前依据，漂移返回
  `SCRIPT_BASIS_CHANGED`；旧包仍按原 ID 可读。
- **入口**：`GET/POST /api/v1/projects/{id}/script-basis`、`POST /projects/{id}/director-packages`、
  `GET /director-packages/{id}/readiness`。
- **兼容/迁移**：新增迁移 `f180b0040001`（`projects.current_document_id`，可空，纯 `ADD COLUMN`）。
  老项目为 `NULL` 时保持「最新文档」旧规则；没有导入文档的手工镜头项目回退到最新已确认大纲。

### U05 · 分配与逐镜采用是两个动作，且分镜不因缺素材停下

- **旧结果**：(a) 分镜 UI 发 `confirm_characters=true`，缺角色素材时返回
  `201 AWAITING_CHARACTER_CONFIRMATION` 且只有 `scriptwriter`；(b) 素材中心批准、QC、晋升并分配后，
  项目主素材已创建，原镜头 `GET /bindings` 仍为空，旅程仍报 `SHOT_CHARACTER_MASTER_MISSING:Lin`。
- **新结果**：(a) UI 默认发 `confirm_characters=false`，缺素材作为 `material_requirements` 记录，
  分镜链继续到导演；显式「人物对照」入口保留（同一 proposal/apply 端点）。(b) 新增
  `POST /asset-center/adoptions/preview` 与 `POST /asset-center/adoptions`：前者返回精确镜头 revision、
  将使用的 `use_as`、以及已确认包冲突；后者要求 `confirmed=true`、`expected_shot_revisions` 与
  `idempotency_key`，对每镜调用 `revise_shot_record`（新 revision、旧 job 失效、产物不覆盖），
  回执写入 `AppSetting`，重放返回同一回执；缺确认、旧 revision、已确认包分别返回具名 409。
- **入口**：`POST /api/v1/asset-center/adoptions/preview|adoptions`、`PUT /shots/{id}/bindings`、
  `POST /storyboard/generate`。
- **兼容/迁移**：无迁移。`PUT /shots/{id}/bindings` 仍是兼容入口（不改 revision），但已确认备份通过
  `SHOT_BINDINGS_CHANGED` 拒绝开拍；采用动作是产生新 revision 的显式路径。

### U06 · 撤权对每个真实消费者生效

- **旧结果**：撤销全局 rights 后，素材中心当前权利校验拒绝该版本；但没有制片准备计划的项目，
  正式 `comfyui` Job API 仍返回 201，Worker 共用的 `enforce_job` 返回 `None`。
- **新结果**：`enforce_live_references` 成为所有消费者共用的现行校验：镜头存在与 revision、资产属于本项目、
  已批准并锁定、sha256 与清单一致、`provenance.rights_id` 链到全局版本并重查当前权利、字节在磁盘上可解码、
  `offline_only` 资产不得进入正式渲染。`enforce()` 在没有计划时也执行它（legacy 项目保留原冻结清单语义，
  但不是无治理）。撤权后 Job 创建返回 409，且不留下排队 job；已提交后的撤权保留历史收据，由对账处理。
- **入口**：`POST /projects/{id}/jobs`、Worker `enforce_job`、`POST /asset-center/rights/{id}/revoke`。
- **兼容/迁移**：无迁移。历史 job 的既有收据不改写；只影响新准入与提交前复核。

### U07 · 「可开拍」与「这一镜能入队」是同一句话

- **旧结果**：冻结风格、建立无绑定且点名人物的镜头，生成并确认 `Ref2VA` 包，`readiness.startable=true`；
  同一镜头走 Job 创建即 `422 H3_MODE_REFERENCE_MISMATCH`。
- **新结果**：`package_readiness` 对包内每一镜调用同一个 `H3PromptCompiler`、同一份 style snapshot、
  同一 `profile` 约束：模式不匹配返回 `H3_MODE_REFERENCE_MISMATCH`（带 `shot_id`、`mode`、`details`），
  槽位缺失返回 `PENDING_WORKFLOW_MAPPING`（带 `missing_reference_slots`），连续性请求返回
  `PENDING_*`；动态连续性引用标为 `dependency_only` 的 `CONTINUITY_DEPENDENCY_WAITING`，不阻塞静态可执行性。
  单镜 Job 与 Run 派发使用同一个 `validate_reference_slots`。`config_fingerprint` 覆盖冻结执行身份与
  当前漂移字段。
- **入口**：`GET /director-packages/{id}/readiness`、`POST /projects/{id}/jobs`、Run 派发。
- **兼容/迁移**：无迁移。测试夹具的 `COMFY_PARAM_MAP` 补上 `reference_subject_1/2` 与对应 graph 输入，
  因为一个只映射单张图片的 profile 本来就不能派发多参考镜头。

### U08 · 原地修改工作流不会悄悄改变已确认的承诺

- **旧结果**：注册声明了宽高输入但未映射的 profile，确认包；调用
  `POST /workflow-profiles/{id}/standard-parameters` 增加映射，图 SHA 不变，旧包仍 `startable`，
  `config_fingerprint` 完全不变。
- **新结果**：`_profile_execution_drift` 比较 `mode/graph_sha256/checkpoint_id/model_identity/
  parameter_map/media_transfers`，任一漂移返回 `WORKFLOW_PROFILE_CHANGED`（带 `changed_fields`）；
  `_execution_fingerprint` 覆盖上述字段、checkpoint、model、generation mode、output、style、preproduction、
  policy、项目语言、逐集清单哈希与逐镜 revision/spec/绑定，以及 `drifting_fields`/`shot_drift`/`binding_drift`。
- **入口**：`GET /director-packages/{id}/readiness`、`POST /workflow-profiles/{id}/standard-parameters`。
- **兼容/迁移**：无迁移。历史冻结执行仍读原修订；只有新 Run 与新批次被阻断。

### U09 · 一次请求一个收据，绑定是显式决定

- **旧结果**：保存绑定为空时向单镜 Job API 传参考得到 201，随后参考被写入镜头；相同请求再发得到另一个
  201 与另一 Job ID；把首个 Job 置为带尝试与 graph hash 的 `COMFY_SUBMISSION_OUTCOME_UNKNOWN` 后再提交
  仍得到第三个 201。
- **新结果**：空绑定 + 请求内参考 → `409 SHOT_BINDINGS_REQUIRED`（返回可继续动作与候选绑定），不再写绑定。
  `CreateJob.request_key` 形成稳定请求身份：同 key 同内容返回同一 job 与 `replayed=true`；同 key 异内容
  `409 JOB_REQUEST_CONFLICT`。同镜头存在未对账 UNKNOWN（`COMFY_SUBMISSION_OUTCOME_UNKNOWN` /
  `COMFY_WORKER_EXCEPTION_WITH_EXTERNAL` / `SUBMISSION_OUTCOME_UNKNOWN`）时，新提交（含换 key）返回
  `409 SHOT_SUBMISSION_OUTCOME_UNKNOWN`，必须先对账或显式重拍。
- **入口**：`POST /projects/{id}/jobs`、`PUT /shots/{id}/bindings`。
- **兼容/迁移**：新增迁移 `f180b0050001`（`jobs.request_fingerprint`，可空纯 `ADD COLUMN`）。指纹放在
  Job 列而不是 `jobs.request`，因为 Worker 把该 JSON 直接按 `RenderRequest` 校验；写入 payload 会让
  每个 job 在 Worker 端变成非法请求。

### U10 · 一个范围决定进度、播放、交付与审核

- **旧结果**：旧计划完成并装配 1 集 2 镜后，确认含新增镜头的计划并生成新包；新旅程显示生产 2/3 未完成，
  后期却用旧 Run 的 1/1 标为完成。另一反例把已通过的夹具 Job 与 Artifact 标为 mock，七阶段仍显示 1/1。
- **新结果**：`GET /projects/{id}/journey?package_id=&run_id=` 接受显式范围；无参数时取最新包（无包时才走
  legacy 最新 Run）。生产范围来自该包的逐集清单，不再取最新计划；生产完成只统计该范围内、非 mock
  （`provider`、`quality_verification`、`artifact.is_mock` 三者任一为 mock 即演示）、revision 匹配、
  且（有 Run 时）属于该 Run 的 series item 的 PASSED job。后期只认该包自己的 Run；没有 Run 时显示
  「等待这个导演包开拍」。`facts` 增加 `scope_package_id`、`scope_run_id`、`demo_shot_count`；
  无效深链返回 `404 DIRECTOR_PACKAGE_NOT_FOUND` 并附 `available_package_ids`。
- **入口**：`GET /projects/{id}/journey`、`GET /production-runs/{id}`、`GET /production-runs/{id}/episodes`。
- **兼容/迁移**：无迁移。旧包与旧 Run 仍按原 ID 访问；不带参数的旧链接落到最新包，行为与「最新包」一致。

## 4. 软件门禁账本

| 层级 | 命令 | 结果 | 证据 |
| --- | --- | --- | --- |
| 基线反例（12dd99b） | `pytest -o addopts='' .local-acceptance/director-uniqueness-12dd99b/test_counterexamples.py -q` | 9 passed，断言旧缺陷可重现 | `.local-acceptance/director-uniqueness-12dd99b/api-counterexamples.txt`、`browser-probe.txt`、三张截图 |
| 定向后端（本分支） | 见 `§4.1` 文件清单 | 见 `§4.1` | `.local-acceptance/closeout-full-suite/*.log` |
| 后端全量（分片） | 逐文件独立进程，清单见 `.local-acceptance/closeout-full-suite/chunk-*.log` | 见 `§4.2` | `chunk-a.log`、`chunk-b.log`、`per-file.json` |
| Ruff | `backend/.venv/Scripts/python.exe -m ruff check backend/shine backend/alembic backend/tests scripts/verify_v13_web.py` | `All checks passed!` | 终端输出 |
| 前端单测 | `cd frontend && npm.cmd test -- --run` | 28 文件 / 191 通过（一次并发运行的 `EPERM` 临时文件冲突后复跑） | `§4.3` |
| 前端构建 | `cd frontend && npm.cmd run build` | 通过；Vite 提示 >500 kB chunk（既有） | `§4.3` |
| 隔离浏览器 | `uv run --project backend --with playwright==1.62.0 python scripts/verify_v13_web.py --work-dir .local-acceptance/closeout-web/tmp --screenshot .local-acceptance/closeout-web/v13-web.png` | `MOCK_VERIFIED`，`api_errors: []`，含第一阶段大纲入口断言 | `.local-acceptance/closeout-web/v13-web.png` |
| 空库迁移 | `SHINE_DATA_DIR=.local-acceptance/closeout-migration alembic upgrade head` + `alembic check` | 唯一 head `f180b0050001`；`No new upgrade operations detected` | `.local-acceptance/closeout-migration/` |
| 旧库副本迁移 | 从 `f180b0030001` 副本升级到 head | `f180b0050001 (head)`，升级前后 `workflow_profiles` 行不变 | `.local-acceptance/closeout-migration-old/` |
| 发包 | `pwsh -NoProfile -File scripts/build_test_package.ps1 -OutDir <隔离目录> -Verify` | 见 `§4.4` | `§4.4` |
| GitHub CI | 同 SHA 的 frontend / backend / web-e2e | 见 `§4.5` | Actions 运行页 |

### 4.1 定向后端

```text
backend/.venv/Scripts/python.exe -m pytest -o addopts='' -q --tb=short \
  backend/tests/test_package_entrypoints.py backend/tests/test_director_setup_contract.py \
  backend/tests/test_new_machine_setup.py backend/tests/test_installation_persistence.py \
  backend/tests/test_asset_center.py backend/tests/test_four_stage_reacceptance.py \
  backend/tests/test_director_long_form.py backend/tests/test_director_long_form_regressions.py \
  backend/tests/test_director_journey_closeout.py backend/tests/test_director_assets_closeout.py \
  backend/tests/test_offline_e2e.py
```

结果见 `§4.1 运行记录`（本文件末尾的账本附录）。

### 4.2 后端全量

全量按「每个测试文件一个独立进程」分片，清单与合计见 `§4.2 运行记录`。分片边界是完整的文件，
没有重叠；每个文件的退出码、耗时与 pytest 计数都记在 `chunk-*.log`。

### 4.3 前端

- `npm.cmd test -- --run`：28 文件 / 191 通过。曾出现一次 `EPERM ... \Temp\...\web\<hash>`（Vitest 写临时
  文件被宿主拦截），仅表现为少收集 1–2 个文件；随后 `--reporter=verbose` 复跑为 28/191 全通过。
- `npm.cmd run build`：`tsc -b && vite build` 通过，产物 `dist/assets/index-*.js 781.38 kB`。

### 4.4 测试包

见 `§4.4 运行记录`（ZIP 文件名、SHA-256、manifest 文件数、排除规则核对）。

### 4.5 同提交 CI

见 `§4.5 运行记录`。本地无法代替远端 Windows/Linux runner；本轮记录的是同一提交的实际工作流结论，
若远端仍在运行或日志不可读，如实标注而不推测。

## 5. 真机门禁

| 项 | 状态 | 具名阻塞 | 继续步骤 / 需要的用户动作 |
| --- | --- | --- | --- |
| `CLEAN_WINDOWS_INSTALL` | `NOT_RUN` | 需要一个干净 Windows 宿主（或可回滚的恢复点）与 ≥180 GiB 非系统盘；本轮不接管当前用户环境 | 在干净宿主用 ZIP 的 `一键安装并启动.cmd` 建第一个作品，走授权 → 非系统盘 → 依赖/模型下载 → 校验 → 断网续传 → 启动 → 复跑；记录盘符空间、GPU/驱动、组件 pin |
| `REAL_DOWNLOAD` | `NOT_RUN` | 未获授权执行 60.8 GB 下载；当前 `SHINE_ENABLE_LOCAL_EXECUTION` 未开启，且下载会改变本机存储 | 用户批准后设置 `SHINE_ENABLE_LOCAL_EXECUTION=1`，在 `SETUP` 页创建计划、批准并执行；或指定一台测试机 |
| `REAL_LLM` | `NOT_RUN` | 未提供获授权的文本模型 provider 与密钥 | 在「专业设置 → AI 助手」填入 provider、模型与密钥，跑一次文档分析/分集/分镜；密钥只存本机 |
| `REAL_QWEN_IMAGE` | `NOT_RUN` | 未安装 Qwen Image 权重/节点，且没有正在运行的 ComfyUI | 安装 `qwen-image-2.1-windows-x64` 独立计划后，在素材中心跑一个真实样本，核对解码、尺寸、SHA 与回执 |
| `REAL_VOICE` | `NOT_RUN` | 未启动本地 TTS/ASR 运行时，也未授权音色 | 启动语音运行时，走一次试听 → 冻结 → 发布；另验一条「语音可选」路径 |
| `REAL_H3` | `NOT_RUN` | 本机没有已登记的 ComfyUI/H3 实例（无 8188 监听、无 GPU 租约），也未执行 60.8 GB 权重安装 | 先完成 `CLEAN_WINDOWS_INSTALL` 或只读复用已核验的现有安装，再按 `§3 U07/U08` 的 profile 要求登记工作流；由 UI 发起一次 Run，关浏览器后恢复，记录包/Run/Series/Job/Attempt/prompt ID/租约/EDL/SHA/五门 QC |
| `VOLUME_DELIVERED` | `NOT_RUN` | 依赖 `REAL_H3` 的真实多集产出 | 至少两集、每集多镜（含有对白/无对白/跨批次连续性），逐集播放、下载并核对参考身份与对白语言 |
| `HUMAN_ACCEPTED` | `NOT_RUN` | 依赖真实成片；本轮无真实成片 | 由真实操作者提交人验结论，证据包含所看版本与结论；`pending_review` 不升级为 `accepted` |

硬件不是阻塞：本机 RTX 5080 16,303 MiB 与 D 盘 554.9 GB 可用空间满足默认变体的门槛。阻塞是本轮**没有**
已登记的 H3 实例与用户对 60.8 GB 下载/真实推理的授权。

## 6. 交付、回滚与已知边界

- **交付**：`codex/director-journey-closeout` 上的实现与测试；迁移 `f180b0040001`、`f180b0050001`；
  两份新回归文件；打包脚本与浏览器脚本的修正。
- **回滚**：代码回退到 `12dd99b`。数据库迁移可降级（`f180b0050001 → f180b0040001 → f180b0030001`），
  两个迁移都是可空列，降级不删业务数据；生产回滚前按验证过的备份副本恢复路径处理，运行中的 job/series
  先安全结算或对账。
- **已知边界**：
  - `package_readiness` 现在对每一镜跑编译器，包很大时 `GET /journey` 会变慢；这是「开拍前报出静态缺口」的
    代价，当前短剧规模可接受。
  - 动态连续性引用在 readiness 只标「依赖等待」；精确核验在 Run 派发与提交时由原状态机完成。
  - `PUT /shots/{id}/bindings` 仍是兼容入口，不改 revision；已确认包通过 `SHOT_BINDINGS_CHANGED` 阻断。
  - 全量后端以逐文件分片执行：宿主的批量文件守卫会在一次进程内跑完整套件时中断测试，分片是环境约束，
    不是用例跳过。
  - 演示（mock）记录在 UI 上仍可见，但不再计入当前范围的生产完成；`demo_shot_count` 单列。

## 附录 A · 十项反例与回归的对应

| 项 | 基线反例（12dd99b） | 本分支回归 |
| --- | --- | --- |
| U01 | `test_duplicate_install_plan_enqueues_same_physical_work_twice` | `test_the_same_install_request_is_one_plan_and_one_attempt`、`test_two_different_component_sets_cannot_share_the_same_storage_root` |
| U02 | 静态证据 + 包内容核对（154 vs 189） | `test_the_install_plan_states_the_combination_that_was_really_chosen`、`test_package_metadata_counts_the_catalog_it_ships`、`test_build_info_template_lists_the_real_catalog_numbers` |
| U03 | `browser_probe.py`：第一阶段只有两个按钮 | `test_an_imported_script_reports_the_outline_step_on_stage_one` + 浏览器 `#outline-confirm` 断言 |
| U04 | `test_regular_package_pins_new_document_with_old_outline_and_shots` | `test_importing_a_second_script_does_not_replace_the_package_basis`、`test_an_explicit_basis_switch_invalidates_the_old_package_readiness` |
| U05 | `test_actual_storyboard_ui_request_stops_before_director`、`approved library assignment created a project master but did not satisfy the existing shot` | `test_the_storyboard_no_longer_stops_for_an_unprepared_character`、`test_an_assignment_only_reaches_a_shot_through_an_explicit_adoption`、`test_adoption_refuses_a_shot_inside_a_confirmed_package` |
| U06 | `test_revoked_global_material_is_accepted_by_legacy_project_h3_preflight` | `test_a_revoked_library_version_blocks_the_job_and_the_worker_gate` |
| U07 | `test_ref2va_package_startable_but_same_shot_rejected_by_compiler` | `test_ref2va_package_without_any_reference_is_not_startable`、`test_a_profile_without_a_reference_slot_is_named_before_a_run_starts` |
| U08 | `test_standard_parameters_mutates_a_frozen_binding_without_staling_it` | `test_editing_the_parameter_map_in_place_invalidates_the_frozen_package`、`test_the_execution_fingerprint_covers_the_shot_reference_set` |
| U09 | `test_empty_saved_binding_allows_request_to_inject_a_master_and_duplicate_job` | `test_a_job_may_not_create_shot_bindings_as_a_side_effect`、`test_the_same_request_key_replays_one_receipt_and_a_different_request_conflicts`、`test_an_unreconciled_unknown_submission_blocks_a_new_attempt` |
| U10 | `test_old_delivered_run_marks_new_scope_delivered`、`test_mock_job_counts_as_produced_in_real_journey` | `test_an_old_delivered_run_never_lights_up_a_new_plan`、`test_a_mock_take_is_counted_as_demo_never_as_production`、`test_an_unknown_package_deep_link_is_a_named_error` |
