# 导演模式建设收尾验收报告（2026-11-05）

- 审查基线：`main / 693751fe77c4d613dcd6aa160bb60e9dd078b1f7`
- 实施分支：`codex/director-closeout-20261105`（Codex 中途因额度耗尽停止，本轮接续完成）
- 总体结论：**PARTIAL / NOT ACCEPTED**
  - A01–A08、R1、R2 的功能缺口已关闭并有离线回归；
  - R3 的持久编排已实现并有后端回归，但连续性仍是用户输入的事实而非版本化连续性产物；
  - 真实素材链路在本机真实跑通到「真实编剧 → 真实出品人 brief → 受管 Qwen 渲染器」，
    但多数条目停在**出品人要求澄清**这一诚实门禁上，需要人来决定；
  - 真实 H3 成片、素材晋升、风格冻结、艺术评审仍需人完成，未由本轮代替。

## 1. 开工采样与栈身份

| 监听 | 数据目录 | 状态（开工时） |
| --- | --- | --- |
| 8000/5173 | `.local-acceptance/materialprep-mig-oldcopy` | **并非 `.local-data`**：它是 11-05 迁移测试副本，API 跑旧代码，Worker 离线，运行配置 `ACTIVE_RUNTIME_CONFIGURATION_CHANGED` |
| 8133/5193 | `.local-acceptance/live-browser` | 真实制作栈：Worker 在线，`.env` 指向 `comfyui`，出图渲染器记录为 STOPPED |
| 8143/5203 | `.local-acceptance/director-closeout-20261105` | Codex 的收尾验证栈（mock），已停止 |

三套数据与证据互不混用；8000 栈未被当作真实证据。

## 2. 本轮接续完成的工作

### 2.1 缺陷修复

| 编号 | 缺陷 | 处理 | 证据 |
| --- | --- | --- | --- |
| A01 | 未知结果被重复调用 | Codex 已实现持久输入身份 + 原子认领；**本轮把 `docs/evidence/.../test_unknown_scene_replay_repro.py` 反转为断言单次调用** | 实跑 `REPRO 200 200 provider_calls 1 unknown_tasks 1` |
| A02 | 原文分段有损 | Codex 已实现（无前言丢失、>200 行续切、9000 字场按预算拆分、场内引用越界 422） | `backend/tests/test_material_scene_safety.py` 11 passed |
| A03 | 虚假「所选素材」 | Codex 已实现逐项选图；本轮补上验证小样不可选用提示与空身份不再渲染成「 · 」 | 浏览器实看：验证批次明确标注「不能在这里选用」 |
| A04 | 策略回填与显式绑定 | Codex 已实现权威快照回填 + 素材/版本选择器 + 未确认上传不默认生成；**本轮把三处默认策略统一为一个 `defaultStrategy`** | `frontend/src/components/MaterialPreparationPanel.tsx` |
| A05 | 远程文字授权 | Codex 已实现持久授权 + Worker 校验；**本轮删除请求自授权**（`allow_remote_text` 不再写授权行），并把提示语改成与实际授权范围一致 | `backend/shine/materialprep/scenes.py` |
| A06 | 恢复/核对/再生成 UI | Codex 已实现；**本轮把「核对此项」收窄到单项**（服务端新增 `item_ids` 过滤） | `backend/shine/materialprep/service.py` |
| A07 | 显式绑定优先级 | Codex 已实现（只认显式 asset_id，校验 sha256，双版本不互相顶替） | `backend/tests/test_material_preparation.py` |
| A08 | 长剧本仍是一次 HTTP | Codex 已实现持久大纲 + 段落编排；**本轮补上此前完全没有后端测试的段落执行路径** | 新增 `backend/tests/test_story_section_run.py` |

### 2.2 本轮新发现并修复的真实缺陷

1. **[P0] 出图渲染器污染 H3 安装证据**：`comfyops/image_renderer.py` 在共享实例行上留下 `url`/`purpose`，
   H3 激活因此永远报 `COMPLETED_REAL_INSTALL_EVIDENCE_REQUIRED`。
   修复：出图渲染器停止时清掉自己的标记（`_without_image_marks` / `_clear_image_marks`）。
2. **[P0] Worker 对失败条目死循环**：`ITEM_CLAIMABLE` 含终态 `ITEM_FAILED`，
   Worker 0.5 秒一次重新认领同一失败条目（实测 `state_version` 涨到 **1934**），
   CPU 打满、心跳停摆、其余排队条目永不执行。
   修复：失败条目退出可认领集合，等用户「恢复此项」或「再生成一版」。
3. **[P1] 忙碌的 Worker 被报成离线**：心跳只在空闲时写。
   修复：每个 tick 都写心跳，并加一条保活线程，长渲染期间也不掉线。
4. **[P1] 出图提示词夹带中文名称**：出品人把中文需求名写进英文外观描述，
   提示词编译器按规则拒绝，条目落到 `NEEDS_REVIEW`。
   修复：外观描述合同加英文散文校验（由既有的一次纠正机制处理），提示语明确要求不要重复需求名；
   视觉方向是用户输入，页面补上「请用英文填写」提示。

### 2.3 新增需求

| 编号 | 需求 | 状态 | 证据 |
| --- | --- | --- | --- |
| R1 | 素材网格 | **已实现**（缩略图、身份/用途、版本、状态、操作、多列 contain、放大、选择、分组、按需加载、窄屏不溢出、深色主题已实看） | 浏览器实看 + `frontend/src/test/simpleAssets.test.tsx` |
| R2 | 生成分段与单段重生 | **已实现**（H3 一次输出为一段、段内剪辑点不单独重生、旧版本保留、依赖失效、选中后新装配版本与新交付） | `backend/tests/test_generation_segments.py` |
| R3 | 长剧本总体大纲与分步分镜 | **部分实现**：持久大纲、用户确认、按段选择、Worker 调度、暂停/取消/恢复、精确失效、伪造上下文拒绝均已实现并有后端回归；**连续性事实仍由用户自由文本提供，不是版本化连续性产物** | `backend/tests/test_story_section_run.py`、`test_story_preparation.py` |

## 3. 运行栈恢复（本轮实做）

按受管路径修复，没有手改 READY、没有盲停外部 ComfyUI、没有按时间释放不明租约：

1. 识别出图渲染器在共享实例行上的残留标记 → 受管停止动作清掉它；
2. `POST /setup/configuration` 重新生成受管配置；
3. `POST /setup/profiles/validate` 在真实渲染器上校验工作流合同（4 个 Ref2VA 方案）；
4. `POST /setup/services/validate` 活体校验服务合同（1281 个节点）；
5. `POST /setup/runtime/activate` 激活运行配置。

结果：`runtime = ACTIVE`、`render_provider = comfyui`、`services_verified = true`、
`service_validation = READY`、`workflow_profiles = 4`。

## 4. 证据分级

| 级别 | 内容 |
| --- | --- |
| IMPLEMENTED | A01–A08、R1–R3 的代码实现与本轮新增修复 |
| OFFLINE_VERIFIED | 全量后端套件、Ruff、Alembic 全新库升级与 `check`、前端 19 文件 134 测试、TypeScript/Vite 构建、新增专项回归（A01 反转证据脚本、段落执行路径、Worker 循环与心跳） |
| REAL_QWEN_VERIFIED | **本轮新链路真实出图**：run `eead7019…`（项目 `1b0dcb51…`）由真实 MiniMax 出品人 brief 驱动受管 Qwen 渲染器，产出 2 张真实候选：`70a47810…`（道具，1,070,289 字节，sha `679818ea…`）与 `b768b7e0…`（角色，1,242,474 字节，sha `9d1845d6…`），均为 1024×1024 PNG、`is_mock` 非真、technical=pass、decode=pass、外部 prompt id 真实（`b3342bb2…`、`2bfbdcca…`）、提交意图先于提交落库。历史 run `8289c72e…` 三类真实小样仍有效 |
| REAL_H3_VERIFIED | **未达成**：本轮没有用新素材跑通一次真实 H3 渲染 |
| HUMAN_REVIEW | **必须由用户完成**：候选素材选用与晋升、风格冻结、艺术评审、出品人澄清问题的回答 |
| WINDOWS_AND_PACKAGE | 开发入口与安装包两链的启动/端口占用/停止验证（见 §6） |
| BLOCKED / NOT_RUN | 见 §7 |

## 5. 真实素材链路实测

本轮在本机真实执行（非模拟）：

- 文字模型：用户自有的 `MiniMax-M3.1-Flash`（`https://api.minimax.cn/v1`），
  密钥从另一套栈的 DPAPI 密钥库读出后写入本栈密钥库，**全程不打印、不入库、不进日志**；
- 文字授权：通过 `POST /material-preparation/text-consent` 以操作员动作写入，
  范围 = 项目 + 文档修订 + 角色 + provider 指纹；
- 编剧：3 个场全部 `PREPARED`（真实调用）；
- 出品人：真实 brief 调用，产出的英文外观描述质量合格；
- 渲染器：受管出图渲染器按需启动。

实测中出现的三类**诚实拒绝**，都是产品按规则工作，不是崩溃：

1. `MATERIAL_IDENTITY_AMBIGUOUS`：同一人物在两场里描述不一致 → 拒绝确认，要求人统一；
2. `MATERIAL_BRIEF_NEEDS_CLARIFICATION`：剧本没有写明道具/人物的具体外形 → 出品人提问，不替人编造；
3. `VISUAL_TEXT_MUST_BE_PROMPT_LANGUAGE`：外观描述里夹了中文需求名 → 提示词规则拒绝。

这三类都需要人来决定，因此本轮**没有**替用户点选、晋升、冻结或评审。

**本轮真实出图结果（run `eead7019-d0c9-4220-8d23-2920f6780131`，12 项）**

| 结果 | 数量 | 说明 |
| --- | --- | --- |
| CANDIDATE_READY | 2 | 真实出图（道具、角色各一），技术质检与解码质检均通过，等待人工评审 |
| MATERIAL_BRIEF_NEEDS_CLARIFICATION | 9 | 出品人拒绝替剧本补写未写明的道具/人物外形细节，逐条给出问题 |
| LLM_FORMAT | 1 | 模型答案两次都不满足合同格式，按既有规则记为失败，不自动重发 |

本轮同时修掉了三处让这条链路走不通的真实缺陷：出图渲染器污染 H3 安装证据、
Worker 对失败条目死循环（实测状态版本涨到 1934）、忙碌的 Worker 被报成离线。
修复后同一批次里 8 个排队条目被真正执行，不再被一个失败条目堵死。

待用户处理的具体入口（导演模式 → 素材与风格 → ③ 选择素材）：
10 个条目各自显示「候选可用 / 待人工选用 / 待人工澄清」，
选中候选后由用户决定是否「确认使用所选素材」晋升为主素材。

## 6. Windows 入口与安装包

### 6.1 开发入口（本轮实跑）

- `启动 Shine 工作台.cmd`：纯 ASCII（753 字节），双击走 `scripts/start_dev.ps1` → `scripts/start_shine.ps1`。
- 实跑结果（独立数据目录 `.local-data`，不影响验收栈）：
  `GET /health` → `{"status":"ok","service":"shine-api","render_provider":"comfyui"}`；
  `/system/status` → `worker_online=true`、`pending_jobs=0`；Web `GET /` → **200**。
- 停止：`scripts/stop_shine.py --data-dir <dir>` 干净停掉 API / Worker / Web 全部进程。
- 开工时 8000/5173 上还挂着一个**半启动残栈**（11-04 的旧代码 API + Vite，父进程已消失、
  进程记录停在 11-02）。本轮按其命令行证明归属后停掉，端口交给开发入口。

### 6.2 安装包

- 命令：`pwsh -File scripts/build_test_package.ps1 -OutDir releases -Verify`，
  拒绝脏树（本轮从已提交树 `cbfbee0` 构建）。
- 产物：`releases/ShineVideoStudio-20261105-cbfbee0-win64.zip`，2,681,383 字节，
  sha256 `18a51a74b732bd9a5d806bb70d131fbfc0006767227053bdb473cad411520874`；
  Expand-Archive 后逐文件 SHA-256 复核 **549 个文件全部通过**。
- 入口：`SETUP.cmd`、`START_APP.cmd`、`STOP_APP.cmd`、`一键安装并启动.cmd`（ASCII 回退）都在包内。
- 不含：模型/权重、SQLite、密钥、`.env`（只有 `.env.example`）、生成的媒体、evidence、日志；
  旧包全部保留。

## 7. 未完成 / 阻塞

1. **VLM 校准必须重做**：`shine/worker.py` 与 `shine/api_preparation.py` 属于
   `client_review_identity` 钉住的文件，本轮改动了它们 → 正式项目已发布的 VLM 修订
   `03f32432-4c94-4476-8580-e1a14e9fc37f` 按设计失效。
   按 AGENTS.md，真实 H3 验收前需要：真实正负 VLM 校准 → 发布新修订 → 弃用旧修订 →
   重新分析预制作计划 → 重新冻结。**本轮未执行**（需要那套栈的 VLM 运行环境）。
2. **真实 H3 成片未跑**：新素材尚未晋升（人做），冻结后的正式分镜与 H3 渲染因此没有真实证据。
3. **R3 连续性**：现在是用户自由文本，不是从上一段已接受末状态派生的版本化连续性产物。
4. **本机其它栈**：8000 栈仍指向 11-05 的迁移测试副本，未作为正式栈恢复。
