# 《Shine视频工作台》真实用户全流程最终验收报告 V4

- 验收日期：2026-11-03（Asia/Shanghai）
- 上轮远端基线：`cc0d6ec119e2352d6822489a4c2f34b9ed3a7cad`
- 本轮被测代码提交：`17c94daaf9b6a65f845fd37e78a0da69bd07cb82`（`17c94da`）
- 报告提交：包含本文件的文档提交；推送后远端 `main` 与该提交一致
- 本地证据目录：`.local-acceptance/real-user-journey-final-v4/`（被 Git 忽略，不进入发布包）
- 主验收数据目录：`.local-acceptance/real-user-journey-final/data/`
- 正式包隔离安装目录：`.local-acceptance/real-user-journey-final-v4/package-install2/`
- GitHub Actions：`WAIVED_EXTERNAL_BILLING`
- 真实作品版权、艺术质量与正式发行审批：`PENDING_HUMAN`

## 最终结论

**`PARTIAL`，不得签署 `REAL_USER_JOURNEY_ALL_PASS`。**

本轮把 P0 建议式审片阻断问题、P1 风格冻结缺陷、P1 正式包首次安装端口缺陷全部修复；真实三镜 H3 生产、异步 ASR/VLM、FFmpeg 成片、浏览器播放、真实下载、正式 Windows 包双击启动、关闭浏览器中途恢复、全量软件测试均取得本轮证据。仍有两条真实产品门禁未通过，且不允许用伪造或跳过门禁的方式转绿：

1. **正式新作品预制作冻结被能力证据门禁阻断**：隔离新项目已完成剧本、五个 Agent 分镜、三镜创建、素材与风格冻结、规则包绑定；`preproduction/readiness` 返回 `BLOCKED`，阻断项为 `CAPABILITY_MISSING`（`H3_REF2VA`、`VLM_REVIEW`、`ASR`），冻结返回 `PREPRODUCTION_NOT_READY`。隔离库没有 `PRODUCTION_APPROVED` 的 H3/审片工作流版本；现有真实 H3 证据为 `quality_verification=real` 且五门禁并非全部 `passed`，不满足 `manual_verified` 原生证据绑定条件。没有导入、编造或降低门禁。
2. **正式包在“已有外部 ComfyUI”机器上的真实模式接管仍被安全门禁阻断**：包内向导已自动识别并复用已有安装（1,877 个文件、10 项 41 GB 权重哈希全部匹配、0 项失败、0 字节复制/下载），但明确停在“运行环境与节点版本仍待核验”，自动安装按钮被禁用。主工作台的真实模式是 `READY` 且已用真实 H3 出片；但“从正式包首次使用直接切换到真实制作”在本机仍不能完成。

因此本轮不能把局部真实通过包装成整体 `ALL_PASS`。以下矩阵逐项区分真实用户、真实模型、纯软件、Mock、阻断与未运行。

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

预制作分析与冻结（真实界面操作）：

- `POST /projects/{id}/preproduction/analyze`：建立计划 `100919b3-c45d-4579-afcd-8f8207d25db6`，需求中包含 `character 杜万山`、`scene 酒馆外景`、`prop 旧怀表`，三者均 `SATISFIED`。
- `readiness`（本地生产证据模式）：`status=BLOCKED`，阻断：
  - `CAPABILITY_MISSING` / `H3_REF2VA`
  - `CAPABILITY_MISSING` / `VLM_REVIEW`
  - `CAPABILITY_MISSING` / `ASR`
- 点击“操作员冻结选定模式清单”：返回 `PREPRODUCTION_NOT_READY`（错误代码 `PREPRODUCTION_NOT_READY`）。

原因不是脚本、资产、风格或规则缺失，而是隔离库没有任何 `PRODUCTION_APPROVED` 的工作流版本；正式原生证据绑定要求真实任务的 `quality_verification=manual_verified` 且 technical/semantic/audio/style/final 五门禁全部 `passed` 或 `not_applicable`。本轮真实任务为 `quality_verification=real`，且建议式门禁存在 `needs_review`/失败项，不满足条件。系统正确拒绝，未伪造工作流版本、未降低门禁、未把建议式结论冒充人工审批。

**结论：`BLOCKED`。这是本轮不能签发 `ALL_PASS` 的首要原因。**

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
- 同一页面明确：**“当前版本尚不能直接接管外部目录，因此自动安装已停用，不会重复下载。请先在专业设置完成运行兼容性检查。”** “下载缺项并自动配置”按钮保持禁用。

**结论：`PASS_REAL_USER`（启动、识别、复用、不重复下载）；`BLOCKED`（从正式包直接切换真实制作）。**

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
| 后端全量 | `backend\.venv\Scripts\python.exe -m pytest -q` | 收集 1,449 项，1 项跳过，其余通过，`EXIT=0`，2 条既有告警 |
| 前端全量 | `npm test -- --reporter=dot` | 15 个文件、90/90 通过，exit 0 |
| 前端构建 | `npm run build`（`tsc -b && vite build`） | TypeScript 与 Vite 通过，exit 0 |
| Ruff | `python -m ruff check shine tests` | `All checks passed!`，exit 0 |
| Alembic | 隔离库 `upgrade head` + `check` | `upgrade=0`、`check=0`、`No new upgrade operations detected.` |
| 建议审片专项 | `pytest tests\test_advisory_queue.py -q` | 5/5 通过 |
| 正式包端口专项 | `pytest tests\test_bootstrap_entry.py -q` | 14/14 通过 |

日志均在 `.local-acceptance/real-user-journey-final-v4/evidence/`：
`full-pytest-final2.log`、`frontend-vitest-final.log`、`frontend-build-final.log`、`ruff-final.log`、`alembic-final.log`。

---

## 八、验收矩阵

| 范围 | 状态 | 证据与边界 |
|---|---|---|
| 正式包构建与逐文件 SHA-256 | `PASS_SOFTWARE` | 从 `17c94da` 干净树构建；453 项清单 + BUILD_INFO，454 文件校验；ZIP `571ee421…` |
| 正式包首次安装（真实入口） | `PASS_REAL_USER` | 首次安装成功；自动占用空闲端口 8001/5174；API/Worker/Web 启动；首次提示可见 |
| 正式包第二次双击 / 停止后重启 | `PASS_REAL_USER` | 复用安装；历史项目保留；单实例；STOP 释放端口；无孤立进程 |
| 已有 ComfyUI 识别与权重复用 | `PASS_REAL_USER` | 1,877 文件扫描；10 项 41 GB 哈希匹配；0 失败/0 复制/0 下载；未重复读取 |
| 正式包直接切换真实制作 | `BLOCKED` | 外部运行环境与节点版本未核验；自动安装被安全门禁禁用；未强行接管或重下 |
| 主工作台真实模式 | `PASS_REAL_USER` | `/api/v1/system/status` 显示真实制作已就绪；H3 真实出片 |
| 新作品、剧本、素材、风格、规则 | `PASS_REAL_USER` | 项目 `ff6096c5…`；3 个锁定主资产；风格 V1；规则包 `shine-h3-rules/1.1.0` |
| 五 Agent 分镜 | `PASS_REAL_MODEL` | 12 个 Agent 任务全部 SUCCEEDED；真实 MiniMax LLM；artifacts 不可变 |
| 应用为正式分镜 | `PASS_REAL_USER` | 通过 UI 点击；创建 3 个镜头 |
| 正式预制作分析与冻结 | `BLOCKED` | readiness `CAPABILITY_MISSING` × 3；freeze `PREPRODUCTION_NOT_READY`；未伪造/降门禁 |
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
| 后端全量测试 | `PASS_SOFTWARE` | 1,449 收集、1 跳过、exit 0 |
| 前端测试/构建/Ruff/Alembic | `PASS_SOFTWARE` | 90/90；build exit 0；Ruff 通过；Alembic upgrade/check 通过 |
| GitHub Actions | `WAIVED_EXTERNAL_BILLING` | 不作为本轮本地技术阻断 |
| 版权、艺术质量、正式发行 | `PENDING_HUMAN` | 未冒充人工签字；审片建议不等于人工批准 |
| 最终总判定 | `PARTIAL` | 两条真实门禁阻断，不得签 `ALL_PASS` |

---

## 九、仍存在的真实阻断与说明

1. **正式预制作能力证据缺口**：隔离库没有 `PRODUCTION_APPROVED` 的 `H3_REF2VA`、`VLM_REVIEW`、`ASR` 工作流版本；真实 H3 任务不满足原生手工证据的 `manual_verified` + 五门禁全通过条件。解决需要真实、可审计的工作流发布与原生证据流程，不能用本轮验收数据伪造。
2. **正式包“外部 ComfyUI 接管”缺口**：向导能识别与复用权重，但不会自动接管外部的 Python/节点运行环境；需要专业设置的运行兼容性核验。该限制沿用上轮结论，本轮没有为此扩张功能或重复下载。
3. **主工作台与正式包是两套数据目录**：主工作台真实模式 `READY`，正式包隔离数据目录处于演示/待配置；不能把两者混成“首次安装即真实出片”。
4. **BUILD_INFO 元数据**：`BUILD_INFO.json` 的 `known_blockers` 仍保留一条历史 Alembic 说明；本轮隔离库 `alembic check` 已通过（`No new upgrade operations detected.`）。这是包元数据文案滞后，不影响运行。
5. **成片字节确定性**：本轮成片 SHA 与上一轮相同，原因是确定性归一化装配；本轮镜头/任务/artifact/EDL 引用均为新生成。历史成片与历史下载文件未被覆盖。
6. **审片结论不是人工审批**：VLM/ASR 结果保持 advisory/needs_review，`acceptance_status` 未提升；真实作品的审美、版权与发行仍为 `PENDING_HUMAN`。

---

## 十、证据索引

全部在 `.local-acceptance/real-user-journey-final-v4/`（Git 忽略）：

- `evidence/v4-db-evidence.json`：系列、任务、artifact、门禁、审片、租约、新项目与预制作计划。
- `evidence/full-pytest-final2.log`、`frontend-vitest-final.log`、`frontend-build-final.log`、`ruff-final.log`、`alembic-final.log`。
- `screenshots/`：分镜结果、应用、预制作分析/预检/冻结尝试、成片、播放、下载等。
- `downloads/9039fc307f3dee89d09039a7f50dd603a36d460efd1bfde38f05f914a370e99c.mp4`：真实下载文件。
- `recovery/01-series-started-before-browser-close.png`、`02-after-browser-reopen.png`、`series-final.json`、`series-id.txt`：关闭浏览器恢复。
- `package-wizard/existing-scan.png`、`existing-verified-final.png`、`existing-verified-final.txt`：正式包已有 ComfyUI 识别/复用。
- `evidence/package-build-final2.log`、`package-second-launch.log`、`package-stop.log`：正式包构建、第二次双击、停止。
- `viewports/viewport-report.json` 与 12 张视口截图。
- `seed`/`input`/`browser-profile`：隔离素材与浏览器配置；不含真实用户私人素材。

## 十一、Git 交付

- 应用代码提交：`17c94daaf9b6a65f845fd37e78a0da69bd07cb82`
- 本轮代码改动：`6ea4b9c`（建议式审片解耦 + 风格冻结修复）、`17c94da`（正式包端口转发）
- 本报告提交后正常推送 `main`；不强推、不覆盖远端历史。
- 远端 `main` 与本地一致性将在推送后核对；若远端有新提交，以实际远端为准，不伪造本地一致。

**最终签核：`PARTIAL`。真实用户旅程的主要制作、播放、下载、审片、恢复和 Windows 打包链路已通过；正式新作品预制作冻结与正式包真实模式接管仍为真实阻断，故 `REAL_USER_JOURNEY_ALL_PASS` 不予签署。**
