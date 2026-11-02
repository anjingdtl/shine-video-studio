# Shine 视频工作台 · 最终工程验收 V2 · 2026-11-02

当前结论：本轮软件工程检查全部通过；已提交源码打包及远端核对完成前，暂不宣告最终 ENGINEERING ALL PASS。

## 1. 基线与范围

- 开始时本地 HEAD、fetch 后的 origin/main 均为 `5b06f5e17784d899230b91a6fd6c438c7bc6e57a`，工作树干净。
- 修复分支：`codex/director-acceptance-v2`；没有覆盖其他工作树或重写历史。
- 本轮是向导合同、模式意图、恢复与工程验证修复。用户随后明确要求把核心 LLM 配置纳入新机准备，已一并实现。
- 最终源码/交付提交及包身份：见本报告末尾的交付记录。

## 2. 根因与修复

| 优先级 | 根因 | 修复与证据 |
| --- | --- | --- |
| P0 | 向导使用 `install_approve`，真实 GrantRequest 只接受 `install` | 新增 `frontend/src/setupExecution.ts`，两个设置入口共用授权/审批/执行服务；真实 TypeScript → FastAPI 合同测试 |
| P0 | Decision 缺少 evidence，执行缺少 body，审批还会改变 sha256 | 严格提交 grant_id/snapshot/evidence，安装执行显式提交批准后的 plan_hash 与 executor=local；不把 grant 响应里的 expires_at 混入严格请求 |
| P0 | 实例计划未经批准即 execute | 同一服务先执行 install 授权与批准，再调用 instance-plans 执行端点；实例执行不附带安装专用 executor 字段 |
| P0 | HTTP 200 被当成全部执行成功 | 检查 FILES_VERIFIED/COMPLETED、拒绝 is_mock；服务必须 READY、激活必须 ACTIVE，最终再读取权威 production_mode，Worker 离线不能宣布就绪 |
| P0 | 安装进程的本地执行环境变量未可靠跨重启保留 | bootstrap 持久化默认 opt-in；已有显式 0 保留；launcher 白名单加载该值；客户端读 execution-context，未许可时给出本机配置与重启办法 |
| P0 | 新机准备遗漏文本 LLM，存在 H3 已装好但文本工种不能运行的路径 | 新增 `/setup/llm-readiness`；向导在下载安装前要求已测试真实模型及 12 个文本工种的显式路由；专业新机准备无需项目即可配置 LLM |
| P1 | 单镜头遵守 demoOverride，而连续制作只看 READY | 持久化用户制作意图并共享给两个入口。明确选演示时连续制作禁用，提供切回真实制作按钮。真实能力失效不会把真实提交改成 Mock |
| P1 | 部分 await 无异常闭环，复用取消丢失选择，候选字节被误当成已复用量 | 所有向导入口经互斥异常边界；读取有超时；保留中文原因及错误码；取消保留可继续界面；使用实际 reused_bytes；目录检查失败不悄悄退到重下载 |
| P1 | 关闭、切换作品或 StrictMode 可能留下旧链/停转 | 检查 mounted/cancelled/project 身份；await 后与副作用前设检查点；App 按作品重挂载向导；StrictMode 延后启动且只保留有效 effect |
| P1 | 断线后可能重投未知执行；重复检查新建实例计划 | 请求执行前记录浏览器未确认标记；网络未知不清除，不自动重投；先读数据库计划/运行配置；后端 next-instance-plan 复用已有待审/批准计划，未知状态拒绝继续 |

主要文件：`RealProductionWizard.tsx`、`SetupPanel.tsx`、`setupExecution.ts`、`ProductionSeriesPanel.tsx`、`useStudio.ts`、`App.tsx`、`api.ts`、`api_setup.py`、`api_llm.py`、`comfyops/deployment.py`、`comfyops/llm_readiness.py`、`bootstrap.ps1`、`start_shine.ps1`。

LLM 设置复用 `LLMConfiguration.tsx` / `LLMSetupPanel.tsx`，保留现有 Gateway、SecretStore、provider-profiles 与 agent-routes；没有另造运行时、存储明文密钥或选择备用服务。用户明确确认后，才将所选配置用于全部文本工种；VLM/ASR 路由不被此动作覆盖。检测绑定模型、地址、启用状态、密钥引用与生成配置；修改配置/密钥或重测失败会使旧成功失效。连接/模型列表检测不是结构化文本生成质量验收。

`production_mode` 保留 H3 运行环境的权威语义；LLM 准备有自己的后端状态。新机启用向导同时检查两者，不把用户选择演示与机器是否可真实生产混在一起。

## 3. 真实 API 合同证据

`backend/tests/test_director_setup_contract.py` 通过 Node 执行仓库中的真实 TypeScript 服务，逐次将 fetch 请求送入 FastAPI TestClient、隔离 SQLite、真实 Pydantic、授权/审批接口与执行状态机。仅安装器和 OS/HTTP 外部端口隔离。

| 路由 | 验证 |
| --- | --- |
| POST /preparation/approval-grants | action=install、快照、一次性 grant；错误 install_approve 为 422 |
| POST /comfyops/install-plans/{id}/approve | evidence 必需；批准身份 local-user；批准后哈希改变 |
| POST /comfyops/install-plans/{id}/execute | plan_hash + local；旧哈希/身份不符/过期/未许可拒绝；重复执行拒绝 |
| POST /setup/next-instance-plan | 真正生成/复用计划；STOP/CREATE/INSTALL_NODE/BIND_MODELS 链由真实状态决定 |
| POST /comfyops/instance-plans/{id}/execute | 先审批再执行；未批准/INSTALLING/NEEDS_REVIEW 拒绝，不绕过状态机 |
| POST /setup/profiles | 浏览器直接调用真实路由，注册现有固定工作流 |
| POST /setup/configuration | 浏览器直接调用真实路由，绑定作品/实例生成配置 |
| POST /setup/services/validate | 浏览器直接调用真实路由，真实身份/工作流合同检查，隔离服务探针 |
| POST /setup/runtime/activate | 浏览器直接调用真实路由与持久化状态，读取 ACTIVE/production_mode 验证 |

配置/作品/hash 不符的反向覆盖同时见 `test_runtime_activation.py`、`test_service_validation.py`、`test_setup_cold_start.py`。审批接口、Pydantic 和数据库状态机均没有被 mock 掉。

## 4. 浏览器与模式验收

命令：`uv run --project backend --frozen --with playwright==1.64.1 pytest scripts/test_director_browser.py -v`。

真实 Chrome → Vite 代理 → 独立 loopback Uvicorn → SQLite，不使用 page.route 伪造 API 成功。测试用微型合成清单与真实哈希缓存，隔离 OS 命令、硬件读数及服务探针；不碰用户 ComfyUI、不下载真实模型、不进行 H3 推理。

| 场景 | 结果/范围 |
| --- | --- |
| 缺少模型/组件且 LLM 未配 | 首先阻断 LLM；浏览器添加配置、检测并显式指定全部文本工种后再继续；无提前安装 |
| 需补依赖 | 授权→审批→真实安装状态机→实例链→工作流→配置→服务验证→激活 |
| 服务合同首次失败 | 真实 services/validate 返回 409；外部探针恢复后重新检查成功；已完成安装不重复执行 |
| 模型完整、需建立受管实例 | 真实 next-instance-plan/审批/实例状态机完成链，最终 READY |
| 模型与实例都完整 | 不重新安装；工作流、配置与服务验证、激活成功 |
| 激活后 renderer 空闲 | renderer_online=false 仍保持 READY；刷新不触发安装 |
| 用户选演示 | READY 能力保留，但连续制作禁用；刷新仍保留演示意图，不发送真实 series |
| 漂移、未知执行、身份变化 | 单元/真实路由反向测试阻断；不回退 Mock 或重投 |

浏览器原始 JSON 和截图：`.local-acceptance/director-browser/`；外部执行为 `ISOLATED_SYNTHETIC_PORTS`，真实 H3 为 `NOT_RUN`。不得把这些测试生成的非 Mock 状态机回执解释成真实软件/模型安装证据。

## 5. 最终工程矩阵

| 门禁 | 命令/证据 | 当前结论 |
| --- | --- | --- |
| 后端全量 | `uv run --project backend --frozen pytest backend/tests -ra`，v2-backend-final.log | 1379 passed / 1 skipped / 2 warnings，654.04 秒，0 failed |
| 前端全量 | `npm --prefix frontend test -- --reporter=dot`，v2-vitest.log | 70 passed |
| TypeScript / Vite | `npm --prefix frontend run build`，v2-build.log | PASS |
| Ruff | `uv run --project backend --frozen ruff check backend scripts/test_director_browser.py`，v2-ruff.log | PASS |
| 锁文件/环境 | `uv sync --project backend --locked --check`，v2-lock.log | PASS，Would make no changes |
| 新库 upgrade / check | v2-migrations-verified.log | PASS，f150a0030001，无新升级操作 |
| 历史库副本 upgrade / check | 同上；只读 SQLite backup，原库 SHA-256 不变 | PASS，无漂移 |
| 浏览器合同 E2E | v2-browser.log，三条参数化路径 | 3 passed |
| Windows 启停 | `pwsh -NoProfile -File scripts/verify_stack_operations.ps1 -ApiPort 49281 -WebPort 49282` | PASS，独立目录；自定义代理、NoWorker、重复启动拒绝、Worker 重启、退出验证 |
| cmd / PowerShell 编码与入口 | test_package_entrypoints.py、test_bootstrap_entry.py | PASS，已包含于最终全量 |
| 已提交树打包与逐文件/SHA-256 校验 | build_test_package.ps1 -Verify | PENDING |
| 正常提交与 push / 远端 SHA 核对 | git | PENDING |

证据日志均位于 `.local-acceptance/`，无私人媒体入库或入包。Ruff 为 Python 静态检查；前端没有单独 ESLint script，不虚报额外 lint 门禁。

历史库副本首次采用“所有旧字段逐字节不变”审计，识别出原有 `f121a0010001` 对两条 workflow_profiles.checkpoint_id 的图推导规范化。复核严格按该既有迁移的确定性规则计算期望；其余历史列、行数、请求、素材、镜头、质检与审计均不变，之后 upgrade/check 通过。原库始终只读，源 SHA-256 为 `6b45860caaa9b2ea9c914e62041b639a2883eeba0d3c0320617d95d6baf7a43f`；没有修改迁移来获得 PASS。

## 6. 失败记录、豁免与验证边界

- 首轮全量：1368 passed / 2 failed / 1 skipped。两个失败均为安装脚本错误覆盖显式执行许可 0；已修复并保留原保护断言，另增加“未配置时默认持久化 1”的两例。最终结果以最后一轮完整日志为准。
- 早期前端旧 stub 未提供 execution-context，以及新增最终 Worker 检查揭示旧 fixture 激活后仍返回 DEMO；已补足合同与真实状态迁移，未删除失败用例。
- UI / API 合同 / 外部执行 / 真实 H3 推理为四层证据。前两层本轮实际运行；外部安装/服务进程用合成端口隔离；真实 H3、真实 LLM 文本生成与干净新机大模型安装本轮未运行，不作为软件工程 PASS 的替代证据。
- GitHub Actions 托管 CI：用户豁免。真人体验评价与成片艺术审查：用户豁免。
- 已有真实影片的 pending_review/人工批准状态不改写。没有新增用户主资产、冻结风格或代替用户批准素材，没有修改既有 ComfyUI 安装、模型、用户数据库或历史任务。
- 唯一跳过项：`test_real_managed_startup.py:85`，原有显式门控要求 `SHINE_REAL_COMFY_PROBE=1 and a local ComfyUI install are required`。本轮未启用真实进程探针，不增加 skip、不放宽断言。两条 warning 为现有 AnyIO 废弃别名和一例 SQLAlchemy NULL 主键提示，不是失败。

## 7. 交付记录

全部软件检查通过。源提交、打包文件、校验结果及正常推送核对在提交后的交付审计追加。当前待已提交树打包和推送核对。


提交保留的脱敏证据：[摘要](evidence/20261102-director-v2/summary.json)、[LLM 首步](evidence/20261102-director-v2/llm-first.png)、[演示意图与就绪环境并存](evidence/20261102-director-v2/demo-intent-ready-environment.png)。原始日志 SHA-256 见摘要，详细请求与原始截图留在隔离证据目录，不随安装包发放。
