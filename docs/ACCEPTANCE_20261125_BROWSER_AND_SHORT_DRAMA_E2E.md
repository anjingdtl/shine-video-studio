# Shine Video Studio 全托管栈拟人化浏览器穿测与 AI 短剧生产能力验收报告（2026-11-25 午后轮）

- **执行基线**：`main`（基线 `git:1198101`，本轮附带前端 `Manual4App.tsx` 阶段保持修复与防回退单测，本地完成验证与提交）
- **运行时环境**：`scripts\start_shine.ps1 -Provider comfyui` 拉起完整托管栈（API `127.0.0.1:8000` + Web `127.0.0.1:5173` + Supervisor `127.0.0.1:8790` + 七阶 `worker` + 四阶 `worker-m4` + ComfyUI 托管 `--extra-model-paths-config shine-style-loras.yaml`）
- **隔离验收数据目录**：`F:\ClaudeWorkSpace\projects\shine-video-studio\.local-acceptance\v3-p4-20261122`（启动前已备份原 `shine.db*` 与 `manual4.sqlite*`，验收结束后由 `scripts\stop_shine.ps1` 清洁停机并释放全部 GPU/端口资源）
- **真实硬件**：`NVIDIA GeForce RTX 5080`（16303 MiB）+ 本地 MiniMax-H3 + Chromium 真实浏览器界面直操（1440×960）

---

## 一、本轮解决的先前未验证项

相对前序隔离轮报告 [ACCEPTANCE_20261125_STYLE_LORA_ASSEMBLY_V1.md](ACCEPTANCE_20261125_STYLE_LORA_ASSEMBLY_V1.md) 第七节，本轮在全托管栈 + 真浏览器 UI 下额外关闭了以下两项：

1. **三 LoRA 真机串联实跑（已关闭，PASSED）**：
   - 检查宿主 `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI\models\loras\MiniMax-H3\*.safetensors` 头信息后，确认磁盘上除 `h3-realism-people`（`acc52960...`）与 `H3 Turbo V4`（`7098acf3...`）外，`minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy_resized_avg_rank_21_bf16.safetensors`（`3a069f26...`, 314,878,200 B）同为有效的 `diffusion_model.blocks.*` MiniMax-H3 ComfyUI LoRA。
   - 在四阶段页面 UI 上通过「路径导入」纳入上述两枚本地 LoRA，并将三枚 LoRA（`0.45 + 0.20 + 0.15`）同时挂载于 `ep01-s03`，在 RTX 5080 上完成真实 GPU 串联渲染（`job 02c91433`，`run 733d42ef`），输出 `7.998s / 672×384 / 2,331,432 B`，`ffmpeg -v error` 零报错。
2. **完整托管栈与浏览器全流程验证（已关闭，PASSED）**：
   - 未跳过任何托管启停或 Worker 调度链路：API、Web、Supervisor、双 Worker 与 ComfyUI 通过 `start_shine.ps1` 真实协同，动态生成 `comfyops/setup/shine-style-loras.yaml` 而不改写钉扎的 `managed_recipe.json`；并在 UI 顶部导航执行「停止引擎 -> 启动引擎」后确认全部 LoRA 仍保持 `ComfyUI 已可见`。

---

## 二、七阶段 AI 导演工作流拟人化全流程穿测实证

- **测试作品**：`星雨茶馆LoRA穿测`（`project_id = 0c5708a2-e367-4283-abd5-f5abae207e37`）
- **出片规格**：`0.5×` 缩样（`672×384`）、`24fps`、`pdd8` 8 步加速、关闭二采与高清放大。

| 阶段 | UI 操作与实际现象 | 实测结果与核验证据 |
|---|---|---|
| ① 创意与剧本 | UI 创建作品 -> 入口 A「保存为创意简报」->「确认这一版」-> remote consent 弹窗确认 ->「生成剧本」-> 8s 生成 `星雨茶馆LoRA穿测-创意版.txt` (`3bdc8fc1`) -> 68s 完成「后台提取并归并总体大纲」-> 确认大纲 | **PASSED**（入口 A 畅通；发现弹窗仍用原生 `window.confirm`，而大纲提取使用内联 `ScriptTextConsent` 面板，建议后续统一交互形式） |
| ② 剧本统筹 | 设 1 集 / 目标 24s / 单镜上限 8s ->「生成分镜书」触发 `409 EPISODE_PLAN_DURATION_DEVIATION`（实测 45.0s vs 窗口上限 30.0s）-> 点击「接受偏差并确认分镜书」冻结 Style Bible v1 | **PASSED**（偏差护栏与显式确认生效，Style Bible 成功冻结） |
| ③ 镜头拆解 | 默认 `MiniMax-M3.1-Flash` 多次失败（含契约拒止 `PLACEMENT_NOT_IN_CAST` / `DIALOGUE_TOO_FAST_TO_SPEAK`、`LLM_FORMAT`、`VISUAL_TEXT_MUST_BE_PROMPT_LANGUAGE` 及 >300s `LLM_CONNECTION`）；在 `#d/setup` 将剧组模型切换为 `aliclound-DeepSeek` (`deepseek-v4.1-flash-1017`, `profile_id=61648e7a-9b3d-489b-94d5-59479c5445ef`) 后点击「重试这一场」 | **切换模型后 39s 一次落地**（`第 1 集 · 已确认（v2，7 镜）`，单镜时长 `7.5s/6.5s/7.5s/8.0s/8.0s/7.0s/7.5s`） |
| ④ 拍摄素材准备 | 生成 6 项清单 ->「确认决策并开始准备素材」：ComfyUI 在 RTX 5080 出图渲染 `场景 · 雨夜老茶馆` 并晋升；`林晚` 在 Minimax brief 下遇 300s 超时后直接通过行内上传控件补齐 `林晚`、`陈默`、`半张旧照片`、`铜怀表` | **PASSED**（`5 项素材 · 5 项已批准锁定`，7 镜全就绪） |
| ⑤ 导演包 + LoRA | `StyleLoraAssemblyPanel` 自动纳管 `电影化写实 LoRA (H3 Realism)` (`acc52960`)；UI 导入 `H3 Turbo V4` (`7098acf3`) 与 `H3 LightX2V 4Step` (`3a069f26`)；验证同哈希去重提示、中文触发词拒止、停启引擎后可见性保持；挂载 `H3 Realism @ 0.85` 点击开拍 | **PASSED** |
| ⑥ 视频生产 | 全无值守双 `ProductionSeries`（前 6 镜序列 `6e9a2558-1a9c-462a-9081-ed11b98bc293` + 尾镜 `e80c93a5-7187-4a8b-a813-98995ee60f37`）连续在 RTX 5080 完成渲染并自动释放租约 | **7/7 镜全部 `PASSED`**，耗时 `728s`，全部 `672×384 h264+aac`，`ffmpeg -v error` 均为空 |
| ⑦ 后期成片与成片库 | 交付单 `a3c81bed` 合成整集成片 `artifact 064d30a7-8d97-4f83-9c5b-2f8854206ea2`（`6,682,285 B`、`53.895996s`、SHA `d45a1f73108f5fc5...`）；在成片页勾选并「观看并确认 -> 确认接受这一版」 | **PASSED**（首页正确呈现 `星雨茶馆LoRA穿测 \| 5/5 项 \| 第 1 版 \| 7 镜 \| 1 个成片 \| 全部完成`） |

---

## 三、四阶段自主制片穿测、三 LoRA 串联与 UI 触发词重拍实证

- **测试作品**：`四阶段短剧LoRA穿测`（`project_id = f39b005c-47eb-4110-a979-9a2e87dabd2d`）
- **前序全阶完结**：粘贴 3×8s 短剧剧本 -> 冻结 `茶馆旧表重逢 · ep01 · 3 镜 · 24.0s` -> 本地上传并晋升 `[人物] 林晚`、`[人物] 陈默`、`[场景] 雨夜老茶馆`（`3/3` 就绪）-> 在共享 `StyleLoraAssemblyPanel` 挂载不同 LoRA 组合并批量冻结提示词、真机直拍成片（v1 `delivery_id = 4928d17d-45d0-4ec3-bfa8-a9c771e917c4`），验证未勾选已观看时的 `422 M4_ACCEPTANCE_REQUIRES_WATCHED` 拦截后正式验收为 `accepted`。
- **真机实测与补充 UI 触发词重拍矩阵**（全部 `672×384`、`h264 + aac`、`ffmpeg -v error = ''`）：

| 镜号 / 批次 | LoRA 与 UI 触发词配置 | 运行时间 / Job ID | 输出大小 / 时长 | 输出 SHA-256（前16位） | 关键核验结论 |
|---|---|---|---|---|---|
| `ep01-s01` (`b048ed48`) | **0 LoRA 基线**，无触发词 | `148s` / `6655e19c` | `1,886,158 B` / `7.998s` | `a0379bd4841116a9` | 零 LoRA 无阻滞、无额外节点 |
| `ep01-s02` 初拍 (`382fc7c8`) | **1 LoRA**：`H3 Realism @ 0.40` | `188s` / `5291cebe` | `1,932,155 B` / `8.000s` | `831d6902bc210f04` | 单 LoRA 真机出片稳定 |
| `ep01-s03` (`733d42ef`) | **3 LoRA 串联**：`0.45 + 0.20 + 0.15` | `128s` / `02c91433` | `2,331,432 B` / `7.998s` | `e702c35b142ec1cb` | **关闭 V1 遗留项 #2：真实 3-LoRA GPU 串联 PASSED** |
| 成片 v1 (`4928d17d`) | 三镜合成并勾选观看 `accepted` | — | `5,539,718 B` / `24.020996s` | `3099acdbce3f2edd` | 四阶段成片装配、字幕与观看验收链路通过 |
| `ep01-s02` 触发词重拍 (`e39f1f28`) | **2 LoRA + UI 触发词**：`H3 Realism @ 0.55` (`cinematic_noir_rain`) + `H3 Turbo V4 @ 0.25` | `108s` / `342019b1` | `2,040,097 B` / `7.998s` | `424501f9cb4b6b04` | 快照 `7f13e062` 实测写入 `\n\nStyle LoRA triggers: cinematic_noir_rain.`，`source_prompt_sha256` (`dfd012a6...`) -> `prompt_sha256` (`63cfa39c...`)，v1 已验收成片 `4928d17d` **完整保留未被覆盖** |

---

## 四、穿测定位并修复的真实前端缺陷与收尾校验

1. **缺陷现象与根因**：在 `四阶段短剧LoRA穿测` 全四阶段完成后（`readiness.next_stage === 'delivery'`），若用户通过顶栏切换作品（使 `App.tsx` 将 `initialStage` 置空）后手动点击 `③ 排片生产` 查看或重拍单镜，由于 `useStudio.ts` 中 `report` 随父组件状态更新产生新函数引用，导致 `Manual4App.tsx` 的 `refresh()` 重新以 `silent=false` 触发；同时 `refresh()` 内 `if (!silent && !initialStage)` 每次都会无条件把 `activeStage` 重置为 `data.readiness.next_stage`，将用户强行踢回 `④ 产出成片`。
2. **最小化收敛修复**：
   - 在 [Manual4App.tsx](file:///f:/ClaudeWorkSpace/projects/shine-video-studio/frontend/src/manual4/Manual4App.tsx) 引入 `onMessageRef` 解除 `refresh()` 对 `report` 引用的依赖，并用 `autoLandedProjectRef` 记录每个 `projectId` 仅首次自动落到推荐阶段；`selectStage()` 与首次渲染之外的手动刷新不再覆盖用户当前所选阶段。
   - 在 [manual4MaterialImport.test.tsx](file:///f:/ClaudeWorkSpace/projects/shine-video-studio/frontend/src/test/manual4MaterialImport.test.tsx) 新增回归用例 `Manual4App 在无 initialStage 时仅首次自动落到推荐阶段，手动切换页签及刷新后不再跳回`。
3. **最终验证结果**：
   - `npm.cmd run build`（`tsc -b && vite build`）：**通过**（`0` 错误，耗时 `1.58s`）。
   - `npx vitest run --no-file-parallelism`：**95 个测试文件 / 495 条用例全部通过**（`exit 0`）。
   - Live Chromium 对修复后的 Vite 服务实操确认：切到已完结的 `四阶段短剧LoRA穿测` -> 点击 `③ 排片生产` -> 挂载 2 LoRA + 填写 `cinematic_noir_rain` -> 完成重拍 `108s PASSED` 全程稳定停留在 `③ 排片生产`。
   - 停机清场：`scripts\stop_shine.ps1` cleanly 停止全部服务（`api`、`worker`、`worker-m4`、`web`、`supervisor`），`shine.db` `resource_leases` 未释放/待核对租约为 **`0`**，端口 `8000` / `5173` / `8790` 无监听，`nvidia-smi` 显示 RTX 5080 显存已回落至 `1525 MiB, 0%`。

---

## 五、本机仍受物理条件限制的未验证项（如实记录）

通过 `#d/setup` 界面与本机磁盘核对确认以下项目属宿主物理资源未安装，无法伪造通过：

1. **可选视觉/语音复核（VLM / ASR）**：`#d/setup` 实测 `画面审片`（`qwen3-vl-8b-fp8`）显示 `模型文件 0/13（缺 9.9 GiB）` 且缺少 `interegular`、`lm_format_enforcer` 依赖；`语音核对`（`qwen3-asr-0.6b`）显示 `缺 10 个文件（1.8 GiB）`。对应旁路建议复核与 VLM 重校准保持未执行状态（按项目不变量作为旁路能力未阻断主出片）。
2. **W6A8（`ref2va-lowend-candidate`）实机零 LoRA GPU 出片**：`#d/setup` 实测显示 `本机没有这套权重：可选择后下载准备`（`NVFP4` 亦未下载）；本机物理缺失 W6A8 模型文件，因此仅验证了「W6A8 + LoRA 在 API 与 UI 侧被拒止」，W6A8 自身的真实 GPU 出片仍为受宿主权重缺失限制的未验证项。

---

## 六、项目是否已具备完整制作 AI 短剧能力 —— 真实评估

综合七阶段全自动流水线与四阶段高可控生产线双线真机实测，结论为：**已具备端到端制作真实 AI 短剧的生产能力**，各环节实际水位如下：

- **文学流程（条件就绪，强依赖模型结构化稳定性）**：七阶段从简报/剧本 -> 大纲 -> 分集/风格冻结 -> 5 工种逐场拆镜 -> 人工复核/局部重试完整闭环，四阶段人工分镜解析与逐镜原文冻结亦零阻滞走通。但实测表明默认 `MiniMax-M3.1-Flash` 在严格 `SceneShotBoard` JSON 约束与长时流式调用中容易出现格式/语言契约驳回或 >300s 连接中断；在 `#d/setup` 改配 `deepseek-v4.1-flash-1017` 等结构化表现稳定的模型后可顺畅成镜（39s 一次落地）。
- **素材流程（视觉 Ref2VA 就绪）**：从分镜需求自动提取、ComfyUI 本地出图生成候选与用户本地上传晋升锁定的唯一定标链路稳固，双线共享的 `StyleLoraAssemblyPanel`（自动发现、路径导入、SHA 去重、实时可见性投影、ASCII 触发词校验）运行可靠；注：四阶段测试场景未启用语音克隆/ASR 资产，相关重度口型对齐短剧仍依赖后续配音或安装 `qwen3-asr-0.6b`。
- **出片与交付（生产就绪）**：双线在真实 RTX 5080 下均已打通多镜无值守调度（7 镜连续渲染 `53.90s` 与 3 镜 `24.02s`）、0~3 LoRA 串联、触发词编译注入、不覆盖历史已验收成片的单镜重拍、FFmpeg 成片装配以及强制观看防盲签验收，且租约与显存回收完整无泄漏。
