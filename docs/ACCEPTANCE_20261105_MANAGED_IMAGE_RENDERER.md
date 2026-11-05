# 受管出图渲染器与浏览器闸门：本轮实现与测试报告

- 日期与时区：2026-11-05（北京时间，UTC+8）
- 分支 / HEAD / 基线：本轮提交落在 `main`；开工时 `HEAD = cfe3233`，本地 `main`
  领先 `origin/main`（5c4842f）16 个提交。
- 本轮主题：**把"出图渲染器"变成工作台自己拥有的进程**，并把
  "谁拥有渲染器"写进台账，让出图证据的等级可判定；同时修完浏览器 E2E 闸门
  最后一段的真实断点。
- 结论分层：**IMPLEMENTED + OFFLINE_VERIFIED**；
  `REAL_QWEN_VERIFIED` / `REAL_H3_VERIFIED` / `HUMAN_REVIEW` 仍是 `NOT_RUN`。

## 一、为什么先做这件事

上一轮的真实本机检测已经确认：既有 ComfyUI 安装在
`F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI`，RTX 5080 显存可用，
Qwen-Image-2.1 的三份权重就位，但**没有任何产品路径能把渲染器启动起来**：
出图运行时只绑定一个 `base_url`，而 `POST /setup/image-runtime/validate` 会
把任务丢给 Worker，Worker 直接向那个地址提交 `/prompt`。
也就是说：要么手工拉起 ComfyUI（工作台无法证明归属，事后也释放不掉，
AGENTS.md 2026-11-04 明确禁止），要么出图永远跑不起来。

本轮补上这条产品路径，并顺带发现并修掉一个**证据等级**漏洞：
替身渲染器（离线回环服务）产出的字节此前会被台账当成"真实出图验证"。

## 二、实现

### 2.1 受管出图渲染器（新增 `backend/shine/comfyops/image_renderer.py`）

启动纪律与 H3 受管渲染器一致：

| 步骤 | 做法 | 为什么 |
| --- | --- | --- |
| 配置先验 | 先读受管运行配置（`comfyops/setup/managed_recipe.json`），实例 id 必须与配方一致 | 渲染器不是随手起的进程，是本机登记过的那一个 |
| 意图先落库 | `AppSetting["renderer-startup:image:<instance>"]` 先写 INTENT 再拉进程 | 中断后可对账，不会盲目重启 |
| 所有权三证 | PID + OS birth identity + 命名 Job Object 成员 | 无法证明归属的进程一律不碰 |
| 队列先关闭 | `SHINE_COMFY_START_DRAINED=1` 启动，适配器自证身份后才 `resume` | 不是本安装的渲染器永远不接提示词 |
| 实测准入 | `probe_memory` + `evaluate_readiness`（显存 ≥ 12000 MiB、内存 ≥ 8192 MiB） | 量不到容量的渲染器不登记 |
| 可释放 | 记录写在 `renderer-startup:` 前缀下，既有释放路径能停它并回收显存 | ComfyUI 属于工作台，不属于机器 |

配套接口：`POST /api/v1/setup/image-runtime/start`、`POST /api/v1/setup/image-runtime/stop`；
`GET /api/v1/setup/image-readiness` 新增 `renderer` 段（状态、端口、PID、配方路径）。
界面「基础配置 → AI 素材出图能力」新增一行「本机出图渲染器」与两个按钮
（启动 / 停止并释放显存）。

### 2.2 谁拥有渲染器，决定证据的等级（本轮修掉的漏洞）

- 出图运行时只接受**受管实例**：`POST /setup/image-runtime` 对
  `payload.managed` 为假的实例返回 `IMAGE_RUNTIME_INSTANCE_NOT_MANAGED`。
- Worker 逐次记录 `renderer_owned`（attempt payload 与候选 provenance 都带）：
  受管实例 → 工作台自己启动渲染器 → `renderer_owned = true`；
  替身或外部渲染器 → `renderer_owned = false`。
- 第四层就绪度（`RENDER_VERIFIED`）只认 `renderer_owned = true` 的成功样本：
  `runtime.render_verified_families()` 多了一条硬条件。
- 由未归属渲染器产出的候选，晋升出来的素材一律带 `offline_only`，
  在 `promote()` 里与 mock 同等对待，不能算作已验证的生产素材。

### 2.3 等待显卡不是失败

图片项的执行顺序改为：**先取共享 GPU 租约 → 租约内启动渲染器 → 校验运行时 → 提交**。
租约被视频系列占着时按 `GPU_BUSY_RETRY_OR_QUEUE` 等待而不是失败；
渲染器不可用时按具名原因（`IMAGE_RENDERER_UNAVAILABLE` + 具体 code）拒绝。
释放动作在渲染器已经不在时按"没有模型驻留"确认，不留假的待对账记录。

### 2.4 浏览器 E2E 闸门（`scripts/verify_v13_web.py`）

上一轮留下的最后一段断点不是"实例计划没渲染成卡片"，而是**只批准、不执行**：
假实例计划批准后没有执行，就没有实例行，下一步生命周期计划被
`EXTERNAL_INSTANCE_PROTECTED` 拒绝，页面自然没有下一张待批准卡片。
本轮：

- 实例生命周期段在批准后**执行**假计划（"演练执行（不改动系统）"），
  并断言执行结果的状态；
- 快照 → 恢复用的 id 改为**快照 attempt id**（从计划 attempts 里取），此前传的是计划 id；
- 单镜头提交段对齐当前界面：`提交方式`→`生成方式`、`真实工作流 Profile`→`生成模板`、
  `预期输出宽/高`→`预期画面宽/高`，并先选`审片建议（可选）= basic`；
  为了让真实选项可用，该段用路由桩把 `system/status` 的 `production_mode` 置为 READY；
- 弹窗改由**一个可切换策略**的处理函数接管（Playwright 里第二个处理函数无法再处理
  已被处理的弹窗，原写法在"取消"路径上直接报错）。

## 三、测试报告

| 类别 | 命令 | 结果 |
| --- | --- | --- |
| 素材与出图定向回归 | `uv run --project backend --locked pytest -c backend/pyproject.toml backend/tests/test_material_preparation.py backend/tests/test_material_image_jobs.py backend/tests/test_material_director_flow.py backend/tests/test_material_brief.py backend/tests/test_material_scenes.py backend/tests/test_material_render_e2e.py backend/tests/test_material_render_ownership.py backend/tests/test_image_installation_catalog.py backend/tests/test_image_managed_renderer.py backend/tests/test_film_crew.py backend/tests/test_crew_prompt_contract.py -q` | **136 passed，EXIT=0** |
| 前端 | `npm.cmd test`（frontend/） | **18 个测试文件 / 130 个测试通过，EXIT=0** |
| 前端构建 | `npm.cmd run build`（含 `tsc -b`） | **成功**，vite 1609 modules，EXIT=0 |
| 静态检查 | `uv run --project backend --locked ruff check backend/shine backend/alembic backend/tests scripts/verify_v13_web.py` | **All checks passed** |
| 浏览器 E2E | `uv run --project backend --with playwright==1.62.0 python scripts/verify_v13_web.py` | **仍未转绿**：本轮已修完实例生命周期段与单镜头提交段，脚本推进到更靠后的位置后仍有一处待定位（本轮未跑到该步的最终断言） |
| 全量后端 | `uv run --project backend --locked pytest -c backend/pyproject.toml backend/tests -q` | **本轮被中止**（用户要求中止长跑任务），**不得据此声称全量通过**；上一轮基线为 1648 例 EXIT=0 |
| Alembic | 本轮未改表结构 | head 仍为 `f170a0010001`（无新增迁移） |

新增/改写的测试：

- `backend/tests/test_image_managed_renderer.py`（6 条，新）：配方缺失是具名阻塞；
  绑定的实例必须与配方一致；非受管实例被拒；没有记录时不谎称有渲染器；
  已归属的渲染器只复用不重启；启动键有长度上限。
- `backend/tests/test_material_render_ownership.py`（1 条，新）：受管渲染器产出的样本
  才能把类别提升到第四层。
- `backend/tests/test_material_render_e2e.py`（改写 1 条断言）：替身渲染器的字节
  `renderer_owned = false`，第四层保持关闭。

## 四、真实机验证：本轮做到哪一步

本轮**尚未**跑真实出图。已在本机只读确认的事实：

| 项 | 实测 |
| --- | --- |
| 既有安装 | `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI` 存在，`custom_nodes/shine_comfy_handoff`（`__init__.py` 3910 B、`barrier.py` 2624 B）与 `shine_h3_sampling_mode.py` 就位 |
| Qwen-Image-2.1 权重 | `diffusion_models/qwen_image_2.1_int8_convrot.safetensors` 7,256,783,064 B；`text_encoders/qwen3vl_8b_int8_convrot.safetensors` 9,350,798,360 B；`vae/qwen_image_2.1_vae_bf16.safetensors` 675,509,688 B —— 三份齐备 |
| H3 权重 | `diffusion_models/Minimax-H3/minimax_h3_ref2va_pruned_int8_convrot.safetensors` 20,970,379,616 B；`clip/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` 15,687,142,551 B；`loras/MiniMax-H3/h3-realism-people-t2v-i2v-r2v.safetensors` 131,229,656 B；`pdd_acc/MiniMax-H3-Ref2VA-Acc-8Step.safetensors` 1,372,450,680 B；H3 vae 在 `vae/MiniMax-H3(1)/` |
| 显卡 | RTX 5080，16303 MiB 总量、15030 MiB 空闲（上一轮探测） |
| 渲染器 | 8188 实例 **OFFLINE**（未启动，符合预期：启动必须走受管路径） |

即：收编所需的证据与权重都在本机，真实出图的**唯一缺口**是"还没点下那个受管启动"。

## 五、仍未做（不得按已完成计）

1. **真实三类小样出图**（角色 / 场景 / 道具）：需要走完
   收编（`existing-installations/inspect` → `verify` → `adopt`）→
   `setup/configuration` 生成受管配方 → `setup/image-runtime` 绑定 →
   `setup/image-runtime/start` 受管启动 → `setup/image-runtime/validate` 排三类小样。
2. **活体 `object_info` 校验**：第三层就绪度必须在真实渲染器上取得，本轮未取得。
3. **真实 H3 引用新素材**：依赖人工晋升后的真实主素材。
4. **人工评审**（构图与美术）：只能由用户完成。
5. **浏览器 E2E 闸门未转绿**：已推进两段，仍有一处待定位。
6. **全量后端回归与安装包**：本轮全量被中止，安装包未从本轮提交重建。
