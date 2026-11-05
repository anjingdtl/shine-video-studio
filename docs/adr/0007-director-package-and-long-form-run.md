# ADR 0007：内嵌导演包与整部作品运行的边界

- 日期：2026-11-06（Asia/Shanghai）
- 状态：已实现（M0/M2/M3/M4/M5 的离线闭环）
- 相关：`docs/DIRECTOR_WORKFLOW_AND_LONG_FORM_PRODUCTION_PLAN_20261105.md`、ADR 0003、ADR 0006

## 背景

导演模式已经具备剧本分析、素材准备、风格冻结、专业 crew、H3 连续生产（series）与自动交付。缺少的是统辖“全部选定分集”的持久对象：一个能确认的创作合同（导演包）和一个能持续推进整部作品、并在局部失败时继续其它部分的运行层。

## 决定

### 1. 导演包是身份快照，不是物料副本

`director_package_versions` 只保存已有对象的 id / revision / hash（剧本、分集、镜头、素材版本、风格、生成方案、制片准备计划、政策），不复制素材、不新建可编辑素材库，也不能批准素材、冻结风格或应用专业提案。

### 2. 分集清单按集分行，不塞进一个 JSON

根 manifest 存范围、政策、绑定身份和每集清单的 `manifest_sha256`；每集自己的镜头清单存在 `director_package_episodes`。读取单集时用根里钉住的 hash 校验，不需要把整部长剧一次载入内存。这与方案 §8.1 建议的“根 manifest + 分页子 manifest”语义一致，只是落地介质从 Artifact Store 改为同一账本的两张表 —— 原因见下条。

### 3. 不复用 `AgentArtifact` 存放导演包

`agent_artifacts.source_task_id` 非空且外键指向 `agent_task_records`。导演包由确定性编译产生，没有对应的持久任务；为它伪造一条任务记录会把“包是编译产物”混成“包是某次模型调用的产物”。因此包与分集清单各自建表，可被索引、可被分页读取，且不污染专业 Agent 的证据链。

### 4. `ready` 与 `startable` 分开

- `ready`：内容是否可满足（风格、素材、绑定、准入、清单完整性）。
- `startable`：`ready` 且已被用户确认且未失效。

一次成功生产不会让包获得永久资格；风格重冻、生成方案图变更、制片准备计划变更、镜头 revision 变更都会在就绪检查里变成明确 blocker，而不是静默沿用旧结论。

### 5. 运行层不拥有第二个调度器

`ProductionRun` 只在 Worker 的同一个 tick 里做一件短事：决定哪个批次可以变成可领取。每天最多创建一个 `ProductionSeries`，真正的 GPU 租约、renderer、job/attempt 全部仍在既有 series 机制里。Run 不持有整部作品期限的 GPU 锁。

`_advance_production_runs` 在 mock / offline / 无受管运行配置时直接返回 0：一台跑不了真实 H3 的机器上，运行不得凭空派发真实任务。

### 6. 交付范围从 series 扩到 scope

`delivery_orders` 新增 `scope_kind` / `scope_id` / `input_digest`，并把 `series_id` 改为可空。旧行回填 `series` 并保留 `(series_id, version)` 唯一约束；分集订单用部分唯一索引 `(scope_kind, scope_id, version) WHERE scope_id IS NOT NULL` 防重，避免 SQLite 把 NULL 当成互不相同的漏洞。

属于某个 Run 批次的 series **不再自动发布自己的纪录片**：一批不是一集，重复发布就是重复交付。分集交付由 run episode 订单负责，跨多个 series 生成一条有序 EDL。

### 7. 分层装配而不是无限放大单次上限

`EditDecisionList` 的 100 项合同保持不变。分集装配把清单分页（`page_size=100`），先折页再拼总片，每一层都是 `-c copy` 流式拼接，源片段只编码一次。这样既不受单次合同上限影响，也不把数小时媒体整体读入内存。

### 8. RAM 门槛只认一个口径

`host_env.RAM_MB_MIN` 早已采用“32 GB 机器实际报告约 31.7 GB”的口径（30 GiB floor），而部署目录里写的是 32768 MB。推荐模块用同一个容差读目录（`effective_floor_mb`），否则会推荐层否决安装器接受的机器 —— 正是方案禁止的“三套结论”。

## 后果

- 新增表 4 张（含事件表）、导演包 2 张、分集计划 1 张；迁移 `f180a0010001` 为纯增量 + 一次 SQLite 表重建（`delivery_orders`）。
- 前端六阶段导航的完成态只能来自 `/journey`，不能再用“有一个素材/镜头/视频”判断。
- 真实 H3 与小样验证不在本 ADR 范围内：本机渲染器未启动、磁盘低于 H3 组合的 180 GB 门槛，相关验收记为 BLOCKED。
