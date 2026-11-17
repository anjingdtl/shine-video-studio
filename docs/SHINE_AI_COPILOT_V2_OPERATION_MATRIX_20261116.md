# Shine AI 管家 V2.0 全业务操作覆盖矩阵

- 初版：2026-11-16；远端验收修复复核：2026-11-17
- 能力目录版本：`shine-copilot-capabilities-2026.11.16-v2`
- 后端 FastAPI 方法级路由操作：469；唯一路径：431；矩阵操作：44
- 审计口径：真实 UI → 正式 API → 参数 Schema → 原业务门禁 → 唯一执行者 → 回读接口 → AI 能力状态。未列入矩阵的操作不计入覆盖率；NOT_SUPPORTED 不提供假动作。

## 1. 统计

| AI 状态 | 数量 | 含义 |
| --- | --- | --- |
| COVERED | 0 | 自然语言可直接委托（当前无此级业务写操作） |
| READ_ONLY | 11 | 可查询、可解释，不可修改 |
| GUARDED | 17 | 需预览差异并由用户明确授权后委托原执行者 |
| HUMAN_REQUIRED | 11 | 必须由人亲自确认，AI 只准备事实与解释 |
| NOT_SUPPORTED | 5 | 当前未接入，附真实工程原因 |

## 2. 全页面操作矩阵

| 区域 | 用户操作 | 原 API | 原执行者 | AI 状态 | AI 动作 |
| --- | --- | --- | --- | --- | --- |
| 全局 | 新建项目 | `POST /api/v1/projects` | `shine.services.create_project` | GUARDED | project_create |
| 全局 | 切换/查看项目 | `GET /api/v1/projects, GET /api/v1/projects/{id}` | `projects ledger` | READ_ONLY | read_project_journey |
| 全局 | 导入小说/剧本 | `POST /api/v1/projects/{id}/documents` | `shine.services.import_project_document` | GUARDED | document_import_text |
| 全局 | 切换正式底稿 | `POST /api/v1/projects/{id}/script-basis` | `shine.services.switch_script_basis` | GUARDED | document_switch_basis |
| 全局 | 查看项目设置 | `GET /api/v1/projects/{id}` | `projects ledger` | READ_ONLY | read_project_journey |
| 全局 | 修改项目语言 | `PATCH /api/v1/projects/{id}/settings` | `shine.services.update_project_settings` | GUARDED | project_settings_update |
| 导演模式 | 七阶段旅程 | `GET /api/v1/projects/{id}/journey` | `director.overview.build_overview` | READ_ONLY | read_project_journey |
| 导演模式 | 生成故事大纲 | `POST /api/v1/projects/{id}/story-preparation/outlines` | `materialprep.story.queue_outline` | GUARDED | story_outline_generate |
| 导演模式 | 修订/确认故事大纲 | `POST /api/v1/projects/{id}/director/outline/revision, /confirm` | `api_streamline` | HUMAN_REQUIRED | — |
| 导演模式 | 生成分集剧本 | `POST /api/v1/projects/{id}/pipeline/volumes/{volume_id}/scripts` | `shine.director.pipeline.queue_scene_scripts` | GUARDED | literary_scripts_generate |
| 导演模式 | 生成/确认分镜书 | `POST /api/v1/projects/{id}/director/storyboard-book[/confirm]` | `api_streamline` | HUMAN_REQUIRED | — |
| 导演模式 | 生成导演分镜 | `POST /api/v1/projects/{id}/director/storyboard/generate` | `director.streamline.queue_director_storyboard` | GUARDED | storyboard_generate |
| 导演模式 | 查看逐集镜头 | `GET /api/v1/projects/{id}/director/storyboard/episodes/{episode_id}/shots` | `director.streamline.episode_shot_table` | READ_ONLY | read_project_journey |
| 导演模式 | 逐镜头行编辑 | `PATCH /api/v1/projects/{id}/director/storyboard/shots/{shot_id}` | `reshape_shot` | NOT_SUPPORTED | — |
| 导演模式 | 确认单集镜头 | `POST /api/v1/projects/{id}/director/storyboard/episodes/{episode_id}/confirm` | `director.streamline.confirm_episode` | HUMAN_REQUIRED | — |
| 导演模式 | 查看缺失素材 | `GET /api/v1/projects/{id}/director/overview` | `director.overview.build_overview` | READ_ONLY | read_missing_materials |
| 导演模式 | 备料决策与提交 | `POST /api/v1/projects/{id}/director/materials/commit` | `materialprep.service` | HUMAN_REQUIRED | — |
| 导演模式 | 确认导演包 | `POST /api/v1/director-packages/{package_id}/confirm` | `director.package.confirm_package` | HUMAN_REQUIRED | — |
| 导演模式 | 开始真实拍摄 | `POST /api/v1/projects/{id}/director/production/start` | `director.streamline.start_director_production` | GUARDED | production_start |
| 导演模式 | 暂停/继续/取消生产 | `POST /api/v1/production-runs/{run_id}/pause|resume|cancel` | `shine.director.run_engine` | GUARDED | production_control |
| 导演模式 | 查看生产进度/阻塞 | `GET /api/v1/production-runs/{run_id}[/blockers]` | `production ledger` | READ_ONLY | read_production_run |
| 导演模式 | 审片与交付 | `GET/POST /api/v1/projects/{id}/deliveries` | `media.delivery` | HUMAN_REQUIRED | — |
| 素材中心 | 搜索/复用素材 | `GET /api/v1/projects/{id}/asset-center/search` | `asset_center search` | READ_ONLY | read_missing_materials |
| 素材中心 | 候选图生成 | `POST /api/v1/projects/{id}/candidate-assets/generate` | `candidate generation` | NOT_SUPPORTED | — |
| 素材中心 | 候选图人工确认 | `POST /api/v1/candidate-assets/{identity}/promote` | `asset promote` | HUMAN_REQUIRED | — |
| 素材中心 | 素材任务进度/失败原因 | `GET /api/v1/projects/{id}/material-tasks` | `material ledger` | READ_ONLY | read_task_failure |
| 成片库 | 字幕逐句编辑与导出审批 | `PUT /api/v1/projects/{id}/director/storyboard/shots/{shot_id}/subtitles` | `api_streamline` | NOT_SUPPORTED | — |
| 成片库 | 对外发布/分享 | `—` | `—` | NOT_SUPPORTED | — |
| 设置 | 工作流节点图数值级改写 | `workflow JSON` | `—` | NOT_SUPPORTED | — |
| 设置 | 查看环境裁决 | `GET /api/v1/setup/verdict` | `api_setup.setup_verdict` | READ_ONLY | — |
| 设置 | 一键整备缺失文件 | `POST /api/v1/setup/prepare/execute` | `comfyops.setup_preparation.enqueue` | GUARDED | setup_repair |
| 设置 | 核验/登记已有文件 | `POST /api/v1/setup/reconcile` | `comfyops.setup_reconcile.reconcile_installation` | GUARDED | setup_reconcile |
| 设置 | 安装计划授权与执行 | `POST /api/v1/comfyops/install-plans/{identity}/execute` | `comfyops approval` | HUMAN_REQUIRED | — |
| 设置 | 释放受管渲染服务 | `POST /api/v1/setup/managed-processes/release` | `comfyops.renderer_release` | GUARDED | managed_release |
| 设置 | 选择 ComfyUI 实例 | `POST /api/v1/setup/installation/select` | `setup summary` | HUMAN_REQUIRED | — |
| 设置 | LLM Provider / 角色路由 | `GET/POST /api/v1/llm/providers, /llm/routes` | `llm gateway` | HUMAN_REQUIRED | — |
| 设置 | 创建用户工作流副本 | `POST /api/v1/workflow-profiles` | `api_comfy.create_workflow_profile` | GUARDED | workflow_user_copy |
| 设置 | 工作流参数映射调整 | `POST /api/v1/workflow-profiles` | `api_comfy.create_workflow_profile` | GUARDED | workflow_user_copy |
| 设置 | 生成策略（变体/加速/二次采样/RelayVSR） | `POST /api/v1/projects/{id}/director/production/start` | `strategy.GenerationStrategy` | GUARDED | production_start |
| 设置 | 联网检索开关（管家自身） | `POST /api/v1/support/preferences` | `support.agent.set_web_search` | GUARDED | — |
| 成片库 | 浏览/下载成片 | `GET /api/v1/projects/{id}/video-library` | `media.video_library` | READ_ONLY | — |
| 成片库 | 删除历史视频 | `POST /api/v1/projects/{id}/video-library/delete` | `media.video_library` | HUMAN_REQUIRED | — |
| 全局 | 任务失败诊断 | `jobs/job_attempts + /setup/verdict` | `support.diagnostics` | READ_ONLY | read_task_failure |
| 全局 | 生成工程问题报告 | `POST /api/v1/support/reports` | `support.reports.generate_report` | GUARDED | generate_support_report |

> 调参与开拍严格分离：策略预览只返回「待使用策略」与差异（`proposed_action=null`、`apply_via=production_start`、`scope=NEXT_PRODUCTION_ONLY`），不会自动生成开拍动作；真正开拍必须另一次资源影响展示与授权。

## 3. 可委托动作（Capability Registry）

| action_id | 名称 | 层级 | 作用域 | 参数合同 | 回读 | 可逆 | GPU | 付费 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `setup_repair` | 补齐真实制作环境 | PREVIEW | PROJECT | capabilities, deep_scan, installation_path, port, project_id, variant_id, verify_runtime | `/api/v1/setup/verdict` | NO | NO | NO |
| `setup_reconcile` | 重新核验并登记已存在的本机文件 | PREVIEW | PROJECT | adopt, capability, installation_path, port, project_id, variant_id | `/api/v1/setup/verdict` | NO | NO | NO |
| `managed_release` | 停止本工作台拥有的渲染服务并释放显存 | PREVIEW | MACHINE | only | `/api/v1/setup/verdict` | NO | NO | NO |
| `workflow_user_copy` | 创建用户级工作流副本 | PREVIEW | PROJECT | name, parameter_overrides, project_id, source_profile_id | `/api/v1/setup/verdict` | NO | NO | NO |
| `project_create` | 创建影视项目 | PREVIEW | MACHINE | title, description, language | `GET /api/v1/projects/{project_id}` | NO | NO | NO |
| `document_import_text` | 导入原文文本 | PREVIEW | PROJECT | project_id, filename, text | `GET /api/v1/projects/{project_id}/script-basis` | NO | NO | NO |
| `project_settings_update` | 修改项目语言等用户级设置 | PREVIEW | PROJECT | project_id, language | `GET /api/v1/projects/{project_id}` | YES | NO | NO |
| `story_outline_generate` | 生成故事大纲 | EXECUTE_GUARDED | PROJECT | project_id, document_id, locked_facts, pacing | `GET /api/v1/projects/{project_id}/story-preparation` | NO | NO | YES |
| `storyboard_generate` | 生成导演分镜 | EXECUTE_GUARDED | PROJECT | project_id, plan_version | `GET /api/v1/projects/{project_id}/director/storyboard/state` | NO | NO | YES |
| `production_start` | 开始真实拍摄选中的集 | EXECUTE_GUARDED | PROJECT | project_id, episode_ids, workflow_profile_id, strategy | `GET /api/v1/production-runs/{run_id}` | CONDITIONAL | YES | NO |
| `document_switch_basis` | 切换正式原文底稿 | PREVIEW | PROJECT | project_id, document_id | `GET /api/v1/projects/{project_id}/script-basis` | YES | NO | NO |
| `production_control` | 暂停/继续/取消生产任务 | EXECUTE_GUARDED | RUN | run_id, operation | `GET /api/v1/production-runs/{run_id}` | CONDITIONAL | YES | NO |
| `literary_scripts_generate` | 生成文学分集剧本 | EXECUTE_GUARDED | PROJECT | project_id, volume_id | `GET /api/v1/projects/{project_id}/pipeline/volumes/{volume_id}` | NO | NO | YES |

前端只按本表白名单构造参数：不再给所有动作统一追加 `project_id`；`managed_release` 只传 `only`；`project_create` 不传当前项目；`production_control` 只传 `run_id/operation`；动作自带的目标项目不会被当前项目静默改写。

## 4. 只读能力

| action_id | 名称 | 执行者 | 回读 |
| --- | --- | --- | --- |
| `read_project_journey` | 读取项目旅程与每集状态 | `shine.director.overview.build_overview` | `GET /api/v1/projects/{project_id}/journey` |
| `read_missing_materials` | 检查缺失素材 | `shine.director.overview.build_overview` | `GET /api/v1/projects/{project_id}/director/overview` |
| `read_production_run` | 读取真实生产进度 | `shine.api_director.get_run` | `GET /api/v1/production-runs/{run_id}` |
| `read_task_failure` | 读取失败任务与尝试 | `shine.support.context.build_evidence_snapshot` | `jobs/job_attempts ledger` |
| `search_project_knowledge` | 检索版本化项目百科 | `shine.support.project_knowledge.search` | `backend/shine/support/knowledge_manifest.json` |
| `search_official_docs` | 检索 ComfyUI / H3 官方摘要 | `shine.support.official_docs.search` | `官方来源索引（参考证据）` |
| `read_parameter_contracts` | 读取可调参数合同 | `shine.support.parameters.contracts` | `workflow_profiles / GenerationStrategy` |

## 5. 必须人工确认 / 永久禁止

| 操作 | 原 API | 状态 | 原因 |
| --- | --- | --- | --- |
| 确认故事大纲 | `POST /api/v1/projects/{project_id}/director/outline/confirm` | HUMAN_REQUIRED | 用户对创作方向的确认，AI 只能准备与解释 |
| 确认并冻结导演风格 | `POST /api/v1/projects/{project_id}/style/freeze` | HUMAN_REQUIRED | 冻结是不可逆的艺术裁决 |
| 确认分集镜头 | `POST /api/v1/projects/{project_id}/director/storyboard/episodes/{episode_id}/confirm` | HUMAN_REQUIRED | 镜头取舍属于导演判断 |
| 确认主资产/候选图 | `POST /api/v1/candidate-assets/{identity}/promote` | HUMAN_REQUIRED | 主资产必须由用户确认 |
| 备料决策（复用/生成/跳过） | `POST /api/v1/projects/{project_id}/director/materials/commit` | HUMAN_REQUIRED | 涉及素材版权与创作选择 |
| 审片与成片验收 | `GET/POST /api/v1/projects/{project_id}/deliveries` | HUMAN_REQUIRED | 验收是人的裁决，AI 不宣称通过 |
| 删除历史成片 | `POST /api/v1/projects/{project_id}/video-library/delete` | HUMAN_REQUIRED | 破坏性操作，必须在界面逐次确认 |
| 安装计划授权与执行 | `POST /api/v1/comfyops/install-plans/{identity}/execute` | HUMAN_REQUIRED | 下载/写入用户安装目录需逐次授权 |
| 修改节点源码/内置模板 | `worktree files` | FORBIDDEN | 运行时管家永久禁止 |
| 任意 Shell / Git / pip | `—` | FORBIDDEN | 运行时管家没有这些工具 |

## 6. 当前未接入的操作及工程原因

| 操作 | 原 API | 原因 |
| --- | --- | --- |
| 单镜头逐字改写与拆分合并 | `POST /api/v1/projects/{project_id}/director/storyboard/shots/{shot_id}` | API 存在，但没有稳定的自然语言参数合同与回读校验；V2 不代理，请在导演页编辑。 |
| 素材候选图批量生成与 QC | `POST /api/v1/projects/{project_id}/candidate-assets/generate` | 涉及付费/GPU 与人工选片；当前只做缺失清单与结果查询，不代提交。 |
| 字幕逐句编辑与导出审批 | `PUT /api/v1/projects/{project_id}/director/storyboard/shots/{shot_id}/subtitles` | 文本级编辑缺少幂等键与差异回读合同；保留人工。 |
| 工作流节点图数值级改写 | `—` | 工作流合同没有节点级参数 schema；只能创建参数映射副本，不能改图。 |
| 外部发布/分享 | `—` | 当前产品没有对外发布 API；不伪造能力。 |
| 任意内部 HTTP 调用 | `—` | 运行时管家没有通用 HTTP 工具；能力必须逐项注册。 |

## 7. 覆盖结论

核心导演链路（创建项目、导入原文、切换正式底稿、故事大纲、分集剧本、导演分镜、真实开拍、生产暂停/继续/取消、生产进度、失败诊断、受管释放、环境整备与对账、工作流副本、用户级调参、工程报告）已具备可委托动作；创作确认、风格冻结、主资产、备料决策、审片交付仍由人工完成；镜头逐字编辑、候选图批量生成、字幕逐句编辑、节点图数值改写、外部发布仍无稳定自然语言合同。

全部写动作仍走「预览 → 用户明确授权 → 原执行者 → 权威回读」；项目级动作使用唯一已校验的 `target_project_id`，跨项目声明一律拒绝，历史授权不能跨项目复用。
