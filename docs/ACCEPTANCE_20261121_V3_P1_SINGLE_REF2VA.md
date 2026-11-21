# Shine V3 P1 单 Ref2VA 工作流验收

2026-11-21，北京时间。开发和 RTX 5080 验收均在原 checkout 的本地 `main` 执行。

- `BASE_SHA` 为 `060083194c8fedaa58b1737496baf4d46a1bace5`，fetch 后与 `origin/main` 一致。
- `AFTER_SHA` 由 `git log -1 --format=%H -- docs/ACCEPTANCE_20261121_V3_P1_SINGLE_REF2VA.md` 读取。
- 方案分支读取到 `6f36136`。方案引用的 `13595ad` 之后，实际基线已有 `0600831` 的续跑、RelayVSR、图片渲染器、整备、四阶监听和测试分片修复。本轮在这些行为上增量实施。开工时的 `.trae/` 与 `.vite/` 仍留在工作区。

## 调用关系与责任

| 入口 | 基线 | P1 |
| --- | --- | --- |
| 七阶段新拍 | 导演包和 Job 各自按参考清单直调图编译器。省略策略时从已登记 profile 选择 | 导演包、Job 和 Worker 统一调用 `foundation/h3.py::compile_h3_ref2va_graph`。新建请求在省略策略时可直接使用基础策略 |
| 四阶段新拍 | 手写冻结 prompt 经 manual4 adapter 进入公共门面，另行应用图参数 | 同一门面、同一逐镜图与参数化提交函数。四阶段在提交前核对冻结的配方 SHA |
| 整备与启动 | 安装目录、旧模板登记、历史图节点集合分别决定是否就绪 | 最小配方决定基础节点与权重，服务核验读一次真实 `object_info`。设置页、变体就绪和受管启动读同一套事实 |
| 历史回放 | 已冻结包、Job、Attempt 与显式登记 profile | 仍从原行读取 graph、parameter map、prompt 和哈希。显式 profile 注册与核验 API 继续可用 |

`shine.ref2va.v1` 是唯一图定义。变体通过参数化的模型节点链选择自己的 DiT、VAE 和 patch。
W6A8 的 native20 候选配方移入同一个模型节点链，来源哈希仍来自变体 manifest。
图与配方的 v1/v2 历史序列继续保留，已有请求也可按原 SHA 复现。

```text
七阶段 DirectorPackage → RenderRequest ─────────┐
四阶段 frozen prompt → Manual4 adapter ────────┤
整备和资源预检 → 同一变体与最小图 ───────────────┤
                                              ▼
         foundation/h3.py → comfy/graph_compiler.py
              → 参数映射 → 既有 submit_prepared_prompt
              → 原 Worker 回执与对账 → 技术 QC
```

| 唯一责任 | 权威来源 |
| --- | --- |
| 镜头输入 | 已冻结镜头和参考 manifest，七阶段策略与四阶段手写 prompt 快照各自保留 |
| 可执行范围与节点合同 | `h3/mode_policy.py`、公共 H3 门面和逐镜编译器。整备基础节点直接从配方派生 |
| 运行资源 | 现有 setup 和实例台账、模型验证回执、ComfyUI `object_info`、`ResourceScheduler` |
| 执行与恢复 | 原 Worker 持久化提交意图和 ACK，复用既有 ComfyUI 客户端与对账。UNKNOWN 保持待核对 |

一键整备移除了自动登记历史 profile 的步骤。已落库任务中的旧 `PROFILES` 步可以继续
读取并直接收尾。删除了按旧文件名汇总启动节点、逐份旧模板验收以及四阶段重复参数映射的路径。
新七阶段请求和四阶段工作台首次选择默认使用 `native20`，因此基础安装可以直接出片。
用户已经选择的采样与已冻结请求继续按其记录执行。旧版部分策略和四阶段 API 省略采样时
沿用版本 1 的 `pdd8` 解读，避免重启后的历史序列切换采样。

## 历史身份与防阻滞

9 份静态 JSON 全部留存。默认目录的 `2img-1aud`、`pdd8-2img-1aud`、竖屏
`pdd8-2img-1aud-portrait` 和 `pdd8-6img` 保留为历史注册来源。
`2img`、`pdd8-2img` 为既有样例及注册来源，`pdd8-upscale-2img` 是历史延后配方。
`lowend-pdd-free-2img` 与 `nvfp4-turbo-4step-2img` 保留各自变体和历史图的审计身份。
显式指定旧 profile 的请求照原图运行。1.2.x 至 1.4.0 的 H3 提示词编译与规则路径保持原版本。

从 `BASE_SHA` 在原 checkout 内直接加载旧编译器，逐项比较生成图和配方 SHA。
INT8 以 `native20/pdd8 × 1–9 图 × 0–3 音 × 单采/二采` 比较 144 组，
W6A8 `native20` 单采比较 36 组，180 组两类 SHA 均一致。
结果见本机 `frozen-graph-compatibility.json`。
只读核对日常库 40 条既有 profile，图 SHA 全部与存储值相符。隔离库原有的七阶段 2 条
Job/Attempt 和四阶段 3 条 Job/Attempt 保持原提示词、提交图与产物哈希。
汇总见 `history-compatibility-evidence.json`。

| 反例 | 结果 |
| --- | --- |
| 旧模板目录为空，作品没有 profile 行 | INT8 基础节点和权重可推导，整备与入拍可走动态图。旧 v1/v2 封条的兼容与漂移提示有回归覆盖 |
| 仅有基础包，缺 PDD、VHS、latent upscaler 或 RelayVSR | 原生单采闭包只含核心包与 4 个基础权重包，能核对运行合同并开拍 |
| 缺 H3 核心节点或镜头明确要求的装载节点 | 具名报告实际缺少的节点或输入。未选分支无占位 loader，指定素材不会丢失 |
| 无图、13 个输入、未验证视频 loader | 分别拒绝为 `PROJECT_REQUIRES_REF2VA`、`TOTAL_REFERENCE_LIMIT_EXCEEDED`、`VIDEO_REFERENCE_LOADER_UNVERIFIED` |
| W6A8 或 NVFP4 组合超出已审计范围 | 候选和未核验状态保留，拒绝时保留原 checkpoint 身份。两者均未登记为新动态生产默认 |
| 请求重复、提交回执不确定 | 正常重复请求复用 Run。断连恢复仍按 ACK 或幂等键加提交图 SHA 核对 |

## RTX 5080 真机

使用本机已收编的 ComfyUI `b0b743566f65daafc423b4fea8a2fbda94b3384a`、真实
INT8 ConvRot 权重、RTX 5080 16,303 MiB、驱动 621.24、FFmpeg/FFprobe 8.1。
复用 V2 隔离项目的已确认分镜和已采用素材，将数据库和素材复制到本轮隔离目录。
四阶段创建一镜新快照，采用同一素材中心里已批准的场景图。

真实 `object_info` 返回 1,281 类节点。零条 `WorkflowProfile` 时，两个采样的单图基础
输入合同均过核验，运行态为 `ACTIVE`。两条生产线读取同一物理安装与配置 SHA
`081254eee2a35490e2a03cfc8eca515c497e9dccd5646ee20a1ac9a132590966`。
七阶段与四阶段的受管进程沿用已有端口隔离，GPU 由同一资源调度台账串行分配。

| 真机镜头 | 参考 | 图节点 | 提交图 SHA | 媒体 SHA | 媒体结果 |
| --- | --- | ---: | --- | --- | --- |
| 七阶段 `5b5622d9`、PDD 8 步 | 1 图、0 音 | 23 | `5fefa0a6fb4b7bab4ab60c49e450ef9cda4bbfe11ab2c3dce8647ee6178b6a9f` | `cb53eb1a91968c08fdd6f93fc99604d76385a6713bf8bf4a5dd738189ca2ed48` | 576×320，6.583 s |
| 七阶段 `e78e616f`、PDD 8 步 | 2 图、0 音 | 24 | `2a1ac788fc820f5139c8a9a560cd7b80cc4545d3a552cfe46a3b76d29723ee3b` | `f7399b7ecad3574c7d4c84af3e90b682cb8bbfb3825bc7f4b9cdb070b9b98318` | 576×320，5.166 s |
| 七阶段 `4588ec0c`、原生 20 步 | 1 图、0 音 | 22 | `fa4bf28025f1200e011177510918c6aff1bcd1ef52040e9876c083b1ed7302bb` | `d3ab9555a05708e012036a8bf29a2d09477e5cac814ee6077567d201b1e9b5ad` | 576×320，6.583 s |
| 七阶段 `8e0640dd`、原生 20 步 | 2 图、0 音 | 23 | `248f0379cca3105ce34ca7d1354d86667c4f1f75fc6c887e4240967f4a2407c8` | `4137c49b766239ed97c3695a101e1eb8d377bee88dcc13164e4566d6881bd0e0` | 576×320，5.166 s |
| 四阶段 `224f4fee`、原生 20 步 | 1 图、0 音 | 22 | `d29ce526901383f57361fe03f0c5fc3b0dc4c5d009150babd86eadc32164d82f` | `a012389b6a6d23253823e900a6ce7a58387831c4084a16a854b8251a081545f2` | 672×384，4.458 s |

五镜均为 H.264/AAC、技术 QC 通过、全片解码通过、单次尝试。各采样的单图与双图分别
只差一项图片 loader。原生图使用 Sage Attention patch，PDD 模型与采样节点只出现在
PDD 图中。七阶段原生样片在最终源码下经省略策略和 profile 的启动请求生成。
两条线均验证了正常重放启动请求返回同一 Run。七阶段两版成片各为 11.771 s，下载
字节 SHA 与各自存储一致。四阶段的单镜播放端点返回原存储字节。
收尾时活跃和待核对 GPU 租约均为空，隔离栈已停止。
首轮隔离配方沿用了复制前的 YAML 路径，启动提前退出，并向原 V2 验收日志追加了该次启动记录。
经现有配置入口重建隔离配方后，真实服务核验和两条生产线均跑通，当前工作台的 `.env` 校验一致。

机器证据位于 `.local-acceptance/v3-p1-20261121/`。其中 `service-validation.json`、
`activation.json`、`setup-summary.json` 与 `manual4-status.json` 记录环境事实。
`seven-evidence.json`、`seven-native-evidence.json`、`manual-evidence.json`、`*-ffprobe.json`、`submitted/` 和
`comfyops/handoff-history/` 记录媒体与提交身份。验收数据库、日志和视频保留在本机。

## 回归与收尾

```powershell
$env:CODEBUDDY_SAFE_DELETE_ENABLED='0'
pwsh -NoProfile -File .\run_backend_shards.ps1 -WorkRoot F:\ShineTest -Shards 8 -KeepRuns 11
.\backend\.venv\Scripts\python.exe -m ruff check backend/shine backend/tests scripts/shine_supervisor.py
.\backend\.venv\Scripts\python.exe backend/scripts/generate_shine_copilot_contracts.py --check
.\backend\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini current
.\backend\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini check
```

前端目录执行 `npm.cmd exec -- vitest run --no-file-parallelism`、`npm.cmd exec -- tsc --noEmit`
和 `npm.cmd run build`，93 文件共 471 项通过，类型检查与构建通过。Ruff 与 Copilot
契约检查通过。Alembic 在本轮隔离库为 `f180f0010008 (head)`，未发现差异。

最终后端全量覆盖 301 个测试文件，8 个分片全部退出 0，无 `FAILED` 或 `ERROR` 行，
总用时 1021.2 s。汇总见 `backend-full-final.log`，分片日志位于
`F:\ShineTest\shards-20261121-230513-708`。

| 分片 | 文件数 | 退出码 | 用时（s） |
| --- | ---: | ---: | ---: |
| `chunk-0` | 39 | 0 | 352.2 |
| `chunk-1` | 37 | 0 | 656.5 |
| `chunk-2` | 36 | 0 | 702.3 |
| `chunk-3` | 36 | 0 | 635.3 |
| `chunk-4` | 37 | 0 | 1020.5 |
| `chunk-5` | 42 | 0 | 816.5 |
| `chunk-6` | 37 | 0 | 853.2 |
| `chunk-7` | 37 | 0 | 773.8 |

分片脚本清理了本轮 7 个 `bt-*` 临时目录。`bt-1` 中一项被 Windows 拒绝访问，
仍留在最终分片目录。

后端定向验收通过 169 项，首轮差异专项复跑通过 148 项，最终旧策略兼容专项通过 35 项。
具体模块和计数见 `pytest-p1-targets.log`、`pytest-p1-rerun.log` 与
`pytest-historical-strategy.log`。覆盖 24 种基础矩阵、拒绝边界、历史 profile、
Worker 提交/对账、四阶段专项及 offline E2E、film crew。

初轮全量暴露了默认采样、安装闭包和历史事件码断言需要同步。测试期间修改被审片
锁定的源文件触发了 `REVIEW_CLIENT_SOURCE_CHANGED`。对应定向测试在源文件稳定后
通过。一次中途启动的复跑因补齐旧部分策略兼容而终止，最终口径以完整的静态源码复跑为准。

从仓库外调用 `scripts/start_shine.ps1 -DataDir <隔离目录> -Provider comfyui -AutoPorts`。
独立测试进程占用 8057 时，启动器改用 8058/5258，API 与 Web 均返回 200，占用进程仍在。
`scripts/stop_shine.ps1 -DataDir <隔离目录>` 完成，进程记录与隔离端口均已释放。
见 `launcher-port-smoke.json` 与 `final-stop.json`。

本地分发包以提交后的 `pwsh -NoProfile -File scripts/build_test_package.ps1 -OutDir <本机验收目录> -Verify`
校验，执行回执保存于 `package-build.log`。

## 尚待复核与回滚

- 参考音频、二采和 RelayVSR 本轮工程回归覆盖其图或闭包，未做新增真机样片。视频参考维持具名拒绝。
- 4070、5090vd2、W6A8 与 NVFP4 没有本轮硬件证据。VLM/ASR 资源和运行包在隔离栈中为
  `NOT_READY`。`worker.py` 和 handoff 源文件变化后，旧 VLM client identity 按既有规则失效，
  本轮没有取得新的真实正负校准。成片仍待人工观感与声音复核。
- 回滚使用本轮 commit 的 `git revert`，部署到空闲工作栈。生产台账、旧封条、profile、模型
  与媒体留存。需定位单镜时，用报告中的 Worker intent、prompt ID 和图 SHA 核对相同尝试。
