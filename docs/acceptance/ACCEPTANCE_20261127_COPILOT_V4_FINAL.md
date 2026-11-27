# Shine Video Studio AI 管家 V4 最终验收报告（2026-11-27）

## 1. 验收范围、基线与用户裁定

- **代码基线**：`main` / `origin/main` 起始 commit `ccb5eeb6e83c9254940647b22658c056b7ebf003`（保留既有一并交接的 33 个未提交修改 + 2 个新文件，以及工作区 `.trae/`、`.vite/`）。
- **核心约束遵守情况**：
  - 受保护核心文件零改动：`git diff --exit-code -- backend/shine/comfy/client.py backend/shine/comfyops/preproduction.py backend/shine/quality/client_identity.py backend/shine/worker.py` 为空（退出码 `0`）。
  - 隔离验证全过程使用 `.local-acceptance/copilot-v4-final-20261127/data`（端口 `18822` / `15180` / `18792`），未触碰或恢复交接前已撤销的 `grant-df57a38d689d43449256157c`，未修改日常 `D:\Shine\data` 台账。
  - 真机服务：**真实 LLM**（`aliclound-DeepSeek / deepseek-v4.1-flash-1017`）+ **真实 ComfyUI**（本机 `NVIDIA GeForce RTX 5080 16GB`，`Qwen-Image-2.1` + `MiniMax-H3`），非 mock。
- **用户中途中止与裁定记录（2026-11-27）**：
  1. **Test B 实拍规格变更**：用户裁定将 B 轮自动拍片由原高配规格调整为 **`output: {"width": 672, "height": 384, "fps": 24}`**（0.5× 分辨率）、**`strategy: {"model_variant_id": "h3-ref2va-int8-convrot", "accelerator_id": "pdd8", "second_pass": {"enabled": false}, "upscale": {"enabled": false}}`**（8 步 PDD，关闭二采与 2X 精修）。原 `1344×768 + native20` 整集实拍记为 **`NOT RUN`（已被用户新规格裁定替代）**。
  2. **镜间衔接与色漂处置**：实拍 B 成片验收时用户与帧级核查发现第 2→3→4 镜 `last_frame` 接缝存在明显色调跳变与开场回溯抖动（见第 4 节）。用户确认：**先完成 AI 管家 V4 既定收尾与 `origin/main` 推送，并将实测帧级现象与三大根因如实写入本报告；后续单开专门轮次治理镜间衔接与色调连续性**。

---

## 2. 本轮实测暴露并修复的代码缺陷

| # | 缺陷现象与根因 | 修复文件与说明 | 覆盖单测 |
| --- | --- | --- | --- |
| 1 | `production_start` 合同缺少 `output`（宽/高/帧率）参数导致 AI 管家无法按 `672×384 @ 24fps` 开拍 | `backend/shine/copilot/declarations.py`（`contract_revision=3`）、`backend/shine/support/action_handlers/production.py`、`backend/tests/test_support_copilot_v2.py`（同步 `fake_start` 签名）、重生成 `copilot_contracts.json` 与 `knowledge_manifest.json` | `test_copilot_v4_final.py`, `test_support_copilot_v2.py` |
| 2 | `read_tools._read_original_task` 传入未知 `action_id` 时抛 `ActionUnknown` 导致 HTTP 500 | `backend/shine/copilot/read_tools.py` 捕获 `(ActionUnknown, ActionBlocked)` 并转为 `grant_contract.GrantError` | `test_read_original_task_converts_unknown_action_to_grant_error` |
| 3 | `allow_download=false` 时零下载 `setup_repair`（配方绑定对齐）被权限表连带拦截 | `backend/shine/copilot/task_grant.py`（`_capability_allowed_permissions` 允许 `configure_managed_runtime` 执行 `setup_repair`）、`backend/shine/support/actions.py`（若预估 `download_bytes > 0` 或存在 `missing_components` 且未授 `download_models`，在 `execute_under_grant` 中强制以 `GRANT_PERMISSION_NOT_GRANTED` 拒绝） | `test_allow_download_false_keeps_zero_download_setup_repair_and_blocks_downloads` |
| 4 | 非生产向图像整备/小样验证目标（含「不产出成片」否定表述）误触发 `H3 WorkflowProfile/Job/交付` 里程碑校验导致 Grant `FAILED` | `backend/shine/copilot/objective_verify.py`（新增 `_image_setup_verification` 读取权威 `image_readiness` 与 `setup_summary.capabilities.image` 的 `FILES_VERIFIED`/`INSTANCE_BOUND` 裁决） | `test_objective_verify_reads_image_readiness_for_image_setup_objectives` |
| 5 | LLM 提交 `full_production` 或勾选 `allow_gpu=true` 但漏传 `"test"` 分组时，`preview` 仅在 `allow_gpu=false` 剔除 `"test"` 而不为 `allow_gpu=true` 补回，导致 `material_generate`/`production_start` 不可用 | `backend/shine/copilot/chat_investigator.py`（`_validate_grant_proposal` 为 `full_production` / `"production"` 自动补 `"test"`）、`backend/shine/api_support.py`（`allow_gpu=True` 且含生产/素材/运行时分组时补回 `"test"`；`allow_gpu=False` 逻辑保持不变） | `test_full_production_and_allow_gpu_true_preserve_test_group_while_allow_gpu_false_strips` |
| 6 | 点击「继续推进拍片」执行 `material_promote` 抛 `AttributeError: module 'shine.comfyops.assets' has no attribute 'public'`，且 `approve_action` 未回滚半成品 flush 导致已写入的 `PROMOTED` 状态落库 | `backend/shine/materialprep/service.py`（L2040/L2058 改为 `from shine.api_preparation import Decision, public` 与 `return public(row)`）、`backend/shine/support/actions.py`（`except Exception:` 分支先执行 `db.rollback()`） | `test_approve_action_rolls_back_uncommitted_writes_on_handler_exception` |
| 7 | `declarations.py` 中 `material_promote` 的 `offline`/`evidence` 未标 `binding="executor"`，继承 `Decision.offline=True` 默认值把真实 Qwen-Image-2.1 主素材打标成 `offline_only=true`，直接导致 `evaluate_materials` 报 `MOCK_ASSET_NOT_PRODUCTION` 阻塞生产 | `backend/shine/copilot/declarations.py`（`offline`/`evidence` 加 `binding="executor"`，`contract_revision=3`）、`backend/shine/support/action_handlers/materials.py`（透传 `evidence`）、重生成契约/知识索引；另清理 `test_support_agent.py` 写动作白名单计数至 `40` | `test_promote_material_candidate_and_material_promote_action_succeed` |
| 8 | `health_check._gpu` 自建白名单 `{"running", "idle", "starting"}` 而 `worker_instances` 在线状态为 `'online'`，误报 `WORKER_OFFLINE (BLOCKED)` | `backend/shine/support/health_check.py` 复用系统唯一权威裁决 `shine.comfyops.service_validation._worker_online(db, settings)` | `test_health_check_uses_worker_online_authority` |

---

## 3. 真实 LLM + ComfyUI 浏览器端到端穿测结果

### 3.1 穿测 A：自主环境整备与真实 Qwen-Image-2.1 小样验证（`PASSED`）

- **测试项目**：`管家整备验证 · 暮色样板`（`d04de31d-3854-4a30-a242-04538425d77e`）
- **实测路径**：
  - **Round 2 (`grant-9a346bc119824d8486565a0e`)**：验证零下载（`allow_download=false`, `max_download_bytes=0`）调用 `setup_repair`（动作 `09e91bfb-3aec-431a-966f-fbb63447442f` -> 任务 `setup-b03d15913001`）完成配方对齐：`image` 状态从 `PENDING_RECIPE` 转为 `READY`（`INSTANCE_BOUND: false -> true`, `FILES_VERIFIED: true`, `config_revision: 6 -> 7`, `recipe_sha256: d7543e4adf94bd8d7522f24a50a0a9781aabe6f305a397a94d6db02288fc4730`, `blockers: []`）；随后执行真实 `Qwen-Image-2.1` 小样 `image_runtime_validate`（动作 `73a88972-50e9-4b6d-ab1e-6acd3a92ea62` -> 任务 `a24f9f5f-6b21-4d3f-9fa0-7a362fa12b43`）通过。
  - **Round 3 (`grant-737d81dac1c84154947dfa62`)**：验证 `objective_verify` 修复后，`deepseek-v4.1-flash-1017` 共消耗 `max_llm_tool_calls=4` 完成环境核查与真实 `image_runtime_validate`（动作 `3c141d3a-3de9-4a05-94d9-5f850158108a` -> 任务 `de312386-8359-4e16-a4a0-668060d4500b`），`max_download_bytes=0`，Grant 终态达成 **`SUCCEEDED`**（`MODEL_FINISHED_WITH_EVIDENCE`，`image_files_verified` / `image_binding_ready` / `image_sample_verified` 全 `VERIFIED`）。
- **轻量截图证据**：
  - [A-06-round2-preview.png](../evidence/20261127-copilot-v4-final/A-06-round2-preview.png)
  - [A-07-setup-ready.png](../evidence/20261127-copilot-v4-final/A-07-setup-ready.png)
  - [A-08-grant-succeeded.png](../evidence/20261127-copilot-v4-final/A-08-grant-succeeded.png)
  - [A-08b-grant-details-succeeded.png](../evidence/20261127-copilot-v4-final/A-08b-grant-details-succeeded.png)

### 3.2 穿测 B：全链路自主生产 + 审片暂停 + H3 刷新重连 + 自动拼装交付（`PASSED`）

- **测试项目**：`古堡暮色：管家实拍验证 · 复验`（`d9ee4e25-0b8f-4ad0-bd8f-627b77b3b0ea`，4 镜头 × 4.0s，`ep-01`）
- **授权与产出清单（Grant `grant-ba57ad884a984a9bbadf2654`）**：
  - **模型与预算消耗**：`aliclound-DeepSeek / deepseek-v4.1-flash-1017`；`budget_preset = full_production`，权限分组 `["inspect", "materials", "production", "test"]`，`max_download_bytes = 0`，累计工具预算消耗 `max_llm_tool_calls = 63`（剩余 `117/180`），`max_test_inference_jobs = 1`，`max_gpu_minutes = 240`（剩余 `120/360`）。
  - **审片检查点暂停与确认**：首次执行 `material_promote` 自动暂停于 `PAUSED_USER_DECISION (MATERIAL_REVIEW_REQUIRED)`，展示 2 张真实 `Qwen-Image-2.1` 候选图（宁岚 `30916fde-d804-4e56-839e-a7c803dd77ed`、古堡回廊 `7d790400-0975-4cec-9bc5-6478eb813fbf`）；人工点击「继续推进拍片」后，动作 `756adcb4-a7a9-435a-a36f-5a27c519792d` 与 `15dc9dd9-58f8-4068-bb9f-c24e73e2fc6b` 均一次 `SUCCEEDED`，晋升主资产 `42b10206-21d5-4936-ab12-13d34520a652`（宁岚）与 `eb4e4753-43ba-4165-9c26-5f67262a9875`（古堡回廊），均确认 `is_mock=false, offline_only=false`。
  - **导演包与真实 H3 生产**：自动构建导演包 `5c603775-0961-4a05-8d3e-a95d78e357f4`（`revision = 1`），动作 `191dce60-3d5b-42e3-8ec8-5ea7c751bf58` (`production_start`) 启动 `ProductionRun 02c3eab2-bdaf-43fb-b495-a0bacc2e00f8`（`2026-11-27T10:50:56Z → 10:57:31Z`，约 `6 分 35 秒`）。
  - **`WAITING_EXECUTOR` 刷新重连与零空转**：H3 渲染期间 Grant 挂起等待，浏览器整页硬刷新后直接重连回同一个 `grant-ba57ad884a984a9bbadf2654` 与 `02c3eab2-bdaf-43fb-b495-a0bacc2e00f8`，等待期间 LLM 剩余调用数锁定在 `160` 零增长、无重复派发。
  - **4 个真实 ComfyUI (`provider=comfyui`) 镜头台账**（策略均为 `h3-ref2va-int8-convrot` + `pdd8`，`second_pass.enabled=false`，`upscale.enabled=false`）：
    1. Job `5102ab22-e265-4542-945f-4cf91c4790aa`（Shot `54aba6cb`）：`PASSED`，`prompt_id=69f2a5fa-2405-4a75-b1b6-0fd1ce2d6d28`，`10:50:56 → 10:52:51`，产物 `e3222bc8-e8dc-4448-bb4f-644a39797d38`
    2. Job `6bd71d4c-12d9-4371-b4ba-37d7b58dccd8`（Shot `fc8c2d3c`）：`PASSED`，`prompt_id=cc8fc273-48a3-4f96-ad2d-2f604a64ebf3`，`10:52:52 → 10:54:20`，产物 `a2c53b16-e720-492f-aa46-b24b1f4529dc`
    3. Job `4ea9bc43-1797-460b-a647-9808d284b573`（Shot `6cfdf6e2`）：`PASSED`，`prompt_id=362bc7bc-ffef-4e48-b3d6-ca96eeedf0d3`，`10:54:21 → 10:55:55`，产物 `c06461d3-92f6-4da9-b901-edd1d7f0cd65`
    4. Job `f60a5209-ab5d-4955-a0b9-66c0f2171247`（Shot `ee9d5d0a`）：`PASSED`，`prompt_id=d87aaa88-c9fa-4125-9bbe-a4e0b66ff015`，`10:55:56 → 10:57:29`，产物 `696c0252-28fa-4f26-8ab1-7bd56cafb5f7`
  - **自动交付成片与 Grant 闭环**：交付单 `fea8fd5d-a09a-43ab-b8c6-c9944e4eda58`，MP4 产物 `e34bf967-3437-4c55-9bda-c759a51f5e26`（`sha256=c59e8152d214eee4d917f62ebde8babb7472e77b529a2c0a89f7e1551eb46837`，`1,924,459` 字节）；隔离库 `ACTIVE_LEASES = 0`；Grant 于 `2026-11-27T11:00:23` 达成 **`SUCCEEDED`**（`objective_verification.satisfied=true`，`episode_delivery=VERIFIED`）。
- **MP4 技术校验**：
  - `ffprobe`：`672×384`、`h264 (High)` / `yuv420p`、`24/1` (`24fps`)、`428` 帧、音画总时长 `17.854329s`（含 `aac LC` `48000Hz stereo` 音轨）。
  - `ffmpeg -v error -i <mp4> -f null -`：退出码 `0`，零解码报错。
  - 3×3 抽帧拼图：[B-06-mp4-contact-sheet.jpg](../evidence/20261127-copilot-v4-final/B-06-mp4-contact-sheet.jpg)（人物服装/特征、石拱回廊与夕照氛围识别清晰、无水印或黑帧）。
- **轻量截图证据**：
  - [B-02-pdd8-05x-preview-fixed.png](../evidence/20261127-copilot-v4-final/B-02-pdd8-05x-preview-fixed.png)
  - [B-03-material-review-pause-verified.png](../evidence/20261127-copilot-v4-final/B-03-material-review-pause-verified.png)
  - [B-04-h3-waiting-after-refresh.png](../evidence/20261127-copilot-v4-final/B-04-h3-waiting-after-refresh.png)
  - [B-05-grant-succeeded-delivery.png](../evidence/20261127-copilot-v4-final/B-05-grant-succeeded-delivery.png)
  - [B-05b-delivery-library.png](../evidence/20261127-copilot-v4-final/B-05b-delivery-library.png)

---

## 4. 实拍成片发现的已知质量问题（单列待治理项）

尽管 AI 管家流程与技术质检全线跑通，用户人工审片及帧级像素核查确认，本次 `672×384 + pdd8` 成片的 **第 2→3→4 镜（`last_frame` 连续镜）存在显著镜间色调跳变与开场回溯重演抖动**：

1. **实测帧级数据**：
   - 第 2 镜尾帧 `s2[106]` 平均 RGB `(70.6, 71.9, 76.7)`，第 3 镜开场 `s3[00]` 与之 `MAE=27.60`；`s3` 前 `0.00s–1.38s`（前 34 帧）持续向第 2 镜中段 `frame 35–36`（`1.46s–1.50s`，最低 `MAE=10.49`）贴合，形成开场倒带感。
   - 第 3 镜尾帧 `s3[106]` 平均 RGB `(68.7, 68.7, 72.5)`，而第 4 镜第 0 帧 `s4[00]` 骤亮至 **`(84.0, 82.6, 84.9)`**（`MAE=30.88`），随后后半段又跌暗到 `(39.9, 42.0, 47.6)`；`s4[00..33]`（`0.00s–1.38s`）逐帧对应第 3 镜 `frame 15 → 38`（`0.62s–1.58s`，最低 `MAE=5.87`），呈现明显重演前镜与色温漂移。
2. **定位的三大根因**（后续单开专门轮次解决，按用户裁定不在本轮展开改动）：
   - **Ref2VA 软参考冲突**：`last_frame` 镜头在传入尾帧 `<Picture 3>` 时仍并排传入明亮空镜 `<Picture 2>` 和角色图 `<Picture 1>`，8 步 PDD 下 `t=0` 被原始亮图与正文反复拉回初始亮度/姿态。
   - **连镜拆镜与布光提示词互相拉扯**：同一静立窗前场景连拆 2 跳 `last_frame`，第 2 镜尾（光线褪去）→ 第 3 镜（微光重回眉骨）→ 第 4 镜（收束变蓝灰暗部）形成亮度反转。
   - **拼装与质检缺少接缝回溯检测/裁切与色彩连续性校验**：`delivery_engine.py` 对所有 `last_frame` 镜头按 `in_s=0.0` 全段硬拼，无接缝相似度回溯对齐或跨镜色漂核验。

---

## 5. 全量回归与静态检查记录

| 检查项 | 执行命令 | 实测结果 |
| --- | --- | --- |
| 契约 & 知识索引一致性 | `backend\.venv\Scripts\python.exe backend\scripts\generate_shine_copilot_contracts.py --check && backend\.venv\Scripts\python.exe backend\scripts\generate_copilot_knowledge.py --check` | **PASSED**：`contracts ok: app=2.2.0 actions=40 knowledge=22 discovered=461` |
| Alembic 迁移头校验 | `backend\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini heads` | **PASSED**：单一头 `f180f0010010 (head)` |
| 定向后端门禁（离线 E2E / 剧组 / V4 终版 / V4 动作） | `$env:CODEBUDDY_SAFE_DELETE_ENABLED='0'; backend\.venv\Scripts\python.exe -m pytest backend/tests/test_offline_e2e.py backend/tests/test_film_crew.py backend/tests/test_copilot_v4_final.py backend/tests/test_copilot_v4_actions.py -q -p no:cacheprovider` | **PASSED**：`73/73` 用例通过（`test_film_crew.py` 38、`test_offline_e2e.py` 2、`test_copilot_v4_final.py` 27、`test_copilot_v4_actions.py` 6） |
| 后端 8 分片并行全量回归 | `$env:CODEBUDDY_SAFE_DELETE_ENABLED='0'; pwsh -File run_backend_shards.ps1 -WorkRoot F:/ShineTest -Shards 8 -KeepRuns 3` | **PASSED**：8/8 分片全绿（`wall=734.4s`，`shards_with_failure_markers=0`，总计 **321 模块 / 3,888 用例**，`chunk-0`~`chunk-7` 均退出码 `0`；日志归档于 `F:\ShineTest\shards-20261127-200319-913`） |
| 前端全量测试、类型检查与构建 | `Set-Location frontend && npx vitest run --no-file-parallelism && npx tsc --noEmit && npm.cmd run build` | **PASSED**：Vitest **100/100 文件（524/524 用例）**通过（`146.18s`）；`tsc --noEmit` 退出码 `0`；`vite build` 1662 模块构建成功（`1.65s`） |
| Ruff 静态检查 | `backend\.venv\Scripts\python.exe -m ruff check --ignore E501 backend/shine/copilot backend/shine/support backend/shine/materialprep/service.py backend/shine/api_support.py backend/tests/test_copilot_v4_final.py backend/tests/test_support_agent.py backend/tests/test_support_copilot_v2.py` | **PASSED (`--ignore E501`)**：`All checks passed!`；**注**：若全量跑 `ruff check --statistics backend/shine backend/tests`（含既往遗留代码），存在 150 条既有静态提示（145 `E501`、2 `F401`、1 `F841`、1 `I001`、1 `UP035`），本轮新写代码行零新增非 E501/超长问题 |
| 既定 `1344×768 + native20` 全清 B 轮实拍 | — | **`NOT RUN`**：用户于 2026-11-27 明确裁定 B 轮改为 `0.5× (672×384) + pdd8`，已按新规格完整实拍替代 |

---

## 6. 日常工作台配置交还与原入口复验（`D:\Shine\data`）

- **隔离栈清理**：`scripts/stop_shine.py --data-dir .local-acceptance\copilot-v4-final-20261127\data` 已停机，端口 `18822` / `15180` / `18792` / `8188` 监听数为 `0`，隔离库活跃 GPU 租约数为 `0`。
- **根目录 `.env` 完整性**：`SHA-256` 严格保持 **`931B8BBAAE2E803225ACD56A00E512329509EB00F5F99025B9A98F355C25D5C8`**（与开工前基线 `daily-env.resume-before.local` 一字节不差）。
- **原日常入口启动与复验（`启动 Shine 工作台.cmd`）**：
  - `API`：`http://127.0.0.1:8000`（PID `6732`，`health.status=ok`, `render_provider=comfyui`）
  - `Web`：`http://127.0.0.1:5173/`（PID `32676`，HTTP `200`）
  - `Worker`：PID `7240` (`worker`) + PID `21176` (`worker-m4`)，`worker_online=true`
  - `data_dir` / `SQLite`：`D:\Shine\data` / `sqlite:///D:/Shine/data/shine.db`
  - **日常作品核对**：`GET http://127.0.0.1:8000/api/v1/projects` 返回 **18 个作品**，与基线 `.local-acceptance/copilot-v4-final-20261127/daily-target-project-ids.local.json` 的 18 个作品 ID 集合比对 **0 差异（100% 一致）**，日常工作台已保持在线运行交还。
