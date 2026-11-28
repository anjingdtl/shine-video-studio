# 验收报告：故事逻辑、分镜衔接、物理状态连续性与 H3 2.8.0 升级（2026-11-28）

- 开发基线：`main` 分支 `8b4c7dd`（前置提交已入库，保留本机既有工作区，未修改 `copilot/` 业务模块，未执行 `reset`/`push`）
- 架构遵循：`AGENTS.md` 核心原则「一套事实、一个裁决、一个执行者」、不可变历史编译器版本铁律、8 分片并行回归及 2026-11-27 日常工作台原入口交还裁定

---

## 1. 针对前版 7 项成片缺陷的系统性架构优化

本轮优化全部落于通用剧作、分镜调度、连续性裁决与 H3 编译管线（零单一剧本硬编码）：

1. **故事逻辑与节拍收敛**（[crew_prompts.py](../../backend/shine/agents/crew_prompts.py)）
   - 60s 短剧节拍引导收敛为单条清晰起—承—转—合因果链（约 6–9 拍），禁止为凑镜拆碎；
   - 明确跨拍物理状态账本规范：骑乘/徒步状态、站位区、武器三态（刀口向上 / 刀尖插地 / 拔出）与已建立的背景军阵/人群均需按因果推进，禁止跳步或末尾蒸发。
2. **分镜编排与前景视差洁净**（[scene_director.py](../../backend/shine/agents/scene_director.py)）
   - 强制跨镜物理状态账本（对 `cut` 与 `last_frame` 均生效）：禁止徒步 ↔ 骑马无过渡瞬移、禁止已立阵前又重穿人群、禁止武器插地后未经拔出直接变刀口向上或未插地却凭空拔刀、要求全景/拉远镜显式保留已建立的背景军阵/人群；
   - 建立 `FOREGROUND PARALLAX HYGIENE` 红线：`foreground_parallax` 仅限环境固有元素（浮尘、战旗边缘、雨丝、木柱等），严禁写入马匹、武器刀杆或人物盾牌/身体部位；
   - 台词尾留白红线：要求末句台词 `end_s <= duration_s - 0.8`，避免话未说完硬切。
3. **唯一连续性裁决口升级**（[continuity_engine.py](../../backend/shine/agents/continuity_engine.py)）
   - 扩展武器家族正则（含 `modao / broadsword / spear / halberd / 陌刀 / 长刀` 等）并升级三态物理检测，新增 3 条确定性拦截码（Refusal）及精确修复指令（`REFUSAL_LABELS` 与 `_REPAIRS`，共 32 条）：
     - `CROSS_SHOT_PROP_STATE_CONFLICT`（插地→未拔出变刀口向上 / 刀口向上→凭空从土中拔刀，覆盖 `cut` 与 `last_frame`）
     - `CROSS_SHOT_MOUNT_CONFLICT`（同角色相邻镜徒步 ↔ 骑马无过渡瞬移）
     - `DIALOGUE_TAIL_TOO_SHORT`（末句台词距镜尾 `< 0.5s` 硬拦截）
   - 新增 `FOREGROUND_PARALLAX_CONTAMINATION` 非阻塞告警，标记马匹/武器/盾牌污染前景视差。
4. **表演窗尾留白保护**（[acting_director.py](../../backend/shine/agents/acting_director.py)）
   - `compute_pre_speech_budget()` 在镜头硬结尾处启用 `_SHOT_END_TAIL_S = 0.70s` 保护（镜内切子镜保留 `_SAFETY_TAIL_S = 0.15s`），确保 Acting Pass 绝不将台词平移挤入镜尾；提示词新增 Rule 9 强化物理状态承接与闭口收尾。
5. **核心道具自动绑定 + 离场角色过滤**（[services.py](../../backend/shine/services.py)、[api_film.py](../../backend/shine/api_film.py)）
   - 新增 `complete_prop_bindings()`（含 `陌刀 / modao / mo-dao / mo dao` 等双语别名匹配），在 `_land_shot()` 中自动为出现核心道具的镜头补齐已锁定道具主素材，严格遵循 `MAX_IMAGES (9)` / `MAX_TOTAL (12)`；
   - 在 `_land_shot()` 中过滤 LLM 在单人镜头 `asset_ids` 中多余夹带的离场角色 UUID，杜绝单人镜乱入第二主体。
6. **`H3PromptCompiler` 升级至 `shine-h3-compiler/2.8.0`**（[compiler.py](../../backend/shine/h3/compiler.py)）
   - 冻结 `2.7.0` 至 `LEGACY_270_VERSIONS`（所有 `<= 2.7.0` 历史请求保持逐字节等价回放）；
   - `2.8.0` 在场景参考定义中加入背景人群/军阵防蒸发保护（防止空景板导致全景/拉远镜清空画面），并在有对白镜头中明确要求末音节干净说完后闭口留韵。

---

## 2. 离线全量回归证据

- **新增单测**：[test_narrative_continuity_v28.py](../../backend/tests/test_narrative_continuity_v28.py)（5 用例：跨镜插地/拔刀矛盾拦截、跨镜骑乘/徒步瞬移拦截、台词尾留白拦截与 `0.7s` 表演窗保护、前景视差污染告警、核心道具 `modao / mo dao` 自动绑定、`H3PromptCompiler 2.8.0` 提示词规范 0 告警与 `2.7.0` 字节等价回放）— **5/5 通过**。
- **后端 8 分片全量回归**：`CODEBUDDY_SAFE_DELETE_ENABLED=0 .\run_backend_shards.ps1 -WorkRoot <测试根> -Shards 8`
  - 首轮 7/8 通过，`chunk-5` 检出 `test_v4_drama_layer.py:409` 一处旧提示词子串断言（已同步更新为新的 `focused at 2 to 3 cohesive beats`）；
  - 终轮复跑：**8/8 分片全部退出码 0，`files=328`，`shards_with_failure_markers=0`，墙钟 `751.4s`**，归档日志：`<测试根>\shards-20261128-194045-723`，`bt-*` basetemp 已自动清理。
- **静态检查 / 契约 / Alembic / 卫生检查**：
  - 所有改动及新增后端文件 `ruff check` **All checks passed**；
  - `generate_shine_copilot_contracts.py --check` 与 `generate_copilot_knowledge.py --check` **ok**；
  - `alembic heads` 单头 `f180f0010010`；
  - `test_workspace_hygiene.py` 通过；
  - 密钥防泄漏核查：仓库 diff 与本报告明文 API Key 出现次数 **0**。

---

## 3. 《香积寺大战》先文学审核后隔离真机投产验收（RTX 5080）

### 3.1 文学流程前置审核闭环
- 使用真实 DeepSeek LLM（`deepseek-v4-flash`，密钥仅在内存中注入隔离实例受保护密钥库）生成 1 场 8 拍 `DramaSceneScript` → 8 镜 `SceneShotBoard`（58.0s，`Board Warnings = 0`）→ `Acting Pass` → `shine-h3-compiler/2.8.0` 全量英文提示词（`review_h3_prompt` 8/8 镜 **0 告警**）；
- 经用户审核确认并按建议对 S03 `0.0s` 起手 `blocking.posture` 做微调（`blade angled upward before driving it down`）后方启动 GPU 拍摄。

### 3.2 四项真实 GPU 主素材（主角双五视图 + 场景四视图 + 陌刀道具）
- 同时开启 `character_sheet_mode=true` 与 `scene_sheet_mode=true`，本地 Qwen-Image 2.1 + `<mva>`（`qwen-image-2.1-lora-multiple-angles`）全部一次生成并晋升锁定：
  - **李嗣业**（`c5c7aa25-...`）：`3584 × 2016` **角色五视图定妆照**（`t2i_to_character_sheet_v2`）— [master_lisiye_5view.jpg](../evidence/20261128-narrative-continuity-v28/master_lisiye_5view.jpg)
  - **安守忠**（`8f8efc88-...`）：`3584 × 2016` **角色五视图定妆照**（`t2i_to_character_sheet_v2`）— [master_anshouzhong_5view.jpg](../evidence/20261128-narrative-continuity-v28/master_anshouzhong_5view.jpg)
  - **香积寺外旷野**（`23f98be8-...`）：`2752 × 1600` **16:9 场景四视图母版**（`t2i_to_scene_sheet_v1`，含 `front_wide / left_quarter / right_quarter / reverse_wide` 四张 `1344×768` 空景原图）— [master_xiangji_4view.jpg](../evidence/20261128-narrative-continuity-v28/master_xiangji_4view.jpg)
  - **陌刀**（`16cec87d-...`）：`1024 × 1024` 核心道具主素材 — [master_modao_prop.jpg](../evidence/20261128-narrative-continuity-v28/master_modao_prop.jpg)

### 3.3 真实 H3 渲染参数与逐镜产出核验
- **隔离环境**：`DataDir = <测试根>\acceptance\20261128-xiangji-continuity-v2\data`（独立 `SHINE_RUNTIME_ENV_PATH`），端口 API `18830` / Web `15190` / Supervisor `18800`，渲染器沿用规范安装 `<规范渲染器目录>`（端口 `8188`）。
- **渲染参数（严格按用户指定）**：
  - 分辨率：**0.8 倍横屏 `1088 × 608 @ 24fps`**
  - 二采（Second Pass）：**关闭**（`second_pass.enabled=false`）
  - 超清化（Upscale）：**关闭**（`upscale.enabled=false`）
  - 加速与模式：**PDD 8 步（`pdd8`）**，`h3-ref2va-int8-convrot`（Ref2VA only），编译器 **`shine-h3-compiler/2.8.0`**
- **实测结果**：
  - 项目 `ccbbd882-5b94-483d-a35a-3ec581c1e944`，Production Series `a824a8a9-bf80-437f-ae76-a4e4576af5a4`，总墙钟 **`1921.9s`（32.0 分钟）**，状态 **`COMPLETED`**，活跃 GPU 租约 **`0`**（22 条租约全部 `RELEASED`）。
  - **8/8 镜头全部 `PASSED`**，0 OOM，0 重试，单镜 `ffmpeg -v error -f null -` 8/8 零错通过。
  - **官方合成成片**：交付成品 `e13fa86c-6025-4cc6-9348-6de6aa3f4ea4`，实测 `1088 × 608 @ 24fps`，片长 **`60.48s`（1,451 帧）**，`24.30 MiB`（`25,476,641` 字节），SHA-256 `f764908cb02a...`，`ffmpeg -v error -f null -` **退出码 `0` 且 stderr 完全为空（0 告警）**。
  - 8 镜 24 帧抽帧总览：[xiangji_v28_8shot_contact_sheet.jpg](../evidence/20261128-narrative-continuity-v28/xiangji_v28_8shot_contact_sheet.jpg)；60s 成片 MP4、四项原尺寸主素材、文学复审包均留存于 `<测试根>\acceptance\20261128-xiangji-continuity-v2\evidence\`。

| 镜号 | 实测时长 | 运镜 / 视角 | 绑定参考（真实执行 Job 核验） | 对白数 / 尾 0.6s 均值声压 | 视觉与物理连贯性核验 |
|---|---:|---|---|---|----|
| S01 | 8.00s | `crane` / `front` | `<Subject 1>:李嗣业`, `<Subject 2>:安守忠`, `<Picture 3>:scene_angle:front`, `<Picture 4>:陌刀` | 0 / `-91.0 dB`（全静默） | 两军阵前对峙；李徒步持刀刃斜上，安骑黑马 |
| S02 | 8.00s | `push_in` / `reverse` | `<Subject 1>:李嗣业`, `<Subject 2>:安守忠`, `<Picture 3>:scene_angle:reverse`, `<Picture 4>:陌刀` | 1 / `-26.2 dB` | 过肩反打推至安守忠马背特写，末帧闭口稳坐马背 |
| S03 | 8.00s | `push_in` / `front` | `<Subject 1>:李嗣业`, `<Picture 2>:scene_angle:front`, `<Picture 3>:陌刀` | 0 / `-91.0 dB`（全静默） | 阵前空地（无穿人群）亲手将陌刀刀尖朝下贯入黄土 |
| S04 | 9.42s | `push_in` / `front` | `<Subject 1>:李嗣业`, `<Picture 2>:scene_angle:front`, `<Picture 3>:陌刀` | 2 / `-24.0 dB` | 双手紧按插地刀柄怒吼誓言，末帧完整说完闭口凝视 |
| S05 | 6.58s | `pull_back` / `front` | `<Subject 1>:李嗣业`, `<Picture 2>:scene_angle:front`, `<Picture 3>:陌刀` | 0 / `-91.0 dB`（全静默） | 0.0s 紧握插地刀柄 → 拔出地面 → 扬刃稳稳横举过肩 |
| S06 | 6.58s | `push_in` / `reverse` | `<Subject 1>:安守忠`, `<Picture 2>:scene_angle:reverse`（零离场主体） | 1 / `-20.3 dB` | 反打古寺背景，安骑黑马挥鞭举刀令铁骑冲锋，末帧闭口 |
| S07 | 6.58s | `track` / `front` | `<Subject 1>:李嗣业`, `<Subject 2>:安守忠`, `<Picture 3>:scene_angle:front`, `<Picture 4>:陌刀` | 1 / `-22.7 dB` | 李高举陌刀率唐军盾阵冲锋，安率黑甲骑兵迎面压来 |
| S08 | 7.29s | `handheld` / `front` | `<Subject 1>:李嗣业`, `<Subject 2>:安守忠`, `<Picture 3>:scene_angle:front`, `<Picture 4>:陌刀` | 0 / `-91.0 dB`（全静默） | 两军黄尘轰然对撞（两军全程在场），李徒步挥刀迎斩黑马 |

### 诚实边界与观察记录
1. 两位主角五视图定妆照（`3584×2016`）与四视图场景单角原图在全部 8 镜中零分屏/拼贴泄漏，服化道与陌刀形制高度一致。
2. 细微瑕疵观察：在 S04/S05 李嗣业单人镜中，因 Style Bible 全局 `shot_blocking` 含右翼骑兵描述，远景右侧出现了少量敌军骑兵轮廓，略偏近于中远距（不影响主叙事连贯性，已如实记录）。
3. 本轮未重做本地原生 VLM 审片正负样本校准（仍标明为「未校准」）。

---

## 4. 竣工清理与日常工作台交还

- **隔离栈停机**：经 `scripts/stop_shine.py --data-dir <测试根>\acceptance\20261128-xiangji-continuity-v2\data` 停机；`18830 / 15190 / 18800 / 8188` 监听数为 **`0`**，活跃 GPU 租约 **`0`**。
- **测试根清理与量化占盘**：安全卸除临时 `review-runtime` 联接后移除隔离 `data/` 与 `literary_data/`（保留 `evidence/` **67.3 MiB**，含 60s 成片 MP4、四项原尺寸主素材、24 帧对比图与文学复审包）；执行 `scripts/clean_test_workspace.py --work-root <测试根> --apply --prune-links`（错误 0 条）；清理后 `<测试根>` 总占用 **`652.7 MiB`**（远低于 `≤ 2 GiB` 预算）。
- **日常配置与原入口复验**：
  - 仓库根 `.env` SHA-256 逐字节未变：`931B8BBAAE2E803225ACD56A00E512329509EB00F5F99025B9A98F355C25D5C8`；
  - 经原日常入口恢复启动复验通过：API `:8000/health` 200（`comfyui`）、`worker_online=True`、`data_dir=<日常数据目录>`、Web `:5173/` HTTP 200、日常库原 **18** 个作品完整可见。
