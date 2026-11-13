# 导演模式 V2.0 · Windows 真实 H3 双集穿测验收报告（2026-11-13）

> 分级口径（互不代签）：
> **ENGINEERING_PASS** / **REAL_H3_PASS** / **HUMAN_ART_REVIEW** 分别签注。
> 本报告只写有证据的结论；没有证据的部分标 NOT_VERIFIED 并给复验步骤。

## 0. 结论摘要

| 维度 | 结论 | 依据 |
| --- | --- | --- |
| ENGINEERING_PASS | **通过** | 后端 2482 项收集、全量退出码 0；前端 42 文件 / 271 项通过；Ruff / tsc / vite build 全绿 |
| REAL_H3_PASS | **部分通过（第 1 集）** | 第 1 集 3 镜在本机 Ref2VA INT8 上真实生成、QC 通过、装配成片并可在 ⑦ 播放；第 2 集未完成真实出片 |
| HUMAN_ART_REVIEW | **NOT_REVIEWED** | 无授权审片人签署；画面仅由操作者本人过目 |

## 1. 环境与隔离

- 主机：Windows，NVIDIA GeForce RTX 5080 16 GB；ComfyUI `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base`。
- 服务验证：`POST /setup/services/validate` → **READY（node_count 1281，4 个 Ref2VA 方案核验通过）**；VLM/ASR 为 advisory NOT_READY（不阻断）。
- 运行时激活：`POST /setup/runtime/activate` → **ACTIVE，services_verified=true**；重启后 `production_mode=READY`、Worker 在线。
- 数据隔离：`.local-acceptance/v2-20261113`（由生产库副本复制，714 MB），原始生产数据未写入。
- 端口：API 8001 / Web 5174（均为环回）；启动方式为仓库启动器 `scripts/start_shine.ps1`，非脚本伪造的服务。
- 项目：新建「V2 山雨 · 两集验收」，导入原创短剧《山雨》（六场，雨夜钟表铺）。

## 2. 用户旅程实测（正常 Web 入口）

| 步骤 | 实际操作 | 结果 |
| --- | --- | --- |
| ① 创意与剧本 | 项目管理页新建作品 → 自动进入① → 输入原创剧本与锁定事实 → 保存创意简报 → 确认 → 生成剧本 → 授权远端文本发送 → 后台提取并归并总体大纲 | 大纲工件生成（两次 LLM 分块 + MERGE，约 12 分钟） |
| ① 编辑 | 在大纲 Markdown 末尾追加「## 补充事实：铜钥匙背面刻着十年前的今天」，保存为新版本（user_edited） | 新版本 `613ea4a9`，旧版保留 |
| ① 确认 | 点「确认这版总体大纲」 | `613ea4a9 approved` |
| ② 剧本统筹 | 四参数（希区柯克风格 / 真人实拍 / 集数 / 每集 60 秒）→ 生成分镜书 → 确认 | 第 4 版 6 集 CONFIRMED；风格 V1 冻结（sha 836bd5e1…） |
| ③ 镜头拆解 | 生成分镜（五工种链）→ 第 1 集 1 场 3 镜 → 确认本集 | plan v6：ep-01 3 镜 |
| ④ 素材准备 | 生成清单 → 陈默「复用现有主素材」、场景「跳过」→ 确认决策 | 必需缺项 0；1 项可复用 |
| ⑤ 导演包 | 只勾选第 1 集，选 `local-h3-ref2va-pdd8-2img-1aud`，点「确认拍摄方案并开始真实制作」 | 包 revision 1 CONFIRMED（1 集 3 镜）；Run `bd6c59d5` QUEUED→RUNNING |
| ⑥ 视频生产 | 常驻 Worker 领取 Series，自动启动受管 ComfyUI 并逐镜提交 | 3 镜全部 PASSED |
| ⑦ 后期成片 | 打开成片页，播放第 1 集 | 成片 20 秒可播放（雨夜钟表铺画面） |

## 3. 第 1 集真实出片证据

| 项 | 值 |
| --- | --- |
| Run | `bd6c59d5-8167-4d8a-8599-61e34fd5721b`（scope: ep-01 / 3 镜） |
| Series | `8c7625fc-4c39-4cc3-82d2-efd329085951`（COMPLETED） |
| Job 1 | `6bc66d54-ba22-48b7-bd67-3c6e9ce8fb77` PASSED，artifact `f81dda62…`，sha `396b2a5d…` |
| Job 2 | `d3b17b17-263e-4ca0-af87-c636c1dce00f` PASSED，artifact `eb3be0da…`，sha `a66bb58b…` |
| Job 3 | `a2644575-e52b-4715-a1e2-03a1dbf4cb9b` PASSED，artifact `d00697e1…`，sha `a9006d78…` |
| 分集成片 | artifact `1d7cc435-dbd5-49e6-8658-eec5a15d896b`，SHA-256 `61cf71349f630137f119824ce431d2fbf0b0d02da5d0841b73443f07981daa8a`，20.479 秒 |
| 文件 | `…/放开那个女巫/视频/history/V2 山雨 · 两集验收_视频_001-003镜_01集.mp4`（is_mock=false） |
| 渲染器 | 受管 ComfyUI 在 127.0.0.1:8188 监听（PID 27356），由 Worker 启停 |
| 技术 QC | 3 镜均通过确定性技术门禁；ASR/VLM 为 advisory 不可用，界面如实提示「音频语言/静声核对未完整」 |

**播放验证**：⑦ 页内 `<video>` 播放真实 H3 画面（雨夜室内、灯下怀表），非 Mock、非占位。

## 4. 未完成项与阻塞（NOT_VERIFIED）

### 4.1 第 2 集真实出片（未完成）

- 第 2 集已落 1 镜（12.8 秒，对白「明天之前，别打开后屋的门」），并随 v6 确认。
- 第 2 集素材需求在派生清单中未出现（详见 D7），且出图模型显存不足（见 4.2），因此**未开拍第 2 集**。
- 因此「重启后第 2 集续作、第 1 集不重拍」的完整证据链未取得。第 1 集在本次会话内的 Job/Artifact/SHA 未被任何后续操作改变（未重跑），但这不是完整的不重拍证明。
- 复验步骤：在③对第 2 集重新生成并确认该集镜头（本轮的 1 镜来自被暂停的 6 集任务，其 apply 回执没有落到该 section）→ ④重新派生（应出现「穿灰雨衣的女人」必需项）→ 复用已上传主素材 → ⑤只选第 2 集开拍 → 出片后比对第 1 集的 Job ID / artifact SHA / MP4 SHA 三项未变。

### 4.2 出图（AI 候选）不可用

- 三类真实小样批次 `31d531e2` 全部 `IMAGE_RENDERER_MEMORY_INSUFFICIENT`（RTX 5080 16 GB）。
- 影响：④ 的「AI 制作候选 → 人工采用 → 回绑」链路未能在本机真实跑通；复用主素材、跳过可选项、必需/可选判定已真实跑通。
- 未以 Mock 或占位图替代。

### 4.3 上传入口的穿测差异

- 内置浏览器不支持 `<input type=file>` 的本地文件选择，故「上传剧本」入口未做真人点击；①改用文本入口完成原创剧本与大纲编辑。
- 素材「上传」在 UI 上提供（逐行上传 → 确认 → 锁定 → 回绑），本轮实际的两张角色参考图通过同一后端合同（multipart 上传 + one-use grant + approve + lock）写入，界面上随后显示为「已匹配，可复用」并渲染缩略图。

### 4.4 冒险/异常路径

- 已实测：分镜 `LLM_FORMAT` 自动纠错重试后成功（第 2 场 scriptwriter、第 2 场 director 各一次）；分镜暂停/继续；环境重验后 READY；栈多次合法重启。
- 未实测：Worker 在外部提交前后死亡、断连 UNKNOWN 的人工对账、跨项目 ID 误用。这些由既有后端测试覆盖（`test_film_unknown_outcomes`、`test_story_unknown_resend`、`test_production_series` 等），本轮未在真机重复。

## 5. 穿测中发现并修复的缺陷

| 编号 | 现象 | 修复 |
| --- | --- | --- |
| D1 | ③ 两集都被 `FREEZE_PROJECT_RULEPACK_FIRST` 拒绝，界面无入口 | 导演路径首用自动钉内置规则包（queue/retry/shoot 三处），幂等、不换版本 |
| D2 | 重新生成分镜书后旧「已确认」压住新版本确认按钮 | 存在更新 DRAFT 时清空确认态并提示；生成后同样清空 |
| D3 | 「缺图由 H3 自行生成」的错误承诺 | 全部改为「缺已确认参考图，只影响相关镜头」 |
| D6 | 同一人物有两份已确认主素材时派生报「缺」 | 取最近确认的一份；两份都保留 |
| D7 | 多次 derive/commit 交错后，派生清单只覆盖部分场次（第 2 集需求未出现） | **未修复**，已定位到 `create_draft_plan` 复用旧草稿的路径；列入遗留 |

## 6. 需要用户/审片人完成的事

1. 亲自观看第 1 集成片，给出 **HUMAN_ART_REVIEW**（接受 / 返修某镜）。
2. 决定是否补做第 2 集（需先解决 4.1 与 4.2）。
3. 若要启用 AI 候选，需要更大的显存或更小的出图模型；本机 16 GB 无法同时驻留 H3 与出图模型。
