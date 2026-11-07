# 新用户全旅程修复：实施报告（阶段 A / D）

- **日期**：2026-11-07
- **依据**：`AGENTS.md`、`docs/NEW_USER_JOURNEY_REPAIR_PLAN_20261107.md`
- **状态**：代码与配置已实施；真实硬件验收与干净机验收仍未闭环（见文末“剩余阻塞”）
- **证据纪律**：本报告只写已经跑过的检查。没有跑过的检查一律写在“剩余阻塞”，不写成通过。

---

## 1. 官方 H3 权重核实（A 级证据，2026-11-07）

来源：`https://huggingface.co/Comfy-Org/MiniMax-H3`，revision `54472221d27c695fca9621b0c3ed52f06c4af707`（lastModified 2026-11-02），经官方 tree API 逐目录读取全部 **39** 个文件。

### 1.1 结论：官方仓库没有 W4/AWQ/GPTQ/INT4 的 DiT

| 检查项 | 结果 |
| --- | --- |
| `diffusion_models/` 的 12 个 DiT | bf16 / int8_convrot / pruned_bf16 / pruned_fp8_scaled / pruned_int8_convrot / pruned_w6a8（Ref2VA 与 FL2VA 各一套） |
| 名称含 w4/awq/gptq/int4/nf4/4bit 的文件 | **只有 1 个**：`text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`（文本编码器，不是 DiT） |
| `MiniMax-H3_Ref2VA-W4-ConvRot.safetensors` | **在官方仓库不存在**。官方命名是 `minimax_h3_ref2va_<quant>.safetensors` |

因此“更小权重包”方向落地为官方 `pruned_w6a8` 组合，而不是自造一个 W4 文件。

### 1.2 低端机候选与基线的官方钉点（size / SHA-256 均来自官方 tree API）

| 角色 | 文件 | size | sha256 |
| --- | --- | --- | --- |
| DiT（候选） | `diffusion_models/minimax_h3_ref2va_pruned_w6a8.safetensors` | 15,983,746,636 | `ece96bbbce76670ec782de84acc888fd9d9b210bd2ab97c1957b39896734f9f1` |
| DiT（当前基线） | `diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors` | 20,970,379,616 | `9255f52b6677845ad238f20dfaafa94727053694127ab7f255c048f0f9365779` |
| 视频 VAE（小） | `vae/minimax_h3_video_vae_int8_convrot.safetensors` | 2,811,065,184 | `52a2c8c73583c86e4f41cdcce3a6ad0ea562987bc0bf3d60a0cef5f5c8e60c0e` |
| 视频 VAE（基线） | `vae/minimax_h3_video_vae_fp16.safetensors` | 5,207,808,496 | `7c1f131492e7eddacaac9069a61b81bdd39de5cc96561e677c5eab1cdce5e522` |
| SOL VAE（小） | `vae/minimax_h3_sol_vae_int8_convrot.safetensors` | 1,988,306,504 | `a742069ba173bc3aba64cf8d8289d073e0818f2753f2fc2768f0342645089682` |
| SOL VAE（基线） | `vae/minimax_h3_sol_vae_fp16.safetensors` | 3,596,335,728 | `61eaab3d7ca1659f20bce4d02bbc5bd5e5af608c73328d30cda96c8dc4d033e0` |
| 文本编码器 | `text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | 15,687,142,551 | `35a88d51044231fe332301d7a62aa81e3f2cba62febeb446e2c1e3e0ef76f2c6` |
| 音频 VAE | `vae/minimax_h3_audio_vae_fp32.safetensors` | 605,254,808 | `8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48` |
| PDD-free LoRA 候选 | `loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors` | 1,956,193,000 | `5b9ab5ade15d0775676d01a907268a69a1468dc6033b3b0d3ded5502f3ebb84c` |
| ControlNet INT8 | `model_patches/minimax_h3_fun_controlnet_union_pruned_int8_convrot.safetensors` | 2,296,635,360 | `9c645c0a308c8af361efd43b409710f6f8fec0db297c29503e141a84991fed0c` |

**官方页面没有给出任何 W6A8/W4 的显存或内存门槛，也没有给出与本工作台 PDD、LoRA、VAE、文本编码器的兼容说明。** 这些项目一律标记为待核，不由 LLM 或安装器猜测。

---

## 2. 阶段 A：LLM 配置保存与测试闭环（P0）

### 2.1 改动

| 文件 | 行为变化 |
| --- | --- |
| `backend/shine/llm/base.py` | 新增 `LLMCatalogError`（`LLM_CATALOG_INVALID`）：目录不可用是独立分类，不再被当成“无说明的 500”。 |
| `backend/shine/llm/transport.py`（新增） | 统一 HTTP 客户端；**本机地址默认绕过环境代理**（`127.0.0.1`/`localhost`/`::1`），远端保留显式代理能力。 |
| `backend/shine/llm/openai_compatible.py` | 目录解析改为严格：非 JSON、非对象 JSON、非法结构、缺列表、404 一律 `LLM_CATALOG_INVALID`；**空目录不再声称模型已确认**；`model_listed` 只认精确匹配，不做大小写模糊；聊天与目录 URL 从同一 API 根推导，消解两者冲突。 |
| `backend/shine/llm/anthropic.py` | 探测的网络/JSON/鉴权异常全部归一化为分类错误；目录同样严格。 |
| `backend/shine/llm/secrets.py` | 每次写入生成**新版本文件**（`<profile_id>.<version>.secret`），不再覆盖当前密钥；新增 `verify()`（回读比对）与 `status()`（`not_set/stored/missing/unreadable`）。旧引用仍可读。 |
| `backend/shine/llm/gateway.py` | 新增 `test_generation()`：固定、合成、无项目素材的最小结构化生成探测；Mock 供应商不算验收。 |
| `backend/shine/api_llm.py` | ①密钥轮换改为 saga（新版本写入→回读验证→单事务切换→提交后清理旧文件）；②新建/更新共用同一跨字段校验；③连接测试返回统一证据模型；④新增生成测试端点；⑤新增文本工种批量绑定（单事务）；⑥公开响应增加 `secret_status`，永不返回密钥正文或引用。 |
| `backend/shine/comfyops/llm_readiness.py` | 每角色增加 `readiness_status`（UNCONFIGURED/CONNECTION_READY/GENERATION_READY/BLOCKED/STALE）与 `generation_code`；顶层增加 `generation_status`/`text_agent_ready`；连接门禁语义保持不变（`status` 仍是 READY/BLOCKED）。 |
| `backend/shine/schemas.py`、`api_common.py` | 承载新的证据字段与 `secret_status`。 |
| `frontend/src/components/LLMSetupPanel.tsx` | 写入状态与刷新状态分离；写成功但刷新失败显示“配置已保存，但状态刷新失败”；失败/超时都不丢弃服务端真相；测试失败后仍重读；批量绑定一次请求；连接检查与生成验收分开呈现。 |
| `frontend/src/components/LLMConfiguration.tsx` | 密钥草稿只在服务端确认后清空；新增“生成验收”按钮；展示 `secret_status` 与候选 Model ID。 |
| `frontend/src/api.ts` | 新增 `ProviderTestResult` 类型与 `secret_status`。 |

### 2.2 关键契约

- 连接测试 `ok=false` 当且仅当“服务不可达/鉴权失败/目录不可用/模型未列出”。`reachable=true` 与 `model_listed=false` 可以同时成立，这正是旧版“连接成功但准备工作台阻断”的矛盾来源。
- 生成测试是**独立**证据：`generation_ok`/`structured_output_ok` 与连接证据分开存放，任一失败都会让 `text_agent_ready` 回落，不会保留旧的 READY。
- 密钥更新：写盘失败 → 旧引用与旧文件原样可用；数据库提交失败 → 旧密钥仍可加载、新版本文件被清理；只有提交成功后才删除旧文件，清理失败只留可恢复告警。

### 2.3 测试

新增 `backend/tests/test_llm_setup_contract.py`（16 项）与 `frontend/src/test/llmSetupContract.test.tsx`（5 项）。

```
cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_llm_setup_contract.py tests/test_llm_gateway.py tests/test_setup_llm_readiness.py -q
→ 51 passed
cd frontend; npm.cmd test -- --run src/test/llmSetupContract.test.tsx src/test/llmConfiguration.test.tsx
→ 9 passed
```

覆盖：保存后刷新失败不误报、密钥写盘失败/提交失败的原子性、密钥轮换与清理、非法目录（HTML/数组/缺键/空目录/无该模型）、401/404/429/503/超时/无效 URL 的确定分类、连接测试与 readiness 不再矛盾、生成测试的通过与回落、配置变更使生成验收失效、更新路径复用创建路径校验、批量绑定在第 1/6/12 个角色失败时都不产生半套提交、Mock 不能接管文本工种、应用重启后配置/密钥/证据仍可用。

---

## 3. 阶段 D（可实现部分）：图形化安装外壳与用户侧包校验

### 3.1 改动

- 新增 `scripts/setup_wizard.ps1`（WinForms，UTF-8 with BOM，PS 5.1 解析通过）：首屏校验 `PACKAGE_MANIFEST.json`（逐文件 SHA-256）、列出各固定盘可用空间并默认选非系统盘、展示隐私与权限说明、以安装器**真实输出**作为日志与状态（marquee 进度条，不显示任何未测量的百分比）。
- `packaging/windows/SETUP.cmd` 改为调用向导；无桌面会话（服务/CI/恢复场景）时向导自动回落到 `scripts/install_once.ps1`，因此**仍然只有一个测试者入口**，命令行路径保留为恢复入口。
- 新增 `backend/tests/test_package_entrypoints.py` 用例：单一入口 → 向导 → 控制台回落；向导不申请管理员权限；向导不做第二套安装实现。

### 3.2 未做与未声称

- 向导只负责**工具链与依赖**安装与包校验；模型权重仍由工作台向导在用户确认后处理。
- 图形外壳未在真实干净 Windows 上做过人工验收（见“剩余阻塞”）。

---

## 4. 大纲任务卡在「逐场提取剧情要点」的修复（用户现场反馈）

### 4.1 现象与根因

用户反馈：导演模式走到 ③ 选择素材 时，后台大纲任务停在「逐场提取剧情要点 · 已完成 0/6」，
错误码 `STORY_EXTRACTION_NEEDS_REVIEW`，「核对已有结果」可点但「恢复未完成工作」一直是灰的，
整条旅程无法继续。

根因不是网络或权限，而是**外部结果分类把“已明确失败”记成了“结果未知”**：

1. `backend/shine/materialprep/agent_call.py` 把所有没有显式 `external_outcome` 的 Provider 异常
   一律压成 `UNKNOWN`。于是一个 401（密钥错误）、404（模型不存在）、429（限流）或“模型答复了但
   不满足 JSON 约定”的失败，都被记成“结果未知”。
2. `backend/shine/materialprep/story.py` 的核对只认三种结论：`RESULT_ADOPTED`、
   `NOT_SENT_PROVEN`、`UNCONFIRMABLE`。既然外部结果是 `UNKNOWN`，核对只能给 `UNCONFIRMABLE`。
3. `backend/shine/api_story.py` 的恢复门禁据此返回 409，前端的「恢复未完成工作」按钮据此禁用。
   → 用户只剩「取消后续工作」，**没有任何前进路径**。

另外发现两个会放大死锁的缺陷：

- 重复点「核对已有结果」会把已经判定的结论重新覆盖成 `UNCONFIRMABLE`（判定不幂等）；
- 后端 `resumable` 判定与恢复门禁不是同一套规则，界面会给出服务端必然拒绝的按钮。

### 4.2 修复（不放松 UNKNOWN 门禁）

| 文件 | 改动 |
| --- | --- |
| `backend/shine/materialprep/agent_call.py` | 新增 `classify_external_outcome()`：`LLM_NOT_CONFIGURED`/`LLM_CAPABILITY` 记为 `NOT_SENT`（根本没发出请求）；Provider 明确答复（4xx 拒绝、2xx 不可用答复）记为 `RESPONSE_RECEIVED`；5xx/超时/连接中断仍为 `UNKNOWN`。同时把 HTTP 状态写进 `response_status`。 |
| `backend/shine/materialprep/story.py` | 核对新增 `RESPONSE_RECEIVED_NO_ANSWER` 结论：Provider 已答复且没有可用答案 → 请求已结束、不存在可被重复购买的答案 → 在 `MAX_SUBTASK_RETRIES` 预算内释放身份并允许父任务继续；记录的是**真实结论**，不是 `NOT_SENT_PROVEN`。另加「已判定即保持」守卫，使重复核对不再翻转结论；`resumable` 判定与恢复门禁统一。 |
| `backend/shine/api_material.py`、`backend/shine/api_film.py` | 单场素材与影视代理的核对走同一套结论与同样的有界释放；同样加「已判定即保持」守卫。 |
| `backend/shine/materialprep/scenes.py` | 场景任务 trace 落盘 `response_status`，作为可核对的事实证据。 |
| `frontend/src/components/StoryPreparationPanel.tsx` | 恢复按钮的可用性同时看状态与核对结论（与后端一致）；显示被阻塞子任务的原因；`UNCONFIRMABLE` 文案改为真实可执行的下一步（修改剧本后重新导入并重新提取，或取消本任务）。 |

### 4.3 门禁仍然完好

- `UNKNOWN` 路径**一行未改**：可能已发出的请求仍然不重发、不替代、不释放身份，核对结论仍是
  `UNCONFIRMABLE`，恢复仍是 409。
- 释放有次数上限（每场 2 次）：模型永远满足不了的场景会停下来等人，而不是无限烧调用。
- 核对本身**从不**联系 Provider（`external_calls: 0`），新的请求只在用户点「恢复未完成工作」后
  由 Worker 发出。

### 4.4 测试

```
cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_story_outline_unblock.py \
  tests/test_story_preparation.py tests/test_story_section_run.py tests/test_storyboard.py \
  tests/test_material_scene_safety.py tests/test_material_scenes.py tests/test_material_brief.py \
  tests/test_film_unknown_outcomes.py -q
→ 49 passed
cd frontend; npm.cmd test
→ 23 files / 162 tests passed
```

新增 `backend/tests/test_story_outline_unblock.py`（3 项）与 `frontend/src/test/storyOutlineUnblock.test.tsx`（3 项）：
已答复但无可用答案的场景被释放且大纲能跑完；`UNKNOWN` 仍然被挡住、身份不被释放、Provider 不会被再次调用；
重试到上限后停止并等人；界面给出真实原因与真实下一步。

---

## 5. 用户授权的一次性重发 + 连续模型调用间隔控制（对第 4 节的补充）

### 5.1 显式确认后的一次重发

- 入口：`POST /projects/{id}/story-preparation/tasks/{task_id}/resend-unknown`，`expected_state_version`
  绑定用户看到的状态，`evidence` 记录用户的确认说明；界面按钮「明确重发这一场」先弹确认框，
  写明「旧请求可能仍有答案，可能多花一次模型调用」。
- 只在用户点按钮并确认后发生；系统**绝不自动**发起，核对（reconcile）仍然只读证据。
- 旧任务退休时记录的原因写 `USER_AUTHORIZED_NEW_REQUEST`，**不得**写 `NOT_SENT_PROVEN`：旧请求的
  结果仍然未知，它只是被用户明确覆盖的一条未知事实。
- 每一场最多一次（`MAX_UNKNOWN_RESENDS = 1`），第二次同一场被拒（`STORY_UNKNOWN_RESEND_ALREADY_USED`）；
  审计事件 `STORY_UNKNOWN_SUBTASK_RESEND_AUTHORIZED` 记录旧身份、证据与「允许一次新请求」。
- 其余门禁不变：UNKNOWN 仍不会被自动重放或自动替代。

### 5.2 调用间隔收归唯一入口

- `agent_call.call_material_agent` 现在在**每次** Provider 调用前调用 `pace_provider_call()`
  （`SHINE_AGENT_STEP_DELAY_MS`，默认 1200ms）。此前只有 brief 路径调用它，而**逐场大纲每场是
  一个任务步，跨步之间没有任何间隔**——21 场连续调用共用同一条连接与同一个限流，失败的表现正是
  「卡住」。`scenes.prepare_scenes` 里那次重复 sleep 已移除，间隔只有一个口径。
- 逐场调用保持**串行**：一次一个任务步，不并发、不并行扇出。
- 验收：`backend/tests/test_story_unknown_resend.py`（3 项，含「两次调用之间确有间隔」的实测）、
  `frontend/src/test/storyOutlineUnblock.test.tsx`（5 项）。

---

## 6. 阶段 B：安装改为持久任务 + 统一续传合同

### 6.1 交付

| 文件 | 行为 |
| --- | --- |
| `comfyops/installation.py`（新增） | 持久 attempt 状态机：阶段、已确认字节、速度、重试、心跳、取消/暂停标记、幂等键 |
| `comfyops/installation_worker.py`（新增） | Worker 领取 setup 类型安装任务、心跳续租、崩溃恢复；只有**可证明的下载阶段**才自动 resume，其余走对账/`NEEDS_REVIEW`，不盲目重放 |
| `comfyops/installation_precheck.py`（新增） | 由真实安装计划驱动的预检：对每个真实源做 `Range: bytes=0-0` 与重定向合同检查，`DEGRADED` 不显示成「全部通过」 |
| `comfyops/installer.py` | 续传合同：`408/429/500/502/503/504` 走指数退避 + jitter，`Retry-After` 有上限（60s）；传输中断从**已确认字节**换下一个候选源；镜像只是同一份固定字节的传输候选，size 与 SHA-256 仍是唯一验收标准 |
| `comfyops/models.py` + 迁移 `f180b0020001` | `installation_attempts` 增加阶段/字节/心跳/错误/租约等持久字段 |
| `api_preparation.py` | `POST /comfyops/install-plans/{id}/execute` → **202** `{attempt_id, status: QUEUED, plan_hash}`；新增 `GET/pause/resume/cancel /comfyops/installation-attempts/{id}` |
| `worker.py` | 安装队列排在其它队列之前：安装是耐久工作，不该被长片生产饿死 |
| `frontend/src/setupExecution.ts` | 202 队列 + 按 attempt id 重新附着 + 暂停/继续/取消；`waitForInstallationAttempt` 是短轮询而不是一次长请求 |
| `frontend/src/components/SetupPanel.tsx` | 真实阶段、已确认字节、进度、暂停/继续/取消；刷新后按 attempt id 重新附着 |
| `scripts/bootstrap.ps1` | 工具链下载用 `curl -C -` 续传；解压进 staging，校验入口与清单后**原子换入**最终目录；中断后再次运行可自愈 |
| `scripts/install_once.ps1` | 工具链、后端依赖、前端依赖、迁移、服务健康各留**成功回执**；「目录存在」不再等于装好了 |
| `scripts/start_shine.ps1` | 重复启动同时校验 API、Web、Worker 与记录的 listener 进程身份，半死栈进入收尾/重启而不是误报「完整运行」 |

### 6.2 验收

`backend/tests/test_installation_persistence.py` 覆盖：202 队列与重新附着、阶段推进、暂停/继续/取消、
下载阶段可安全 resume、不确定阶段不得重放、Range 被忽略时安全重写、退避与镜像切换、迁移升降级。

---

## 7. 阶段 C：统一硬件资格与 H3 变体清单

### 7.1 硬件事实与资格

- `comfyops/host_env.py` 返回完整硬件事实：所有 GPU（index/UUID/名称/驱动/总显存/**空闲显存**/选中单卡）、
  CUDA compute capability 与 Torch arch list、内存总量与可用量、Windows commit 总量与可用量、页面文件异常、
  磁盘（空闲/所需/可复用/安装后余量）、平台（Windows 版本、x64、目录可写、中文/空格/长路径）、
  证据（探测时间、失败原因、estimated 与 measured）。**多卡显存不相加**，由用户选择单卡 UUID。
- `comfyops/recommendation.py` 的结论集合是 `SUPPORTED / BLOCKED / UNKNOWN`：GPU 未识别、内存或 commit
  不可读、CUDA 未知、非 Windows、非 x64 一律**不得**返回 `SUPPORTED`（原来把 `DETECTION_FAILED` 判成
  `SUPPORTED` 的断言已改为期望 `UNKNOWN`）。
- `api_setup.py` 在**服务端**复核资格：绕过前端直接调 install-plan 也会被同一套门禁挡住；
  新机 blocker、推荐器、install-plan 与生产启动共用同一份阈值与资格服务。

### 7.2 变体清单与下载量三分法

- `comfyops/variants.py` + `config/deployment/variants/*.json`：四条变体，每条都有独立的
  `checkpoint_id` / `graph_sha256` / `workflow_profile_ids` / 兼容矩阵 / `acceptance_status`，
  **互不继承证据**。
- `comfyops/deployment.py` 的 `download_breakdown()` 把三个不同的数字分开返回：完整目录、
  默认生产组件、可选审片组件；当前基线为 60,804,054,569 B（完整）/ 48,317,862,339 B（默认生产）/
  12,486,192,230 B（可选审片）。界面、安装脚本与打包脚本不再把三者写成同一个数。
- `scripts/generate_deployment_catalog.py` 现在**幂等**：连续两次生成的目录哈希不变，版本不倒退，
  3 个默认 Profile 不丢。

### 7.3 12GB / NVFP4 候选的诚实边界

- 变体 `h3-ref2va-nvfp4-lowend`（CANDIDATE）：DiT 用 `DmitryDB/MiniMax-H3-ComfyUI-Quants`
  revision `06aba4816457400d1dcb12ffd77e37499c8116b1` 的 `Ref2VA/MiniMax-H3_Ref2VA-NVFP4.safetensors`
  （11,663,443,448 B / sha256 `f970a8a3…dbc95e2`）；文本编码器、视频 VAE、SOL VAE、音频 VAE、Turbo LoRA
  全部用官方 `Comfy-Org/MiniMax-H3` revision `54472221d27c…` 的钉点。
- 工作流 Profile `config/workflows/ref2va-nvfp4-turbo-4step-2img.json` 独立、**不启用 PDD**，
  接入 Turbo LoRA 时采样步数 4；`--lowvram` 与 ≥32GB 内存登记为 `runtime_requirements`（来源标注为
  发布方/用户说明，不是本项目实测）。
- **界面已接线**：frontend/src/components/VariantRecommendation.tsx 由安装向导在「检测本机」后渲染，
  逐条显示候选状态、证据缺口、第三方声明与运行要求；此前该组件只建好未渲染，推荐到不了用户。
- **不进默认就绪集合、不自动下载、`acceptance_status = CANDIDATE`**；推荐结果必带证据缺口：
  发布方分档表「8–12GB 仅 RTX 50/Blackwell」「Ada/4070S 未覆盖」「未在 5090 上做完整生成运行」
  「文件大小 ≠ 峰值显存」，以及 `requires_explicit_user_ack`。

---

## 8. 阶段 D：图形化安装外壳与官方权重核实

- `scripts/setup_wizard.ps1`（新增，UTF-8 with BOM，PS 5.1 解析通过）：首屏逐文件 SHA-256 校验
  `PACKAGE_MANIFEST.json`、列出固定盘可用空间并默认选非系统盘、展示隐私与权限说明、以安装器**真实输出**
  作为日志与状态（marquee 进度条，不显示任何未测量的百分比）、悬停/焦点可见、尊重
  `prefers-reduced-motion`。
- `packaging/windows/SETUP.cmd` 改为调用向导；无桌面会话（服务/CI/恢复场景）时自动回落到
  `scripts/install_once.ps1`，因此**仍然只有一个测试者入口**，命令行路径保留为恢复入口。
- 官方权重核实结论见第 1 节（revision、size、SHA-256 全部来自官方 tree API，并与其自带
  `SHA256SUMS` 交叉核对）。

---

## 9. 剩余阻塞与明确未声称

1. **干净 Windows 全流程**：未在空白机器上跑过「分发包 → 安装 → LLM 配置 → H3 准备 → 首个真实镜头」。
2. **真实弱网续传矩阵**：断线/重启/取消/恢复的完整故障矩阵需要真实网络；离线测试只覆盖可模拟的部分。
3. **H3 变体真机验收**：NVFP4 / w6a8 候选都没有真实最小镜头、资源峰值与人工质量对比，
   不得进入生产推荐。
4. **12GB / Ada 可行性**：官方未公布门槛，w6a8 权重本体 15.98GB 已超过 12GB 显存；
   NVFP4 需要 Blackwell。待核。
5. **W4 ConvRot**：官方仓库不存在该文件；仓库只保留显式未钉住的槽位，等待可信来源。
6. **分发**：本轮改动要到达其他用户，必须先提交并从提交树重建安装包（构建脚本拒绝脏工作树）。
7. **未声称**：不把「文件已下载」「节点存在」「模型目录可连接」写成「真实生成已通过」；
   不把发布方的分档表写成本项目实测；不把候选变体写成生产可用。
