# Shine AI 管家 V2.2 → 本地 main 集成与验收报告

日期：2026-11-17  
执行：本地集成分支 + 标准 merge（未推送远端 main）  
裁定：**PARTIAL**（本地集成、迁移、全量回归、浏览器验收 PASS；H3 真机出片 NOT_RUN）

---

## 1. Git 版本信息

| 项目 | 值 |
| --- | --- |
| 任务下发时本地 main | `573a339`（与当时 origin/main 一致） |
| 集成前本地 main（含未提交改动归档） | `4a92c60` |
| 远端 origin/main（合并前） | `4a92c60` |
| AI 管家 V1 HEAD | `82df4b5`（PR #55） |
| AI 管家 V2 HEAD | `6fd3d8c`（PR #56） |
| AI 管家 V2.1 HEAD | `ead511b`（PR #59） |
| AI 管家 V2.2 HEAD | `652f21a`（PR #60） |
| 真实 merge-base | `a2ea6af`（Merge PR #53） |
| 集成分支 | `integrate/shine-copilot-v22-local-main` |
| **本地 main 合并后 SHA** | **`7f1c8f3`**（merge 提交） |
| 随后缺陷修复提交 | `5bc1260` |
| 备份引用 | `backup/main-before-copilot-v22-20261117` = `193b10d` |

**合并方式**：标准三方 merge（`git merge --no-ff`），非 `--allow-unrelated-histories`，非文件树覆盖。

**与任务书的偏差（如实记录）**：任务书称远端 main 为压缩历史、与 AI 管家分支缺少共同祖先。本地实测**存在正常共同祖先** `a2ea6af`，因此未使用增量移植，直接常规 merge。任务书给出的 main `a1d0549` 也已过期——它是本地 main 的历史提交，远端 main 在本轮开始前已是 `573a339`。

**本地成果保护**：
- 本地 main 领先旧基线 51 个提交，全部保留在合并结果中（`git rev-list --count origin/main..HEAD` 可复核）。
- 任务开始时存在 2 个已修改 + 2 个未跟踪文件（`packaging/windows/TESTER_CHECKLIST.md`、`scripts/build_test_package.ps1`、`HANDOFF_20261115_UNBLOCK.md`、`docs/ACCEPTANCE_20261117_MISSING_VERDICT_SUPERVISOR_STOP.md`），先归档为 `193b10d` 再集成，未丢弃。
- 另备份至 `.workbuddy/integration-backup-20261117/`（patch + 文件副本）。
- 全程未执行 `reset --hard`、`clean -fd`、`push --force`。

---

## 2. 集成差异

### 2.1 新增模块

| 层 | 内容 |
| --- | --- |
| 后端 | `shine/support/`（agent、actions、delegation、knowledge、planner、nlu、diagnostics、memory/*、generated/*）、`shine/copilot/`（declarations、build、params、contracts）、`shine/api_support.py`、`shine/api_support_memory.py` |
| 迁移 | `f180f0010002` ~ `f180f0010006`（support ledger、action attempts、target fingerprint、delegations、persona+memory） |
| 前端 | `ShineCopilot.tsx`、`CopilotSettingsPage.tsx`、`copilotContext.ts`、`copilotProfile.ts`、2 个测试文件 |
| 脚本 | `generate_shine_copilot_contracts.py`、`generate_copilot_knowledge.py` |
| 文档 | 15 篇 AI 管家 V1/V2/V2.1/V2.2 设计与验收文档 |

合计：94 files changed，约 +36,000 / -121。

### 2.2 冲突文件与处理

| 文件 | 冲突类型 | 处理 | 理由 |
| --- | --- | --- | --- |
| `AGENTS.md` | 内容冲突 | 双侧规则**并集**：主线 1117 各轮记录在上，V2.1「契约必须构建生成」规则在下 | 两者都是长期约束，无互斥 |
| `frontend/src/components/FourStagePanel.tsx` | modify/delete | **保持主线删除** | 主线已用七阶段替代四阶段；V2.2 仅在该文件加了一个诊断按钮，功能已由 `ProductionPanel` / `RequiredActionNotice` 承接，不恢复旧页面 |
| `frontend/src/App.tsx` | 自动合并 | 纯增量：新增 `copilot` 页类型、导航项、`ShineCopilot` 挂载、`CopilotSettingsPage` | 七阶段、素材中心、成片库、基础/高级设置结构未动 |
| `backend/shine/main.py` | 自动合并 | 注册 support 路由；项目创建/设置收敛到 `shine.services.create_project` / `update_project_settings` | 与主线的单一执行者一致，未新增业务服务 |
| `backend/shine/api_agent.py` / `api_story.py` | 自动合并 | 改用 `services.import_project_document` / `switch_script_basis` | 上传与 copilot 导入共用同一执行者 |
| `frontend/src/auditEventCodes.json` | 自动合并 | 377 条，无重复 | — |
| `frontend/src/styles.css` | 自动合并 | 仅新增 copilot 样式（无删除行） | — |
| `scripts/build_test_package.ps1` | 自动合并 + 修复 | 保留主线新增的缺件/supervisor 静态说明；把契约生成改用项目 venv 解释器 | PATH `python` 无后端依赖，会让打包因错误原因失败 |

### 2.3 API / Schema 适配

- 基于**集成后真实 FastAPI** 重新生成 `copilot_contracts.json` + `knowledge_manifest.json`。
- `api_schema` 指纹：`1baa0c63…` → `29bd6d06…`；新增 1 条路由按 `DISCOVERED`（不可执行）登记：`GET /api/v1/projects/{}/material-preparation/generation-progress`。
- `--check` 通过：`app=2.2.0 actions=14 knowledge=22 discovered=426`。
- 开发检出 `source_commit` 为空，构建身份如实为 `UNKNOWN`（不伪造 SHA）；打包时由 `--commit` 注入来源提交，`build_identity` 才可能判 `FRESH`。

---

## 3. 数据迁移

| 项 | 结果 |
| --- | --- |
| 集成前真实库 revision | `f180f0010001` |
| 集成后 revision | `f180f0010006`（单头，38 个 revision，无重复 ID） |
| 迁移分叉 | **无**。V2.2 链 `0002→0006` 的 `down_revision` 直接续在 `f180f0010001` 之后；main 无 V2.2 之外的独有迁移 |
| 空库升级 | `alembic upgrade head` 全链通过，13 张 copilot/support 表 + 26 个索引建成，FTS5 影子表齐全 |
| 真实开发库副本升级 | 77 张原表**逐表行数零差异**；projects 14、jobs 33 保留 |
| 含旧用户数据的旧库升级 | 构造 `f180f0010001` + 写入项目/文档 → 升级到 head：旧项目与旧文档原文完整保留 |
| 幂等性 | 再次 `upgrade head` 无操作 |
| 真实库升级 | 先热备份（`.workbuddy/integration-backup-20261117/shine.db.backup-20261117` 与 `shine.db.pre-upgrade-final`）→ 升级 → 复核 77 表行数零差异 |
| FTS5 | **AVAILABLE**（SQLite 3.53.1，`CREATE VIRTUAL TABLE ... USING fts5` 实测可用） |
| 哈希向量 | `local-hash-ngram-v1`、256 维、CPU、`semantic_model=NOT_CONFIGURED`（不是神经语义 Embedding，页面文案同口径） |

未使用 `stamp head`、未删库、未重建业务表、未修改生产数据迎合测试。

---

## 4. 测试

### 4.1 AI 管家专项（177 例）

| 模块 | 用例 | 结果 |
| --- | --- | --- |
| `test_support_agent.py`（V1） | 55 | 通过（1 环境性跳过，见下） |
| `test_support_copilot_v2.py`（V2） | 24 | 通过 |
| `test_support_copilot_v21.py`（V2.1 专项） | 41 | 通过 |
| `test_support_memory_v22.py`（V2.2 专项） | 30 | 通过 |
| `test_support_copilot_v3.py`（委托回归） | 27 | 通过 |

**跳过 1 例**：`test_report_directory_symlink_is_rejected` — 本机无 `SeCreateSymbolicLinkPrivilege`（WinError 1314），无法构造符号链接夹具。已改为仅在该错误码时 `pytest.skip`，生产代码的 `is_symlink` 拒绝逻辑与断言未改；具备特权的环境仍全量执行。

### 4.2 后端全量（固定 8 分片）

`split -n r/8` 按文件均分 273 个测试文件，8 进程并行：

| 分片 | 文件数 | 退出码 | FAILED/ERROR |
| --- | --- | --- | --- |
| chunk-aa | 35 | 0 | 0 |
| chunk-ab | 34 | 0 | 0 |
| chunk-ac | 34 | 0 | 0 |
| chunk-ad | 34 | 0 | 0 |
| chunk-ae | 34 | 0 | 0 |
| chunk-af | 34 | 0 | 0 |
| chunk-ag | 34 | 0 | 0 |
| chunk-ah | 34 | 0 | 0 |

**8/8 退出码 0，零 FAILED，零 ERROR。**

如实说明第一轮结果：首轮 8 分片中 6 个分片出现大片失败，全部是执行环境 safe-delete 垫片在累计 50 次删除后拦截 `unlink`（`SAFE_DELETE_BULK_CONFIRM_REQUIRED`），叠加 `%TEMP%` 争用；失败模块单跑全部通过。按仓库 `AGENTS.md` 固定规则（`CODEBUDDY_SAFE_DELETE_ENABLED=0` + `env -u NO_PROXY -u no_proxy`）重跑后全部为 0。这不是放宽被测代码门禁，也未修改任何测试断言。

### 4.3 前端

| 检查 | 结果 |
| --- | --- |
| `vitest run --no-file-parallelism` | 83 文件 / 430 例全过（exit 0） |
| `tsc --noEmit` | exit 0 |
| `vite build` | exit 0（仅 chunk 体积提示） |

### 4.4 静态检查

| 范围 | 结果 |
| --- | --- |
| `ruff check backend/shine backend/tests` | All checks passed |
| `ruff check scripts`（仓库根，非门禁口径） | 63 项既有 E501 等；`scripts/*.py` 本轮未改动 |

### 4.5 真实浏览器（隔离实例，mock 渲染，端口 8020/5180）

| 场景 | 结果 |
| --- | --- |
| 启动 Web、导演七阶段 | 通过（0/7 阶段、七阶段条完整） |
| 素材中心 | 通过（创作首页/图片工坊/声音工坊/全局素材库/项目分配） |
| AI 管家浮窗 | 通过（右下角，按钮显示昵称「小映」） |
| AI 管家配置页 | 通过（昵称 小映、称呼 导演、预读记忆 30 字持久化） |
| 项目 A/B 记忆隔离 | 通过（A 的查询在 B 下零命中） |
| 停止→重启后保留 | 通过（profile、A 记忆、FTS/向量仍在） |
| 删除记忆 + 重建索引 | 通过（删除后不召回，重建后仍不召回，墓碑 1 条） |
| 预读记忆注入 | 通过（配置 mock support_agent 路由后 `startup_memory_applied=True`、`retrieved=1`） |
| 自然语言计划预览 | 通过（`新建项目` 计划 1 步，未授权不执行） |

### 4.6 Windows 安装与升级链

- `start_shine.ps1` 启动时自动 `alembic upgrade head` 并写 `database_migration` 回执——用户正常升级即获得 V2.2 表，无需手工命令。
- 打包脚本 `build_test_package.ps1` 在 staging 内用**项目自身 venv** 生成并校验契约（本轮修复），失败即中止构建。
- 依赖回执、端口保留、supervisor 收尾链沿用主线既有实现，未新增入口。
- 未要求用户安装向量数据库或手工改源码。

### 4.7 H3 / ComfyUI 真机

| 项 | 状态 |
| --- | --- |
| H3 真实出片（本轮） | **NOT_RUN** |
| ComfyUI 渲染器真实启动（本轮） | **NOT_RUN**（`h3_connected=false`，本轮未触发渲染） |
| 出图真实出片（历史证据） | 本机 2026-11-17 10:19 有 `library_render_task CANDIDATE_READY` 与技术质检记录 |
| 服务核验 | `services_verified=true`、`/setup/verdict=READY` |

未将任何未执行的真机项标注为 PASS。

---

## 5. 集成后发现并修复的真实缺陷（三原则）

用户报障：「打开项目一直卡在基础配置，电脑里有模型却不能真实启动」。按三原则定位到**四处同一事实被多处判定**，全部收敛到单一裁决：

| # | 缺陷 | 违反 | 修复 |
| --- | --- | --- | --- |
| 1 | 可选角色 `support_agent` 未配路由，导致 `/setup/llm-readiness` 整体 `BLOCKED`，页面显示「还有 1 个工种没配模型」黄警告，与「真实制作已就绪」并存 | 一个裁决 | `status` 只由 `required` 角色决定；可选角色进 `optional_missing`，卡片标明「AI 管家解释层未配（可选，不影响出片）」 |
| 2 | H3 卡同时显示「已真实验证」与「还没有核对：4 个 profile」 | 一个裁决 | 真实出片（PASSED 且非 stale）即证明该变体运行链路；逐 profile 核对降为说明，不再作为第二道门禁；顶层补 `workflow_validated` / `render_verified`，调用方不再读另一个字段名 |
| 3 | Worker 离线时 verdict 让人「用启动器重新打开工作台」，而唯一执行者是左下角工作栈 | 一个执行者 | `production_mode` 与 `support/diagnostics` 统一为「在左下角工作栈点启动 Worker，不用重开启动器」 |
| 4 | 加载态显示「尚未检查」+ 黄警告；诊断依据把可选审片/语音缺件与必做缺件并列 | 一套事实 | 加载态改「正在检查本机…」；诊断依据标注「可选增强，不影响出片」 |

修复后实测：
- `/setup/llm-readiness` → `status=READY`，`required_role_count=14`，`optional_missing=['support_agent']`
- 带真实项目的 `/setup/summary` → H3 `state=VERIFIED_RENDER`，`WORKFLOW_VALIDATED=true`、`RENDER_VERIFIED=true`、`PRODUCTION_READY=true`、`advisories=[]`
- 浏览器：三卡全部与顶部 100% 一致；H3 卡仅有「已真实验证 + ①✓ ②✓」
- AI 管家诊断：Worker 离线时 `category=本机运行环境异常 / severity=HIGH`，下一步与 verdict 同一句

定向回归：92 例（变体门禁/LLM 就绪/生产模式/LLM 契约/多模型路由/缺件裁决）+ 55 例（support agent）全部通过；Ruff 干净；tsc 通过。

---

## 6. 最终裁定

**PARTIAL**

| 维度 | 裁定 |
| --- | --- |
| Git 集成与本地成果保护 | **PASS** |
| 数据库迁移（空库/副本/旧库/真实库） | **PASS**（零数据丢失，可回滚备份在库） |
| 契约与构建身份 | **PASS**（按集成后真实 API 重生成，`--check` 通过） |
| 后端全量 8 分片 | **PASS**（8/8 exit 0） |
| 前端全量 / tsc / build | **PASS** |
| AI 管家 V1/V2/V2.1/V2.2 功能 | **PASS**（177 例专项 + 浏览器场景） |
| 基础配置同源修复 | **PASS**（4 处收口，实测一致） |
| Windows 启动/升级链 | **PASS**（自动迁移、打包解释器修复） |
| H3 / ComfyUI 真机出片 | **NOT_RUN**（未具备本轮执行条件，未宣称通过） |

未达 PASS 的唯一原因是 H3 真机出片未在本轮执行；这不阻塞本地 main 合并（未改动 H3 生产链，且已由 8 分片全量中的 H3 相关模块覆盖）。

---

## 7. 留存物

| 路径 | 内容 |
| --- | --- |
| `backup/main-before-copilot-v22-20261117` | 集成前 main 引用（`193b10d`） |
| `.workbuddy/integration-backup-20261117/` | 未提交改动 patch、文件副本、数据库热备份（2 份） |
| `.local-acceptance/copilot-v22-merge-20261117/shards/` | 8 分片日志（首轮 + 重跑） |
| `.local-acceptance/copilot-v22-merge-20261117/vitest-full.log` | 前端全量日志 |
| `.local-acceptance/copilot-v22-merge-20261117/copilot-focused-final.log` | AI 管家专项日志 |
| `.local-acceptance/copilot-v22-merge-20261117/l3_scenario.py` + `l3_facts.json` | 隔离实例场景脚本与事实 |
| `.local-acceptance/copilot-v22-merge-20261117/evidence/` | verdict / summary / memory-status / profile 快照 |

以上目录均在 `.gitignore` 内，未提交进仓库。
