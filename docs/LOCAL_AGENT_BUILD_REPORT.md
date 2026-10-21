# Shine 本地 Agent 自主补强建设报告

- 日期：2026-10-21
- 分支：`feat/m1-hardening-m2-offline`
- 基线：`main` / `65e0fb590f85f036add495009f36f5519d2e2924`（M0+M1）
- 实现提交：`9d0a09f327a21d4995e68a97d9e50f0bf85e1022`
- 报告补丁提交：见 `git log`（本报告随文档补丁提交）

状态标签：

- **IMPLEMENTED**：代码真实实现。
- **TESTED_OFFLINE**：非 GPU 条件下自动化测试通过。
- **MOCK_VERIFIED**：Fake HTTP / Mock Provider 集成验证通过。
- **PENDING_LOCAL_H3**：必须在用户 Windows + GPU + ComfyUI + H3 环境验证。
- **BLOCKED**：因客观条件本轮无法完成。

---

## 1. 开发开始时的 Git 状态与基线

开始时工作区位于 `main`，无未提交改动，HEAD 为 `65e0fb5 feat: implement Shine M0 and M1 foundation`。远端 `origin/main` 同步。已创建分支 `feat/m1-hardening-m2-offline`，未重置、未强推、未直接修改 `main`。

基线验证：

- `uv sync --project backend --locked`：通过。
- `pytest`：15 passed。
- `ruff check`：通过。
- `npm ci`：首次因本机网络 `ECONNRESET` 失败；改用 npmmirror 与较低并发后安装成功。
- `npm --prefix frontend run build`：通过。

## 2. M1 遗留问题逐项修复结果

| 项目 | 结果 | 实际实现与验证 |
| --- | --- | --- |
| M1-01 风格版本管理 | IMPLEMENTED / TESTED_OFFLINE | 新镜头默认当前冻结版本；新镜头不能绑定旧版本；已有历史镜头可继续修订并保留原版本；新建任务必须使用当前版本；相同草案重复冻结被拒绝；冻结记录版本、哈希、批准人、时间；新版本返回影响范围，并把使用旧版本的未开始任务置为 `NEEDS_REVIEW` + `STYLE_VERSION_SUPERSEDED`，已通过历史任务保持不变。测试覆盖跨项目风格、旧版本新建/生产、版本切换、非法重复冻结。 |
| M1-02 Worker 并发竞争 | IMPLEMENTED / TESTED_OFFLINE | Job 增加 `state_version`；每次状态转换递增；Worker 认领使用条件 UPDATE；回写前校验状态、state version、lease owner、shot revision；渲染期间修订镜头会丢弃过期结果，不写 QC、不进入合格库；过期/不确定外部提交进入对账状态，不盲目重投；失败、语义失败、不确定提交分别处理；保留幂等、Attempt、租约和重试预算。测试覆盖双 Worker 抢任务、渲染中修订、ACK 后崩溃恢复、SUBMITTING 超时、重复确认。 |
| M1-03 镜头级资产绑定 | IMPLEMENTED / TESTED_OFFLINE | 新增 `shot_asset_bindings` 表和 API；每个镜头独立保存多个引用，记录 use_as/role/frame_role/ordinal；任务可省略 references 并使用保存的绑定；显式 references 必须与保存绑定完全一致，防止跨镜头串扰；生成前可读取完整 Reference Manifest；H3 标签由任务级确定性编号重新映射；校验项目、批准、锁定、媒体类型、9/3/3/12 限制。测试覆盖 A/B 镜头隔离、跨项目、未审批、重复、类型错误、数量超限。 |
| M1-04 项目级审计 | IMPLEMENTED / TESTED_OFFLINE | `audit_events` 增加 `project_id`；所有项目/资产/风格/镜头/任务/Worker/QC/返工事件写入项目作用域；迁移回填旧事件；API 支持时间、实体、事件类型、actor 过滤与分页；payload 统一脱敏凭据、Bearer、绝对本地路径；测试覆盖完整历史、过滤、分页、跨项目不可读、路径/密钥不入库。 |
| M1-05 Worker 健康检查 | IMPLEMENTED / TESTED_OFFLINE | 新增 `worker_instances` 心跳表；Worker 启动/每轮/长渲染/错误时更新状态、阶段、当前任务、处理数、最近错误；`/api/v1/system/status` 区分 API、Worker、Mock、真实 H3，并返回待处理/执行中/失败/阻滞数量；Web 顶栏和设置页显示四类状态。测试覆盖在线/离线心跳、异常任务、计数。 |
| M1-06 Windows 单机可靠性 | IMPLEMENTED / TESTED_OFFLINE | SQLite WAL、外键、`busy_timeout`、`synchronous=NORMAL`；中文路径、长文件名、Windows 非法字符、路径穿越测试；上传原子写入与中断残留清理；磁盘满返回 507；数据库短时写锁等待测试；双 Worker、重复 Worker 启动、租约到期、崩溃恢复测试。 |

## 3. M2 已实现与尚未实现

### 已实现

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 独立 LLM Gateway | IMPLEMENTED / TESTED_OFFLINE | OpenAI-compatible 实际 HTTP、Anthropic Messages、Ollama-compatible、Mock；自定义 Base URL/Model/Key；超时/重试/错误分类；JSON 解析与 JSON Schema 校验；能力探测；按 Agent 角色路由；不在业务逻辑硬编码供应商。 |
| API Key 安全 | IMPLEMENTED / TESTED_OFFLINE | Windows DPAPI（非 Windows 为 `0600` 文件）；密钥不入 SQLite、Git、日志、审计或 API 响应；API 只返回 `has_api_key`。 |
| Agent Runtime | IMPLEMENTED / TESTED_OFFLINE | 保留原 `ProducerAgentRuntime` ShotSpec 校验；新增剧本解析、故事结构、分场/分镜规划、资产匹配、结构化 ShotSpec、风格绑定、修复建议；Agent 无 DB/文件/Shell/审批工具。 |
| 剧本导入 | IMPLEMENTED / TESTED_OFFLINE | TXT、Markdown、JSON、DOCX（stdlib zip+XML）、PDF（pypdf）；PDF/DOCX 错误与空文档拒绝；大小限制 20 MiB。 |
| H3 Prompt Compiler | IMPLEMENTED / TESTED_OFFLINE | 独立 `H3PromptCompiler`；T2VA/I2VA/FL2VA/L2VA/Ref2VA；官方三字段/六段结构；标签、数量、时长、对白、风格版本校验；记录编译器/规则版本与 Prompt SHA；固定官方 skill/rule 哈希。 |
| ComfyUI HTTP Adapter | IMPLEMENTED / MOCK_VERIFIED | `/system_stats`、`/object_info`、`/upload/image`、`/prompt`、`/queue`、`/history`、`/view`、中断；超时、断线、prompt_id 持久化、对账；`/prompt` 超时标记为不确定，禁止盲目重投。 |
| Fake ComfyUI Server | IMPLEMENTED / MOCK_VERIFIED | 模拟提交成功、超时但已接收、历史恢复、输出缺失、节点错误、参数错误、重复任务、断线。 |
| 镜头续接数据契约 | IMPLEMENTED / TESTED_OFFLINE | `cut/last_frame/video_reference/audio_reference`；显式依赖；上游修订只失效下游；ComfyUI 续接要求真实源 artifact。 |
| Fake OpenAI Server | IMPLEMENTED / MOCK_VERIFIED | 鉴权、模型列表、JSON 模式回退、500 重试、401、超时、畸形 JSON。 |

### 尚未实现 / 阻塞

- **BLOCKED**：真实 H3 ComfyUI API Workflow JSON 未提供，因此无法把具体 H3 节点/输入字段映射为已验证模板。当前适配器是通用、严格校验的模板机制，UI 中的默认图仅为开发占位，不能冒充真机 H3 图。
- **PENDING_LOCAL_H3**：真实 T2VA/Ref2VA 生成、人物一致性、口型、画质、GPU 性能、显存占用、24 FPS/分辨率实测。
- **PENDING_LOCAL_H3**：真实 ComfyUI 崩溃后的队列/历史对账（Fake Server 已验证协议，但未验证真实 ComfyUI 行为）。
- **BLOCKED/PENDING**：真实 Anthropic/OpenAI/Ollama 凭证与真实 VLM/ASR 响应未测试；仅有 Fake HTTP 与 Mock。
- 未实现真实长镜头续接实验；本轮只完成数据契约、依赖与模拟测试。

## 4. M3 已实现与尚未实现

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 确定性技术 QC | IMPLEMENTED / TESTED_OFFLINE | 文件存在/大小/SHA、容器结构、可选 FFprobe 解码、时长、分辨率、帧率、音轨、可选 FFmpeg 静音检测；缺失工具标记 `unavailable`，绝不冒充 `pass`。 |
| 纯 Python 媒体探测 | IMPLEMENTED / TESTED_OFFLINE | MP4/MOV 容器、mvhd/tkhd/stts、WAV、PNG/JPEG/WEBP；仅结构探测，不声称解码。 |
| VLM/ASR 插件协议 | IMPLEMENTED / TESTED_OFFLINE | 类型化 finding/evidence；Mock/OpenAI-compatible VLM/ASR；远端审片必须项目级授权且只发送派生证据帧。 |
| 规则融合 | IMPLEMENTED / TESTED_OFFLINE | 技术失败优先；高置信 hard 失败；低置信 hard / uncertain 转人工；高置信 soft 转人工；覆盖范围明确记录是否真正运行语义/ASR。 |
| RepairPlan 决策引擎 | IMPLEMENTED / TESTED_OFFLINE | 引用/对白/提示词/工作流/临时故障分类；最多 2 次语义返工；自动重试仅限用户开启且有界的安全动作；通过任务保护；记录返工记录与成本字段。 |
| 自动返工状态机 | IMPLEMENTED / TESTED_OFFLINE | `REPAIR_PENDING → READY → QUEUED`，预算耗尽/低置信/类型不明转 `NEEDS_REVIEW`；Mock 失败不会自动成功。 |
| 真实画质合格率 | PENDING_LOCAL_H3 | 未宣称任何真实镜头画质通过率。 |
| Evidence Viewer / 成片导出 | 部分实现 / 未完成 | Web 已展示 QC 检查、hard failure、coverage、返工记录；未实现抽帧证据浏览器、字幕/成片拼接。 |

## 5. 新增及修改的代码文件

新增后端模块：

- `backend/shine/api_common.py`、`api_agent.py`、`api_comfy.py`、`api_llm.py`
- `backend/shine/llm/{__init__,base,secrets,openai_compatible,anthropic,mock,gateway}.py`
- `backend/shine/agents/{script,planner,tools}.py`（并扩展 `runtime.py`）
- `backend/shine/h3/{__init__,compiler}.py`
- `backend/shine/comfy/{__init__,client,workflow,provider}.py`
- `backend/shine/quality/{__init__,technical,semantic,repair}.py`
- `backend/shine/media/{__init__,probe,artifacts}.py`
- `backend/alembic/versions/9f2c4d7a10b3_m1_hardening_m2_offline.py`

修改后端：

- `config.py`、`db.py`、`models.py`、`schemas.py`、`security.py`、`services.py`、`worker.py`、`main.py`
- `backend/alembic/env.py`（迁移不关闭应用 logger）
- `backend/alembic.ini`（`path_separator`）
- `backend/pyproject.toml`、`backend/uv.lock`（`httpx`、`jsonschema`、`pypdf`）

新增测试：

- `backend/tests/conftest.py`、`fake_http.py`
- `test_style_versions.py`、`test_bindings.py`、`test_audit.py`、`test_worker_health.py`、`test_concurrency.py`、`test_security_windows.py`
- `test_llm_gateway.py`、`test_agent_runtime.py`、`test_h3_compiler.py`、`test_comfy_adapter.py`、`test_continuity.py`
- `test_technical_qc.py`、`test_semantic_qc.py`、`test_repair_engine.py`、`test_alembic.py`

前端：

- 重写 `frontend/src/App.tsx`
- 新增 `frontend/src/api.ts`、`constants.ts`
- 新增 `frontend/src/components/{AssetPanel,StylePanel,ShotPanel,ProductionPanel,SettingsPanel,AuditPanel}.tsx`
- 扩展 `frontend/src/styles.css`

脚本与文档：

- `scripts/check_environment.ps1`、`start_shine.ps1`，新增 `scripts/check_health.ps1`
- `.env.example`、`config/environment.md`
- `README.md`、`AGENTS.md`、`docs/architecture.md`、`docs/contracts.md`、`docs/local-h3-acceptance.md`、`docs/upstream-audit.md`
- `docs/adr/0002-offline-llm-h3-qc-boundaries.md`、本报告

## 6. 数据库迁移变更及兼容性

新迁移：`9f2c4d7a10b3_m1_hardening_m2_offline`，`down_revision = 70afa9c775ab`。

- 新增表：`project_documents`、`shot_asset_bindings`、`media_artifacts`、`repair_records`、`provider_profiles`、`agent_routes`、`workflow_profiles`、`external_job_reconciliations`、`app_settings`、`worker_instances`。
- 扩展列：项目语言/远端审片/自动返工；资产 role/entity/tags/时长/分辨率/来源/审批/锁定时间；风格审批说明；Job `state_version`/mode/workflow/prompt hash/artifact/QC；Attempt 进度/输出/成本/对账；依赖 kind；QC reviewer/artifact；审计 `project_id`。
- 审计回填：按 project/asset/shot/job/style_version 实体回填 `project_id`。
- **兼容性发现与修复**：最初使用 SQLite batch 迁移，Alembic 的 move-and-copy 会 DROP 父表并触发 `ON DELETE CASCADE`，导致已有 style/shots/jobs 行被静默删除。已改为 SQLite 原生 `ALTER TABLE ADD COLUMN` / `DROP COLUMN`，迁移测试验证 M1 旧库数据完整保留。
- 验证：全新空库 `upgrade head` 通过；从 `70afa9c775ab` 携带旧项目/风格/镜头/任务/审计数据升级到 head 通过。

## 7. 自动化测试实际命令和结果

```powershell
uv sync --project backend --locked
uv run --project backend --locked pytest -c backend/pyproject.toml backend/tests
uv run --project backend --locked ruff check backend/shine backend/alembic backend/tests
npm ci --prefix frontend
npm --prefix frontend run build
```

实际结果：

- `uv sync --locked`：通过。
- `pytest`：**105 passed, 1 warning**（warning 为 starlette TestClient 的 anyio 弃用提示）。
- `ruff check`：**All checks passed!**
- `npm ci`：本机首次失败（`ECONNRESET`），使用 `registry=https://registry.npmmirror.com/` 且降低并发后成功；不是代码缺陷。
- `npm run build`：通过（`tsc -b && vite build`，dist 生成）。
- Alembic：空库与 M1 旧库升级均通过（包含在 pytest 中）。

测试覆盖的关键场景：

- 未冻结风格禁止生产；旧风格新镜头/新任务被阻止；历史镜头可保留旧风格。
- 未审批/未锁定素材不可用；跨项目素材/风格拒绝；镜头 A/B 引用不串扰。
- 渲染中修订镜头不非法回写；双 Worker 不重复认领；ACK 后恢复不重投；SUBMITTING 超时进入对账。
- Job DAG 依赖失效传播；独立镜头不受影响；续接需要已通过源任务。
- Agent 非法 JSON/错误风格被拒绝；H3 标签/数量/时长/对白错误被拒绝。
- Fake ComfyUI 完整生命周期；超时但已接收可对账；缺失输出/节点错误/参数错误/重复任务/断线。
- 技术 QC 缺失工具为 `unavailable`；损坏/时长错误/缺音轨失败；语义硬错误/低置信/soft 分流。
- 返工预算耗尽、低置信转人工、通过版本保护。
- 路径穿越、长文件名、Windows 非法字符、磁盘满、SQLite 忙等待。
- API Key 与绝对路径不进入审计；Mock 结果不冒充真实视频。

## 8. Windows 运行验证结果

- `alembic upgrade head` 在 `.local-data/e2e` 全新目录成功。
- `uvicorn shine.main:app --host 127.0.0.1 --port 8000` 启动成功，`/health` 返回 `status=ok`、`render_provider=mock-only`、`h3_connected=false`。
- 通过真实 HTTP 创建项目、七维风格草案、冻结 V1、上传中文文件名 PNG、批准/锁定、创建带绑定分镜、编译 Ref2VA Prompt、创建 Mock 任务。
- `python -m shine.worker --once` 完成 Mock 任务，状态 `PASSED`，`video_created=false`；`/system/status` 正确报告 Worker 停止与处理数。
- 启动持续 Worker 与 Vite `127.0.0.1:5173`，页面返回 200。
- 浏览器 QA：顶部 API/Worker/Mock/H3 状态、指标卡、素材行、冻结 V1、镜头绑定、PASSED MOCK 任务、生产与质检、Prompt 预览、设置与 Worker 在线均正常；无 console error、无失败网络请求。唯一观察：新增任务在轮询间隔内已完成，状态条显示 0 待处理属于真实结果，不是状态不同步缺陷。
- `scripts/check_environment.ps1` 已验证可执行；`scripts/check_health.ps1` 可读取 API/Worker/Mock/H3 状态。

## 9. Mock HTTP 集成测试结果

- Fake OpenAI-compatible：鉴权、`/models`、JSON 模式、JSON 模式被拒后的回退、500 重试、401 分类、超时、畸形 JSON、schema 不匹配。全部通过。
- Fake ComfyUI：`/system_stats`、`/object_info`、上传、提交、队列、历史、下载、节点错误、参数错误、重复提交、超时但已接收、历史对账、输出缺失、断线。全部通过。
- ComfyUI Worker E2E：Fake 输出 MP4 结构文件；离线无 FFprobe 时技术 QC 为 `needs_review`，任务进入 `NEEDS_REVIEW`，`video_created=true`、QC `is_mock=false`，证明没有把“结构可读”冒充“真实解码通过”。

## 10. 安全审查结果

已落实：

- API/Vite 默认 loopback；未实现认证，未开放 LAN。
- 上传：路径分隔符清洗、Windows 非法字符拒绝、扩展名/签名校验、可配置大小限制、内容寻址、原子写入、磁盘满 507。
- 密钥：DPAPI / `0600` 文件，不入 SQLite/Git/审计/日志/前端响应；API 只返回 `has_api_key`。
- 审计：敏感键/值、Bearer、绝对本地路径脱敏。
- Agent：无 DB/文件/Shell/审批工具；无法批准/锁定资产或冻结风格。
- 远端语义审片：项目级显式同意 + 仅派生证据帧/音频。
- 真实 H3 唯一性：无备用视频模型；ComfyUI 错误不静默回退。
- 数据库：单一 SQLite；迁移不回退用户真实库；测试使用临时库。

残余风险：

| 风险 | 级别 | 说明 |
| --- | --- | --- |
| 无用户认证 | 中 | loopback 下可接受；开放 LAN 前必须补认证/CSRF/CORS。 |
| 可配置 Provider/ComfyUI URL 的 SSRF 面 | 低-中 | 仅本机管理员可配置；未提供任意 URL 读取工具。 |
| Prompt/剧本文本含隐私 | 中 | 存 SQLite，不入日志；仍需用户自行保护本机数据目录。 |
| 真实密钥/VLM 未实测 | 中 | 只有 Fake/Mock 证据。 |
| 工作流模板未映射真实 H3 | 高 | 已明确标记 BLOCKED，不能用于真机生产。 |

## 11. 待真实 H3 测试的具体清单（PENDING_LOCAL_H3）

1. 记录 Windows 版本、GPU/VRAM、ComfyUI commit、H3 节点 class、checkpoint 文件名/SHA-256、FFmpeg 版本。
2. 导出用户真实 ComfyUI API Workflow JSON，注册 Workflow Profile，映射 `positive_prompt`、`seed`、`duration`、参考图/视频/音频槽位；验证 `/object_info` 节点与字段。
3. 在真实 ComfyUI 上运行 4–6 秒 T2VA，记录 submit intent、`prompt_id`、prompt/workflow/checkpoint hash、输出路径。
4. FFprobe 验证解码、音轨、24 FPS、4–15 秒、分辨率、画幅；无 FFprobe 时必须保持 `needs_review`。
5. Ref2VA：验证 9 图/3 视频/3 音频/12 总文件、每类 15 秒、标签与文件 hash 一一对应、对白 speaker 绑定。
6. 提交后、ACK 前停止 ComfyUI，验证历史/队列对账、`NEEDS_REVIEW`、绝不盲目重投；ACK 后恢复不产生第二个任务。
7. 渲染中修订镜头，验证过期结果丢弃、下游失效、独立镜头继续。
8. 制造时长错误/缺音轨/损坏输出，验证技术 QC 失败、返工分类、预算上限、人工升级。
9. 如配置真实 VLM/ASR，验证项目授权、只发送派生证据、低置信转人工。
10. 仅保留非敏感证据并标记 PASS/FAIL/BLOCKED；不得把 Mock/Fake 结果写成 H3 验收。

## 12. 未解决问题及严重程度

| 问题 | 严重程度 | 状态 |
| --- | --- | --- |
| 真实 H3 API Workflow 映射缺失 | 高（阻塞真机生产） | BLOCKED，等待用户导出 workflow JSON |
| 真实 GPU/H3 画质、口型、人物一致性、性能 | 高（产品验收） | PENDING_LOCAL_H3 |
| FFmpeg/FFprobe 未安装 | 中（QC 覆盖不足） | PENDING_LOCAL_H3；代码如实标记 unavailable |
| 真实 LLM/VLM/ASR 凭证与响应 | 中（仅 Fake 验证） | BLOCKED（无凭证） |
| 无用户认证 | 中（LAN 暴露风险） | 未实现；保持 loopback |
| 真实长镜头续接实验 | 中 | 仅数据契约/依赖测试 |
| 成片拼接/字幕/交付 | 中 | 未实现（M4） |
| 前端状态条在任务快速完成时显示 0 | 低（真实计数） | 非缺陷；可后续增加“最近完成”提示 |
| npm 本机网络不稳 | 信息 | 环境问题；代码构建通过 |

## 13. 实际 Git Commit SHA

- 实现提交：`9d0a09f327a21d4995e68a97d9e50f0bf85e1022`
- 报告 SHA 补丁提交：本报告所在的文档补丁提交（`git log` 可见）

未强推、未合并 `main`。远端推送/PR 按仓库现有授权策略执行；如无授权，保留本地提交并在此报告。
