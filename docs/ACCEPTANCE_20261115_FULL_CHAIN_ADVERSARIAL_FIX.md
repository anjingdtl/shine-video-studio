# 新装机全链路对抗性审查与修复验收（2026-11-15）

## 1. 背景与方法

用户反馈：非开发者的新装机电脑上，依赖（node/FFmpeg/PowerShell）、模型权重
（H3/素材中心出图）、工作流节点与方案登记、视频播放等环节仍把正常使用权阻滞。
本轮沿「装机 → 依赖整备 → 权重/实例/方案 → 生产 → 验收 → 播放/交付」全链路，
按**唯一性原理**（同一事实/判定/入口只有一个权威来源）做对抗性审查，逐个修复。

勘察方式：亲自精读 + SearchWorkspace 跨文件取证（Explore/general-purpose 代理
在本会话被禁用）。审查阳性对照（已经统一、无需再改）：production_mode 单一权威、
profile_gate 与单镜判定共用 make_render_request、journey 只从台账推导、
setup_preparation 持久整备链、一键开拍自动登记缺档方案、工具 pin 清单共享、
audio 传输族缺绑定已降为警告、UNKNOWN 重发在 story/素材分场/四阶段均有入口。

## 2. 发现与修复（全部有代码证据，按批次提交）

| # | 问题 | 修复 | 提交 |
| --- | --- | --- | --- |
| P0-1 | 导演模式「待你确认」死状态：无验收动作；审片入口仅在专家层、每镜 10 项证据表 | 分集级人工验收台账 + API + EpisodeAcceptance 对话框；导演层单镜「分项审片」入口 | c5c7a50 |
| P0-2 | 交付 review_state 冻结在装配时，人工接受镜头后成片仍显示待审（两处真相） | 验收叠加层：成片库/分集总览/run summary/journey 读最新人工决定，冻结值保留为历史 | c5c7a50 |
| P0-3 | 媒体路由每请求（含每个 Range 分段）全量 read_bytes + SHA-256，大成片播放卡死/内存爆炸 | media/integrity.py 分块哈希 + (路径,大小,mtime) 验证缓存；ETag/304；Range 原生支持 | a0b2c0b |
| P0-4 | 缩略图 304 复验也全量读+哈希，整文件管道喂 ffmpeg | 先查 ETag 再触文件；ffmpeg 直读已验证路径 | a0b2c0b |
| P0-5 | 「已有权重」不跟随选定变体：有 INT8 时选 W6A8 被报成已准备好，候选权重永不下载 | summary/reconcile 增 variant_id，h3 事实与核验清单跟随变体自己的目录 | ed42bb2 |
| P1-6 | install_once「已在运行」只查 API，半死栈被送到死页面（违反启动器硬规则） | 委托 start_shine 三证判定与半死栈收尾；删除 API-only 探针 | bf29bbc |
| P1-7 | errorActions 仅 ~24 码，装机/门禁常见拒绝无导航 | 补齐 15 个拒绝码映射；未知码保留服务端原文 | 8ec2ceb |
| P1-8 | summary「唯一下一步」把可选审片报成必做 | vlm/asr 标 optional 并跳过；能力卡渲染「可选增强」与 advisories | 8ec2ceb |
| P1-9 | 工具门槛在 bootstrap.ps1 与 host_env.py 两处硬编码 | 清单 command_floors 单一来源（npm 与 node 版本号体系不同，不按包继承） | 52b71ef |
| P2-10 | FilmCrewPanel 残留密码时代凭据框 | 收进「高级」折叠，默认 local-user | 8ec2ceb |
| 裁定 | 安装器硬性非系统盘 ≥180GiB，单磁盘机器装不了 | 显式确认（YES/-AllowSystemDrive）后可装系统盘，门槛不降；确认事实贯穿三处守卫 | fb94e07 |
| 裁定 | 12GB 显卡：INT8/出图均要 15GB 显存，W6A8 激活 BLOCKED | 本轮只做诚实指引：runtime_advisories 门槛对比随下载结论展示 + 拒绝码导航；不私换模型、不猜门槛 | 8ec2ceb |
| 回归发现 | Windows 同一时钟 tick 内同长改写不改变 mtime，纯 stat 缓存（核验回执/播放）可能把改过的字节当「没动过」（既有弱点，8 分片负载下抖红） | 三处 stat 捷径与 sidecar 加头尾 64KiB 内容见证（≤128KiB 即全文）；旧回执无见证自动回退全量哈希；复用契约不变 | e4481ca |
| 回归发现 | 生成器再生成工具清单时丢弃 command_floors（测试回写真实配置）；三个迁移测试钉旧 head；新审计码未入前端清单 | 生成器保留已审计门槛（幂等）；head 常量更新 f180e0010001 + 新表断言；审计码与中文标签补齐 | 2d6998a |

## 3. 验收证据

### 3.1 定向回归（每批次）
- 批次 1：test_media_integrity（9 新）+ test_job_media / test_material_thumbnails /
  test_video_library / test_delivery_orders / test_delivery_assembly，共 63 项通过。
- 批次 2：test_delivery_acceptance（7 新）+ video_library / delivery_orders /
  director_overview / generation_segments / director_run_scope 复跑；前端
  deliveryAcceptance（4 新）+ videoLibrary / directorRun 复跑；Alembic 升级与
  check 无 drift（新 head f180e0010001）。
- 批次 3：test_variant_aware_summary（4 新）+ test_setup_one_stop_v41 /
  test_setup_capability_gates / test_variant_binding_and_gate 共 60 项；前端
  setupCenter / setupCapabilityGates / setupEntry 24 项。
- 批次 4：test_package_entrypoints 16 项（含新静态契约）。**启动器功能验收**
  （隔离目录 .local-acceptance/launcher-r4-20261115，mock）：干净启动 API/Web 均
  200；按身份杀 Web 后重跑 → 「missing: Web」收尾重建；完整运行重跑 →
  「already running」exit 0；stop_shine exit 0、记录清除、端口释放。
- 批次 5：test_tool_floor_single_source（3 新）+ test_bootstrap_entry（14）/
  test_host_tool_resolution / test_host_facts / test_new_machine_self_heal /
  test_deployment_storage 共 94 项。
- 批次 6：test_summary_optional_review（3 新）+ 前端 errorActionsCoverage（17 新）/
  setupCenter / directorBlockerNavigation / setupPanelBlockerText / filmCrewAbandon。
- 批次 7：test_package_entrypoints（16）+ test_bootstrap_entry（15，含系统盘
  显式确认接受/默认拒绝新功能用例）；三脚本语法解析；install_once -CheckOnly 正常。

### 3.2 全量回归（最终代码，干净运行）

- **后端 8 分片并行**（AGENTS.md 固定规则：按文件 `split -n r/8`、仓库根运行、
  `CODEBUDDY_SAFE_DELETE_ENABLED=0`、`env -u NO_PROXY -u no_proxy`）：
  **8/8 分片 exit 0，零 FAILED / 零 ERROR**；tier 守卫合计 selected=2839。
  分片日志留存 `/tmp/shine-r10-chunk-*.log`。
- **前端全量**：`npx vitest run --no-file-parallelism` → **78 文件 / 427 用例全过**。
- `npx tsc --noEmit` 通过；`npm run build` 通过。
- `ruff check backend/shine backend/tests` 全干净。
- Alembic：临时库 `upgrade head`（f180e0010001）+ `alembic check` 无 drift。

第一轮全量曾暴露三类本轮引入问题（迁移 head 常量、生成器丢弃 command_floors、
审计码清单），均已修复（2d6998a）；并顺带加固了既有 stat 缓存的同 tick 同长
篡改弱点（e4481ca）。上表为修复后最终代码的干净运行结果。

### 3.3 UI 验收（隔离目录 .local-acceptance/ui-accept-20261115，mock 栈）

种子：合成真实字节（imageio-ffmpeg，4s/320×240/含音轨）的交付产物 +
已确认分集计划 + COMPLETED Run/DELIVERED 分集行。**非 H3 生产证据**。

HTTP 层（API 8050）：
- `GET /deliveries/{id}/media` → 200，`ETag` = 产物 SHA，`accept-ranges: bytes`，
  `cache-control: private, no-cache`；`Range: bytes=0-99` → 206 +
  `content-range: bytes 0-99/57988`；`If-None-Match` → 304。
- `POST /deliveries/{id}/acceptance`（accepted/watched）→ 200，actor=local-user；
  同 request_id 重放 → 409。
- 成片库：review_state 由 pending_review → accepted，assembly_review_state
  保留 pending_review（历史不重写），summary.pending_review 归零。
- run episodes.acceptance.decision=accepted；journey post：
  「1 / 1 集已交付 · 1 集已确认」，accepted_episode_count=1。

浏览器层（真实 Chromium，Web 5250）：
- 成片库卡片显示「已确认接受」徽标与「观看并确认」按钮；
- 验收对话框：真实播放器（总时长 0:04）、当前验收态、观看声明门禁
  （未勾选时「确认接受这一版」禁用）、拒绝需原因；
- 脚本驱动播放：currentTime 推进至 1.15s，readyState=4，src 指向媒体路由；
- 导演第 7 阶段待审清单：「技术完成 ≠ 你已经认可」+ 分集「已确认接受」徽标
  + 「观看并确认」动作。截图 `.local-acceptance/ui-accept-20261115/post-review-list.png`。

验收后栈已停止（stop_shine + 监督进程按身份结束），8050/5250/8791 端口释放。

## 4. 诚实边界

- 本轮**未改** worker.py / preproduction.py / handoff / quality 客户端，不触发
  client_review_identity 重校准义务；历史 VLM 正负校准仍未完成，**不得声称已校准**。
- 验收闭环的 UI 验证使用合成真实字节（imageio-ffmpeg）的离线产物，**不是** H3
  生产证据；不声称任何新真实出片。
- 12GB 显卡真机路径、W6A8 候选受管激活、裸机 Windows 安装全链：NOT_RUN/BLOCKED，
  需用户机真机验收；本轮只保证指引诚实、门禁不被放宽。
- 门禁强度不变：UNKNOWN 不自动重放、候选不私换、权重逐字节核验、Ref2VA only、
  受管渲染器三证、人工验收不自动通过。历史产物/风格/交付不重写。

## 5. 过程事故与纠正

- 全量回归期间曾误在前端 vitest 运行时启动隔离栈，触发 start_shine 的 npm ci
  与运行中进程冲突（EPERM），node_modules 被部分删除、当次前端套件崩溃。
  已按锁文件恢复依赖并重跑前端全量；后端分片不受影响。教训：**依赖安装与
  测试/运行栈必须串行**，与 AGENTS.md 共享环境红线一致。