# Shine V2.0 双生产线统一基础底座收尾最终验收报告

> 验收日期：2026-11-20  
> 验收分支：`main`  
> 硬件环境：Windows 11 / NVIDIA GeForce RTX 5080 (16,303 MiB VRAM, UUID `GPU-f3fda412-dabc-0f4a-c8e0-9fd69902205b`)  
> 共享 ComfyUI 环境：`F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI`（已收编实例 `22ea7c4e-316c-5ff3-a4aa-632186c81a5e`，零重复安装、零重复下载）  
> 隔离真机验收数据根：`F:\ShineTest\v2-real-e2e`（全程未修改用户生产目录 `D:\Shine\data`）

---

## 1. 阶段提交记录（Git Commit Ledger on `main`）

| 阶段 | Commit SHA | 提交说明 |
|---|---|---|
| **P0** | `650db6c` | `docs(p0): freeze V2 dual-pipeline closeout plan and foundation boundary audit` |
| **P1** | `6834332` | `feat(p1): unify GPU arbitration, shared ComfyUI environment/runtime foundation, and cross-line startup isolation` |
| **P2** | `54f8f34` | `feat(p2): unify Public Asset Center foundation for manual4 Qwen T2I, upload, promotion, and cross-line adoption` |
| **P3** | `d823679` | `feat(p3): unify H3 Ref2VA execution foundation, cross-episode continuity reset, and worker crash reconciliation` |
| **P4** | `0fb933b` | `feat(p4): fix manual4 dual-line defects, routing isolation, chunked LLM, and readiness/delivery gates` |
| **P5** | `f33cd06` + 收尾提交 | `refactor(p5): retire duplicate manual4 owned_process/workflow modules; fix multi-file component pin match in resource resolver` + Windows Job Object `ShineM4Handoff` 命名修复与终稿验收文档 |

---

## 2. 真机发现并根因修复的两项关键硬件阻塞

在 RTX 5080 真机双流水线跑片过程中，暴露并原地修复了两项仅在真实硬件/多文件清单下触发的根因缺陷（均严格遵守“一套事实、一个裁决、一个执行者”三条禁令）：

1. **多文件模型组件（MiniMax-H3 双 VAE）清单比对误报 `VARIANT_CATALOG_MISMATCH`**
   - **根因**：`backend/shine/comfyops/resource_resolver.py:62` 在按文件名核对 `binding.manifest["files"]` 与 `binding.catalog["components"]` 时，仅按 `component_id == "minimax-h3-vae"` 过滤，导致同一组件下的视频 VAE（`minimax_h3_video_vae_fp16.safetensors`）与音频 VAE（`minimax_h3_audio_vae_fp32.safetensors`）互相跟对方的 SHA-256 比对失败。
   - **修复**：在 `declared` 过滤条件中同时限定 `_name(item.get("relative_path")) == name`，使 `POST /api/v1/platform/capabilities/check` 对 `h3` (`h3-ref2va-int8-convrot` + `pdd8`) 真机核验直接返回 `status: "ready"`。
2. **Windows Job Object 命名正则漏配 `ShineM4Handoff-` 前缀导致真机启动受管 ComfyUI 报 `NAMED_JOB_ID_INVALID`**
   - **根因**：`backend/shine/comfyops/owned_process.py:20` 已支持 `Local\Shine(?:M4)?Handoff-[a-f0-9]{32}`，但底层 `backend/shine/comfyops/isolated_stage.py:73` (`WindowsJob.__init__`) 仍写死 `Local\ShineHandoff-[a-f0-9]{32}`，导致四阶真机创建 Job Object 时被拒绝。
   - **修复**：将 `isolated_stage.py:73` 统一对齐为 `r"Local\\Shine(?:M4)?Handoff-[a-f0-9]{32}"`，四阶受管 ComfyUI 一次拉起并完成全部真机渲染与自动回收。

---

## 3. 全量自动化回归验证结果

### 3.1 后端 8 分片全量回归（286 个测试文件）
- 执行命令：`pwsh -NoProfile -File .\run_backend_shards.ps1 -WorkRoot F:\ShineTest\v2-shards`
- 日志目录：`F:\ShineTest\v2-shards\shards-20261120-180556-634`
- 分片结果：
  - `chunk-0`: `clean (exit 0)`
  - `chunk-1`: `clean (exit 0)`
  - `chunk-2`: `clean (exit 0)`
  - `chunk-3`: `clean (exit 0)`
  - `chunk-4`: `clean (exit 0)`
  - `chunk-5`: `clean (exit 0)`
  - `chunk-6`: `clean (exit 0)`
  - `chunk-7`: `clean (exit 0)`
  - `shards_with_failure_markers=0`（8 个分片全部 `exit 0`，零 `FAILED` / `ERROR`）

### 3.2 四阶与统一底座专项测试套件（9 个测试模块 / 26 项测试）
- 执行命令：`$env:PYTHONPATH="backend"; $env:CODEBUDDY_SAFE_DELETE_ENABLED="0"; backend\.venv\Scripts\python.exe -m pytest backend\tests\manual4\ -q`
- 结果：**26 passed (`exit 0`)**
  - `test_p1_platform_routing_and_isolation.py` (5 cases)
  - `test_p2_storyboard.py` (3 cases)
  - `test_p2_public_asset_center.py` (3 cases)
  - `test_p3_materials.py` (2 cases)
  - `test_p3_unified_h3.py` (2 cases)
  - `test_p4_production.py` (2 cases)
  - `test_p4_defects_and_readiness.py` (4 cases)
  - `test_p5_delivery.py` (2 cases)
  - `test_isolation_static.py` (3 cases)

### 3.3 静态检查与前端全量回归
- Ruff 静态检查：`backend\.venv\Scripts\ruff.exe check backend\shine backend\tests\manual4` -> **`All checks passed!`**
- 前端全量 Vitest：`cd frontend; npx.cmd vitest run --no-file-parallelism` -> **`Test Files 88 passed (88), Tests 453 passed (453)`**
- 前端生产构建：`cd frontend; npm.cmd run build` -> **`tsc -b && vite build` 通过（`exit 0`）**

---

## 4. 双流水线 RTX 5080 真机生产与实际出片证据

### 4.1 公共素材中心 Qwen-Image-2.1 真机出图与四阶晋升证据
在隔离数据根 `F:\ShineTest\v2-real-e2e` 上，四阶项目 `28e1b381-f94a-45c6-988e-8af270e037c1`（《V2 真实双集验收》）通过 Stage 2 `select-or-generate` (`mode="generate"`) 提交异步 `LibraryRenderTask`，由共享 Worker 在 RTX 5080 上调用 `Qwen-Image-2.1` (`qwen_image_2.1_int8_convrot.safetensors` + `qwen3vl_8b_int8_convrot.safetensors` + `qwen_image_2.1_vae_bf16.safetensors`) 生成真实 1024×1024 PNG 并完成人工晋升：

| 角色/场景 | `LibraryRenderTask` ID | 公共库 `LibraryVersion` ID | 四阶 `M4AssetVersion` ID | 字节数 | SHA-256 | 状态 |
|---|---|---|---|---|---|---|
| **人物：林晚** | `80f89060-1ad2-46c2-8497-6c0c34dd283d` | `eefde66e-e8dc-48bc-a5ff-76562127dae3` | `7936c2cc-888c-488d-85d3-4840621ce87b` | `1,693,778` | 见 `shine.db:library_versions` | `PROMOTED` / `CLEARED` (`is_mock=False`) |
| **场景：雨夜窄巷** | `a42054f1-39fa-42d3-a072-d88ee1110be7` | `afdecf44-3e57-4d3f-a942-f06709f6a025` | `d20c2adf-ee52-42a0-92fe-e58e75fd5e61` | `1,725,765` | `14a195947ca59fbbdcb784f924c608aebd1cfda68bd19af78dd131561cb1f4f7` | `PROMOTED` / `CLEARED` (`is_mock=False`) |

- **未晋升候选门禁实测**：对未晋升小样版本 `e970848b-3749-426e-8499-b53a8f1d7ed3` 调用 `POST /api/v1/manual4/projects/{pid}/materials/library/adopt`，接口如实拦截并返回 `409 LIBRARY_VERSION_NOT_PROMOTED`。

### 4.2 四阶 H3 自主制片线（`manual4`）2 集 3 镜真机 Ref2VA 出片与 FFmpeg 成片证据
- 生产运行 ID：`f2c423b2-a2c6-46aa-9561-3a8b36450718`（`execution_generation=2`，变体 `h3-ref2va-int8-convrot`，加速 `pdd8`，分辨率档位 `0.5` -> `672x384`）
- 跨线 GPU 租约自动协调：`worker-m4` 启动前自动退休空闲的七阶出图渲染器（审计事件 `IMAGE_RENDERER_STOPPED`），申请 `gpu-0` 租约 `bca823a6-0e89-41ad-8f69-7b3c4e095d81`，全部 3 镜完成后自动释放租约并停止受管 ComfyUI。

#### 逐镜真实 H3 渲染产物（`manual4`）
| 镜序 / 镜号 | `M4Job` ID | 衔接模式 & `previous_job_id` | ComfyUI `prompt_id` | 提交前落盘 `prompt_graph_sha256` | 输出 MP4 SHA-256 | 字节数 / 时长 | 物理路径 |
|---|---|---|---|---|---|---|---|
| `0` / `ep01-s01` | `f0ed09ae-95a7-40f2-986d-933875183957` | `cut` / `None` | `699d25f3-4de1-4c40-975b-284bc58d2afa` | `9d08377d9c42676894d64f70abb9319e6a17790a739c8533e24accf87eeaf6a2` | `eb91b107a1866530250abae115d6b9ae5d981ed67853f4e76b71dc4b2ee662d1` | `706,583` B / `4.458s` | `F:\ShineTest\v2-real-e2e\manual4\projects\28e1b381-f94a-45c6-988e-8af270e037c1\renders\eb\eb91b107a1866530250abae115d6b9ae5d981ed67853f4e76b71dc4b2ee662d1.mp4` |
| `1` / `ep01-s02` | `b85ed280-dc96-4d74-a38c-990973fe8ad7` | `last_frame` / `f0ed09ae-...`（提取上镜真实尾帧） | `85bf0c64-db73-450e-b563-b07f7a14b2a4` | `bbbd24a33af984bab2b8fa5b349a04b2b6b2e9e2c3f949ccedebd409f6b2c290` | `21dd8d35e3c4b9f773cb04e4bec1a5f52525fa0e674143f36e58fa4f3bcf7d5b` | `865,627` B / `4.458s` | `F:\ShineTest\v2-real-e2e\manual4\projects\28e1b381-f94a-45c6-988e-8af270e037c1\renders\21\21dd8d35e3c4b9f773cb04e4bec1a5f52525fa0e674143f36e58fa4f3bcf7d5b.mp4` |
| `2` / `ep02-s01` | `6cb699b9-1869-4f1f-bb61-302cad01ae10` | `last_frame` / `None`（**A17 跨集自动重置**） | `ef56e990-99e3-4fe0-b974-450de9df6e2a` | `382c137a91cef74755c65d336c43f82f5c8cf344fbb09441f7852610f93f1485` | `3e75525ce1add2cd0a514914797d5521bb83f769fd5a67bd17ce113a6afca76e` | `1,482,851` B / `4.458s` | `F:\ShineTest\v2-real-e2e\manual4\projects\28e1b381-f94a-45c6-988e-8af270e037c1\renders\3e\3e75525ce1add2cd0a514914797d5521bb83f769fd5a67bd17ce113a6afca76e.mp4` |

#### 逐集 FFmpeg 装配与人工验收成片（`manual4` Stage 4）
- **`ep01` 成片**（Delivery ID `fe4f44db-d57e-4992-8eb7-3f59e7982bb4`）：
  - 物理路径：`F:\ShineTest\v2-real-e2e\manual4\projects\28e1b381-f94a-45c6-988e-8af270e037c1\deliveries\95\95ce4ca76ad46d15c7ff97a4c8b0e9cf10faf0ed3be339f0002e3b36c8b80a55.mp4`
  - 字节数：`1,490,117` B；SHA-256：`95ce4ca76ad46d15c7ff97a4c8b0e9cf10faf0ed3be339f0002e3b36c8b80a55`
  - FFprobe 实测：`h264 672x384 24/1 fps` + `aac` 音轨，总时长 `8.937667s`，人工验收 `accepted`（`watched=True`）。
- **`ep02` 成片**（Delivery ID `15b59f9d-fbc0-4c2d-9df1-3ba318dfe971`）：
  - 物理路径：`F:\ShineTest\v2-real-e2e\manual4\projects\28e1b381-f94a-45c6-988e-8af270e037c1\deliveries\2f\2f4a5563534c932469ab13dcb7a42dabaaf16bfb8972907e3bf71d02c7f59b44.mp4`
  - 字节数：`1,355,960` B；SHA-256：`2f4a5563534c932469ab13dcb7a42dabaaf16bfb8972907e3bf71d02c7f59b44`
  - FFprobe 实测：`h264 672x384 24/1 fps` + `aac` 音轨，总时长 `4.479333s`，人工验收 `accepted`（`watched=True`）。
- **四阶全局就绪态**：`all_done = True`，四阶段 `storyboard / materials / production / delivery` 全部 `done`。

### 4.3 七阶 AI 导演线（`director7`）真机 Ref2VA 出片证据
在同一隔离环境 `F:\ShineTest\v2-real-e2e` 上，七阶项目 `08e9526b-4d91-45d0-abd3-c464fde2d832` 镜头 `7788147e-7e29-41aa-af29-9269795cb2df` 通过共享 Worker (`shine.worker`) 与同一套 ComfyUI 环境执行真实 H3 Ref2VA 出片：
- `Job` ID：`ca45bd72-d0fe-42d8-ab5e-745ed872b5cd`（状态：`PASSED`）
- `JobAttempt` ID：`0600c4cc-1874-4d2a-9c04-e5c037a4b3f1`
- ComfyUI `provider_job_id`：`1b92f95e-8915-4f32-8f90-674bce0003b7`
- 提交前落盘 `prompt_graph_sha256`：`1db44f2550386513b114207b6904dc26bdb3506b7c9129dea4457c8ac727e4d0`
- `MediaArtifact` ID：`15e2efe9-47a0-428a-b944-5a0d55e9fc45`（`is_mock = False`）
- 物理路径：`F:\ShineTest\v2-real-e2e\女巫1119\视频\current\女巫1119_视频_004镜_未分集.mp4`
- 字节数：`1,444,240` B；SHA-256：`849cdb34b885aa30508f1617a6bea88e8b542a97b8bd844f9ea9e9bd188f6674`
- FFprobe 实测：`h264 672x384 24/1 fps` + `aac` 音轨，时长 `4.458000s`，全帧解码 TechnicalQC `PASSED`。

---

## 5. A01–A22 专项验收逐项核对台账

| 编号 | 验收要求摘要 | 裁决 | 实测证据 |
|---|---|---|---|
| **A01** | 一次基础配置双线共享，无需二次配置或重下权重 | **PASS** | `GET /api/v1/platform/environment` 与 `/api/v1/manual4/system/status` 共享同一收编实例 `22ea7c4e-316c-5ff3-a4aa-632186c81a5e` (`F:\AIGC2026\...\ComfyUI`) |
| **A02** | 按配方能力预检五态返回，不支持加速档明确拒绝 | **PASS** | `POST /api/v1/platform/capabilities/check`：`h3+pdd8 -> ready`，`image -> ready`，`h3+turbo4 -> unsupported` |
| **A03** | 单卡 GPU 跨线原子互斥，忙时返回 `409 GPU_BUSY_OTHER_LINE` | **PASS** | 七阶持有 `h3:d7-mutex-drill-*` 时，四阶 `acquire_m4_gpu_lease` 立即报 `409 GPU_BUSY_OTHER_LINE`；释放后四阶立即获锁 |
| **A04** | 租约释放推进 `physical-resource-epoch`，长任务后台续期，死进程对账 | **PASS** | 真机验证 `physical-resource-epoch` `589 -> 590`；四阶 4.5 分钟真机连续渲染期间后台心跳续期正常，结束后租约 `RELEASED` |
| **A05** | 四阶上传与生成统一进入公共素材中心 `library_*` 台账 | **PASS** | `林晚` (`eefde66e-...`) 与 `雨夜窄巷` (`afdecf44-...`) 均落入 `shine.db:library_versions` 并映射至 `M4AssetVersion` |
| **A06** | 仅人工晋升且权属合规版本可作为生产主素材；撤销权属立即阻断 | **PASS** | 未晋升小样 `e970848b-...` 采纳被 `409 LIBRARY_VERSION_NOT_PROMOTED` 拒绝；`REVOKED` 权属在 `test_p2_public_asset_center.py` 与 `readiness.py` 阻断 |
| **A07** | 四阶 Qwen 出图改为异步队列（`LibraryRenderTask`），禁止 HTTP 同步冷启 | **PASS** | `select-or-generate` 返回 `status="QUEUED"` 与 `task_id`，由共享 Worker 异步完成并经 `/materials/tasks/{id}` 同步 |
| **A08** | 历史四阶素材无损回填，严禁将 Mock 资产晋升入公共库 | **PASS** | `backfill_m4_asset_to_library` 与 `test_p2_public_asset_center.py` 实测通过 |
| **A09** | >12,000 字长文本分块 LLM 抽取，保留跨块 `source_span` 偏移与 `ambiguities`，严防 `127.0.0.1.evil.com` SSRF 绕过 | **PASS** | `is_loopback_url` 严格解析 hostname；`test_p4_chunked_llm_extraction_and_strict_loopback_guard` 实测通过 |
| **A10** | 分镜任何内容/资产/时长/画幅/衔接方式变更均递增 `shot.revision` 并作废已冻结提示词，保留原草稿供修订 | **PASS** | `M4Shot.content_sha256` + `update_shot_render_options` + `confirm_storyboard_plan` 实测通过 |
| **A11** | 严禁 T2VA 裸跑，Ref2VA 唯一门禁与槽位上限校验 | **PASS** | 纯音频/无图像绑定返回 `PROJECT_REQUIRES_REF2VA`；槽位上限在 `readiness.py` 与 `foundation/h3.py` 统一校验 |
| **A12** | 四阶人工冻结提示词一字不改直通 H3，禁止 AI 编译器静默改写或模板回退 | **PASS** | 提交前强制校验 `sha256(prompt_text.encode('utf-8')) == job.prompt_sha256` |
| **A13** | 落盘参数化终图 `prompt_graph_sha256` 在 `/prompt` 之前，外部 `prompt_id` 紧随其后 | **PASS** | 真机 `m4_job_attempts` 三镜 `submit_intent_at < submit_ack_at`，`prompt_graph_sha256` 与 `prompt_id` 完整落库 |
| **A14** | 帧数 `17k+5`（24fps）与分辨率 16 像素对齐唯一实现 | **PASS** | `foundation/h3.py` 复用 `h3/output_frame.py`；真机 4s 镜头渲染 `107` 帧 (`4.458s`)、`672x384` |
| **A15** | Worker 认领原子化，参考读取/尾帧提取/ComfyUI 上传提交全部移出 SQLite 写事务 | **PASS** | `_claim_next_run` 使用原子 `update(...).where(status=="PENDING")`；全部磁盘与网络 I/O 均在事务外执行 |
| **A16** | 支持 `PAUSE_REQUESTED`、单镜重拍新鲜度校验与前序源校验 | **PASS** | `test_p3_unified_h3.py` 实测 `PAUSE_REQUESTED -> PAUSED` 及 `409 M4_CONTINUITY_SOURCE_MISSING` |
| **A17** | 跨集边界重置 `last_frame` 与 `previous_job_id`，严禁跨集串尾帧 | **PASS** | 真机 `ep02-s01` 虽设 `last_frame`，`previous_job_id` 在第 2 集边界自动重置为 `None` |
| **A18** | 崩溃恢复与不确定提交对账兼容真实 ComfyUI 5 元组 `/history` 与 `/queue`，保留 `NEEDS_REVIEW` | **PASS** | `foundation/h3.py:reconcile_submission` 及 `test_p3_unified_h3.py` 实测通过 |
| **A19** | 成片装配仅选当前修订非过时且优先真实非 Mock 镜头；去重保留 `acceptance`；装配后技术质检与播放 SHA 校验 | **PASS** | `test_p4_delivery_freshness_prefers_real_and_detects_broken_sha` 与真机双集成片装配验收通过 |
| **A20** | 静态导入隔离与跨线 API 越权拦截 | **PASS** | `test_isolation_static.py` 全绿；`api_asset_center.py` 对 `manual4` 项目越权调用返回 `409 PROJECT_IS_MANUAL4_NOT_DIRECTOR7` |
| **A21** | `manual4.sqlite` 损坏隔离降级，前端顶栏切项目自动续接阶段、隐藏七阶专属导航、非 404 错误保留 `workflowKinds` | **PASS** | `test_p1_platform_routing_and_isolation.py` 与 `frontend/src/test/manual4Routing.test.tsx` 实测通过 |
| **A22** | 用户生产数据零破坏，全量回归与双产线 RTX 5080 真机出片全部通过 | **PASS** | `D:\Shine\data\{shine.db, manual4.sqlite}` 字节与 SHA-256 与 P0 备份 100% 一致 |

---

## 6. 用户生产数据完整性核验与遗留边界说明

1. **`D:\Shine\data` 完整性核验（100% 无损）**：
   - `D:\Shine\data\shine.db`：`32,051,200` 字节，SHA-256 `8c65fbab218fe3c03f0f657030be50b6a9f7327f63aea8449af4267a5b670792`（与 P0 备份完全一致）。
   - `D:\Shine\data\manual4.sqlite`：`356,352` 字节，SHA-256 `2bf978d36a510dfbb006dc93161e07d88c6a6f5eac94ae3405791270a49aeb7e`（与 P0 备份完全一致）。
2. **可选模型与已知边界说明**：
   - 本机可选审片模型（`qwen3-vl-8b-fp8`、`qwen3-asr-0.6b`）与公共素材中心本地声音合成服务尚未下载完整权重，系统如实将其标记为 `advisory` / `PENDING_LOCAL_VALIDATION`，不影响双线 H3 Ref2VA 真实视频生产、Qwen-Image-2.1 真实出图及本地音频上传绑定。
