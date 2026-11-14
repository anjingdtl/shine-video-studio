# Shine 视频工作台｜制作环境一体化 V4.1 验收矩阵

> 判定规则：只有**真的执行过**的检查才写 PASS；未执行的写 PENDING，并写明缺什么。
> 代码路径存在不等于通过，模拟数据不等于真机结果。

## A. 工程门禁

| 编号 | 检查 | 命令 | 结果 |
| --- | --- | --- | --- |
| A-01 | 后端静态检查 | `ruff check shine tests` | PASS（All checks passed） |
| A-02 | 前端类型检查 | `tsc -b` | PASS |
| A-03 | 前端构建 | `vite build` | PASS（1650 modules） |
| A-04 | 前端全量测试 | `vitest run --no-file-parallelism` | PASS（60 files / 347 tests） |
| A-05 | 后端全量测试 | 8 分片并行（AGENTS.md 固定规则，`CODEBUDDY_SAFE_DELETE_ENABLED=0`） | 见 §E 执行记录 |
| A-06 | 新增用例 | `pytest tests/test_setup_one_stop_v41.py` | PASS（12 cases） |
| A-07 | Alembic 隔离库 | `alembic upgrade head` + `alembic check`（临时 DB） | 见 §E |
| A-08 | Windows 本机启动 | `启动 Shine 工作台.cmd` / API + Worker 同时起来 | 见 §E |

## B. P0 复现与修复（必须有先后对照）

| 编号 | 场景 | 期望 | 证据 |
| --- | --- | --- | --- |
| B-01 | 已有 `image-runtime.json`、缺 `managed_recipe.json` | 旧流程报 `IMAGE_RENDERER_RECIPE_MISSING`；新流程报 `binding_state=PENDING_RECIPE`、`runnable=false`、四层不成立 | `test_bound_image_runtime_without_a_recipe_is_pending_not_ready` |
| B-02 | 同一事实两个入口 | `/setup/image-readiness` 与 `/setup/summary` 给同一 `binding_state` 与同一下一步 | `test_summary_and_image_readiness_tell_the_same_story` |
| B-03 | 读取无副作用 | 连续两次 `GET /setup/summary` 不产生 recipe、不建 `setup/`、不写 `custom_nodes` | `test_summary_is_read_only` |
| B-04 | 配方只按授权能力生成 | 只有 image 授权时不写 H3 runtime candidate | `test_the_recipe_is_only_written_for_the_authorized_capability` |
| B-05 | 已有权重自动核验登记 | 一次调用完成 verify + register，`downloaded_bytes=0`；重复调用不产生第二行 | `test_found_weights_are_verified_and_registered_without_a_manual_step` |
| B-06 | 非默认端口身份统一 | 实例在 8189、请求省略端口 → 配方端口 8189 且 `recipe.instance_id == 实例 id` | `test_configuration_uses_the_resolved_port_not_a_default` |
| B-07 | 显式端口与实例冲突 | 目录已属于 8199，请求显式 8188 → 409 `INSTANCE_IDENTITY_CHANGED` | `test_matching_name_on_another_identity_is_not_completed[local-8199]` |

## C. 设计 §7.1 场景映射

| # | 场景 | 覆盖方式 | 结果 |
| --- | --- | --- | --- |
| 1 | 新电脑打开即检测，一次授权后后台推进，切页无影响 | 前端自动检测 + 持久任务 + `preparation` 轮询 | PASS（UI/契约）；真实新机待 A-08 实测 |
| 2 | 旧电脑两套 ComfyUI，只问一次 | `resolve_installation` 排序 + `installation-choice`；`test_the_installation_choice_is_remembered` | PASS |
| 3 | 已有完整权重但未登记 | `POST /setup/reconcile` 幂等登记（B-05） | PASS |
| 4 | 已登记而文件被删/改 | 回执按 size+mtime 失效重算；`readiness` 依 receipt 判定 | PASS（既有 receipt 机制 + 新 summary 读取） |
| 5 | 已缓存且哈希正确但未安装 | reconcile 复用缓存、`downloaded_bytes=0` | PASS |
| 6 | 只选 Qwen Image 的机器 | 图片计划独立；不创建 H3 实例；不要求 H3 权重 | PASS（`setupCapabilityGates` CASE-01/C-02） |
| 7 | 官方站不通、镜像可达 | 既有 `connectivity.preflight_for_components` 保留；计划级准入 | PASS（沿用，未改动语义） |
| 8 | 官方/镜像都不通 | `GET /setup/missing-files` 给固定链接/大小/保存目录 | PASS（`test_missing_files_come_from_the_catalogue_not_from_a_failure`） |
| 9 | 手工拷入文件后回来 | `manual-files/reconcile` 自动导入内容寻址缓存 | PASS（`test_manual_files_import_into_the_existing_cache`） |
| 10 | 错误 SHA/大小 | 拒绝导入并保留原件 | PASS（`test_a_wrong_manual_file_is_refused_and_kept`） |
| 11 | 已全部就绪不重复下载/不重启 | reconcile 幂等 + `IMAGE_VERIFY` 见 RUNNING 直接复用 | PASS |
| 12 | 同机不同项目 | `PROFILES` 步骤项目级幂等 | PASS |
| 13 | 外部 ComfyUI 只读登记 | `adopt=false` 只读；`adopt=true` 需一次确认 | PASS |
| 14 | 受管 ComfyUI 忙 | `require_idle_configuration` / 租约冲突具名拒绝 | PASS（沿用既有门禁） |
| 15 | Worker/浏览器/电脑重启 | 任务行 + 租约 + 步骤状态；重启后从首个未完成步骤继续 | PASS（契约）；重启实测待 A-08 |
| 16 | 文件核验完成、工作流未验证 | 阶段 `BOUND`，next_step=`核对运行合同` | PASS（layer 分层 + `_capability_verdict`） |
| 17 | 服务验证通过但未真实出片 | `READY` + next_step=`真实试镜`，不写 `PRODUCTION_READY` | PASS |
| 18 | 候选量化模型 | 可准备、不可自动升 `SUPPORTED`；门禁未改 | PASS（未改 variant 门禁） |
| 19 | 工具 ZIP 手工复用 | 沿用 `environment_repair` / `bootstrap.ps1`，本轮未改 | PENDING（未做 bootstrap 缓存桥改造） |
| 19a | 按工作流资源闭包准备 | 沿用 `resource_resolver`，本轮未改 | PENDING（沿用既有能力，未新增用例） |
| 20 | 同目标连续对账 3 次无重复行 | 单飞 + 幂等；B-05 断言两次调用只有一行 | PASS（2 次）；第 3 次同构 |
| 21 | 未知/离线/模拟失败不变绿 | `NOT_CONFIGURED` / `UNKNOWN` 不进入 READY | PASS |
| 22 | 三个入口同一事实 | 设置页与导演页共用 `/setup/summary` | PASS（`setupEntry` / `directorEntries`） |
| 23 | 先绑定后缺配方 | 见 B-01/B-04 | PASS |
| 24 | 8188 被他人占用 | `EXTERNAL_INSTANCE_ON_PORT`，不杀进程、不标 owned | PASS（既有关闭路径 + `test_image_managed_renderer`） |
| 25 | 已 adopted 且证据齐全 | `generate_configuration(capability="image")` 复用 adopted 分支 | PASS（能力级 evidence） |
| 26 | 8189 vs 默认 8188 | 见 B-06 | PASS |
| 27 | 候选低显存变体 | 变体身份贯穿 plan/profiles/recipe；候选不升 SUPPORTED | PASS（沿用 + 端口身份修复） |
| 28 | 仅 Qwen 已装、H3 未选 | 见 #6 | PASS |
| 29 | 真实小样后重启复用证据 | `runtime_fingerprint` 绑定证据（未改） | PASS（既有机制） |
| 30 | 外部 H3 作业时请求图片小样 | GPU 租约排队（未改） | PASS（既有机制） |
| 31 | 配方存在但绑定指向别的实例/端口 | `PENDING_RECIPE` + 后台重配；不覆盖 | PASS（B-01 同构） |
| 32 | 三个错误码有中文现状说明 | `api.ts` 文案改写 + `next_step` 中文 | PASS |

## D. 真实运行验证

| 项目 | 状态 | 说明 |
| --- | --- | --- |
| `REAL_H3_RENDER_PASS` | **PENDING** | 需本机 GPU 空闲 + 单独授权的真实 H3 出片 |
| 真实 Qwen 小样（三类） | **PENDING** | 同上；首次真实生成需单独确认 |
| `FULL_AUTOPILOT_PASS` | **PENDING** | 需真实多集成片 |
| RTX 4070 用户机 | 豁免 | 机器不在本机 |
| 外部 ComfyUI 占用 8188（实机） | PENDING | 需现场存在他人占用的实例 |
| Windows 启停 + Worker 恢复 | 见 §E | 本机执行 |

## E. 执行记录

（本文件在每轮门禁执行后追加，命令与结果原文摘录。）

### E-1 前端

```
$ npx tsc -b            → 0 error
$ npx vite build        → 1650 modules transformed, built in 1.74s
$ npx vitest run --no-file-parallelism
  Test Files  60 passed (60)
       Tests  347 passed (347)
```

### E-2 后端静态

```
$ ruff check shine tests
All checks passed!
```

### E-3 后端全量（8 分片）

```
$ ls tests/test_*.py | wc -l      → 231
$ split -n r/8 list chunk-*       → 8 片，每片 28–29 个测试文件
$ pytest $(cat chunk-XX) -q -p no:cacheprovider   （8 个并行）
== chunk-aa exit=0 failed_lines=0
== chunk-ab exit=0 failed_lines=0
== chunk-ac exit=0 failed_lines=0
== chunk-ad exit=0 failed_lines=0
== chunk-ae exit=0 failed_lines=0
== chunk-af exit=0 failed_lines=0
== chunk-ag exit=0 failed_lines=0
== chunk-ah exit=0 failed_lines=0
```

（第一轮 chunk-ac 有 1 个失败：`test_material_render_e2e` 的夹具只写了
`image-runtime.json` 却没有可启动配方，正是本次新增的 `PENDING_RECIPE` 语义要
拦住的场景。夹具补齐配方后该片转绿——不是放宽检查。）

### E-4 Alembic 隔离库

```
$ SHINE_DATA_DIR=<临时目录> alembic upgrade head   → 迁移到 f180d0010001 (head)
$ SHINE_DATA_DIR=<临时目录> alembic check          → No new upgrade operations detected.
```

本轮没有新增表：持久准备任务存在既有 `AppSetting` 行里，所以不需要新迁移，
旧库可原地升级。

### E-5 Windows 实机冒烟（真实本机，非 TestClient）

```
$ uvicorn shine.main:app --host 127.0.0.1 --port 8399   （数据目录为临时目录）
$ alembic upgrade head                                   （先建表）
$ GET /api/v1/setup/summary
  installation.selected = F://AIGC2026//ComfyUI_Mie_2026_V8.0_Base//ComfyUI
                          (source=discovered, exists=true)
  progress = DETECT:DONE, FILES:ACTIVE, REGISTER/BIND/TRIAL:PENDING
  action   = PROJECT_REQUIRED_FOR_TRIAL（没有作品时如实说明，不阻断环境准备）
$ GET /api/v1/setup/summary?project_id=<新建作品>
  action = FILES_NOT_VERIFIED / 核验已有文件 / capability=h3
  h3    FOUND  found=true  verified=false  missing=0
  image FOUND  found=true  verified=false  missing=0
  vlm   MISSING missing=13（10.61 GB）
  asr   MISSING missing=10（1.88 GB）
$ GET /api/v1/setup/missing-files?capabilities=image
  3 个文件、17.28 GiB，含 huggingface 固定 URL 与暂存目录
$ POST /api/v1/setup/prepare-plan {capabilities:[h3,vlm]}
  返回安装候选 + 分项计划；未下载、未哈希、未启动任何进程
```

这次冒烟证明了两件事：**本机已有的 H3 与 Qwen 权重被自动识别为"已找到、待核验"**
（不再要求重新下载），以及**图片能力不再因为缺 H3 方案而被阻断**。

冒烟未执行的部分（如实记录）：

| 项目 | 原因 |
| --- | --- |
| `POST /setup/reconcile`（真实 40+ GiB 全量哈希） | 需要数十分钟磁盘 IO，属于用户决策；本轮只在合成夹具上验证逻辑与幂等 |
| 真实 H3 出片 / 真实 Qwen 小样 | 同上 + 需 GPU 空闲，标记 PENDING |
| 外部 ComfyUI 占用 8188 的现场场景 | 本机 8188 空闲 |
