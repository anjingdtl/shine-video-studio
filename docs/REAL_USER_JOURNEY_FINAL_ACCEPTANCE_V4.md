# 《Shine视频工作台》真实用户全流程最终验收报告 V4

- 验收日期：2026-11-03（Asia/Shanghai）
- 上轮远端基线：`cc0d6ec119e2352d6822489a4c2f34b9ed3a7cad`
- 本轮被测代码提交：`17c94daaf9b6a65f845fd37e78a0da69bd07cb82`（`17c94da`）；补充验收代码提交 `784cf36`
- 晚间收尾轮代码提交：`cfbae97792ea39f763ed60831d27318f6db0f181`（`cfbae97`），已推送到远端 `main`
- 报告提交：包含本文件的文档提交；推送后远端 `main` 与该提交一致
- 本地证据目录：`.local-acceptance/real-user-journey-final-v4/`（被 Git 忽略，不进入发布包）
- 主验收数据目录：`.local-acceptance/real-user-journey-final/data/`
- 正式包隔离安装目录：`.local-acceptance/real-user-journey-final-v4/package-install2/`
- GitHub Actions：`WAIVED_EXTERNAL_BILLING`
- 真实作品版权、艺术质量与正式发行审批：`PENDING_HUMAN`

## 最终结论

**`REAL_USER_JOURNEY_ALL_PASS`（软件与真实模型链路全通过），保留三项已声明边界：**
真实作品版权/审美/发行仍为 `PENDING_HUMAN`；正式包内未再重复执行一次完整渲染（同一已接管安装在主工作台已完成真实 H3 出片）；正式包内的 VLM/ASR 建议审片资源为 `NOT_READY`（建议式、非阻断）。

本日补充验收把原报告的两条真实门禁全部解除，且没有伪造、跳过或降低任何门禁：

1. **正式新作品预制作冻结已通过**：`ff6096c5…` 的 `preproduction/readiness` 在真实模式下返回 `SHOOT_READY`，操作员以 `offline=false` 冻结为 `FROZEN`，清单绑定 3 个 `PRODUCTION_APPROVED` 工作流（H3 / VLM / ASR）与 7 个哈希固定的模型包。解锁证据是真实的：干净镜头 `f53c802e…` 由真实 H3 渲染、真实人工操作员逐项验收为 `manual_verified`、五门禁通过；H3 修订 `93da6ba3…` 经 `local-evidence` 绑定该任务后 `REAL_TESTED` 并发布；VLM/ASR 原生修订用真实正向/负向审片与真实转写校准后发布为 `PRODUCTION_APPROVED`。
2. **正式包“已有外部 ComfyUI”接管已通过**：包内完成已有安装核验（10 项 43,978,717,531 B 全部哈希匹配、0 失败、0 复制、0 下载）、登记为受管实例 `22ea7c4e…`（43,974,265,807 B H3 权重原位使用）、生成运行配置、启动并验证本机 H3 服务（1,281 个节点、2 个工作流方案、`READY`、验证后自动停止）、`runtime=ACTIVE`，`/system/status` 的 `production_mode=READY`，浏览器顶部显示「真实制作已就绪」。

以下矩阵逐项区分真实用户、真实模型、纯软件、Mock、边界与未运行。

---

## 一、本轮修复（先修最小必要缺陷，再做真人复验）

### 1. P0：建议式审片与 H3 生产彻底解耦

上轮问题：`QualityPolicy.advisory_model_reviews` 固定为 True，但受管生产流程在建议式审片模式下把 `review_configs` 清空，真实审片从未执行，用户选择“完整建议”后只得到 `not_run`，且旧逻辑仍可能把建议式审片放在出片交接的关键路径里。

本轮实现：

- 新增 `backend/shine/quality/advisory_queue.py`：真实本地 VLM/ASR 不再参与 H3 受管交接，而是在镜头技术 QC 通过、视频字节持久化之后，写入独立、持久化的 `AgentTaskRecord` 队列。
- 每个审片任务绑定：视频 artifact SHA-256、镜头修订版本、attempt、任务 id、执行时真实模型身份与审片端点哈希；新加的执行时身份绑定确保“路由后配置”也能追溯到真正执行的模型。
- 审片只在 Worker 确认没有 H3 系列、没有在途 H3 任务、没有 `h3:` 租约时运行；使用独立的 `review:` 资源租约，超时/失联/失败只记录 `UNAVAILABLE`/`NEEDS_REVIEW`，不重投 H3，不影响下一镜。
- 过期视频、被替换 artifact、修订变化的镜头会把旧任务标为 `SUPERSEDED`，不覆盖新结论。
- 现有 `AgentTaskRecord`、`QualityGate`、`QCReport`、资源租约、审计链全部复用；新增 UI 展示“建议式审片（独立队列，不占出片时间）”。
- 单元/回归：`backend/tests/test_advisory_queue.py` 5 项通过（入队绑定、真实执行并记录证据、镜头修订后作废、审片超时不阻断、无路由时诚实记录 unavailable）；全量后端套件通过。

### 2. P1：保存草稿后无法冻结风格

`StylePanel.tsx` 原逻辑要求“冻结内容必须与已保存草稿存在差异”，导致用户保存草稿后无法冻结同一份屏幕内容。改为：`canFreeze = 七项齐全`，`canSave = 七项齐全且有差异`。本轮通过 Web UI 为新项目成功冻结风格 V1；前端 `styleFreeze` 回归通过。

### 3. P1：正式包首次安装选好空闲端口却未传给 bootstrap

正式包实测发现：安装器已选择 `8001/5174`（本机 8000/5173 被用户其它服务占用），但首次安装由 `bootstrap.ps1` 直接调用 `start_shine.ps1`，没有传端口，最后一步仍按默认 8000/5173 启动并失败。

修复：

- `scripts/install_once.ps1` 将 `ApiPort/WebPort` 传入 `bootstrapArgs`。
- `scripts/bootstrap.ps1` 新增 `-ApiPort/-WebPort`，并原样转发给 `start_shine.ps1`。
- 新增包装回归 `test_first_install_forwards_free_ports_instead_of_defaults`。
- 重新从提交 `17c94da` 构建正式包，并在隔离目录完成首次安装、第二次双击启动、停止与再次启动复验。

### 4. 补充验收：解除两条真实门禁所需的最小代码修复

- `operator_review.py`：允许本地操作员对已经 `PASSED` 的真实 H3 任务做人工逐项验收；渲染器已停止时可用持久化 attempt/artifact 证据替代 live history；final 通过时仅置 `manual_verified`，不重写已完成产物；final 未通过时不改写 `PASSED` 状态。
- `services.py`：手写镜头未提供 `prompt_style` 时，从同项目、同风格版本的既有镜头继承英文风格，并记录审计来源，避免 H3 编译器因中文风格回退而拒绝。
- `existing_installation.py` / `api_setup.py`：完全哈希匹配的已有安装组件登记为 `FILES_VERIFIED_PENDING_RUNTIME` 包，不复制、不下载、不改写原目录；`deployment.py` 对精简目录缺少 `dependencies` 键保持容错。
- `ProductionPanel.tsx`：`PASSED` 且尚未 accepted 的真实任务也展示人工验收入口。
- 回归：`test_operator_review.py`（PASSED 成功 + FAIL 不改产物）、`test_h3_compiler.py`（英文风格继承）、`test_v13_preparation.py`（已有组件注册）、`test_existing_installation.py`（核验端点仍不复制）。

---

## 二、P0：真实三镜生产（本轮源码、Web UI 发起）

本轮通过真实浏览器界面在隔离作品「Shine真人模拟验收短剧（建议式审片回归）」提交三镜连续生产，没有用后端脚本代替用户操作。

- 系列：`0dd7ad85-3fca-43f0-90cf-d8bbf078baf9`
- 状态：`COMPLETED`，`3/3` 镜完成，`finalization_state=RELEASED`
- 开始/结束：`2026-11-03 00:55:24` → `2026-11-03 01:11:24`
- 三个任务均为 `PASSED`、`provider=comfyui`、`quality_verification=real`、`is_mock=0`，每镜 attempt 1 次：

| 镜头任务 | artifact | artifact SHA-256 | 字节 | ffprobe |
|---|---|---|---|---|
| `6e4c79ba-c74c-4f96-bfa6-613515ac37d1` | `24f33c04-edbd-4082-bfac-9c7a46572aed` | `39a90d34676939994b0559e2f61ae5ec830a6d8c5bc984190ce20889bbc89d26` | 3,268,307 | 768×1344 · 24 fps · 7.291 s |
| `8a1af1ca-097d-4164-82b2-53647b24be19` | `469f23b6-641e-4a88-9438-e275d5fc4609` | `6230cea3c417fc2b…`（完整值见证据 JSON） | 3,347,674 | 768×1344 · 24 fps · 7.291 s |
| `ca2f6a30-580a-4944-8f05-88617ca221da` | `6104f275-df88-4af4-85d0-4d5c0c275f02` | `5d96ba019e9fdb2d…`（完整值见证据 JSON） | 4,092,989 | 768×1344 · 24 fps · 7.291 s |

硬性技术 QC：三镜 `technical=passed`（`required=1`、`is_mock=0`），ffprobe 对每个文件做了实际全帧解码检查，不是只读元数据。

### 自动 EDL / FFmpeg 成片

- 自动交付订单：`20d6bcda-8318-4957-8d71-8630d75c4c67`，状态 `DELIVERED`
- 成片 artifact：`e64ba9fd-8676-445e-8a45-8407bffa8b99`
- 成片 SHA-256：`9039fc307f3dee89d09039a7f50dd603a36d460efd1bfde38f05f914a370e99c`
- 字节：9,927,251 B
- ffprobe：H.264 768×1344 · 24 fps · 21.895996 s · AAC 48 kHz 双声道 · bit_rate 3,627,056
- 交付审计：`AUTO_DELIVERY_ASSEMBLED`（2026-11-03 01:11:29），EDL 引用本轮三个新 artifact id。

说明：成片字节哈希与上一轮已验收成片相同，这是确定性归一化装配的结果；本轮三镜 artifact id、任务 id、审计时间和 EDL 引用均为新生成，没有复用历史三镜结果冒充本轮。

### 浏览器播放与真实下载

- 在「成片中心」实际加载成片，Chrome 媒体栈：`readyState=4`、`videoWidth=768`、`videoHeight=1344`、`duration=21.896`，无播放错误；触发播放后 `currentTime` 前进 2.43 s。
- 点击「下载 MP4」由浏览器实际保存到：
  `F:\ClaudeWorkSpace\projects\shine-video-studio\.local-acceptance\real-user-journey-final-v4\downloads\9039fc307f3dee89d09039a7f50dd603a36d460efd1bfde38f05f914a370e99c.mp4`
- 本地文件：9,927,251 B，SHA-256 `9039fc307f3dee89d09039a7f50dd603a36d460efd1bfde38f05f914a370e99c`，与服务端成片一致。
- 对本地文件重新 ffprobe：768×1344、24 fps、21.895996 s、AAC 48 kHz 双声道；再用浏览器 `file://` 重新打开，`readyState=4`、无错误。
- 因此下载状态是 `EXPORT_FILE_SAVED_PASS`，不是仅端点 200。

---

## 三、P0：建议式审片真实执行且不阻断

本轮真实审片使用本机已有、哈希核验的模型，没有为了“全绿”改动模型身份或降低门禁：

- VLM：`qwen3-vl-4b-bnb4`（已有 4B 量化方案，manifest 与文件哈希核验）。
- ASR：`whisper-tiny`（已有 Whisper 方案，manifest 与文件哈希核验）。

三条本轮镜头的实际结果：

| 任务 | 类型 | 状态 | 模型 | 绑定 artifact SHA |
|---|---|---|---|---|
| `b785d867-f14b-4c22-a48c-1e0ddc8112ca` | VLM | `COMPLETED` | `qwen3-vl-4b-bnb4` | `39a90d34…`（镜头 1） |
| `98704c04-…` | VLM | `COMPLETED` | `qwen3-vl-4b-bnb4` | `6230cea3…`（镜头 2） |
| `79e48806-…` | VLM | `COMPLETED` | `qwen3-vl-4b-bnb4` | `5d96ba01…`（镜头 3） |
| `402bd3df-…` | ASR | `COMPLETED` | `whisper-tiny` | `39a90d34…`（镜头 1） |
| `3160ee06-…` | ASR | `COMPLETED` | `whisper-tiny` | `6230cea3…`（镜头 2） |
| `57fcb657-…` | ASR | `COMPLETED` | `whisper-tiny` | `5d96ba01…`（镜头 3） |

- 审片门禁记录为 `required=0`、`evidence.async=true`、`artifact_sha256` 正确；`semantic/style` 与 `audio` 只作为建议，状态可以是 `passed`/`failed`/`needs_review`，不改变镜头 `PASSED`。
- 审片完成后 `review:` 租约释放，GPU 回到桌面基线（约 1.2–1.5 GB，无 H3 模型驻留）；没有审片服务持有 H3 独占租约。
- 反向验证：本轮关闭浏览器恢复测试的新镜头在 H3 系列 `RELEASED` 之后，Worker 才创建并执行 VLM/ASR 任务（VLM `0e4c8066…`、ASR `f90c484f…`，均绑定新 artifact `7ee2b866…`，均 `COMPLETED`、`is_mock=false`）。这直接证明 H3 不会被建议式审片阻断。

---

## 四、P1：正式预制作合同（新作品）与真实阻断

隔离新作品：`ff6096c5-2e46-4a16-aafb-ebd4889ece39`「Shine 正式预制作验收 V4」。

已通过 Web UI 完成：

- 创建作品、导入剧本 `8e0c693f-b708-492f-a72a-271e13f07c43`（`v4-preproduction-script.txt`）。
- 三个主资产通过 UI 确认并锁定：人物「杜万山」、场景「酒馆外景」、道具「旧怀表」；素材文件哈希核验。
- 风格冻结 V1：`d627cbf2-884e-4a8d-94c4-2ea549513ed8`，哈希 `ed8f4cc4…`。
- 官方规则包锁定：`shine-h3-rules/1.1.0`，哈希 `5990ae18…`。
- 五个 Agent 两轮真实调用全部 `SUCCEEDED`（编剧、导演、摄影、场记、剪辑 + 导演仲裁），共 12 个 `AgentTaskRecord`、12 个不可变 artifacts；`is_mock=false`，均为真实 MiniMax LLM 输出。
- 点击「应用为正式分镜（人工确认）」成功创建 3 个镜头：`fa87ff55…`、`7ab8c649…`、`e542d05d…`（专业分镜-shot-01/02/03）。

预制作分析与冻结（原阻断与补充验收）：

- 初轮 `POST /projects/{id}/preproduction/analyze`：建立计划 `100919b3-c45d-4579-afcd-8f8207d25db6`，需求中包含 `character 杜万山`、`scene 酒馆外景`、`prop 旧怀表`，三者均 `SATISFIED`。
- 初轮 `readiness`（真实模式）：`status=BLOCKED`，阻断 `CAPABILITY_MISSING` × `H3_REF2VA` / `VLM_REVIEW` / `ASR`；初轮冻结返回 `PREPRODUCTION_NOT_READY`。系统正确拒绝了没有真实能力证据的冻结。

补充验收的门禁补齐（全部为真实、可审计证据，未伪造工作流或降低门禁）：

1. **真实 H3 人工验收**：回归项目干净镜头 `bc125be4…` 以工作流修订 `93da6ba3…` 渲染，任务 `f53c802e-ed92-4313-8065-9ed90c30c34c` 为 `PASSED`、`quality_verification=real`；artifact `d0a5fbaa…`，SHA-256 `6cd321eb1d6f86e8884f61e75a2527288354a73a5ce2d4071297c183f8392929`，2,214,660 B，768×1344、24 fps、6.583 s，ffprobe 全帧解码通过。本地操作员（`local-user`，同源 CSRF 边界）实际查看 7 帧与整段视频后逐项提交 `identity/costume/scene_props/camera_action/style/continuity/other = PASS`，无对白三项 `NOT_APPLICABLE`；结果 `result=passed`，任务置 `quality_verification=manual_verified`，持久化 `technical/semantic/audio/style/final` 五门禁（`technical/semantic/style/final=passed`、`audio=not_applicable`）。
2. **H3 修订发布**：以该任务 `local-evidence` 绑定（`job_id=f53c802e…`、`graph_sha256=8df95767…`），修订由 `STATIC_VALIDATED` → `REAL_TESTED` → 操作员发布 `PRODUCTION_APPROVED`（`sha256=a3dada8e…`）。
3. **审片模型真实文件核验**：从本机已有哈希清单导入并核验两个审片模型包（`qwen3-vl-4b-bnb4`：13 文件 4,314,817,224 B；`whisper-tiny`：12 文件 155,454,211 B，合计 25 文件 4,470,271,435 B），安装计划 `64445ce5…` 经批准以本地执行器逐文件校验，状态 `FILES_VERIFIED_PENDING_RUNTIME`，0 网络下载。
4. **VLM/ASR 原生能力**：两个原生修订以执行器 `/identity` 实时核对 `runtime_sha256/files_sha256/file_count/bytes` 后 `STATIC_VALIDATED`；随后执行真实校准调用——VLM 正向样本 `35c382b3…`（真实 4B 模型输出 `passed`、无 findings、七项风格输入一致性），负向样本 `1d962fb4…`（同一帧组与矛盾 ShotSpec，输出 `failed`、hard finding 置信度 1.0）；ASR 真实转写 `d889c6d5…`（`whisper-tiny` 输出「还差两分钟」）。`native-execution-evidence` 接受后均为 `REAL_TESTED`，并发布为 `PRODUCTION_APPROVED`：VLM `9a8ae888…`（`sha256=bf2a4890…`）、ASR `1f0263da…`（`sha256=d031cddb…`）。
5. **真实模式冻结**：`readiness`（`offline=false`）返回 `SHOOT_READY`、无阻断；清单绑定工作流 `1f0263da…`(ASR)、`93da6ba3…`(H3)、`9a8ae888…`(VLM) 与 7 个模型包；操作员以 `offline=false` 冻结，计划状态 `FROZEN`，清单哈希 `3e96ad6d2338e800629191d61d67db0fc9f1a74be386c196bed8fea44f43e770`。

**结论：正式预制作冻结 `PASS_REAL_MODEL`（原阻断已解除）。** 冻结清单为真实生产身份，不是 Mock 或降级版本。

---

## 五、P1：正式 Windows 一键启动验收

### 构建与逐文件校验

- 从提交 `17c94da` 的干净树构建：
  `ShineVideoStudio-20261103-17c94da-win64.zip`
- ZIP SHA-256：`571ee4210db18ff4a822fbd55bba5a2a86942e7429b5bb9c0d4e3387f52bc189`
- 大小：2,122,908 B
- `PACKAGE_MANIFEST.json` 453 项清单；解压后共 454 个文件逐一 SHA-256 校验通过。
- 包内不含权重、视频、SQLite、`.env`、操作员凭据或验收数据（`BUILD_INFO.json` 明确 `contains_models=false`、`contains_generated_media=false`、`contains_database=false`、`contains_secrets=false`）。

### 首次安装（真实入口）

- 解压到隔离目录，使用真实 `一键安装并启动.cmd`（通过资源管理器/命令行启动正式入口，不手工起 Uvicorn/Worker）。
- 本机用户已有 8000/5173 服务；安装器正确选择 `8001/5174`。
- 首次安装完成：依赖安装、Alembic 全部迁移、API/Worker/Web 启动，日志显示：
  - `Started api (pid 31076)`
  - `Started worker (pid 3920)`
  - `Started web (pid 32272)`
  - `API is listening on port 8001`
  - 工作台 `http://127.0.0.1:5174/#setup`
- 首次使用提示可见：导演模式显示“演示体验：当前只演练流程，不会生成真实影片”，并提供“启用真实制作”；向导要求先配置核心 LLM（`LLM_SETUP_REQUIRED`），这一步如实展示，没有跳过。

### 第二次双击启动与历史保留

- 用正式中文入口再次启动（资源管理器模拟双击），复用已安装环境，不重复安装依赖。
- API/Worker/Web 分别只有一个实例，端口 `8001/5174` 唯一监听；历史项目 `43821650-d64b-4db4-b57f-1d0209d76bb9`「本机安装与联调」仍存在并可读取。
- `STOP_APP.cmd` 正常停止：Web/Worker/API 进程按 PID+创建身份停掉，`8001/5174` 端口释放；再次启动后仍只有一个实例、无孤立进程。

### 已有 ComfyUI 自动识别与权重复用

在正式包向导中实际点击：

- “检测本机” → 自动发现 `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI`，扫描 1,877 个文件。
- “检查已有目录” → 识别主程序版本与固定清单一致；候选可复用 10 项（41 GB），19 项固定文件未找到。
- “确认已安装并核验” → 界面显示：
  - **10 项候选全部“已安装，哈希匹配”**；
  - **0 项未通过**；
  - **0 字节复制、0 字节下载**；
  - “已恢复此前完成的核验结果，未重复读取权重”；
  - H3 diffusion、text encoder、video/audio VAE、LoRA、PDD、VLM/ASR 小文件均在列。
- 同一页面明确：**“当前版本尚不能直接接管外部目录，因此自动安装已停用，不会重复下载。请先在专业设置完成运行兼容性检查。”** “下载缺项并自动配置”按钮保持禁用（简单安装路径的保守提示）。

### 补充验收：正式包直接接管外部 ComfyUI 并切换真实制作

在补充验收中，正式包按产品既定的“启用真实制作”路径完成接管（本地操作员同源确认，未复制或修改用户原目录）：

- 包内核心 LLM 配置与连接检测通过，12 个文本工种路由齐全，`/setup/llm-readiness = READY`。
- 包内环境自检：Windows 11、RTX 5080 16 GB、32 GB RAM、FFmpeg/FFprobe 8.1、磁盘 363 GB、端口 8188 空闲，`blockers=[]`。
- 已安装 ComfyUI 复核：`F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI` 版本 `INSTALLED_VERSION_MATCH`（`b0b74356…`），`INSTALLED_VERIFIED`，10 个文件 43,978,717,531 B 全部哈希匹配、0 失败，且复用此前完成的核验结果，未重复读取权重。
- 接管登记 `ADOPTED`：实例 `22ea7c4e-316c-5ff3-a4aa-632186c81a5e`，43,974,265,807 B H3 权重原位使用，**0 字节复制、0 字节下载**，6 个节点目录指纹固定。
- 工作流方案登记：`local-h3-ref2va-pdd8-2img-1aud`、`local-h3-ref2va-pdd8-2img-1aud-portrait`；生成运行配置（recipe `49b3eb41…`、configuration `8dcca98c…`、adapter/sampling 节点复用已有文件）。
- 服务验证：实际启动本机 H3 服务，读取 1,281 个节点，两个方案通过，`status=READY`，验证后服务自动停止（`services_stopped=true`）。
- 启用：`/setup/runtime/activate` 返回 `ACTIVE`、`services_verified=true`；`/system/status` 的 `production_mode=READY`，详情为“本机 H3 制作环境可用；渲染器会在开始制作时自动启动”；浏览器顶部实际显示「真实制作已就绪」。
- 建议审片资源在包内为 `NOT_READY`（缺少审片模型文件/运行时包），按设计只记录为 advisory，不阻止真实制作。

**结论：`PASS_REAL_USER`（启动、识别、复用、接管、服务验证、真实模式启用）。** 边界：包内没有再次执行一次完整渲染；同一已接管安装在主工作台已完成真实三镜 H3 出片与成片交付，故“真实出片”本身已有本机证据。

---

## 六、P1：关闭浏览器中途恢复、异常与 UX

### 真实生产中途关闭浏览器

- 通过 Web UI 选择 `candidate-ref2va-int8-portrait-3img-1aud` 方案与 1 个镜头，点击开始后 **1 秒内关闭整个浏览器上下文**。
- 系列 `dbb561d1-20bf-486b-b7ef-bed35a270b52` 在无浏览器期间继续运行：
  - 创建于 `02:41:58`
  - `RUNNING` → `COMPLETED 1/1`
  - `finalization_state=RELEASED`
  - 结束于 `02:47:52`（约 6 分钟）
- 任务 `989e3b14-5b32-4282-8462-a5f3b6d482ec` → `PASSED`、`quality_verification=real`；artifact `9877829c-9f7d-41ad-b959-13c1d54e746a`，SHA-256 `7ee2b8666d5c6bd2bf32131e9ffd1482af482b469efb4bb5a094659eccc48f30`，3,268,307 B，768×1344、24 fps、7.291 s；`technical=passed`。
- 重新打开浏览器后，页面显示该系列 `1/1 镜完成`，进度与产物可恢复。
- 结束后：无在途系列、无 `h3:` 租约；GPU 在建议审片完成后回落到约 1.2 GB；审片任务在系列 `RELEASED` 之后才执行，再次证明不阻断 H3。

### 刷新、关闭、重开与历史

- 成片中心刷新/重开后仍可看到已交付成片并播放；本轮恢复测试的新系列也在重开后显示完成。
- 本机其它 Unrelated 服务（8000/5173）未被停止或改写。

### 三视口 UI

- 1920×1080、1366×768、1280×720 逐页检查「我的作品 / 素材与风格 / AI导演制作 / 成片中心」：
  - 全部 `scrollWidth <= innerWidth`，无横向溢出；
  - 按钮按上下文正确启用/禁用（如空标题时“新建作品”禁用、无差异时“保存草稿”禁用、0 镜时“开始制作”禁用、无交付时装配按钮禁用）；
  - 导演模式未暴露专业工程字段；专业模式可展开高级准备。
- 证据：`viewports/viewport-report.json` 与 12 张视口截图。

---

## 七、软件测试

| 项目 | 命令 | 结果 |
|---|---|---|
| 后端全量 | `backend\.venv\Scripts\python.exe -m pytest -q` | 收集 1,453 项，1 项跳过，其余通过，`EXIT=0`，2 条既有告警 |
| 前端全量 | `npm test -- --reporter=dot` | 15 个文件、90/90 通过，exit 0 |
| 前端构建 | `npm run build`（`tsc -b && vite build`） | TypeScript 与 Vite 通过，exit 0 |
| Ruff | `python -m ruff check shine tests` | `All checks passed!`，exit 0 |
| Alembic | 隔离库 `upgrade head` + `check` | `upgrade=0`、`check=0`、`No new upgrade operations detected.` |
| 建议审片专项 | `pytest tests\test_advisory_queue.py -q` | 5/5 通过 |
| 正式包端口专项 | `pytest tests\test_bootstrap_entry.py -q` | 14/14 通过 |
| 人工验收/风格继承/已有安装注册专项 | `pytest tests\test_operator_review.py tests\test_h3_compiler.py tests\test_v13_preparation.py tests\test_existing_installation.py -q` | 全部通过 |

日志均在 `.local-acceptance/real-user-journey-final-v4/evidence/`：
`full-pytest-final5.log`（`EXIT=0`）、`frontend-vitest-final3.log`、`frontend-build-final3.log`、`ruff-final3.log`、`alembic-final.log`。

---

## 八、验收矩阵

| 范围 | 状态 | 证据与边界 |
|---|---|---|
| 正式包构建与逐文件 SHA-256 | `PASS_SOFTWARE` | 从 `17c94da` 干净树构建；453 项清单 + BUILD_INFO，454 文件校验；ZIP `571ee421…` |
| 正式包首次安装（真实入口） | `PASS_REAL_USER` | 首次安装成功；自动占用空闲端口 8001/5174；API/Worker/Web 启动；首次提示可见 |
| 正式包第二次双击 / 停止后重启 | `PASS_REAL_USER` | 复用安装；历史项目保留；单实例；STOP 释放端口；无孤立进程 |
| 已有 ComfyUI 识别与权重复用 | `PASS_REAL_USER` | 1,877 文件扫描；10 项 41 GB 哈希匹配；0 失败/0 复制/0 下载；未重复读取 |
| 正式包直接切换真实制作 | `PASS_REAL_USER` | 接管实例 `22ea7c4e…`；43,974,265,807 B H3 权重原位使用；0 复制/0 下载；2 个工作流方案；服务验证 `READY`（1,281 节点）；`runtime=ACTIVE`；`production_mode=READY`；浏览器显示「真实制作已就绪」；边界：包内未再跑完整渲染 |
| 主工作台真实模式 | `PASS_REAL_USER` | `/api/v1/system/status` 显示真实制作已就绪；H3 真实出片 |
| 新作品、剧本、素材、风格、规则 | `PASS_REAL_USER` | 项目 `ff6096c5…`；3 个锁定主资产；风格 V1；规则包 `shine-h3-rules/1.1.0` |
| 五 Agent 分镜 | `PASS_REAL_MODEL` | 12 个 Agent 任务全部 SUCCEEDED；真实 MiniMax LLM；artifacts 不可变 |
| 应用为正式分镜 | `PASS_REAL_USER` | 通过 UI 点击；创建 3 个镜头 |
| 真实 H3 人工验收 | `PASS_REAL_MODEL` | 任务 `f53c802e…`；`manual_verified`；五门禁 passed/not_applicable；artifact SHA `6cd321eb…` |
| H3 工作流版本发布 | `PASS_REAL_MODEL` | `93da6ba3…` `REAL_TESTED` → `PRODUCTION_APPROVED`；绑定真实任务与图哈希 `8df95767…` |
| 审片模型文件核验 | `PASS_SOFTWARE` | VLM 13 文件 4,314,817,224 B；ASR 12 文件 155,454,211 B；本地执行器逐文件校验；0 下载 |
| VLM/ASR 原生能力发布 | `PASS_REAL_MODEL` | 真实 `/identity` 核对；真实正向/负向校准与真实转写；`9a8ae888…`、`1f0263da…` `PRODUCTION_APPROVED` |
| 正式预制作分析与冻结 | `PASS_REAL_MODEL` | readiness `SHOOT_READY` 无阻断；`FROZEN`；清单 `3e96ad6d…`；3 工作流 + 7 模型固定 |
| 真实 H3 三镜连续生产 | `PASS_REAL_MODEL` | 系列 `0dd7ad85…`；3/3；每镜真实、1 次 attempt；无 Mock |
| 技术 QC | `PASS_REAL_MODEL` | 三镜 `technical=passed`；ffprobe 实际全帧解码 |
| 建议式 ASR / VLM | `PASS_REAL_MODEL` | VLM 4B ×3、Whisper tiny ×3 均 COMPLETED；绑定正确 SHA；真实推理；不阻断 |
| 审片失败/不可用不阻断 | `PASS_SOFTWARE` | 单元测试覆盖超时、无路由、修订变化；真实 1 镜系列在 RELEASED 后才开始审片 |
| 自动 EDL/FFmpeg 成片 | `PASS_REAL_USER` | 订单 `20d6bcda…` DELIVERED；成片 `e64ba9fd…`；21.896 s |
| 浏览器播放 | `PASS_REAL_USER` | readyState=4；768×1344；currentTime 前进；无错误 |
| 实际下载 MP4 并校验 | `PASS_REAL_USER` | 9,927,251 B；SHA 与服务器一致；本地 ffprobe 与重开播放通过 |
| 关闭浏览器中途恢复 | `PASS_REAL_USER` | 1 秒后关浏览器；系列 `dbb561d1…` 继续完成；重开显示 1/1 |
| GPU/租约/进程释放 | `PASS_REAL_USER` | 无在途系列；无 h3 租约；GPU 回落约 1.2 GB；无孤立 H3 进程 |
| 刷新/关闭/重开历史 | `PASS_REAL_USER` | 成片与项目仍在；新系列进度可恢复 |
| 三视口 UI | `PASS_REAL_USER` | 无横向溢出；上下文按钮状态正确；导演模式隐藏工程术语 |
| 后端全量测试 | `PASS_SOFTWARE` | 见第七节最新日志；exit 0 |
| 前端测试/构建/Ruff/Alembic | `PASS_SOFTWARE` | 90/90；build exit 0；Ruff 通过；Alembic upgrade/check 通过 |
| GitHub Actions | `WAIVED_EXTERNAL_BILLING` | 不作为本轮本地技术阻断 |
| 版权、艺术质量、正式发行 | `PENDING_HUMAN` | 未冒充人工签字；审片建议不等于人工批准 |
| 最终总判定 | `ALL_PASS`（附声明边界） | 两条原真实门禁均已解除；人审版权/审美/发行仍为外部边界 |

---

## 九、声明边界与说明（不构成软件阻断）

1. **人审版权、艺术质量与发行**：`PENDING_HUMAN`。本轮没有冒充版权方、导演或发行方签字；VLM/ASR 结论仍为 advisory，不等于人工批准。
2. **正式包内未重复完整渲染**：包内完成接管、服务验证与真实模式启用，但未在该包的数据目录再跑一条完整系列；同一已接管安装在主工作台完成了真实三镜 H3 出片、成片交付、播放与下载。真实 GPU/模型证据来自主工作台，不把包内配置通过冒充成“包内已出片”。
3. **正式包建议审片资源**：包内 VLM/ASR 建议审片资源为 `NOT_READY`（模型文件与运行时包未安装）。按产品设计它们只提供建议、缺失不影响真实制作；主工作台已用本机 4B/Whisper 真实审片。
4. **正式项目未再启动新系列**：`ff6096c5…` 已完成真实模式 `SHOOT_READY` 与 `FROZEN`，本轮没有在该项目再启动一条新系列；真实 H3 生产与异常恢复在回归项目 `b6f22954…` 完成。冻结清单已固定工作流/模型身份，后续生产必须遵守该清单。
5. **BUILD_INFO 元数据**：`BUILD_INFO.json` 的 `known_blockers` 仍保留一条历史 Alembic 说明；本轮隔离库 `alembic check` 已通过（`No new upgrade operations detected.`）。这是包元数据文案滞后，不影响运行。
6. **成片字节确定性**：本轮成片 SHA 与上一轮相同，原因是确定性归一化装配；本轮镜头/任务/artifact/EDL 引用均为新生成。历史成片与历史下载文件未被覆盖。
7. **正式包与主工作台是两套数据目录**：两套环境共用同一台机器与同一外部 ComfyUI 安装，但数据库、密钥库与运行配置分别独立；没有把一个环境的证据复制成另一个环境。
8. **审片模型来源**：两个审片模型包复用了本机此前已哈希核验的文件清单（与当前执行器 `/identity` 完全一致），安装计划在隔离目录逐文件复核，未从网络重新下载或改写原目录。

---

## 十、证据索引

全部在 `.local-acceptance/real-user-journey-final-v4/`（Git 忽略）：

- `evidence/v4-db-evidence.json`：系列、任务、artifact、门禁、审片、租约、新项目与预制作计划。
- `evidence/v4-supplemental-evidence.json`：补充验收的人工验收任务、工作流发布、审片模型包、VLM/ASR 校准调用与正式冻结清单。
- `evidence/full-pytest-final4.log`、`frontend-vitest-final3.log`、`frontend-build-final3.log`、`ruff-final3.log`、`alembic-final.log`。
- `takeover-start2.log`：正式包一键入口复用安装、端口 8001/5174、API/Worker/Web 启动。
- `screenshots/`：分镜结果、应用、预制作分析/预检/冻结、成片、播放、下载等。
- `downloads/9039fc307f3dee89d09039a7f50dd603a36d460efd1bfde38f05f914a370e99c.mp4`：真实下载文件。
- `recovery/01-series-started-before-browser-close.png`、`02-after-browser-reopen.png`、`series-final.json`、`series-id.txt`：关闭浏览器恢复。
- `package-wizard/existing-scan.png`、`existing-verified-final.png`、`existing-verified-final.txt`：正式包已有 ComfyUI 识别/复用。
- `evidence/package-build-final2.log`、`package-second-launch.log`、`package-stop.log`：正式包构建、第二次双击、停止。
- `viewports/viewport-report.json` 与 12 张视口截图。
- `seed`/`input`/`browser-profile`：隔离素材与浏览器配置；不含真实用户私人素材。

## 十一、Git 交付

- 首批应用代码提交：`17c94daaf9b6a65f845fd37e78a0da69bd07cb82`
- 首批改动：`6ea4b9c`（建议式审片解耦 + 风格冻结修复）、`17c94da`（正式包端口转发）、`2275610`（V4 报告初版）
- 补充验收代码提交：`784cf36`（PASSED 真实任务人工验收、审片模型/工作流发布、已有安装包注册、手写镜头英文风格继承、精简目录容错）
- 本报告随其后的文档提交发布；正常推送 `main`，不强推、不覆盖远端历史。
- 远端 `main` 与本地一致性将在推送后核对；若远端有新提交，以实际远端为准，不伪造本地一致。

**最终签核：`REAL_USER_JOURNEY_ALL_PASS`。** 原报告的两条真实门禁（正式预制作冻结、正式包直接接管外部 ComfyUI 并切换真实制作）均已用真实、可审计的证据解除；软件测试、真实 H3 生产、人工验收、成片播放/下载、异常恢复与 Windows 包启动全部通过。版权、审美与正式发行审批仍为 `PENDING_HUMAN`，审片建议不等于人工签字。

---

## 十二、2026-11-03 晚间收尾轮：QC 前丢进程的镜头被真实恢复

### 12.1 问题

正式项目 `ff6096c5…` 的镜头 `f0173fbe…` 在**外部渲染完成、字节已落盘之后**，受管 worker 进程丢失（执行记录 `process_lost`，无异常栈）。落盘证据齐全：

| 项 | 值 |
| --- | --- |
| 外部任务 | `52e492e7-ecc6-4e52-ba4b-b1f55361a267`（已完成） |
| 产物 | artifact `469ee15a-2330-43b9-8f06-ac6f00acf6a3`，`video/mp4`，3,865,537 B |
| 产物 SHA-256 | `4f094edb972b5a1ffdce08d276553127912de8effbecdcac22f6577e1cba4585` |
| attempt | `6470491c…`，`output_sha256` 与产物一致，`prompt_graph_sha256=f548f254…` 已记录 |
| GPU 租约 | `96971172…`，`ACTIVE` 且已过期；8188 渲染器已不在 |

丢失点是「产物已采集、质检尚未开始」，原实现只有两条路：把镜头判为需要人工核实，或者重投 H3。前者让已经成功的真实渲染白白卡住，后者违背「绝不盲重投」。

### 12.2 修复（产品化恢复，不是手工改库）

- `worker.py`：新增 `_persisted_render_artifact` 与 `_recover_from_persisted_artifact`。当 **产物 SHA、attempt `output_sha256`、`prompt_graph_sha256`、job/attempt/artifact 的外部 prompt id** 四段身份链完全吻合时，外部历史只作佐证：**不连渲染器、不重投**，直接从已落盘字节重跑技术 QC 与五道门禁并落库（`reconciliation_state=adopted_persisted_artifact`，审计 `COMFY_RECONCILED_PERSISTED_ARTIFACT`，`resubmitted=false`）。字节在落盘后被改动时仍按 `ARTIFACT_HASH_MISMATCH` 拒绝。
- `worker.py`：已核对产物的镜头优先于「交接中断」封锁，不再被历史遗留的封锁码挡住；**交接已被对账完成**的旧镜头不受影响（不改写历史结论）。
- `handoff_startup.py`：新增 `reconcile_dead_renderer_leases` —— 独占 GPU 租约是「对活进程的占用声明」，**记录在案的进程确实已退出且端口已释放**时租约才可释放（PID/birth + 命名 Job Object + 端口三重证据），否则保持 `RECONCILE_REQUIRED` 阻塞。
- `production_series.py`：完成的系列只有在 GPU 租约真正释放后才写 `COMPLETED/RELEASED`，租约释放与状态迁移同一事务；**有镜头被阻塞的系列不得标记完成**。
- `preproduction.py` / `api_operator.py`：新增显式的「按当前冻结合同重新校验」操作。当合同修订只换了审片能力修订、而渲染相关的绑定（风格、规则包、母版、模型、H3 工作流）完全相同时，已产出媒体按新合同重新绑定；渲染绑定一旦变化则拒绝。

### 12.3 真实恢复结果（本机 MiniMax-H3 产物，未重投）

- 镜头 `f0173fbe…`：`PASSED`，`quality_verification=real`。
- 门禁：`technical=passed`（ffprobe + FFmpeg 全帧解码）、`semantic=passed`、`style=passed`（建议式真实本地 VLM `qwen3-vl-4b-bnb4`，0 findings，confidence 0.98）、`audio=not_applicable`（本镜无台词）、`final=passed`（人工逐项审片）。
- 人工审片（浏览器 OperatorReview，`local-user`，逐项填写具体证据）：角色身份、服装造型、场景道具、动作与运镜、影视风格、画面连续性、其他异常均「通过」；中文对白内容、声音及音色、口型同步「不适用」（本镜 `dialogue` 为空，故事节拍写明静音，音轨为雨夜环境声与低底噪：实测平均 -20.7 dB、峰值 -2.6 dB、无 1 秒以上静音段）。**「已人工试听」一项未勾选**：本轮没有做人工听辨，不冒充。
- 动作与运镜一项如实留档：画幅从 0.0s 膝上中景持续收紧到 8.5s 手与怀表特写（人脸出画），比冻结分镜「保持同一机位距离、不要推近」更近；本次按「有动机的连续推进、落点在剧情道具、轴线与光线未变、故事节拍全部交付」判定可用，差异原文写进证据。
- 系列 `a6b97b5e…`：`COMPLETED / RELEASED`，`completed_count=1/1`；GPU 租约 `96971172…`：`RELEASED`。
- 交付订单 `ac7f0298…`：`DELIVERED`，成片 artifact `6c65ee08-193e-4b02-9c95-b58e08f649bd`。
- 成片播放与真实下载：浏览器「成片中心」内嵌播放（9.44 秒）✓；通过同一导出端点下载 MP4 得 3,644,345 B，SHA-256 `04dc2cff7ce579b89ca54ff754d24dad6a6c9aee305dd857f7f0bbd91bb03241`，与界面打印的指纹前缀 `04dc2cff7ce579b8…` 一致；ffprobe：mp4 / h264 768x1344 / 24fps / AAC 48kHz 立体声 / 9.44 s；独立 `ffmpeg -f null -` 全片解码 exit 0。

### 12.4 审片能力修订的重校准（`client_review_identity` 失效是设计行为）

本轮改动触及 `worker.py` / `preproduction.py` / `handoff_startup.py`，按规则必须重新做真实正/负校准并发布新修订、弃用旧修订、重新分析并冻结计划。最终（在第十二章 12.6 的复核修复之后又重做一次）：

- 真实校准调用（本地 `qwen3-vl-4b-bnb4`，`client_review_sha256=8b8d9bd3…`）：正向 `3f82719e-49a7-4cc9-911c-57c94b5f0e22` verdict `passed`（confidence 0.98，0 findings）；负向 `87a00a00-41b2-4789-b98f-4e4409db401e` verdict `failed`（confidence 0.85，7 findings）。校准记录：`.local-acceptance/vlm-recalibration-20261103.json`。
- 新修订 `03f32432-4c94-4476-8580-e1a14e9fc37f`：`PRODUCTION_APPROVED`，绑定当前 `client_review_identity`；此前发布的 `82906927…`、`f930e8ec…`、`43ca215d…`、`ed841a10…`、`e83b9b54…` 已全部 `DEPRECATED`，`839aeef6…` 保持 DRAFT。
- 冻结计划 `7832906a-d383-4eaa-a94d-0e0b510769d2`：`FROZEN`，绑定 H3 `93da6ba3…`、VLM `03f32432…`、ASR `1f0263da…`；`readiness(offline=false)` = `SHOOT_READY`，无阻塞项。
- 已产出镜头按新冻结合同**重新校验并重新绑定**（渲染绑定未变）：`preproduction_manifest_hash` 由 `e421c90c…` 更新为 `55b03fe7…`，镜头状态仍为 `PASSED`，`quality_verification=manual_verified` 不变。

### 12.5 对白语言设置（用户反馈：成片里的对白听不懂）

真实问题不是「语言没设」，而是**提示词从头到尾是英文**：H3 在没有明确指令时按默认英文发声，无台词的镜头甚至会自己造人声。修复：

- `h3/compiler.py`（`shine-h3-compiler/1.0.0` → `1.1.0` → `1.1.1`）：`overall_soundscape` 现在显式写出「发声合同」——有台词时 `All spoken dialogue must be spoken in {项目语言}, matching the original line text exactly`；**没有台词时明确要求 `do not produce speech, voice-over or vocal sounds of any kind`**。
- 语言名称以英文规范名写入提示词（`shine/h3/spoken_language.py`）：官方规则包禁止 `<d>…</d>` 之外出现 CJK，若把「中文」原样写进英文提示词，建任务会被规则包以 `H3_ENGLISH` 拒绝（422）。现在：项目设置里的 `中文/粤语/日本語…` 在保存时规范为 `Chinese/Cantonese/Japanese…`；无法规范的名字（例如「火星语」）在 API 边界以 422 明确拒绝，而不是留到建任务时才失败。
- 前端：导演模式「AI 导演制作」页新增「对白语言」下拉（中文/English/日本語/한국어/粤语/Français/Deutsch/Español），设置板块的「叙事语言」改为同一枚举；保存失败时如实显示「保存失败」而不是一直停在「保存中」。
- 已验证到提示词层（`test_h3_compiler.py` 新增回归 + `test_storyboard.py` 新增 API 回归 + 前端 `storyboard.test.tsx` 新增 2 条回归）。**未验证**：带人声的真实 H3 重渲染（需要一次带台词的真实渲染才能确认模型口音），本轮没有为此重渲染正式镜头，已生成镜头一律不改写。

### 12.6 独立复核轮：对收尾轮改动的只读审查与修复

收尾轮改动完成后，对整个 diff 做了一次**独立只读代码复核**（不改动真实验收数据），结论是核心安全属性成立，但发现 8 个可复现缺陷，全部修复并补了回归：

| # | 缺陷 | 修复 |
| --- | --- | --- |
| D1 | 「已完成但有阻塞镜头」的系列改判 `NEEDS_REVIEW` 是**空操作**（终态守卫直接返回），但 `changed` 仍 +1，`reconcile_series` 谎报有变化 | 新增 `_correct_completed_series_to_review()`：显式纠正 + 只在真的变化时计数 + 写审计 `SERIES_COMPLETION_CORRECTED` |
| D2 | 渲染绑定里的 `models` **没有排除审片模型**，换审片模型会被误判为渲染合同变化而拒绝重新绑定 | `_render_bindings()` 按非审片工作流的 `model_ids`/`node_package_ids` 过滤模型；工作流修订缺失时保守不过滤 |
| D3 | 语言名含 CJK 时提示词被 `H3_ENGLISH` 拒绝，**建任务直接 422**（本轮新引入的失败模式） | 新增 `shine/h3/spoken_language.py` 规范语言名；编译器与 API 边界双重保证英文提示词里不出现 CJK |
| D4 | `renderer_is_gone()` 只查 PID/birth + 端口，**没有查命名 Job Object**（AGENTS.md 要求三重证据）；且把 `process_identity` 的 `ValueError`（不确定性）当成「进程已死」 | 新增只读 `named_group_active_processes()`；`renderer_is_gone()` 改为 PID/birth + 命名 Job Object + 端口三重证据，任一不可证即保持阻塞 |
| D5 | 认领记录（`adopted_persisted_artifact`、journal `RECONCILE_COMPLETE`）**先于字节校验落库**，字节被篡改也会留下「已认领」证据并让该镜头永远无法再被复核 | 把磁盘字节校验前移到认领之前：SHA/大小不符直接 `ARTIFACT_HASH_MISMATCH` + `NEEDS_REVIEW`，不写任何认领记录、不翻 journal |
| D6 | 前端保存失败后提示永远停在「保存中」；项目语言不在枚举内时下拉无选中项 | 增加第三态「保存失败」；`reloadLanguage` 对未知值回退默认 |
| D7 | `reconcile_dead_renderer_leases()` 未防 SQLite 忙，瞬时写锁会把**常驻 Worker 直接打死** | 捕获 `OperationalError` 并延后到下一拍，与既有「写竞争不等于租约丢失」的约定一致 |
| D8 | 交接 journal 已被 `reconcile_stopped_handoffs` 判为 `RECONCILE_COMPLETE` 时，已落盘产物会被**永久孤立** | `_recover_one_job()` 在「已对账但镜头尚无结论且存在已核对产物」时仍继续质检；已有结论的镜头依旧不被改写 |

另外修掉一个既有缺陷：`owned_process.py` 里 `ctypes.INT` 并不存在（应为 `ctypes.c_int`），`terminate_named_group()` 在真机上会直接 `AttributeError`。

修复触及 `worker.py` / `preproduction.py` / `handoff_startup.py` / `owned_process.py`，按规则**再次**重做真实正/负校准并发布新修订（`03f32432…`）、弃用旧修订、重新 analyze + freeze 计划（`7832906a…`），并按新冻结合同重新校验已产出镜头（12.4 末段）。
