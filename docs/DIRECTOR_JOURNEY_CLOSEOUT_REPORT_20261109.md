# 导演模式全旅程收尾复验报告

2026-11-09，分支 `codex/director-journey-closeout`，基线 `main@12dd99bc9e037c53fce9f19aea68cbe342fece03`。
本轮把 [导演模式全旅程唯一性对抗审查](ADVERSARIAL_REVIEW_DIRECTOR_JOURNEY_12DD99B_20261109.md) 的 U01–U10
从「可复现缺陷」改成「有回归测试的合同」，并逐项记录仍然只能在真机上完成的部分。

**判定：`SOFTWARE_VERIFIED` + 本机 `REAL_H3` 已跑通。** 十项缺陷的反例都先在 `12dd99b` 上重现，
修复版用同一组入口断言了正确行为；后端定向与全量、前端单测与构建、隔离浏览器验收、空库与旧库副本迁移、
真实包构建均在本机执行并通过。远端 CI（[PR #11](https://github.com/anjingdtl/shine-video-studio/pull/11)）的
`frontend` 与 `web-e2e` 通过，`backend`/`backend-windows` **没有新增产品失败**（Linux 13 个、Windows 3 个为
基线既有平台限制，详见 §4.5）。

**本机实机运行（§6）**：用这台 Windows 上已有的 ComfyUI（含完整 H3 权重）复用为零下载、零拷贝的实例，
跑通「素材审核 → 冻结风格 → 镜头 → 真实 H3 出片 → 导演包 → Run → 交付 → 下载」，单镜 job `PASSED`、
`quality_verification=real`、1344×768 / 5.17 s / 带 AAC 音频，交付成片带中文文件名下载。
`CLEAN_WINDOWS_INSTALL`、`REAL_DOWNLOAD`、`REAL_QWEN_IMAGE`、`REAL_VOICE`、
`VOLUME_DELIVERED`、`HUMAN_ACCEPTED` 仍为 `NOT_RUN`，具名阻塞与继续步骤见 §5。

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
| 定向后端（本分支） | 见 `§4.1` | **176 passed / 1 skipped** | `.local-acceptance/closeout-full-suite/retest-four-stage-2.log` |
| 后端全量（分片） | 逐文件独立进程，清单见 `§4.2` | **159 文件 / 1658 passed**；19 个文件在分片内被宿主守卫中断，单独复跑 18 个全通过、1 个 9/10 | `final-{0-40,40-80,80-120,120-200}.log`、`fail-*.log` |
| Ruff | `backend/.venv/Scripts/python.exe -m ruff check backend/shine backend/alembic backend/tests scripts/verify_v13_web.py` | `All checks passed!` | 终端输出 |
| 前端单测 | `cd frontend && npm.cmd test -- --run --no-file-parallelism` | **28 文件 / 191 通过** | `§4.3` |
| 前端构建 | `cd frontend && npm.cmd run build` | 通过；Vite 提示 >500 kB chunk（既有） | `§4.3` |
| 隔离浏览器 | `uv run --project backend --with playwright==1.62.0 python scripts/verify_v13_web.py --work-dir .local-acceptance/closeout-web/tmp --screenshot .local-acceptance/closeout-web/v13-web.png` | `MOCK_VERIFIED`，`api_errors: []`，含第一阶段大纲入口断言 | `.local-acceptance/closeout-web/v13-web.png` |
| 空库迁移 | `SHINE_DATA_DIR=.local-acceptance/closeout-migration alembic upgrade head` + `alembic check` | 唯一 head `f180b0050001`；`No new upgrade operations detected` | `.local-acceptance/closeout-migration/` |
| 旧库副本迁移 | 从 `f180b0030001` 副本升级到 head | `f180b0050001 (head)`，升级前后 `workflow_profiles` 行不变 | `.local-acceptance/closeout-migration-old/` |
| 发包 | `pwsh -NoProfile -File scripts/build_test_package.ps1 -OutDir .local-acceptance/closeout-package -Verify` | ZIP `cbae437c…13c60`，698 条目，`fixed_materials.packages = 189` | `§4.4` |
| GitHub CI | 同 SHA 的 frontend / backend / web-e2e | `frontend` 成功、`web-e2e` 成功（基线失败）；`backend` 15（13 既有 + 2 已修）、`backend-windows` 3（全为既有） | `§4.5` |

### 4.1 定向后端

```text
backend/.venv/Scripts/python.exe -m pytest -o addopts='' -q --tb=short -p no:cacheprovider \
  backend/tests/test_package_entrypoints.py backend/tests/test_director_setup_contract.py \
  backend/tests/test_new_machine_setup.py backend/tests/test_installation_persistence.py \
  backend/tests/test_asset_center.py backend/tests/test_director_long_form.py \
  backend/tests/test_director_long_form_regressions.py backend/tests/test_director_journey_closeout.py \
  backend/tests/test_director_assets_closeout.py backend/tests/test_offline_e2e.py
```

结果：**159 passed**（74.12s）。四阶段回归单独运行（它包含 3 分钟以上的长链）：
`backend/tests/test_four_stage_reacceptance.py` → **17 passed / 1 skipped**（184.16s）。
两项合计 **176 passed / 1 skipped**，退出码 0。

定向集合覆盖：安装计划与持久尝试、新机/复用安装、素材中心与制片、四阶段回归、普通导演入口、
package/Run/Series、发布与交付、离线端到端（`test_offline_e2e.py`），以及本轮两份新回归文件。

### 4.2 后端全量

全量按「每个测试文件一个独立进程」分片，四段清单互不重叠（`0-40`、`40-80`、`80-120`、`120-200`），
共 **159 个测试文件**。合计 **1658 passed**。

19 个文件在分片内以 `SystemExit: 1` 结束：本机宿主注入了 `sitecustomize` 的 `safe-delete` 守卫，
安装器/进程/临时目录的删除操作被拦截（例如 `shutil.rmtree` → `_safe_shutil_rmtree`），
不是产品断言失败。把这 19 个文件单独复跑：

| 文件 | 单独复跑 |
| --- | --- |
| `test_deployment_storage` | 13 passed |
| `test_director_setup_contract` | 12 passed |
| `test_existing_installation` | 14 passed |
| `test_llm_gateway` | 18 passed |
| `test_llm_setup_contract` | 27 passed |
| `test_machine_recheck` | 9 passed |
| `test_operator_review` | 36 passed |
| `test_runtime_dependencies` | 8 passed |
| `test_owned_process_group` | 9 passed / 1 failed（同一 `trash` 守卫：`renderer.py` 删除被拒） |
| `test_v13_preparation` + `test_structured_review` + `test_v13_e2e` | 97 passed |
| `test_v13_adversarial` + `test_v13_factory` | 50 passed |
| `test_v13_instances` + `test_installer_redirects` + `test_sdk_role_process` + `test_stack_operations` + `test_security_windows` | 103 passed |

分片里已通过的用例不重复计入复跑。因此全量的准确表述是：**159 文件 / 1658 passed**，
另有 19 个文件受宿主删除守卫影响；其中 18 个单独复跑全通过，`test_owned_process_group` 有 1 例
仍被同一守卫拒绝（与产品逻辑无关，可在无该守卫的宿主上复验）。

### 4.3 前端

- `npm.cmd test -- --run --no-file-parallelism`：**28 文件 / 191 通过**，无未处理错误。
- 默认并发模式在本机会随机出现
  `EPERM: operation not permitted, open '...\Temp\...\web\<hash>'`：Vitest 的多个 worker 同时写同一
  transform 缓存，宿主文件系统 shim 拒绝其中一个，表现为少收集 1–3 个文件并报 1 个 unhandled error。
  串行模式不受影响；这不是测试失败，日志与复跑命令均保留在 `.local-acceptance/closeout-web/`。
- `npm.cmd run build`：`tsc -b && vite build` 通过，`dist/assets/index-*.js 781.59 kB`。

### 4.4 测试包

```text
pwsh -NoProfile -File scripts/build_test_package.ps1 \
  -OutDir .local-acceptance/closeout-package -Verify
```

| 项 | 值 |
| --- | --- |
| 源提交 | `b3f105d726cb0e0593718f594925cef0247eedcd`，`source_tree=CLEAN_TREE` |
| ZIP | `ShineVideoStudio-20261109-b3f105d-win64.zip` |
| SHA-256 | `cbae437c34d32d5f3a0b51107575aebf177972ad66c0c87b3afa47aa06d13c60` |
| 大小 | 3,803,568 bytes |
| 条目 | 698（复制 689 个跟踪文件，跳过 117 个排除路径，加 5 个入口脚本与 2 份渲染文档） |
| 入口编码 | 5 个 `.cmd` 均为 ASCII 源、打包时强制 CRLF；含中文的 `.ps1` 均带 UTF-8 BOM |
| catalog 统计 | `components=14`、`packages=file_declarations=189`、`download_bytes=60,804,054,569`；`statistics_basis` 写明口径 |
| 排除规则 | `packaging/`、`docs/evidence/`、`启动 Shine 工作台.cmd` 不进入包；无模型、数据库、密钥、证据 |

`BUILD_INFO.json` 中 `default_production_bytes=48,317,862,339`、`optional_review_bytes=12,486,192,230`、
`install_required_bytes=173,663,952,354` 与 catalog 计算一致；唯一变化是 `packages` 由手写的 154
纠正为 189 条文件声明。

### 4.5 同提交 CI

PR：[#11](https://github.com/anjingdtl/shine-video-studio/pull/11)（分支 `codex/director-journey-closeout`）。
基线 CI 为 [#42496535584](https://github.com/anjingdtl/shine-video-studio/actions/runs/42496535584)：
`frontend` 成功，`backend` / `backend-windows` / `web-e2e` 失败。

本分支第一次运行 [#42514827552](https://github.com/anjingdtl/shine-video-studio/actions/runs/42514827552)：
`frontend` **success**、`web-e2e` **success**（基线为失败，本轮修正了过期定位并加了第一阶段大纲断言），
`backend` 15 failed / 1902 passed / 138 skipped，`backend-windows` 3 failed / 2050 passed。

逐项对照日志（`gh`/Actions API 下载的原始日志在 `.local-acceptance/ci/`）：

| 通道 | 基线 | 本分支第一次 | 归因 |
| --- | --- | --- | --- |
| `backend`（Linux） | 13 failed / 1883 passed | 15 failed / 1902 passed | 13 个与基线**完全相同**（见下），新增 2 个是本轮 U01 用例缺少平台守卫 |
| `backend-windows` | 3 failed / 2029 passed | 3 failed / 2050 passed（+21 通过） | **完全相同**的 3 个既有失败；本轮全部新用例在 Windows 上通过 |

Linux 的既有 13 个是平台守卫而非产品缺陷：`test_service_validation`（6，`SERVICE_VALIDATION_WINDOWS_ONLY`）、
`test_deployment_storage` / `test_install_plan_qualification` / `test_deployment_acceptance_regressions` /
`test_new_machine_setup::test_next_instance_plan_state_machine`（4，`HOST_NOT_QUALIFIED: OS_UNSUPPORTED
windows-x64 vs linux-x64`）、`test_director_setup_contract`（2，`LOCAL_EXECUTION_NOT_ENABLED`）、
`test_image_managed_renderer`（1，`IMAGE_RENDERER_WINDOWS_ONLY`）。
Windows 的既有 3 个是 `test_gpu_release_confirmation`（2）与 `test_image_installation_catalog`（1），
与基线同样失败。

本分支新增的 2 个（`test_the_same_install_request_is_one_plan_and_one_attempt`、
`test_two_different_component_sets_cannot_share_the_same_storage_root`）已加 Windows 平台守卫：
`os.name != "nt"` 时 `pytest.skip`，因为持久尝试需要 Windows 本地执行器；同一合同由 `backend-windows`
在真实 Windows 上执行。修正提交 `4b48d7f` 已推送，其工作流结论以 PR #11 页面为准。

结论：Linux 与 Windows 两条通道都**没有新增产品失败**；Linux 的 13 个与 Windows 的 3 个是基线既有的
平台/环境限制，应在对应平台的验收轮里单独处理，而不是用本轮的软件验证冒充通过。



## 5. 真机门禁

| 项 | 状态 | 具名阻塞 | 继续步骤 / 需要的用户动作 |
| --- | --- | --- | --- |
| `CLEAN_WINDOWS_INSTALL` | `NOT_RUN` | 需要一个干净 Windows 宿主（或可回滚的恢复点）与 ≥180 GiB 非系统盘；本轮不接管当前用户环境 | 在干净宿主用 ZIP 的 `一键安装并启动.cmd` 建第一个作品，走授权 → 非系统盘 → 依赖/模型下载 → 校验 → 断网续传 → 启动 → 复跑；记录盘符空间、GPU/驱动、组件 pin |
| `REAL_DOWNLOAD` | `NOT_RUN` | 未获授权执行 60.8 GB 下载；当前 `SHINE_ENABLE_LOCAL_EXECUTION` 未开启，且下载会改变本机存储 | 用户批准后设置 `SHINE_ENABLE_LOCAL_EXECUTION=1`，在 `SETUP` 页创建计划、批准并执行；或指定一台测试机 |
| `REAL_LLM` | **本机已跑通**（真实 MiniMax，大纲链路） | 分镜/分集尚未用真实模型跑（本轮只验证到「大纲确认」） | 继续在 UI 里走「分集 → 分镜」，或换回 `MiniMax-M3.1-Flash` 观察 MERGE 输出稳定性（详见 §6.1） |
| `REAL_QWEN_IMAGE` | `NOT_RUN` | 未安装 Qwen Image 权重/节点，且没有正在运行的 ComfyUI | 安装 `qwen-image-2.1-windows-x64` 独立计划后，在素材中心跑一个真实样本，核对解码、尺寸、SHA 与回执 |
| `REAL_VOICE` | `NOT_RUN` | 未启动本地 TTS/ASR 运行时，也未授权音色 | 启动语音运行时，走一次试听 → 冻结 → 发布；另验一条「语音可选」路径 |
| `REAL_H3` | **本机已跑通**（复用现有安装）；干净宿主仍 `NOT_RUN` | 干净宿主需先完成 `CLEAN_WINDOWS_INSTALL`。本机已用既有 ComfyUI 完成：1 镜真实出片（`PASSED`/`real`）+ 1 集 Run 交付，详见 §6 | 第二台机器或干净宿主上重复同一条链路，并补多镜多集；记录包/Run/Series/Job/Attempt/prompt ID/租约/EDL/SHA/五门 QC |
| `VOLUME_DELIVERED` | **部分**：1 集 1 镜已交付 | 未达「≥2 集、每集多镜、含对白与无对白、跨批次连续性」 | 在同一实例上补第 2 集与多镜，验证跨批次连续性与逐集下载 |
| `HUMAN_ACCEPTED` | `NOT_RUN` | 真实成片已产出（§6），但本轮没有人工验收结论 | 由真实操作者观看 `本机联调-第1集.mp4` 并提交结论；`pending_review` 不升级为 `accepted` |

硬件不是阻塞：本机 RTX 5080 16,303 MiB 与 D 盘 554.9 GB 可用空间满足默认变体的门槛。阻塞是本轮**没有**
已登记的 H3 实例与用户对 60.8 GB 下载/真实推理的授权。

## 6. 本机实机运行（2026-11-09，零下载复用）

目标改成「这台机器上真正跑通」。本机已有一套 ComfyUI（Mie 整合包），因此走**复用现有安装**这条路径，
不下载、不拷贝、不接管用户的手工启动方式。

| 步骤 | 命令 / 入口 | 结果 |
| --- | --- | --- |
| 服务常驻 | `scripts/start_shine.ps1 -Provider comfyui -AutoPorts`（数据目录 `F:\ClaudeWorkSpace\shine-workbench\data`） | API `127.0.0.1:8000`、Web `127.0.0.1:5173`、Worker 各一；`/health` 返回 `schema_head=f180b0050001`、`schema_up_to_date=true` |
| 只读识别 | `POST /setup/existing-installations/inspect` | ComfyUI `b0b7435…`（与 catalog 期望一致）、`tracked_files_clean=true`、扫描 1880 文件、候选 43.98 GB |
| 原地校验 | `POST /setup/existing-installations/verify` | `INSTALLED_VERIFIED`；H3 六个核心文件全部 `INSTALLED_HASH_MATCH`（diffusion 20.97 GB / text encoder 15.69 GB / video VAE 5.21 GB / audio VAE 0.61 GB / LoRA / PDD） |
| 采用 | `POST /setup/existing-installations/adopt` | `ADOPTED`，实例 `22ea7c4e…`，复用 43,974,265,807 bytes，`copied_bytes=0`、`downloaded_bytes=0` |
| 运行配置 | `POST /setup/configuration` → `POST /setup/profiles` → `POST /setup/services/validate` → `POST /setup/runtime/activate` | recipe `03343f92…`；服务验证 `READY`（**1281 个节点**、3 个 Ref2VA profile）；激活后 readiness L0–L4 `READY` |
| 素材与风格 | 上传两张自有合成图 → 审批凭据 → `approve`/`lock`；七项风格 → `freeze` | 2 个素材 `approved=true, locked=true`；StyleVersion V1 |
| 单镜真实出片 | `POST /projects/{id}/jobs`（`provider=comfyui`，profile `local-h3-ref2va-pdd8-2img-1aud`，`generation_mode=Ref2VA`） | prompt `dce364e4…`；GPU 99%、15.4/16.3 GB、63 °C；**job `PASSED` / `quality_verification=real`**；产物 1,768,550 bytes、SHA `5ad57319…`、**1344×768 / 24 fps / 5.166 s / AAC 32 kHz 双声道** |
| 导演包与 Run | `POST /episode-plans` → confirm → `POST /director-packages` → readiness（`startable=true`，指纹 `0582db84…`）→ confirm → `POST /production-runs` | Run `827426e2-7a43-40b2-b502-152c4a9761a9`，范围 `ep-01`、1 镜；Series `14d1ba93…`；第二镜 prompt `c0bcd4f7…`，job `256e9298…` `PASSED` |
| 交付 | Worker 自动装配 | Run `COMPLETED`，`ep-01 DELIVERED`，交付单 `457cb0bb…`；交付产物 1,728,232 bytes、SHA `409949af…`、5.187663 s；EDL 精确到 `job_id`/`attempt_id`/`in 0.0–out 5.166`/`CUT` |
| 下载 | `GET /deliveries/{id}/media` | `200`、`video/mp4`、`content-disposition: attachment; filename*=utf-8''本机联调-第1集.mp4`；单镜 `GET /jobs/{id}/media` 同为 `video/mp4` |
| UI | Chromium 打开 `#d/produce` | 7/7 阶段完成，文案「七个阶段全部完成 · 全部选定分集已交付」；2 个下载入口（单镜 / 交付） |

**质量门（真实记录，不美化）**：`technical=passed`（ffprobe）；`semantic`/`audio` 因 VLM/ASR 权重与运行时
未就绪而记为 `unavailable`（advisory，不伪造通过）；`style=not_run`；`final=needs_review` —— 等待人工验收，
`pending_review` 没有被升级为 `accepted`。

**耗时**：单镜从提交到 `PASSED` 约 2 分 40 秒（含首次模型加载）；Run 从创建到交付约 3 分 28 秒。

**UI 瑕疵：已修（提交 `e1b68a3`）**。Run 已交付后，`#d/produce` 的排产区原本仍显示「还没有勾选镜头 /
还没有生成方案 / 有 1 个角色未绑定参考图」三条红色阻断，与顶部「7/7 阶段全部完成」矛盾。原因是阻断清单只
看页面会话的本地选择，没有看"当前范围内每镜是否已有真实产物"。

修法：以 `jobs` 里带 `artifact_id`、非 `simulated`、状态为 `PASSED/DELIVERED` 的真实产物为准，算出
`scopeAllProduced`；当范围内每一镜都已出片时，「未勾选 / 无生成方案 / 未绑定参考」不再作为阻断原因，
改为一条非阻断提示：「本范围 N 镜已全部出片；要重拍或补拍，勾选镜头后会按当前方案重新生成」。
回归：`frontend/src/test/productionComplete.test.tsx`（2 例，含"无产物时仍要说明缺什么"的反向断言）；
本机实测 Playwright 复核：红色阻断卡消失、完成提示出现、下方序列显示「已装配并释放资源 / 已交付 / 已出片」。

**未跑的部分**：干净 Windows 安装（用户另找机器）、60.8 GB 真实下载、Qwen Image 真实样本、
语音授权与试听、多集多镜与跨批次连续性、人工验收。

### 6.1 真实 LLM（MiniMax，本机）

凭据由用户提供（密钥只写入本机 secret store，不进入仓库、报告或提交）。

| 步骤 | 结果 |
| --- | --- |
| 配置 | `POST /api/v1/provider-profiles`：`openai_compatible`、`https://api.minimax.cn/v1`；`has_api_key=true`、`secret_status=stored`（加密引用，不存明文） |
| 连接 | `POST /provider-profiles/{id}/test` → `ok=true`、`reachable/auth_ok/catalog_ok/model_listed=true`（模型 ID 需与目录大小写一致，`MiniMax-M3.1-flash` → `MiniMax-M3.1-Flash`） |
| 生成 | `POST /provider-profiles/{id}/test-generation` → `generation_ok=true`、`structured_output_ok=true`、4.876 s |
| 角色绑定 | `PUT /agent-routes/bulk`：14 个文本角色绑定该 profile；`llm-readiness` = `READY` |
| 剧本大纲 | 上传短剧 → `GET/POST .../material-preparation/text-consent` 显式授权（只发必要文字，不发字节）→ `POST .../story-preparation/outlines` → **EXTRACT 3 次真实调用（HTTP 200）+ MERGE 成功** → 大纲 3 场次（前言 2.2 s / 码头 17.4 s / 栈桥 14.0 s），含 `summary`、`relationships`、4 条待澄清 `questions` |
| 确认 | `questions` 非空时要求显式 `acknowledge_questions_sha256`（产品合同：接受模糊才能确认）→ `approved=true`；`journey.script.done=true`，依据文档切换为该稿 |

**真实遇到并走完的失败路径**（都按产品合同处理，没有绕过）：

1. 首次调用被 `REMOTE_AGENT_CONSENT_REQUIRED` 拦下 —— 未授权就不发剧本文字；授权后 `resume` 才重跑。
2. MERGE 阶段 provider 返回 **529 过载** → 任务 `NEEDS_REVIEW/LLM_SERVER`，外部结果判为 `UNCONFIRMABLE`
   （`reconcile` 不自动重发），按合同走 `resend-unknown`（人工证据 + 只重问一次）后才重新排队。
3. 重问后 HTTP 200，但模型输出不满足大纲 schema → `LLM_FORMAT`。
   **换 `MiniMax-M2.7-highspeed` 后同一份剧本一次跑通**；`MiniMax-M3.1-Flash` 的 MERGE 结构化输出不稳定，
   这是模型侧差异，不是链路缺陷（连接/生成/结构化输出测试在它上面是通过的）。

**结论**：真实 LLM 已在本机接通并产出真实大纲；分集与分镜尚未用真实模型跑，属于下一步。


## 7. 交付、回滚与已知边界

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
