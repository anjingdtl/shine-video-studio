# 统一素材生图 LoRA 与剧本对齐提示词 V1 验收

- 建设基线 `main` · `6f780b8ba9fb023f92fe15a03b47537897ef5fec`
- 方案来源 `origin/docs/image-lora-and-prompt-v1-20261125` · `751f786e0687349e5799dd5740a329f81f2a36e8`
- 验收日期 2026-11-25，Windows 11 本机，RTX 5080 16 GB。
- 分工：M1–M3 建设、首轮全量回归、M4 的下载与前台续传由 Codex 完成。Codex 额度中断后由本轮接手，
  接手点是「四枚权重前台续传完成之后」，其后完成自动纳管、指纹验证、真实出图、目检、终检与本文。

## 结论

| 验收项 | 结果 | 依据 |
| --- | --- | --- |
| M1 可选下载、迁移、统一台账与唯一裁决 | 通过 | 专项测试（Codex 记录）；真机自动纳管 |
| M2 统一生图基座与提示词 V6 | 通过 | 专项测试（Codex 记录）；真机图谱与提示词 |
| M3 双入口前后端贯通 | 通过 | 专项测试（Codex 记录）；最终树终检 |
| 全量回归（最终树） | 通过 | 后端 8 分片 315 个文件、前端 95 个文件 497 项，全部一次通过 |
| M4 ① 四枚权重下载、校验、自动纳管 | 通过 | 逐枚 SHA-256 复核；台账 4 行 |
| M4 ② 生图运行时 V6 指纹刷新与验证 | 通过 | 三类家族升到 `RENDER_VERIFIED`。首次尝试失败，原因见 §M4-2 |
| M4 ③ 零 LoRA 与三套配方 T2I | 通过 | 4 张真实出图，风格与负向词生效 |
| M4 ③ I2I | 通过 | 身份保留，负向通道已通 |
| M4 ③ MULTIREF | 管线通过，画面不达标 | 叠加配方后人物被压没，见遗留问题 1 |
| M4 ③ 剧本角色外貌、时代服饰与一致性 | 通过，有保留 | 硬特征命中；未写入剧本的特征会漂移 |
| M4 ④ GPU 释放与租约残留 | 通过 | 15 条租约全部 `RELEASED`，渲染器进程 0 个 |

**本文不声称的事：**

- 没有人工艺术验收。所有产物都是 `CANDIDATE_READY` 候选，没有一张被接受为主素材。
- H3 视频侧 LoRA 本轮没有真机重跑。无回归的依据是全量回归与 H3 分支代码未改动。
- 真机只覆盖 T2I、I2I、MULTIREF。INPAINT、WARDROBE、`RGBA + MASK_CUTOUT_V1` 的 422 拒绝、跨基座 409、
  `LORA_IN_USE` 只有离线用例。
- 「确认整备所选能力」的下载是通过它背后的接口触发的，没有走浏览器点击。
- 没有在真正的新机器上验证零 LoRA 零下载；本轮的隔离目录里出图模型本来就在。
- 本轮未修改 `worker.py`、`preproduction.py`、`comfy/client.py`，不触发 VLM 重校准，也没有做 VLM 校准。

## 建设结果（M1–M3，Codex 记录）

| 阶段 | 交付 | 专项验证 |
| --- | --- | --- |
| M1 | 「制作环境」按需整备 4 枚生图 LoRA。既有安装器复用或下载固定字节，凭据写入后按 SHA-256 自动纳管。`f180f0010010` 给同一台账增加基座、负向词和触发位置。两个生图队列中的在途引用使用既有删除保护。 | 后端 8 个专项模块，62 项均通过。旧 revision 标记伴随现有表结构的场景修正后定向复验；前端基础设置 13 项通过。 |
| M2 | T2I 和编辑图共用 LoRA 串联函数，节点位于 `QwenImage21Cache` 前。V6 从剧本来源提取当前实体档案，主体与不可漂移特征优先，保留介质、防叠加和纸张及屏幕载体规则。预览和生产调用同一提示词装配逻辑。 | 后端 8 个专项模块，75 项均通过。长度上限用不同硬特征复验；角色模板确认排除未翻译的大纲正文。 |
| M3 | 素材中心、项目素材准备和导演④页共用风格装配组件。请求按已有幂等规则冻结配方和 LoRA 快照，原任务、attempt 与候选来源保留证据。Copilot 声明与审计事件已同步。 | 后端双入口及整备 66 项、Copilot 和受管渲染器等 48 项通过。前端 5 个专项文件共 62 项通过。 |

本轮细读了以上改动的核心落点：安装 Worker、LoRA 裁决器、两条生图执行链、提示词装配与 V6 层序、剧本档案注入、
两个请求入口、前端共享装配组件。这些与方案一致，未发现需要修改的缺陷。其余文件没有逐行细读，依赖专项测试与终检。

### 三原则核验

| 原则 | 代码落点 | 真机复核 |
| --- | --- | --- |
| 一套事实 | `style_lora_assets`、固定部署清单、只读配方、任务冻结快照 | H3 和 Qwen 通过 `base_model` 区分。安装 Worker 写入的 4 行台账，与 `GET /api/v1/loras?base_model=qwen_image_21` 返回的是同一组 SHA-256，三套配方的槽位状态由它投影。 |
| 一个裁决 | `evaluate_style_lora_selection()`、`_ensure_lora_not_in_use()` | 两条生图线提交前都走 `resolve_live_image_loras()`。真机只走到通过路径；拒绝路径由离线用例覆盖。 |
| 一个执行者 | 原安装 Worker、原素材 Worker、`attach_qwen_style_loras()`、`apply_image_lora_prompts()` | ComfyUI 历史里的 LoRA 节点只出现在 `451 → 4511 → 4512 → 缓存节点` 这一条链上。项目线的预览提示词与实际提交的提示词逐字相同（选配方与不选配方各验证一次）。 |

当前 `main` 已有 `material-image-v6-qwen21` 版本名。新指纹加入该模板的实际合同摘要，同一个配置上的旧指纹因此失效。
基础 T2I 图谱 SHA-256 保持 `83a2bafb67af29f3a8a5a4b4572f19f940ff5f2c31b64b0cef024eb726d42d85`（真机就绪接口读数一致），
编辑系适配器模板文件未被修改。

三套配方固定为 35、30、30 步，`cfg=3.5`，`euler/simple`。用户手工装配 LoRA 且未选配方时采用 30 步、`cfg=3.5`、
`euler/simple`。未选 LoRA 或配方继续使用原来 25 步、`cfg=1.0`、`euler/simple` 的默认值。以上均与真机 ComfyUI 历史里的采样参数一致。

## 合拢与全量回归

### 首轮（Codex，20:22 启动）

`backend\.venv\Scripts\ruff.exe check backend/shine backend/tests backend/alembic/versions` 通过；
`npx.cmd tsc --noEmit`、`npm.cmd run build` 通过；`npx.cmd vitest run --no-file-parallelism` 共 95 个文件、497 项通过。
后端在 `CODEBUDDY_SAFE_DELETE_ENABLED=0` 下执行 `.\run_backend_shards.ps1 -WorkRoot F:\ShineTest -Shards 8 -KeepRuns 28`，
首轮覆盖 315 个文件。第 0、6 分片各因一处缺陷首轮失败，修复后按原清单补跑通过。

首轮证据有两处覆盖缺口，这也是补做终检的原因：

1. 为修第 0、6 分片而改动的 `setup_summary.py`（20:29）、`image_prompts.py` 与 `service.py`（20:34）发生在其余分片仍在运行时，
   第 1–5、7 分片加载的是改动前的模块。
2. 回归之后又改了 `deployment.py`、`installation_worker.py`、`test_image_lora_assembly.py`（20:55–21:02，下载分段与后台线程），
   当时只有 41 项定向测试覆盖。

### 终检（本轮，最终树，22:44–23:07）

M4 期间没有改动任何产品文件，终检的对象就是提交的这棵树。

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| Ruff | `ruff check backend/shine backend/tests backend/alembic/versions` | 全部通过 |
| 后端全量 | `.\run_backend_shards.ps1 -WorkRoot F:\ShineTest -Shards 8` | 8 分片全部退出码 0，315 个文件，墙钟 745.3 秒，`shards_with_failure_markers=0`，无补跑 |
| 类型 | `npx.cmd tsc --noEmit` | 退出码 0 |
| 前端全量 | `npx.cmd vitest run --no-file-parallelism` | 95 个文件、497 项通过 |
| 构建 | `npm.cmd run build` | 通过；主包 1,000 kB，仍提示大于 500 kB |

| 分片 | 文件数 | 退出码 | 耗时（秒） |
| --- | ---: | ---: | ---: |
| 0 | 38 | 0 | 729.7 |
| 1 | 38 | 0 | 744.9 |
| 2 | 39 | 0 | 713.0 |
| 3 | 39 | 0 | 724.7 |
| 4 | 40 | 0 | 673.9 |
| 5 | 40 | 0 | 719.5 |
| 6 | 41 | 0 | 725.8 |
| 7 | 40 | 0 | 736.2 |

日志只打印每个分片最慢 20 个模块的摘要，总用例数无法从日志还原。归档在 `F:\ShineTest\shards-20261125-224438-993`。

**证据留存的一次操作失误。** 终检用的是脚本默认的保留数（1 次），没有沿用 Codex 的 `-KeepRuns 28`，
脚本因此清理了 `F:\ShineTest` 下 28 个更早的归档日志（11-21 至 11-25）。开跑前只备份了当天的两个
（`prior-shards-archives/`，含首轮全量回归的原始日志与补跑日志），其余 26 个的原始日志已不可恢复。
各轮的结论仍记录在对应的验收文档与 `AGENTS.md` 中。

## M4 真机验收

### 环境

| 项 | 值 |
| --- | --- |
| 显卡 | NVIDIA GeForce RTX 5080，16303 MiB |
| 驱动 | 开始时 `621.24`；21:38:59–21:39:28 被后台升级为 `621.40`（驱动日期 2026-11-21）。其后全部出图均在 `621.40` 上完成 |
| ComfyUI | v0.39.0，源码 revision `b0b743566f65daafc423b4fea8a2fbda94b3384a`，torch `2.10.0+cu130`，comfy-kitchen `0.2.37` |
| 隔离栈 | 数据目录 `.local-acceptance/image-lora-v1-20261125/隔离 数据`（含中文与空格），API `8316`、Web `5376`、Supervisor `8896`、ComfyUI `8248` |
| 测试剧本 | 本轮编写的南宋档案官样例：许知微，四十三岁，方脸，右眉上一道细旧烫疤，乌发额前一绺银白，穿深靛色窄袖圆领丝袍 |

仓库根 `.env` 未被本轮隔离栈改动（修改时间仍为 11-23），日常端口 `8000/5173/8188` 未被占用。

### M4-1 可选下载、校验与自动纳管

四枚权重来自独立清单 `config/deployment/qwen-image-loras-windows-x64.json`，合计 776,384,068 B（740.4 MiB）。

| 组件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `qwen-image-2.1-lora-fix` | 54,800,124 | `3f00361145703a64c7b9812d7797079820a1f7b0dbb5768b9d3df1f3c7750dfa` |
| `qwen-image-2.1-lora-natural-exposure` | 83,943,952 | `a8edea397ce55ae6e1a442f3ba963eb78beeb515d4fe7d217d496542317292b1` |
| `qwen-image-2.1-lora-anything2real` | 318,820,200 | `5db4dd2376e6017812bf1189b2fc46e52aaa2c0b7e688faac75e83e1c4dce849` |
| `qwen-image-2.1-lora-multiple-angles` | 318,819,792 | `548c57977c3f1c09c2c9e5f4e83e930cbc6f719c878a576ab8666ce29fef39b6` |

落盘文件的字节数与摘要由本轮独立重算，磁盘、部署清单、方案 §4.1 钉点三方一致。

下载过程（本机网络下 CDN 反复停顿，如实记录）：

1. 后台首次尝试以 `DOWNLOAD_STREAM_FAILED:ReadTimeout` 失败，默认 64 MiB 分段反复读超时。
2. Codex 的修复：只对纯生图 LoRA 计划使用 16 MiB 分段；纯生图 LoRA 安装改由 Worker 内一条非守护线程执行，
   传输期间 Worker 的其他 tick 可继续。`test_image_lora_assembly.py`、`test_installation_persistence.py`、
   `test_installation_queue.py` 共 41 项通过（Codex 记录）。
3. 第二次后台尝试仍在 21:11:56 于安全点暂停，已确认 63,857,474 B。
4. 前台用同一个 `ControlledDownloader`、同一份 `.part` 断点续传（21:09–21:28）。期间观察到一次停顿，
   120 秒读超时后自动从断点续上。

**字节由前台灌入断点，核验与纳管仍由原安装 Worker 完成。** 本轮通过
`POST /comfyops/installation-attempts/cbeb221c…/resume` 恢复原安装记录，Worker 在 21:36:41 领取，约 3 秒内把四枚包
从已核验缓存落盘为 `AVAILABLE`，安装记录止于 `FILES_VERIFIED_PENDING_RUNTIME`。

台账 `style_lora_assets` 新增 4 行，`base_model=qwen_image_21`、`origin=discovered`，以上查询在 `GET /loras` 投影之前执行：

| 组件 | 默认权重 | 触发位置 | 触发词 |
| --- | ---: | --- | --- |
| `…-fix` | 0.75 | suffix | 无 |
| `…-natural-exposure` | 0.60 | suffix | `Transform the image with balanced neutral exposure` |
| `…-anything2real` | 0.55 | suffix | 无 |
| `…-multiple-angles` | 0.80 | prefix | `<mva>` |

`shine-style-loras.yaml` 列出了用户目录与四个受管包目录共 5 个 LoRA 搜索路径。
随后 image 能力整备八步全部 `DONE`（`TOOL_REPAIR` 按名跳过），`IMAGE_VERIFY` 在 8248 上核对出图节点后渲染器已释放显存。

### M4-2 生图运行时 V6 指纹刷新与验证

| 项 | 值 |
| --- | --- |
| 旧公式指纹 | `0c676a1a497337041c9f6f32e0f97d966c517a2f19a47a10010886977d0aec54` |
| V6 合同指纹 | `a9e85ef44b1673a85a121813bfe7b368c9fc736f586295433dff7f134cd21cd6` |
| 模板 | `material-image-v6-qwen21` |

旧凭证按设计失效。出小样前三类家族都是 `can_generate=false`。

**首次尝试失败（运行 `71ec856a…`）。** 21:39:22 提交第一条小样，渲染器在加载文本编码器
`qwen3vl_8b_int8_convrot` 时报 `CUDA error: out of memory`，随后 `CUDA error: unknown error`，提示工作线程死亡。
根因是显卡驱动恰在此刻被后台替换：驱动库 `nv_dispi.inf` 写入于 21:38:59，系统事件 `UserPnp 20003` 在 21:39:22
记录 `nvlddmkm` 服务重新注册，`NVDisplay.Container.exe` 的新实例在 21:39:22 启动。同一套 ComfyUI、
同一个文本编码器在本机此前多次成功（最近一次为当天 18:40，129 条提示全部完成）。
发起方最可能是 NVIDIA App 的自动更新，安装器进程本身未被日志捕获。

本次与 V6 / LoRA 代码无关：失败的是零 LoRA 小样，基础图谱哈希未变。

故障期间产品的行为，每一道都与设计一致：

| 时刻 | 行为 |
| --- | --- |
| 21:52、22:12 | 「停止生图运行时」被 `RUNTIME_WORK_IN_FLIGHT` 拒绝两次，先是在途条目，后是待核对租约 |
| 21:53 | 取消运行：未开始的两个条目立即 `CANCELLED`，在途条目不被打断 |
| 22:10:01 | 轮询 1800 次到期（31 分钟）。Worker 取证 `/queue` 200、`/system_stats` 500；条目 `NEEDS_REVIEW`，attempt `UNKNOWN`，租约 `RECONCILE_REQUIRED` |
| 22:13 | 「关闭本机后台并释放显存」终止了命名 Job Object，但进程在 20 秒核验窗内未退出，如实报 `unverified` |
| 22:14 | 进程消失、端口释放 |
| 22:15 | 租约由产品对账释放为 `RELEASED`，没有按超时释放 |
| 22:17 | 「停止生图运行时」按 `renderer_gone` 把启动记录与实例行改写为 `STOPPED` |

**重试（运行 `6c2af6e9…`，新幂等键）通过。** 三类小样在 75 秒内全部 `CANDIDATE_READY`，均由工作台自有的渲染器产出
（`renderer_owned=true`，ComfyUI 状态 `success`，图谱内 0 个 LoRA 节点）。出样后三类家族均升到 `RENDER_VERIFIED`、
`can_generate=true`，指纹前后一致。四枚 LoRA 在实时 ComfyUI 列表中全部 `READY`。

### M4-3 真实出图

共 11 张，全部是工作台自有渲染器的真实产出，ComfyUI 历史 `success`，脚本已核对产物摘要与 attempt 输出摘要一致。

| 图 | 入口 | 模式 | 配方 | LoRA 与权重 | 采样 | 尺寸 |
| --- | --- | --- | --- | --- | --- | --- |
| `zero` | 素材中心 | T2I | 无 | 无 | 25 步 / cfg 1.0 | 640² |
| `cinematic` | 素材中心 | T2I | 电影级写实摄影 | fix 0.75 → 自然曝光 0.60 | 35 步 / cfg 3.5 | 640² |
| `fantasy` | 素材中心 | T2I | 暗黑奇幻插画 | anything2real 0.55 | 30 步 / cfg 3.5 | 640² |
| `cyberpunk` | 素材中心 | T2I | 赛博朋克 | fix 0.80 | 30 步 / cfg 3.5 | 640² |
| `i2i` | 素材中心 | I2I | 电影级写实摄影 | fix 0.75 → 自然曝光 0.60 | 35 步 / cfg 3.5 | 640² |
| `multiref` | 素材中心 | MULTIREF | 赛博朋克 + 多视角 | fix 0.80 → 多视角 0.80 | 30 步 / cfg 3.5 | 640² |
| `multiref_zero` | 对照 | MULTIREF | 无 | 无 | 25 步 / cfg 1.0 | 640² |
| `multiref_fix_only` | 对照 | MULTIREF | 赛博朋克 | fix 0.80 | 30 步 / cfg 3.5 | 640² |
| `multiref_words_only` | 对照 | MULTIREF | 无，手工写入赛博配方词 | 无 | 25 步 / cfg 1.0 | 640² |
| `aligned_character` | 项目线 | T2I | 电影级写实摄影 | fix 0.75 → 自然曝光 0.60 | 35 步 / cfg 3.5 | 1024² |
| `aligned_character_zero` | 项目线对照 | T2I | 无 | 无 | 25 步 / cfg 1.0 | 1024² |

采样器均为 `euler/simple`。提交到 ComfyUI 的图谱证实了方案 §6 的几条规则：

- LoRA 节点 `4511`、`4512` 串在 UNET 与缓存节点之间，T2I（缓存 `480`）与编辑图（缓存 `469`）共用这一条链。
- 编辑图的负向通道已接通，`474` 节点的 `negative_prompt` 非空。
- LoRA 词在参考图映射之前注入，`<image1> is the edit target` 仍在末尾。
- 多视角 LoRA 的 `<mva>` 出现在整条提示词的最前端。
- 项目线的 V6 层序可见：外观 → 构图 → 时代锚点 → 防文字与防拼图 → 风格词；选了配方时不再强塞棚拍布光句，
  没选时保留「Even soft diffused lighting」。

显存实测（nvidia-smi，每 0.5 秒）：

| 阶段 | 基线 | 峰值 | 余量 |
| --- | ---: | ---: | ---: |
| 三类小样 | 894 MiB | 15473 MiB | 830 MiB |
| 出图与对照 | 1094 MiB | 15464 MiB | 839 MiB |

每出完一张，工作台的空闲保持逻辑把显存放回 1.3–1.4 GB，下一张再重新载入。挂 LoRA 不增加采样期峰值。

### M4-4 出图质量对比

拼图入库在 `docs/evidence/20261125-image-lora-prompt-v1/`，全分辨率原图在验收目录，摘要见 `summary.json`。

**T2I 三套风格**（`01-t2i-styles.jpg`）

| 图 | 观察 | 结论 |
| --- | --- | --- |
| 零 LoRA | 写实照片感、暖调、偏纪实快照。没听「正面」，画成侧脸 | 基线保持原样 |
| 电影级写实摄影 | 青橙调、明暗对照、浅景深，电影剧照感 | 风格生效 |
| 暗黑奇幻插画 | 油画质感的暗黑概念图，去饱和基调、余烬微光、体积雾；刻纹铁护颈清晰。没有动漫感、平涂、矢量味 | 正向与负向词均生效；「中性容貌」表达偏弱 |
| 赛博朋克 | 夜景窄街大远景，街道空无一人一车，湿地面反射青、品红霓虹，两股体积蒸汽，高对比 | 正向与负向词均生效 |

**剧本对齐**（`02-script-alignment.jpg`）。第一列用的是不含剧本信息的通用提示词，只说明不喂剧本时模型会画成什么样。

| 剧本硬特征 | 素材中心·零 LoRA·通用提示词 | 素材中心·电影级配方 | 项目线 V6·不选配方 | 项目线 V6·电影级配方 |
| --- | --- | --- | --- | --- |
| 四十三岁 | 年长 | 命中 | 命中 | 命中 |
| 方脸 | 侧脸，无从判断 | 命中 | 命中 | 命中 |
| 右眉上一道细旧烫疤 | 无 | 命中，偏红 | 命中，已愈合 | 命中，已愈合 |
| 乌发额前一绺银白 | 灰白长发 | 命中 | 发色花白 | 命中，白区略宽 |
| 深靛色窄袖圆领丝袍 | 蓝色交领袍 | 命中 | 命中 | 命中 |
| 无现代配饰 | 命中 | 命中 | 命中 | 命中 |
| 单人半身、正面、纯背景 | 侧脸、实景 | 实景 | 命中 | 命中 |
| 胡须（剧本未写） | 长灰须 | 白色山羊胡 | 浅胡茬 | 无须 |
| 发型（剧本未写） | 束髻 | 披发中分 | 向后梳 | 披发中分 |

V6 的剧本对齐在不选配方时独立成立，时代服饰（圆领袍形制、丝绸质感、无现代物件）两条路径都正确。
两点保留：未写进剧本的特征（胡须、发型、首服）在四张图里各不相同；基线 `cfg=1.0` 下「一绺银白」被画成花白发色，
而 `cfg=3.5` 的两张画出了明显的前额白发。基线采样参数本轮未改，因果未做单独验证。

**编辑图**（`03-edit-i2i-multiref.jpg`）

I2I 用电影级配方重绘自己的写实图：脸、额前银白、右眉疤、靛色圆领袍、视角与背景全部保留，曝光略提亮，
袍面织纹更清楚。与提示词一致。

MULTIREF 用同一提示词、同一对参考图（人物肖像 + 赛博街景作风格参考）、同一种子做了四配置对照：

| 配置 | LoRA | cfg | 配方正向词 | 结果 |
| --- | --- | ---: | --- | --- |
| 对照 | 无 | 1.0 | 无 | 人物正面平视中景，脸与银白额发保留，长袍完整，受霓虹冷光 |
| 对照 | 无 | 1.0 | 手工写入 | 人物完全消失，只剩街道 |
| 对照 | fix 0.80 | 3.5 | 自动追加 | 街心半身小像 |
| 验收样本 | fix 0.80 + 多视角 0.80 | 3.5 | 自动追加 | 街心一颗头 |

**退化来自配方自动追加的正向词，不是 LoRA 权重。** 这段词是为 T2I 写的场景描述，压过了「保留第一张参考里的人物」
这条编辑指令；多视角 LoRA 让它进一步恶化。零 LoRA 路径不受影响，所以这不是既有编辑能力的回归。
每个配置只有一个种子，结论按此范围陈述。四张图都没有遵守「简洁背景」。

### M4-5 GPU 释放与租约残留

`POST /setup/image-runtime/stop` 返回 `STOPPED`，证据为命名 Job Object `Local\ShineHandoff-05bf4ef1…`，
组内 5 个进程、终止后 0 个存活。停止前后 15 条 GPU 租约全部 `RELEASED`，无一条 `ACTIVE` 或 `RECONCILE_REQUIRED`；
启动记录与实例行均为 `STOPPED`；8248 不再监听；ComfyUI 渲染器进程 0 个；显存回到 941 MiB。

随后用 `scripts/stop_shine.py --data-dir <隔离数据目录>` 停止整栈：退出码 0，按进程身份收尾 Web、两个 Worker、API
与监督进程，四个端口全部无监听，`processes.json` 消失，无残留进程。

## 遗留问题

均未修改，按影响排序，等待裁决。

1. **MULTIREF 叠加配方后画面不达标。** 配方的正向核心词原样追加到编辑提示词后面，赛博配方会让主体消失或缩没，
   多视角 LoRA 进一步恶化。两个修法都改动方案的注入规则：编辑模式只注入 LoRA 触发词与负向过滤词；或为每套配方
   单列一份只含光影、色彩、质感词的编辑用词。
2. **16 GB 显卡上出图余量不足 1 GB，而准入预算低于实测需求。** 实测需求约 14.5 GB，`IMAGE_BUDGET_MB = 12000`。
   空闲 12 GB 就会放行，随后在 ComfyUI 内部 OOM，而不是由工作台回答「等待显卡」。直接把预算提到 15 GB 会让
   桌面占用超过 1.3 GB 时永远不批准出图，取舍需要更多机型数据。
3. **ComfyUI 提示线程死亡但 HTTP 存活时，工作台没有识别。** 要等满 31 分钟轮询上限才收敛，期间「停止运行时」被
   在途保护拒绝。本次进程还因卡在驱动更换中而超出 20 秒核验窗，被如实报为 `unverified`。
4. **被暂停的安装记录在整备摘要里显示成「正在下载并安装…（关闭页面也会继续）」。** `_DOWNLOAD_ACTIVE` 包含
   `PAUSED`。另外对同一请求再次「确认整备」会原样返回运行中的任务，不会恢复被单独暂停的记录，恢复要走 attempt 级
   的 `resume` 端点。前端没有暂停控件，普通用户从界面进不到这个状态。
5. **僵尸渲染器记录改写为 `STOPPED` 后，就绪投影仍挂着 `IMAGE_RENDERER_GONE`。** 整体层级停留在 `FILES_VERIFIED`，
   直到下一次渲染器启动替换记录。
6. **纯生图 LoRA 安装线程是非守护线程。** 下载进行中停止工作台时的行为本轮没有验证，因为接手时下载已完成。
7. **后台 CDN 传输在本机网络下反复停顿。** 字节会保留，用户可以继续，但体验差；本次靠前台续传完成。
8. **未写入剧本的特征会漂移。** 胡须、发型、首服需要进实体档案与 `must_keep`，否则同一人物每次不同。
9. **环境。** NVIDIA App 在 GPU 任务进行中自动升级了驱动；`F:\ShineTest` 当前占用 341 GB，是历史残留，本轮未动。

## 证据位置

验收目录 `.local-acceptance/image-lora-v1-20261125`（Git 忽略）：

| 文件 | 内容 |
| --- | --- |
| `evidence.json`、`summary.json` | 全部接口回执、台账查询、ComfyUI 历史、图谱与提示词 |
| `evidence-attempt1.json`、`validate-attempt1-cuda-oom.log` | 首次小样尝试的证据与日志 |
| `download_recovery.json`、`download_monitor.log` | 前台续传回执（各文件起始字节）与断点进度 |
| `install.log`、`validate-r2.log`、`render.log`、`release.log`、`extra-*.log` | 各阶段运行日志 |
| `vram_r2.csv`、`vram_render.csv` | 显存采样 |
| `final_backend_shards.log`、`final_frontend_*.log`、`final-shards-20261125-224438/` | 终检日志 |
| `prior-shards-archives/` | 当天两次更早的回归归档备份 |
| `accept.py`、`extra.py`、`vram_sample.py`、`make_sheets.py` | 验收脚本 |
| `*.png` | 11 张全分辨率原图 |
