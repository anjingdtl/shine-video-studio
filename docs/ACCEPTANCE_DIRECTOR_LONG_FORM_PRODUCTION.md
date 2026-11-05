# 验收：导演流程重构与全片持续生产

- 日期：2026-11-06（Asia/Shanghai）
- 范围：本机 `main` 工作树（未提交），基线提交 `fca6cabf2564187a641342e6ef3276e0e9a47dbc`
- 进度与未决项：[PROGRESS_DIRECTOR_LONG_FORM_PRODUCTION.md](PROGRESS_DIRECTOR_LONG_FORM_PRODUCTION.md)
- 设计决定：[ADR 0007](adr/0007-director-package-and-long-form-run.md)

## 1. 证据等级总表

| 项目 | 等级 | 依据 |
| --- | --- | --- |
| 分集计划、导演包、就绪检查、运行创建/推进、分集交付、控制与对账 | IMPLEMENTED + OFFLINE_VERIFIED | `backend/tests/test_director_long_form.py`（24 项） |
| 迁移（新库 + 旧库升级 + 唯一性 + 回退） | OFFLINE_VERIFIED | Alembic 隔离临时库实跑（见 §4） |
| 机器部署推荐规则 | IMPLEMENTED + OFFLINE_VERIFIED + 本机只读实测 | `test_deployment_recommendation.py`（10 项）+ 本机 `probe_host_environment` 实跑 |
| 六阶段 Web 导航、导演包面板、运行面板 | IMPLEMENTED + OFFLINE_VERIFIED（组件级） | `frontend/src/test/directorRun.test.tsx` 等 20 文件 140 测试 + `tsc -b` + `vite build` |
| 真实部署（装模型/起渲染器） | NOT_RUN | 本机磁盘 83.11 GB < H3 组合 180 GB 门槛；本轮无安装/下载授权 |
| 真实生图 | NOT_RUN | 同上，未启动出图渲染器 |
| 真实 H3 单段/多集 | NOT_RUN | `127.0.0.1:8188` 无监听；真实生产需单独授权 |
| 8 小时持续生产 / 24 小时长时 | NOT_RUN | 依赖真实 H3 |
| 人工艺术验收 | NOT_RUN | 没有新的真实成片 |

> 本表不含任何“技术通过即艺术通过”的推断。`COMPLETED` 只代表本次范围内每一集都形成了有效交付，不代表模型效果被认可。

## 2. 现在用户能在 Web 完成什么

1. 「我的作品」→ 六阶段导航（创意与剧本 / 剧本统筹与素材 / 镜头拆解 / 导演包 / 视频生产 / 后期成片），每一步的完成态来自 `GET /projects/{id}/journey`。
2. 「导演包」页：填每集目标秒数与容差 → 生成分集草案（按镜头顺序、不切断连续性链、覆盖不重不漏）→ 确认分集 → 选择本机生成方案 → 生成导演包 → 查看就绪检查（含阻塞原因与配置指纹）→ 确认导演包 → 启动整部作品生产。
3. 「视频生产」页：按集看到已交付/拍摄中/阻塞、每集下属批次状态、已通过镜头数、成片秒数、最近进展时间；暂停 / 继续 / 取消 / 核对真实结果；可展开最近进展事件。
4. 「后期成片」页：已交付分集可直接播放与下载，并列出“技术完成、待你确认”的待审清单。

关闭浏览器不影响推进：推进由常驻 Worker 完成，API 只落库。

## 3. 与方案的实现差异

| 项 | 方案拟议 | 实际实现 | 原因与风险 |
| --- | --- | --- | --- |
| 导演包存储 | Artifact Store 根 manifest + 分页子 manifest | 两张表：`director_package_versions`（根）+ `director_package_episodes`（分集） | `agent_artifacts.source_task_id` 非空且指向持久任务；导演包由确定性编译产生，伪造任务记录会污染专业 Agent 的证据链。语义等价，索引更直接。风险：与 Artifact 通用审计视图分离，需要在 UI 里单独展示。 |
| 就绪语义 | 单一 “就绪” | `ready`（内容可满足）+ `startable`（已确认且可启动） | 确认是用户动作，不能由内容检查替代。风险：调用方需要区分两个字段，API 文档已注明。 |
| 批次划分 | 由连续性、工作单元与资源策略决定 | 每集按 `batch_shot_limit`（默认 6）切分，且切点绝不落在连续性镜头之前 | 首版不引入跨批热接管；一批一系列。风险：大集会产生较多 series 启动次数，属预期。 |
| 装配分层 | 100 clip 以上有界分层 | `DeliveryAssembler.assemble(page_size=100)`：先按页折页，再拼总片，每层 `-c copy` | 保持单次 EDL 100 项合同不变；风险：极长集会有多层临时文件，已有 `assembly_pages` 记录。 |
| RAM 门槛 | 推荐、安装器、前端共用方案约束 | 推荐模块用 `effective_floor_mb` 读取目录里的整数 GiB 门槛（减 1 GiB 容差） | 目录写 32768 MB，而 32 GB 机器只报告约 31.7 GB；不这样处理会让推荐比安装器更严。风险：容差是约定值，未在每台机器上重新校准。 |
| 全片合集 | 可选合集 | 未实现 | 逐集交付已可用；合集是纯后期增强，不阻塞主链路。 |
| 旧项目迁移预览 | 只读预览后由用户确认生成包草案 | 未实现（旧项目可直接新建分集方案与包） | 旧项目本身没有分集概念，直接生成就等于替用户决定；当前选择是让用户显式确认分集，风险更低。 |

## 4. 确切命令与结果

### 4.1 迁移

```powershell
# 隔离临时库（新库）
$migrationDir = Join-Path ([IO.Path]::GetTempPath()) ('shine-director-' + [guid]::NewGuid().ToString('N'))
$env:SHINE_DATA_DIR = $migrationDir
$env:SHINE_DATABASE_URL = 'sqlite:///' + (Join-Path $migrationDir 'shine.db').Replace('\', '/')
backend\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini upgrade head   # 到 f180a0010001
backend\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini check          # No new upgrade operations detected
```

旧库路径另取一份副本验证（`upgrade f170a0030001` → 写入 1 个项目 / 1 个 series / 2 条交付订单 → `upgrade head`）：

- 2 条历史订单全部保留，`scope_kind` 回填为 `series`；
- `(series_id, version)` 重复插入仍被拒绝：`UNIQUE constraint failed: delivery_orders.series_id, delivery_orders.version`；
- 分集范围重复插入被拒绝：`UNIQUE constraint failed: delivery_orders.scope_kind, delivery_orders.scope_id, delivery_orders.version`；
- 升级后 `alembic check` 无 drift。

### 4.2 后端

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_director_long_form.py -q        # 23 passed
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_deployment_recommendation.py -q  # 10 passed
backend\.venv\Scripts\python.exe -m ruff check backend/shine backend/tests                    # All checks passed!
backend\.venv\Scripts\python.exe -m pytest backend/tests -q                                   # 见 §4.4
```

新测试覆盖的关键行为（均为离线、合成媒体）：

- 双入口汇合后的分集覆盖：250 镜分集不重不漏；连续链不被分集边界切断；缺镜/重复镜被拒。
- 导演包：缺分集确认 → 409；包固定范围/风格/绑定；风格重冻 → `STYLE_SUPERSEDED`；篡改分集清单 → `EPISODE_MANIFEST_TAMPERED`。
- 运行：重复启动返回同一 run（`created=false`）；第二个活动 Run → 409；一次推进只创建一个 series（有 live series 时不再派发）。
- 交付：一集来自两个 series 时，EDL 顺序与集内镜头顺序一致；`enqueue_auto_delivery` 对 run 所属 series 返回 `None`（不冒充成片）；缺镜的集不生成交付订单，运行进入 `NEEDS_REVIEW`。
- 全链路：两次批次日 → 分集订单 → 真实 FFmpeg 装配 → `DELIVERED` → run `COMPLETED`，成片可播放（`/deliveries/{id}/media` 字节数与登记一致）。
- 控制：暂停不再派发、恢复继续；取消保留已产出 artifact；`NEEDS_REVIEW` 可只读对账。
- 闸门：mock / offline / 无受管运行配置时 Worker 不派发任何批次。

### 4.3 前端

```powershell
npm.cmd run build                          # tsc -b + vite build 通过
npx.cmd vitest run --no-file-parallelism   # 20 files / 140 tests passed
```

> `npm.cmd test`（并行）在本机沙箱里会因 vitest worker 缓存写入 `EPERM` 中断部分文件，故以串行结果为准；并行运行记为 INTERRUPTED，不计为通过。

### 4.4 后端全量

见本文件末尾「最后一次全量运行」。

## 5. 本机真实只读检测（REAL_DEPLOYMENT 只读部分）

`probe_host_environment` 实跑（未加载权重、未启动推理、未下载）：

| 项 | 实测 |
| --- | --- |
| GPU | GeForce RTX 5080，16303 MB，驱动 621.01 |
| 内存 | 32418 MB |
| 目标盘可用 | 83.11 GB |
| FFmpeg / FFprobe | 8.1（满足下限） |
| 端口 8188 | 无监听 |
| API 8000 | 无监听 |
| 常驻 Worker | 1 个（pid 21404，phase `idle`，心跳正常，属于用户环境，本轮未接管） |

推荐结论（`recommend()` 实跑）：

- `h3_video`：**BLOCKED** — `DISK_INSUFFICIENT`（需 180 GB，可用 83.11 GB）。
- `image_prep`：**SUPPORTED** — 需 40 GB，总下载 17.3 GB（复用量 0，未指向受管目录）。
- 两者 `evidence.level = estimated`：本机没有任何组合的实测记录，因此不会被标成“已验证”。

## 6. 待审与失败范围

- 没有任何真实成片进入待审：本轮未执行真实生产。
- 历史验收库中仍有 1 个 `NEEDS_REVIEW` series（`SERIES_INCOMPLETE`，2026-11-05 13:35 记录）与 33 个 `FAILED` 素材项，属于上一轮遗留，本轮未改动。

## 7. BLOCKED / NOT_RUN 与所需最小条件

| 项 | 状态 | 完成所需的最小条件 |
| --- | --- | --- |
| 真实部署小样（H3） | BLOCKED | 目标盘可用空间 ≥ 180 GB（或把部署目录指到满足门槛的盘）；一次明确的安装/下载授权 |
| 真实生图三类小样 | BLOCKED | 启动受管出图渲染器；一次明确的真实出图授权 |
| 真实 H3 单段验证 | NOT_RUN | 128 GB 级显存/磁盘条件满足 + 渲染器启动 + 真实生产授权 |
| 3 集多批次自动生产 | NOT_RUN | 上一条 + 3 集已确认剧本与主素材 |
| 8 小时 / 24 小时持续运行 | NOT_RUN | 上一条 + 连续可用的机器时间窗口 |
| 新 revision 的 VLM 正负校准 | NOT_RUN | 本轮未改动 `client_review_identity` 钉住的模块（worker 只增加了运行推进调用，gates/repair/advisory 未变），但仍需在真实项目环境里重新校准后才能宣称正式验收 |
| 合集（全片拼接） | NOT_RUN | 产品确认后单独实现 |
| 验收库迁移 | NOT_RUN | 用户授权后执行 `alembic upgrade head` |

## 8. 最后一次全量运行

- 命令：`backend\.venv\Scripts\python.exe -m pytest backend/tests -q`（仓库根，2026-11-06 16:50–17:09，18 分 51 秒）
- 结果：**1734 项执行 / 1672 passed / 51 failed + 10 errors / 1 skipped，EXIT=1**。
- 失败归因：**61 项全部落在本机沙箱的删除拦截上，无一落在 `shine/*` 代码**。
  - 失败集中在 `test_v13_instances`(15)、`test_v13_preparation`(16)、`test_v13_adversarial`(10)、
    `test_stack_operations`(5)、`test_v13_factory`/`test_v13_e2e`/`test_structured_review`/
    `test_security_windows`/`test_owned_process_group` 各 1 — 全是“装/删文件、拉起子进程”的用例。
  - 全部 traceback 终止在 `F:\workbuddy
esourcespp.asar.unpacked\cli
  - 全部 traceback 终止在沙箱注入的 `cli/vendor/shim/sitecustomize.py`：
    `[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":50,"threshold":50,"scope":"turn"}` → `SystemExit: 1`。
    单个长进程累计删除超过阈值后，后续任何删除都会中止进程（连 `os._exit(23)` 的崩溃窗口用例也被提前杀掉，
    于是 `assert 1 == 23`）。
- 复核：把同样这些文件**单独运行**后 60 项全部通过：
  - `test_v13_instances.py + test_v13_adversarial.py` → 51 passed
  - `test_v13_factory.py + test_v13_e2e.py + test_structured_review.py` → 47 passed
  - `test_stack_operations.py + test_security_windows.py + test_owned_process_group.py` → 28 passed
  - `test_v13_migrations.py + test_v13_preparation.py` → 70 passed
- 唯一一项与代码无关但确实无法在本会话通过的用例：`test_owned_process_group.py::test_pinned_source_cannot_be_replaced_while_group_runs`。
  它断言“进程占用中的文件 `unlink()` 抛 `PermissionError`”，而沙箱把 `Path.unlink` 换成了回收站实现并抛 `OSError`。
  这是沙箱语义差异，不是产品行为差异。
- §17.4 要求的相关专项套件（不含会被删除拦截打断的文件）在最终代码上：**249 passed**（1 分 41 秒），
  命令：`pytest test_offline_e2e test_film_crew test_production_series test_comfy_completion_window
  test_advisory_queue test_comfy_handoff_journal test_h3_compiler test_material_preparation
  test_material_image_jobs test_story_preparation test_delivery_orders test_delivery_assembly
  test_managed_process_release`。

**结论：在没有沙箱删除拦截的普通终端里，这套变更需要再跑一次全量作为最终发行门禁；本会话的全量结果按
“1672 passed / 61 沙箱拦截 / 1 skipped”如实记录，不记作全绿。**

## 9. 浏览器全旅程穿越（2026-11-06 晚，拟人化点击）

### 9.1 环境

- 隔离数据目录 `.local-acceptance/journey-20261106`（新库，`alembic upgrade head` 到 `f180a0010001`）。
- API `127.0.0.1:8000`（`SHINE_RENDER_PROVIDER=mock`），Vite `127.0.0.1:5173` 代理到该 API。
- 未接管、未停止任何既有进程（旧常驻 Worker 仍在旧库上 idle）。
- 驱动方式：真实 Chromium 1440×900（另做一次 390×844 窄屏），逐步点击/输入并在每一步截图。
- **内置浏览器不可用**：`dom_cua_get_visible_dom` 返回空快照；`tab_screenshot` 即使给 320×240 裁切仍报
  “exceeds the 10 MiB image limit”（裁切参数被忽略），无法用截图核对也无法盲点坐标。
  因此改用仓库内 Playwright 走同一条 `Vite → API → SQLite` 真栈，脚本见 `scripts/journey_longform_browser.py`，
  截图落盘后用图像检查。这是工具链限制，不是产品缺陷。

### 9.2 结果

`scripts/journey_longform_browser.py` → **17/17 通过**（最新一次 25 秒，2026-11-06 07:22）：

| 步骤 | 结果 |
| --- | --- |
| 01 打开工作台 / 02 新建作品 / 03 六阶段导航 | PASS |
| 04 进入素材页 / 05 上传素材 / 06 确认素材 | PASS |
| 07 冻结风格（首次会一并批准规则包） | PASS |
| 08 建立分镜（切到「② 排产制作」→ 展开「单镜头制作与任务明细（高级）」） | PASS（2 镜） |
| 09 生成分集草案 / 10 确认分集方案 | PASS |
| 11 登记生成方案（UI 之外的一次本机设置） | PASS |
| 12 生成导演包 / 13 确认导演包 / 14 启动整部作品 | PASS |
| 15 全片生产 / 16 后期成片 / 17 窄屏布局 | PASS |

截图：`.local-acceptance/journey-shots/`（01-home … 17-narrow、99-final）。

### 9.3 穿越中发现并修复的产品缺陷

| # | 现象 | 修复 |
| --- | --- | --- |
| 1 | 风格面板说明写「点右下角『冻结为新版本』」，但首次冻结的按钮实际叫「批准并冻结」——按说明找不到按钮 | `StylePanel` 说明改为按状态解释两个按钮名；行为不变 |
| 2 | 没有任何分镜时，「生成分集草案」仍可点，点了才 422 `EPISODE_PLAN_REQUIRES_SHOTS` | 按钮按 `shot_count === 0` 禁用并给出「先在镜头拆解建立镜头」的说明 |
| 3 | 演示/模拟态下运行永远停在「已排队」，页面却写「后台会一直推进」，用户会一直等 | 运行页新增演示态提示：不会真的开拍，点「启用真实制作」后 Worker 才推进 |
| 4 | 窄屏（≤1100px）步骤条把所有阶段文字全藏掉，只剩图标；「刷新」按钮被挤成两行 | 只在 ≤760px 收起文字，且当前/下一步始终保留文字；按钮不换行 |

对应回归：`frontend/src/test/directorRun.test.tsx` 增加「无分镜时按钮禁用」「演示态提示」两条断言；
前端 20 文件 140 测试全绿，`tsc -b && vite build` 通过。

### 9.4 记录但本轮未改的观察

- 手工建分镜位于「单镜头制作与任务明细（高级）」双层折叠内，与「镜头拆解」这一阶段的说法不匹配；
  正常路径是「剧本分镜（AI）」或「一键开拍」，手工建镜属于兜底。信息架构调整留待下一轮。
- 开发态没有 favicon，控制台有一条 404（静态资源，不影响功能）。
- 演示态下运行不会推进是设计如此（真实 H3 未就绪），因此本次穿越只验证到「启动 + 运行页诚实显示」，
  真实拍摄/装配/交付仍依赖第 7 节的阻塞条件。
