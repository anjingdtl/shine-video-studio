# Shine V2.0 双生产线最终收尾验收

2026-11-20，北京时间。验收在 Windows RTX 5080（16,303 MiB）上执行，使用 ComfyUI 0.39.0、PyTorch 2.10.0+cu130 和 FFmpeg/FFprobe 8.1。开工时本地 `main` 与远端 `main` 均指向 `31e4c20c5929d03ed62f56329099ad6b9a04035a`。双线生产的代码为 `d22dd8a1f1efc5075644402ea1c20ffae89e0075`。最终代码 `b839c97421069e0b42f8339d8c7fc49da054428d` 复验同一真实项目的确认依据。

七阶在导入剧本路径完成 1 集 2 镜并交付。四阶从人工分镜完成 2 集 3 镜并生成两集成片。三部成片均通过真实解码、落盘与播放端点的 SHA-256 核验，并在 Chrome 原有页面载入视频帧。用户看片签验见验收边界一节。

## 修复与本地提交

| commit | 变更 | 契约 |
| --- | --- | --- |
| `abeafe3` | `foundation/environment.py` 合并读取整备投影与运行回执。四阶 API 嵌入同一快照，`Manual4App.tsx` 用前端类型合同读取 `capabilities.*.state` | H3、Qwen、ComfyUI 和 Worker 各读原有权威来源。加载失败显示原因。配方准入结果仍由 `/platform/capabilities/check.status` 表示 |
| `6e6bc13` | `foundation/h3.py` 和七阶 `comfy/provider.py` 共用 `submit_prepared_prompt`、`ComfySubmission`、ComfyUI 客户端及历史/队列对账。四阶移除重复的真实图编译和 `object_info` 读取 | 先持久化参数化图的 SHA-256，再提交，收到 ACK 后持久化外部 ID。七阶沿用原有幂等键，四阶从已存在的 Job 和 attempt 编号推导提交身份 |
| `a97dad7` | 七阶导入剧本确认成功后触发现有工作台刷新。正文读服务端确认版本，右侧待办重新读 `/journey`。完成的提取任务显示「已提取大纲」 | 确认一次即可进入下一阶段。未确认修订稿仍可确认，已确认的生产依据按服务端版本保留 |
| `a8343aa` | 将首次全量中位置错误的 `fake_comfy.prompt_count` 断言归回原超时用例 | 保留原先的「只提交一次」断言，补测畸形 ACK |
| `d22dd8a` | 基础配置卡和四阶页头复用 `SETUP_CAPABILITY_LABEL` | `READY`、`VERIFIED_RENDER`、`BOUND` 等能力状态在两条线用同一套措辞 |
| `b839c97` | 旧大纲确认入口复用 `confirm_outline_artifact`，七阶读取既有文档确认指针 | 大纲正文、`/journey` 和分集读取同一个确认版本。兼容旧项目的回退读取，以及旧确认入口的响应和错误码 |

对账在外部 ID 已知时按该 ID 查找。ACK 缺失时按幂等键和图 SHA-256 定位，四阶首次历史 attempt 可带旧 Job 身份。证据不足的提交进入待核对流程。七阶仍编译自己的 H3 提示词，四阶仍由业务代码冻结手写提示词。两条线使用自己的 Run、Job、续跑和质检状态，GPU 准入继续交由既有 `ResourceScheduler`。

## 一次基础配置与大纲复验

- 通过公共基础配置执行一次 `/setup/prepare/execute`。任务 `setup-19d3ab02b525` 的 12 步全部完成，下载计划为 0 字节，复用既有 ComfyUI 和权重，对四个适用生成方案做实际节点合同核验。随后在公共素材中心通过既有入口完成 Qwen 的人物、场景、道具真机小样验证。
- 平台与四阶接口的配置哈希、安装根、配方、能力状态和整备回执一致。Chrome 四阶页面显示「ComfyUI 已就绪 · H3 已就绪 · Qwen 出图已真实验证 · Worker 在线」。证据为 `prepare-plan.json`、`prepare-receipt.json`、`shared-environment-evidence.json` 和 `manual4-web-environment.png`。
- 七阶导入本轮自编剧本，经既有 `MiniMax-M3.1-flash` 路由生成大纲。在 Chrome 点击一次确认，API 返回 200，左侧显示「大纲已确认」，右侧「剧本大纲还没有确认」随即消失。`/journey` 的确认依据与回执均指向 `d8e920c1-5511-4e4b-b8cb-3d3f998f04bc`。在 `b839c97` 重启隔离栈并重新进入该作品，确认版本和正文、右侧状态保持一致。证据为 `director7-outline-browser.json`、`director7-web-*-outline-confirm.png`、`director7-web-reopen-final-code.png` 和 `director7-final-outline-journal.json`。

## 共用素材与 GPU 互斥

四阶通过公共素材中心生成三张 Qwen 768×768 候选图。验收操作员核对候选图后，通过既有动作晋升并绑定到验收作品。七阶从同一素材中心采纳这些已晋升版本。七阶实际分镜使用人物、道具，场景为可选条目。四阶三镜均绑定这三个版本。

| 素材 | 公共素材版本 ID | SHA-256 |
| --- | --- | --- |
| 林舟 | `84e59878-b515-4aca-a87a-1c61a7869c16` | `6efe09913118d937bd7ccc07c8d5de3774abe45b2d9270235ca745c0900c9bdb` |
| 蓝色纸船 | `894df617-fe5e-4b37-9961-6cb42cf3e26f` | `8c8c31d08db937812bc9dba769f111fe0d5c79df9a9f7680dbb1772be182b33d` |
| 河岸小屋 | `9ab11592-162c-4a69-ba1d-2898d93c9106` | `b5afba8e16a07c4ad5c23a5872ab95ca519c6952421550e6f2747f572a46bd05` |

- 四阶持真实独占租约时，七阶能力检查返回 `temporarily_busy`，实际尝试向既有仲裁入口申请得到 `409 GPU_BUSY_RETRY_OR_QUEUE`。
- 七阶持真实独占租约时，四阶经既有入口申请得到 `409 GPU_BUSY_OTHER_LINE`。两次冲突的回执为 `gpu-director7-admission-rejected.json` 和 `gpu-manual4-admission-rejected.json`。完工时 `gpu-final-status.json` 的活跃租约和待核对租约均为空。
- 素材原图及采纳回执在 `asset-*.png`、`qwen-contact-sheet.png`、`shared-promoted-assets.json` 和 `director7-shared-asset-links.json`。

## 七阶真机交付

作品 `26d4b378-2533-4efd-abbe-04dea397a8af` 经真实大纲提取、测试操作者显式确认、分集、真实分镜调用、素材采纳及导演包构建进入生产。生产 Run `0da38de1-9343-4146-977c-8bd47187ffd5` 由七阶常驻 Worker 执行，Ref2VA INT8 ConvRot / PDD8，672×384。重放启动请求返回同一个生产 Run。两镜 `PASSED`，剧集 `DELIVERED`，生产 Run `COMPLETED`，`/journey` 七个阶段均为完成。

- 两个 ComfyUI prompt ID 分别为 `73afd955-00bc-4d21-889b-3482aba2da48`、`7c3e7963-648d-4f89-8953-45689c923874`。实际队列图 SHA-256 与各 attempt 的提交意图一致，实际提示词 SHA-256 与七阶编译结果及 ACK 元数据一致。两个 attempt 均先记录 intent，再记录 ACK。
- Series `ab03da96-d2ef-4988-94a8-eddbb3b6af9d` 最终 `COMPLETED/RELEASED`。本次执行在首镜后的既有 review handoff 中释放了第一个 H3 租约，随后顺序取得第二个 H3 租约。回执见 `director7-series-and-gpu-receipt.json` 及 `logs-production-final/worker.err.log`。
- 交付媒体 `254037f0-ad19-483b-ad93-e7ee72a8e400` 的 HTTP 播放响应为 200，响应体 SHA 与交付账本及磁盘相同。媒体均为 H.264/AAC。报告以 FFprobe 实测时长为准，导演包分集规划估算为 17.6 秒，两个 H3 请求的目标分别为 6 秒和 5 秒。
- 证据见 `director7-final-run.json`、`director7-final-journey.json`、`director7-video-evidence.json`、`submitted-director7-*.json`、`d7-*-ffprobe.json`、`director7-shot-contact-sheet.png` 和 `director7-web-delivery.png`。

| 七阶产物 | 实测秒数 | SHA-256 |
| --- | ---: | --- |
| 第 1 集第 1 镜 | 6.583 | `639e457295dd2aab8ab59ee0cebb2d3d66f8023f7c31407aa4a8aba53a0cc1ea` |
| 第 1 集第 2 镜 | 5.166 | `1d43eb5147e1db01fda3a75f19dd19372355b15abe6c8f1e034136fcc7b97d37` |
| 第 1 集成片 | 11.770996 | `1a5aeb56ec73d4e9c9e42f9009898713eab34851a3a14dda46cdd56d89f1cca7` |

## 四阶真机出片与恢复

作品 `123611d5-ad3e-4cb9-9a98-3eaee1964e6a` 导入并确认 2 集 3 镜人工分镜，逐镜绑定公共素材，冻结三份手写提示词。四阶常驻 Worker 使用 Ref2VA INT8 ConvRot / PDD8 和 672×384 配方。重复提交同一幂等键返回批次 `43af7982-24d2-4b9c-957e-883affc7aa12`。

- 首镜 ACK 后暂停批次，等待在途首镜 `PASSED` 和租约释放。通过标准启停入口重启隔离栈，在同一批次恢复。首镜媒体在重启前后 SHA 一致，剩余两镜继续执行。最终三镜均为 `PASSED`，每镜 attempt 次数均为 1。
- 三镜实际 `positive_prompt` 的 UTF-8 SHA-256 与冻结 SHA 一致。ComfyUI 队列图 SHA-256 与提交前落库的 SHA 一致。三个 `submit_intent_at` 均早于 `submit_ack_at`。
- 第 1 集第 2 镜绑定首镜真实尾帧，实际图包含 4 张参考。第 2 集首镜沿用同一人物、场景和道具，按集边界规则清空 `previous_job_id`，图内只有 3 张素材参考。抽帧检查可见两集沿用河岸木窗和蓝纸船。跨集首镜人物不在画面中，人物一致性以绑定账本为凭据。
- 3 个单镜和 2 个集成片均完成真实解码，媒体为 H.264/AAC。两集的播放端点实际响应体 SHA 均与磁盘相符。证据见 `manual4-prompt-and-continuity-proof.json`、`manual4-video-evidence.json`、`manual4-playback-evidence.json`、`m4-*-ffprobe.json`、`manual4-shot-contact-sheet.png` 和 `manual4-web-delivery.png`。重启前后日志在 `logs-manual4-before-restart/`、`logs-production-final/` 和 `manual4/logs/`。

| 四阶产物 | 实测秒数 | SHA-256 |
| --- | ---: | --- |
| 第 1 集第 1 镜 | 4.458 | `7ed48dec8bd41dafe118018021fb49718b6de279545fa6c23adad4a0dee6723d` |
| 第 1 集第 2 镜 | 4.458 | `74cb6edab8bfebfd11c5ca983b9a6413c592c79b690f615f41e679fa9753e828` |
| 第 2 集第 1 镜 | 4.458 | `408e4e8a4efeb5082f7d0ecc3f5036bb697c69a056506aee8a95d42a6932f043` |
| 第 1 集成片 | 8.937667 | `d4dd06a1f83635c69c4676c7f1e88710e0ad633e833fe33c1da15632b5d0f7c5` |
| 第 2 集成片 | 4.479333 | `794848945a60fcd6e833a262929f46c8bb3888410bf2ec8ec46aaa42c0757e2c` |

## 自动化回归

| 检查 | 命令 | 结果及日志 |
| --- | --- | --- |
| 后端全量 | `$env:CODEBUDDY_SAFE_DELETE_ENABLED='0'` 后运行 `pwsh -NoProfile -File .\run_backend_shards.ps1 -WorkRoot F:\ShineTest\v2-shards -KeepRuns 2` | 286 个顶层测试文件分为 8 片。最终代码 `b839c97` 上 21:10 轮 7 片全绿，chunk-3 的 `test_managed_startup` first-close-fence 用例因本地 HTTP 连接被中止（`WinError 10053`）失败，与本次改动无交集且隔离复跑通过；随后 21:47 完整重跑 8 片全部退出码 0、`shards_with_failure_markers=0`。见 `backend-8-shards-b839c97.log` 与 `backend-8-shards-b839c97-rerun.log` |
| 四阶专项 | `backend\.venv\Scripts\python.exe -m pytest backend/tests/manual4 -q -p no:cacheprovider --basetemp=F:\ShineTest\v2-shards\v2-closeout-manual4` | 9 个模块、26 个用例，退出码 0。见 `manual4-tests-final.log` |
| 前端全量 | 在 `frontend/` 执行 `npx.cmd vitest run --no-file-parallelism` | 88 个测试文件、454 个用例通过。见 `frontend-tests-final.log` |
| TypeScript / Vite | 在 `frontend/` 执行 `npm.cmd run build` | `tsc -b` 与 Vite 通过，Vite 报打包块尺寸提示。见 `frontend-build-final.log` |
| Ruff | `backend\.venv\Scripts\ruff.exe check backend/shine backend/tests` | 通过。见 `ruff-final.log` |

首轮全量的第 3 片新增三项畸形 ACK 用例因 fixture 断言放错位置报 `AttributeError`，归档在 `backend-initial-ack-fixture-failure.log`。断言已回归原网络超时用例，修复后单独运行原用例及三项新增用例均通过。

## 验收边界与本机归档

- 七阶样本覆盖导入剧本路径和一集交付，四阶样本覆盖两集及集边界。本轮实机任务恢复采用四阶暂停后重启的路径，ACK 丢失和异常对账由契约测试覆盖，未在实机人为丢弃 ACK。
- 七阶实际交接为两个顺序 H3 租约，双线独占通过。跨镜持续持有一个租约的口径在这次全流程中未满足，现有 review handoff 保持原行为。
- 七阶两镜技术门通过，最终状态为 `pending_review`。隔离安装的 VLM、ASR 路由分别报告 `VLM_ROUTE_NOT_CONFIGURED` 和 `ASR_ROUTE_NOT_CONFIGURED`，因而无本次本地 VLM 标定结果。七阶和四阶成片均待用户看片签验。
- 视频、FFprobe 原始 JSON、内容 SHA、进程及 API 日志保存在 `F:\ShineTest\v2-final-closeout`。各线的 `*-video-evidence.json` 含真实媒体路径，`browser-delivery-evidence.json` 记录三部成片的浏览器媒体尺寸和时长。证据目录与用户数据独立。
- 启动隔离栈时，日常进程占用的 `esbuild.exe` 令 `npm ci` 中止。随后用同一锁文件在独立目录补齐缺失的前端依赖，并核对在用的 `esbuild.exe` 内容哈希。验收后按进程归属停止了隔离栈，校验监听端口已释放。仓库 `.env` 及日常配方、图像运行配置的前后 SHA 一致，开工时已有的 `.trae/` 保留。
- 文本模型复用日常已配置路由。为隔离进程临时复制了一份 Windows DPAPI 加密凭据，未读取或打印明文。工具的自动审批策略拒绝删除这份已核对的隔离副本，副本仍在已停止的隔离数据目录，原凭据内容哈希匹配。
