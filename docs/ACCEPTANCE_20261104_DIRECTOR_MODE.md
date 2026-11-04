# 导演模式贯测与修复记录（2026-11-04）

本轮目标：让「导演模式」自己完成从装机配置到成片规格、再到角色一致性的闭环，并把
「音色参考」从开拍前提降为可选项。**本轮没有触发新的 H3 渲染**：所有真实数据核对
都基于 2026-11-04 已有的三镜成片与真实项目数据库，离线测试使用 Mock 渲染器。

## 1. 导演模式内的基础配置（新用户不再被迫切到专业模式）

- 新增导演模式页面 `#d/setup`：顶部常驻「基础配置」入口（有未完成项时带橙点），
  首启卡片内也有「打开基础配置」。
- 页面三块：
  1. **安装与下载**：复用 `SetupPanel`（环境检测、已有 ComfyUI 复用、下载缺项并
     自动配置、运行配置激活、可选的审片模型）。新用户没有作品时，页面内直接提供
     「给作品起个名字 → 创建并继续」，因为安装计划与生成方案按项目绑定；不会静默
     创建作品。
  2. **模型与生成方案**：复用 `SettingsPanel` + `LLMSetupPanel`（provider profile、
     密钥、文本工种路由、ComfyUI 连接、工作流 Profile 注册/验证）。
  3. **固定音色（可选）**：说明 H3 原生台词；逐项显示本机声音能力（未配置时标
     「可选」，不再展示裸错误码）；需要固定音色时引导到专业模式。
- 状态卡直接读 `/setup/llm-readiness`、`/system/status` 和已登记方案数量，缺什么
  写什么。

## 2. 成片规格：分辨率 / 纵横比 / 长度（H3 规范）

- `ProductionSeries` 新增 `output_spec`（迁移 `f160a0010001`）：`width`、`height`
  （256–2048，32 的倍数）、`fps=24`、可选 `clip_seconds`（4–15 秒）。
- 导演页「成片规格」：竖屏 9:16（768×1344）、横屏 16:9（1344×768）、方形 1:1
  （960×960）、自定义；单镜长度可选 4/5/6/8/10/12/15 秒，或「按分镜计划」。页脚
  实时估算成片长度与镜数。
- **不会假装改分辨率**：`width`/`height` 只有在该工作流 Profile 的 `parameter_map`
  显式映射时才生效；未映射时开始制作被拦下（`WORKFLOW_SIZE_NOT_MAPPED`），界面提示
  去基础配置点「补齐宽高参数」。补齐接口只在该图自身声明了
  `MiniMaxH3ReferenceToVideo.width/height` 时才写入映射。
- 内置 6 个工作流 JSON 已补 `width`/`height` 映射（`31.inputs.width` /
  `31.inputs.height`，与图内真实输入一致）；旧项目副本通过审计过的标准参数迁移补齐。
- 单镜长度会**改写选中镜头的 `duration_target_s`**（对白装不下、内部剪辑点装不下时
  按镜头名拒绝），并在提交前二次确认；历史成片会因镜头修订失效，这是预期行为。

## 3. 人物脸型对不上：根因与修复

真实项目（`苏镇歌舞团`）核对到两个独立问题：

1. **提示词里没有身份**：旧的 `subject_definitions` 对每个 `<Subject n>` 只写
   "approved subject"，详细描述里的人物也只有外观描述，模型无法知道哪张参考图是
   哪个人。
   - 现在每个 subject 写明「第 N 位出场人物」并强调「这张脸/发型/体格必须逐帧保持」；
     `detailed_description` 增加强制性的 cast order 段，明确「描述顺序 = 参考顺序」，
     禁止在两个 subject 之间换脸/合脸。
   - 镜头里列出的角色如果没有绑定人物参考，编译直接失败
     （`H3_CHARACTER_REFERENCE_MISSING`），不再生成一张凭空想象的脸。
2. **参考图槽位顺序 ≠ 人物出场顺序**：manifest 之前只按绑定 ordinal 排序，而 H3
   节点的 `ref_image_0..n` 是先 subject 后 picture；工作流里 subject 顺序也可能是
   导演列素材的顺序。真实项目第三镜的 `characters` 顺序是
   `[小美, 欣欣, 阿德, 小米, 小婉, 小溪]`，旧 manifest 的 `<Subject 1>` 却是别人。
   - `build_reference_manifest` 现在固定 family 顺序；
   - `order_bindings_for_cast` 在**应用分镜**和**每次渲染请求**两处按
     `spec.characters` 重排 subject 槽位（旧镜头也修）；
   - 实测：第三镜现在 `<Subject 1>=小美、<Subject 2>=欣欣、<Subject 3>=阿德…`，与该镜
     描述顺序一致（见截图 `11-identity-check.png`）。
- 导演页新增「角色参考对照（提交前核对）」：逐镜列出 `角色名 → <Subject n>`，
  有未绑定角色时高亮，用户可在花显卡前核对。
- 素材上传页：人物/音色用途时默认展开「人物名称」输入；上传后清空名字，避免下一张
  图继承上一个人名。

## 4. 音色参考改为可选

- `SHOT_REFERENCE_REQUIRED_ROLES` 只保留 `character`；新增
  `SHOT_REFERENCE_OPTIONAL_ROLES=("voice",)`，策略报告里写明 H3 原生生成台词。
- 导演提示词不再要求「每个镜头必须引用音色 master」，改为「只有需要固定音色时才
  绑定」；参考预算说明同步。
- 导演页音色标签写「加分项，不是开拍前提」，缺包时显示「未配置（可选）」。
- 未绑定音色时，音色会在镜头之间变化——这一取舍在页面上明确说明。

## 5. 浏览器贯测（真实 Chromium，隔离数据库）

脚本：`.local-acceptance/ui-round-20261104/walkthrough.py`，结果：
`.local-acceptance/ui-round-20261104/screens/`。

| 步骤 | 结果 |
| --- | --- |
| 空库首启 → 打开基础配置 → 安装页 | 通过，`01`/`02` |
| 模型与生成方案页 | 通过，`03` |
| 固定音色（可选）页 | 通过，`04` |
| 创建作品 → 我的作品 | 通过，`05` |
| 素材与风格 | 通过，`06` |
| AI 导演制作（成片规格 + 角色对照） | 通过，`07` |
| 成片中心 | 通过，`08` |
| 切到专业模式 | 通过，`09` |

真实项目（只读）复核：`10-real-series.png`、`11-identity-check.png`；画幅未映射时
的阻断提示：`12-spec-blocked.png`。控制台无 page error；唯一 404 为浏览器 favicon。

## 6. 测试

- 新增 `backend/tests/test_output_spec_and_identity.py`（12 项）：manifest family
  顺序、cast order、未绑定角色拒绝、voice 可选、尺寸未映射拒绝、尺寸进入请求、
  时长装不下对白按名拒绝、标准参数只映射真实存在的输入、老方案只补两个标准键、
  画幅真正写进 H3 节点输入、后续镜头从序列行读取同一规格。
- 更新：`test_h3_compiler.py`、`test_bindings.py`、`test_storyboard.py`、
  `test_alembic.py`、`test_migrations_windows.py`、
  `test_global_workflow_name_migration.py`、`test_production_mode.py`。
- 已通过：上述文件 + `test_offline_e2e.py`、`test_acceptance_status.py`、
  `test_quality_gates.py`、`test_repair_revisions.py`、`test_production_series.py`、
  `test_film_crew.py`、`test_comfy_*.py`、`test_delivery_*.py`、
  `test_director_setup_contract.py`、`test_v13_e2e.py`。
- 前端：`vitest` 114+1 项全部通过；`tsc --noEmit` 与 `npm run build` 通过；
  `ruff check shine tests` 通过。

## 7. 本轮没有声称的部分

- **没有重新渲染真实 H3 成片**：环境与显存未在本轮重新占用；所有画面结论来自已有
  成片与提示词/manifest 核对，不构成新的画质或人物一致性验收。
- **VLM/ASR 仍然不可用**：`final` 门的 `needs_review` 依旧如实保留，没有为了「自动
  完成」把它改成通过；成片可交付，但验收状态仍是 `pending_review`，需要人工确认。
- 修脸效果只做到「提示词与槽位确定性对齐 + 缺参考直接阻断」；模型是否真的保住每张
  脸仍需要下一次真实 H3 生成来验证。
- 横屏/方形画幅只在参数映射层面打通，未在真实 H3 上跑过对应分辨率。
