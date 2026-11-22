# Shine Video Studio V3 最终收尾验收报告（2026-11-22）

- **验收基线**：远端 `origin/main` 最新提交 `822d2a0efea7f09a2fb989a67cb8f9e6a1083b04`（含 V3 P1–P4），Windows 11 + NVIDIA GeForce RTX 5080 (16 GB VRAM)。
- **核心约束遵守**：未重构架构，未新增平行检查器、状态机、执行器或整备入口；未修改 [client_identity.py](file:///F:/ClaudeWorkSpace/projects/shine-video-studio/backend/shine/quality/client_identity.py) 锁定的任何文件（不触发 VLM 重校准）。
- **独立验收目录**：`F:\ClaudeWorkSpace\projects\shine-video-studio\.local-acceptance\v3-final-20261122`（结束后已干净停机，7 条 GPU 租约全部 `RELEASED`，仓库 `.env` 已恢复）。

---

## 一、总览矩阵（PASS / BLOCKED / NOT_VERIFIED）

| 验收域 | 结论 | 关键事实与证据摘要 |
| --- | --- | --- |
| 1. GitHub Actions CI 失败根因与修复 | **PASS**（本地全绿，待 push 后远端验证） | `gh run view 44937030711` / `44971331892` 三类失败均已定位并完成最小修复（见第二节） |
| 2. 浏览器 E2E 对齐唯一导演工作台 | **PASS** | [verify_v13_web.py](file:///F:/ClaudeWorkSpace/projects/shine-video-studio/scripts/verify_v13_web.py) 经 `ruff` 及连续两次 Playwright Chromium 全通过（`MOCK_VERIFIED`, `api_errors: []`） |
| 3. 基础 H3（1 图 / PDD8 / 二采 / 参考音视频） | **PASS** | 零下载零拷贝核验接管既有 ComfyUI `b0b743566`（43.97 GB）、`/setup/services/validate` 1281 节点 `READY`、RTX 5080 四镜连续真实成片 |
| 4. RelayVSR X2 兼容、整备与投产路径 | **BLOCKED（预期诚实阻断） / NOT_VERIFIED（真机超分）** | 权重已落盘但 ComfyUI `b0b743566` 缺 `RelayVSRConditioning` 与 `VSRDecode`（需 `2f89433+`）；所有真值口一致报 `RELAYVSR_NODES_MISSING_IN_LIVE`，单镜投产返回 `409 STRATEGY_BLOCKED`，基础 H3 不受影响 |
| 5. 电影专业 Skills × 真实五角色 LLM 自动创作 | **PASS** | 真实 `MiniMax-M3.1-flash` 在中性风格（`profile_id=""`）下跑通全流程：大纲提取→分镜书冻结→五角色全部 `SUCCEEDED` 且命中 `cinema_skills`，自动落镜 4 条连续分镜 |
| 6. 连续多镜真实 H3 成片、声音与人工审片 | **PASS（带视觉细节记录）** | RTX 5080 连续出片 4 镜 + 整集交付合成 MP4（23.52s），`ffprobe`/`ffmpeg -f null -` 零错误解码；对白/静默音量分明；12 张关键帧人工审片确认主体/环境连贯与连续手部动作 |
| 7. P1–P4「一套事实、一个裁决、一个执行者」复核 | **PASS（修复 1 处前端遗漏）** | 修复 [useStudio.ts](file:///F:/ClaudeWorkSpace/projects/shine-video-studio/frontend/src/useStudio.ts) 单镜排队不再要求必须先登记旧版 `workflowProfiles`；其余整备、能力与执行链路均保持唯一口径 |
| 8. 全量静态检查、前端测试构建与后端 8 分片回归 | **PASS** | Ruff / Copilot 契约(475) / Alembic / `test_isolation_static` / Vitest(472/472) / `tsc` / Vite Build 与后端 8 分片 304 文件全部 0 失败通过 |

---

## 二、重点 1：GitHub Actions CI 排查、跨平台与安装安全约束保持

对提交 `822d2a0` 触发的 CI Run（`44937030711`、`44971331892`）逐条核对日志后，严格区分**真实生产回归**与**测试夹具/环境问题**，**零放宽**系统盘拒绝策略（`storage.require_storage`）、绝对路径校验（`existing_installation.local_path`）、Windows 专用图片进程启动约束（`IMAGE_RENDERER_WINDOWS_ONLY`）和端口监听证明（`COMFY_LISTENER_UNVERIFIED`）：

1. **L3 browser E2E（[verify_v13_web.py](file:///F:/ClaudeWorkSpace/projects/shine-video-studio/scripts/verify_v13_web.py)）—— 历史脚本未随 V2/V3 唯一工作台收敛而更新 [PASS]**
   - **现象**：旧脚本在 `#v/production` 等待已退场的 `"切换项目"` 下拉框超时；`#style`（`"批准并冻结"`）、`#assets`（`"确认使用"`）、`#preproduction`（`"操作模式"`）、`#production`（`"生成方式"`/`"生成模板"`）及 `#film-crew` 折叠状态均已收敛进七阶段导演工作台。
   - **修复**：在不削弱任何协议校验与安全断言的前提下，将 E2E 完整对齐唯一导演工作台与当前控件：
     - `#d/creative` 阶段操作栏确认大纲；
     - `#d/assets`（`#plan-stage`）生成并确认分镜书、冻结 Style V1（`approved_by == "local-user"`）；
     - `#d/materials`（`#shot-materials`）走 `uploadFor` 上传+批准+锁定+绑定 `isolated-browser-upload.png`（`BrowserUpload`）；
     - `#d/package` 展开 `details#preproduction` 并使用 `"本次维护请求的执行器"` 完成实例生命周期、候选晋升、Voice Studio 试听冻结与 SRT 下载、失败安装对账及 `workflow-capabilities` 路由拦截；
     - `#d/produce` 展开 `details#manual-storyboard` 验证 Ref2VA-only（即使存在 `ui-only-T2VA` 也不选中）两步确认发单拦截与 Mock 回退；
     - `#d/advanced` 展开 `details#film-crew` 与 `details.advanced-fold` 验证 text critic 与未知远端响应放弃；
     - `#d/setup`（`#director-setup`）切至 `"② 文本模型"` 验证 `max_completion_tokens` 与 `reasoning_effort`；
     - 顶栏 `"快速切换作品"` 验证跨项目迟到响应隔离与 `#d/deliver` 恢复。
   - **验证**：`ruff check scripts/verify_v13_web.py` 通过；连续两次实跑 Chromium `1234`（`--work-dir F:\ShineTest\web-e2e`）均输出 `{"status": "MOCK_VERIFIED", ..., "api_errors": []}`。

2. **L3 backend full (Windows)（3/3607 失败）—— 全部为 GitHub Runner `C:` 临时目录测试夹具问题，未改动生产约束 [PASS]**
   - **根因**：`windows-latest` Runner 的 `tmp_path` 位于系统盘 `C:`，触发真实的 `storage.require_storage` -> `409 CHOOSE_DEPLOYMENT_DIRECTORY_OUTSIDE_SYSTEM_DRIVE`。
   - **修复**：为 [test_setup_chain_convergence.py](file:///F:/ClaudeWorkSpace/projects/shine-video-studio/backend/tests/test_setup_chain_convergence.py) 两个用例补充既有 `fake_deployment_host` 夹具，为 [test_setup_preparation_chain.py](file:///F:/ClaudeWorkSpace/projects/shine-video-studio/backend/tests/test_setup_preparation_chain.py) 用例 monkeypatch `shine.comfyops.storage.on_system_drive`。真实安装仍严格拒绝未授权的系统盘路径。

3. **L3 backend full (Linux)（10/3420 失败）—— 1 处真实跨平台生产回归 + 9 处测试环境/Mock 缺失 [PASS]**
   - **真实生产回归（已修复）**：[runtime_configuration.py](file:///F:/ClaudeWorkSpace/projects/shine-video-studio/backend/shine/comfyops/runtime_configuration.py) 的 `_workbench_pin_names` 与 `_production_digests` 直接对持久化 Windows 路径执行 `Path(str(path)).name`，在 Linux 上无法剥离 `\` 导致 `test_an_upgraded_bootstrap_is_not_an_environment_change` 失败。已改为先 `.replace("\\", "/")` 再取 `.name`，实现仅一行局部归一化。
   - **9 处测试环境/Mock 遗漏（已修复，保持生产逻辑零放宽）**：
     - [test_image_found_runnable_convergence.py](file:///F:/ClaudeWorkSpace/projects/shine-video-studio/backend/tests/test_image_found_runnable_convergence.py)：2 个依赖本机 `start_image_renderer` 真实调用的用例补充 `@pytest.mark.skipif(os.name != "nt", reason="managed image renderer start is Windows-only")`；
     - [test_image_renderer_crash_evidence.py](file:///F:/ClaudeWorkSpace/projects/shine-video-studio/backend/tests/test_image_renderer_crash_evidence.py) 与 [test_setup_machine_reuse.py](file:///F:/ClaudeWorkSpace/projects/shine-video-studio/backend/tests/test_setup_machine_reuse.py)（`installed` 夹具共 6 例）：补齐 `windows_port_listeners` mock，使无 PowerShell 的 Linux 环境可隔离端口探测；
     - [test_support_agent.py](file:///F:/ClaudeWorkSpace/projects/shine-video-studio/backend/tests/test_support_agent.py)：将 `redact_text("C:\\Users\\alice\\ComfyUI")` 直接断言保留跨平台覆盖，`/api/v1/support/actions/preview` 使用系统原生绝对路径。

---

## 三、重点 2：基础 H3 可用性与 RelayVSR X2 兼容/整备/投产核查

在独立栈（API `8001`、Worker、ComfyUI `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI`）上的实测事实：

1. **现有 ComfyUI 接管与服务契约验证 [PASS]**
   - `/setup/existing-installations/inspect` & `/verify`：识别真实版本 `0.39.0`（git commit `b0b743566f65daafc423b4fea8a2fbda94b3384a`），完成全量权重 SHA-256 核验；
   - `/setup/existing-installations/adopt`：生成实例 `d99e0095-4f35-5efb-ae86-7484305e5803`，`copied_bytes: 0`、`downloaded_bytes: 0`、`installed_h3_bytes: 43974265807`；
   - `/setup/configuration` 生成配方（`recipe_sha256: 9fa951ed3f9e0ffebf7346ee4f3873399823299362d147afef4ab6223728a75f`）；
   - `/setup/services/validate` 实机拉起并核验 ComfyUI：**`status: READY`**，`node_count: 1281`，`definition: shine.ref2va.v1`，`variant_id: h3-ref2va-int8-convrot`；`/setup/runtime/activate` 激活后 `/system/status` 报 **`mode: READY`**；
   - **基础 1 图 H3 不受 RelayVSR 缺失影响**：单图 Ref2VA、PDD 8 步、`second_pass`、`VHS_LoadVideo`、`VHS_LoadAudioUpload` 全部为 `READY` / `available: true`。

2. **RelayVSR X2 兼容、整备与投产核查 [BLOCKED（节点缺失预期阻断） / NOT_VERIFIED（真机超分）]**
   - **磁盘权重就位情况**：`relayvsr_x2_lightx2v_int8_convrot.safetensors` 与 `relayvsr_flash_vae_x2_fp32.safetensors` 已在本地存在，且纳入了部署清单 `relayvsr-x2-windows-x64.json`。
   - **ComfyUI 节点兼容实测**：当前 `b0b743566`（`v0.39.0`）的 `/object_info` 缺少核心节点 `RelayVSRConditioning` 与 `VSRDecode`（需要上游 `2f89433+` 或对应插件支持；仅有独立 `custom_nodes\flashvsr`）。
   - **三大真值入口与投产拦截一致**：
     - `GET /api/v1/setup/summary`：`optional_capabilities.relayvsr.status == "OPTIONAL_UNCONFIGURED"`，`available: false`，`missing_nodes: ["RelayVSRConditioning", "VSRDecode"]`，且不计入顶部 `blockers`；
     - `GET /api/v1/projects/{id}/director/strategy-options`：`upscale[0]` 为 `engine: "relayvsr", variant: "x2", enabled: false, status: "BLOCKED"`, code `RELAYVSR_NODES_MISSING_IN_LIVE`；
     - `GET /api/v1/manual4/projects/{id}/production/state`：共享同一 `optional_capabilities` 与 `options` 裁决源；
     - `POST /api/v1/projects/{id}/jobs` 显式传入 `strategy: {"upscale": {"enabled": true}}`：返回 **HTTP `409 STRATEGY_BLOCKED`**（`UPSCALE_BACKEND_UNAVAILABLE` -> `RELAYVSR_NODES_MISSING_IN_LIVE`），拒绝静默降级或绕过。
   - **结论**：因本机 ComfyUI 缺少上述核心节点，RelayVSR X2 真机超分出片明确保留 **`NOT_VERIFIED`（整备/投产链路 `BLOCKED`）**状态。

---

## 四、重点 3：电影专业 Skills × 真实五角色 LLM 自动创作与连续多镜 RTX 5080 H3 成片审片

### 4.1 真实 LLM 五角色自动创作与 Cinema Skills 命中验证 [PASS]

- **真实模型配置**：使用 `MiniMax-M3.1-flash`（`https://api.minimax.cn/v1`，DPAPI 密钥管理，连接与结构化输出实测 `ok: true`、`context_window_tokens: 1000000`）。
  - *实测调优说明*：默认未限制推理深度（`generation_options: {}`）时，实机出现 200 OK 后长耗时流式静默被网关诚实标记 `NEEDS_REVIEW` / `LLM_CONNECTION`（`external_outcome: UNKNOWN`，未盲目重投）。在配置 `{"max_completion_tokens": 8192, "reasoning_effort": "low"}` 后，流式结构化响应稳定收敛至 ~2.9–3s 级别。
- **作品与中性风格设定**：新建作品 `11b9fb3b-fe2e-4504-8182-d7cf99071b3a`（`V3 Final Cinema Skills Live Film`），上传真实中文剧本 `rainy_watchmaker.txt`（1 场雨夜古董钟表铺内戏：老钟表匠陈默修表、低语独白、摘放大镜望向雨夜门外并说出第二句对白），远程文本授权 -> 真实 LLM 提取与确认大纲 -> 确认分镜书（`profile_id=""`、`film_type="live_action"`，冻结 Style V1 `65880eb9-120a-430e-b4fc-5bf46e2c2edf`）。
- **五角色全部 `SUCCEEDED` 与 `AgentTaskRecord.trace["cinema_skills"]` 实测证据**：

| 角色 | 状态 / 模型 | 命中 Cinema Skills & 参考文档 | Token 预算使用 | Selection SHA-256（前 12 位） |
| --- | --- | --- | --- | --- |
| `scriptwriter` | `SUCCEEDED` / `MiniMax-M3.1-flash` | `observable-action-performance` (`prop-and-posture-anchoring.md`) | `837 / 1200` | `d8106dd7c682` |
| `director` | `SUCCEEDED` / `MiniMax-M3.1-flash` | `staging-and-narrative-reveal` (`two-person-blocking-and-cuts.md`), `medium-craft-live-and-cel` | `1082 / 1200` | `ef049075d7a2` |
| `cinematographer` | `SUCCEEDED` / `MiniMax-M3.1-flash` | `motivated-camera-and-composition` (`movement-motives-and-lighting.md`), `medium-craft-live-and-cel` | `1083 / 1200` | `41af77502d7d` |
| `continuity_supervisor` | `SUCCEEDED` / `MiniMax-M3.1-flash` | `physical-continuity-and-anti-glitch` (`common-h3-continuity-anomalies.md`) | `791 / 1200` | `4c398f839cac` |
| `editor` | `SUCCEEDED` / `MiniMax-M3.1-flash` | `rhythm-and-match-cutting` (`cut-vs-continuation-pacing.md`) | `669 / 1200` | `57599f7809dd` |

- **自动生成分镜内容**：5 角色链路自动落镜 4 镜（总编排时长 22.0s），且由模型自主规划了“静默建立镜 + 对白近景 + 静默动作 + 对白推镜”与连续三镜 `last_frame`：
  1. `0776278c`（5.0s, `cut`, 无对白）—— 钟表铺雨夜中景建立；
  2. `02934174`（6.0s, `last_frame`, 对白 `"十二点停下的怀表，今晚终于走动了。"` `[0.8s–4.8s]`）；
  3. `5caa883d`（5.0s, `last_frame`, 无对白）—— 摘下铜放大镜放回丝绒垫近景动作；
  4. `48df88ae`（6.0s, `last_frame`, 对白 `"既然针动了，他明天一定会回来取。"` `[0.5s–4.6s]`）。

### 4.2 连续多镜 RTX 5080 真实 H3 出片、声音检测与人工关键帧审片 [PASS]

- **参考绑定与编译**：确认 `ep-01` 后绑定真实参考图（角色 `陈默`、道具怀表、放大镜与雨夜场景），四镜均经 `H3PromptCompiler` 唯一编译入口（`shine-h3-compiler/1.4.0`），对白镜严格内嵌 `<d>[Chinese] ...</d>` 时间轴，静默镜明确非人声环境约束。
- **连续生产系列与租约**：ProductionSeries `c8fa380c-098a-49b9-8fc7-33b7ee0ae104`（`h3-ref2va-int8-convrot` + `pdd8`）**连续执行全部 4 镜均 `PASSED`**，Series 状态 **`COMPLETED` / `finalization_state: RELEASED`**，整场生产 + 交付期间的 7 条 `resource_leases` 全部安全 **`RELEASED`**（0 条卡死租约）；自动完成整集交付 `25669b4d`（EDL digest `e68ad0ef...`，`DELIVERED`）。
- **媒体解码与 `volumedetect` 实测**：

| 产物 | 视频流 / 分辨率 | 时长 | `ffmpeg -f null -` 解码 | `mean_volume` / `max_volume` | 产物 SHA-256（前 12 位） |
| --- | --- | --- | --- | --- | --- |
| 第 1 镜（静默建立） | `h264 1344x768` + `aac 32kHz` | `5.17s` | exit `0`（零错误） | `-49.0 dB` / `-20.2 dB` | `a0212fac51d2` |
| 第 2 镜（中文对白 1） | `h264 1344x768` + `aac 32kHz` | `6.58s` | exit `0`（零错误） | `-18.6 dB` / `-0.6 dB` | `1a6955248448` |
| 第 3 镜（静默动作） | `h264 1344x768` + `aac 32kHz` | `5.17s` | exit `0`（零错误） | `-40.9 dB` / `-12.4 dB` | `2de7fccabcd4` |
| 第 4 镜（中文对白 2） | `h264 1344x768` + `aac 32kHz` | `6.58s` | exit `0`（零错误） | `-15.5 dB` / `-0.4 dB` | `d1458642eeed` |
| **整集成片交付** | `h264 1344x768` + `aac 48kHz` | `23.52s` | exit `0`（零错误） | `-19.3 dB` / `-0.5 dB` | `88a8c0317b4b` |

- **声音检查结论**：静默镜平均音量低至 `-49.0 dB` / `-40.9 dB`（仅轻柔环境氛围音，无误说台词/爆音），而两条中文对白镜达到清晰的 `-18.6 dB` / `-15.5 dB`（峰值 `-0.6 dB` / `-0.4 dB`，未削波），静默镜与台词镜声压区分显著（相差 22–33 dB）。
- **人工视觉审片结论**（逐镜抽取起/中/末 3 帧共 12 张 JPG 审视）：
  - **画风与主体**：4 镜稳定保持真人写实影调（无二次元/塑料感漂移），主角陈默深色衣着、中分黑发、丝绒垫柜台及雨夜冷色调玻璃门背景与温暖桌面灯对比保持 180° 轴线一致；
  - **静默镜与连续性**：第 1 镜人物检表闭口无误张嘴；第 2 镜延续至怀表特写，手部五指自然；第 4 镜首帧（`0.1s`）平滑承接第 3 镜末帧桌面道具构图并切至人物转身面向雨夜木门、门外隐现人影与水光反射。
  - **客观瑕疵如实记录**：① 第 2 镜大特写表盘右下角罗马/阿拉伯刻度（3/4 点位）存在轻微 AI 纹理扭曲；② 第 3 镜（`video_4_f1`）桌面台灯短暂切换为绿罩银行灯，后续帧又回到奶白花边罩台灯，属于跨镜长时程下的小物体外观抖动（不破坏主剧情可读性）。

---

## 五、重点 4：P1–P4「一套事实、一个裁决、一个执行者」复核

1. **发现并修复 1 处前端单镜排队阻滞回归 [PASS]**
   - **问题**：P1 收敛已停止在常规整备时自动向旧表 `WorkflowProfile` 注册模板，统一由唯一裁决口 `services.make_render_request` 回退到动态策略 `shine.ref2va.v1`（`native20` / `pdd8`）。但 [useStudio.ts](file:///F:/ClaudeWorkSpace/projects/shine-video-studio/frontend/src/useStudio.ts) 的 `queueShot` 仍在 `comfyui` 模式下强行查找已登记 `workflowProfiles`，导致无旧模板时高级单镜入口直接报错无法入队。
   - **修复**：允许 `queueShot` 在 `workflowProfiles` 为空时直接传递 `workflow_profile_id: null, generation_mode: null`，由后端唯一 `make_render_request` 裁决并组装动态策略请求；有已绑定 Ref2VA 模板时仍保留显式携带。
2. **整备→生产阻滞、重复提交与历史兼容核查 [PASS]**
   - **整备与可用性唯一源**：`setup_summary`、`environment_snapshot`、`/director/strategy-options` 与 `/manual4/.../production/state` 共享同源 `sealed_optional_capabilities` 与 `shot_reference_capability_blockers`；基础 1 图 H3 与缺失审片/可选模型彻底解耦；
   - **防重放与对账**：本轮实机遇 `MiniMax-M3.1-flash` 流式静默超时时，系统在保存提交意图后如实进入 `NEEDS_REVIEW`（`external_outcome="UNKNOWN"`），未自动重投；
   - **历史兼容**：保留了历史 `WorkflowProfile`、旧 `director_style_binding` 与 `/director-styles*` 只读及复现能力；`SHINE_CINEMA_SKILLS_ENABLED=0` 支持零迁移关闭技能注入。

---

## 六、全量回归与静态检查明细

| 检查项 | 命令与作用域 | 结果 |
| --- | --- | --- |
| Python Lint | `.\backend\.venv\Scripts\python.exe -m ruff check backend/shine backend/alembic backend/tests scripts/run_tests.py scripts/verify_v13_web.py scripts/shine_supervisor.py` | **PASS**（`All checks passed!`） |
| Copilot 契约校验 | `.\backend\.venv\Scripts\python.exe backend/scripts/generate_shine_copilot_contracts.py --check` | **PASS**（`app=2.2.0 actions=16 knowledge=22 discovered=475`） |
| Alembic 迁移漂移校验 | `.\backend\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini check` | **PASS**（`No new upgrade operations detected.`） |
| 四阶边界静态守卫 | `.\backend\.venv\Scripts\python.exe -m pytest backend/tests/manual4/test_isolation_static.py -q` | **PASS**（3/3） |
| 定向后端回归（12 模块） | `test_setup_chain_convergence`、`test_setup_preparation_chain`、`test_image_found_runnable_convergence`、`test_image_renderer_crash_evidence`、`test_runtime_identity_relocation`、`test_setup_machine_reuse`、`test_support_agent`、`test_v3_p2_optional_multimodal`、`test_v3_p3_setup_production_convergence`、`test_v3_p4_cinema_skills`、`test_offline_e2e`、`test_film_crew` | **PASS**（245 项用例，244 通过、1 跳过、0 失败） |
| 前端全量 Vitest | `cd frontend && npx vitest run --no-file-parallelism` | **PASS**（94 文件，472/472 用例通过） |
| TypeScript & Vite Build | `cd frontend && npx tsc --noEmit && npm.cmd run build` | **PASS**（0 错误，1659 模块构建完成） |
| Playwright 浏览器 E2E | `uv run --project backend --with playwright==1.62.0 python scripts/verify_v13_web.py --work-dir F:\ShineTest\web-e2e --browser-path ...` | **PASS**（连续 2 次均 `MOCK_VERIFIED`，`api_errors: []`） |
| 后端 8 分片全量回归 | `$env:CODEBUDDY_SAFE_DELETE_ENABLED='0'; pwsh -NoProfile -File .\run_backend_shards.ps1 -WorkRoot F:\ShineTest -Shards 8`（304 文件） | **PASS**（`shards-20261122-110054-047`，wall `1053.5s`，8 个分片全部 exit `0`，`shards_with_failure_markers=0`） |
