# Shine V3 四阶段建设总指导书：单 Ref2VA 工作流 × 电影原生 Skill

> 状态：建设指导方案，非代码完成/真机验收报告  
> 编制：2026-11-21；基线：origin/main 13595adee3884ae22c33b82d0d8961124eb16291  
> 文档分支：docs/shine-v3-four-stage-single-workflow-skills-20261121  
> 项目：anjingdtl/shine-video-studio；适用：本地开发机 Agent 顺序建设，阶段间人工确认  
> 四份子方案：PHASE1 / PHASE2 / PHASE3 / PHASE4，文件位于本目录；本总指导高于子方案。

## 0. 最终用户体验和工程定义

用户面对的 H3 制片仅是一套 Ref2VA 工作流、一份项目级制作策略和可覆盖的逐镜需求；用户不再选择“2图/6图/1音/上采/超分”等不同工作流文件。相同的 ComfyUI 根目录、模型/节点目录、素材中心和 H3 制片底座同时供七阶段自动导演与四阶段手动制作调用。对于每一镜，根据冻结参考素材集合、用户策略和本机已验证能力，从唯一受审计配方自动编译最小 API 执行图。

“一个工作流”指一个权威定义/配方和一个可追溯的逐镜图编译出口，不要求把 H3、二采和 RelayVSR 三个重型模型塞入一张固定、全节点常驻的 ComfyUI JSON。RelayVSR 保持 H3 后置执行；物理分段不构成第二条业务流水线。

Skill 只进入已有影视创作 Agent 的知识/决策上下文，帮助做出可拍摄的动作、镜头、剪辑选择；不产生第二个独立制片编译器，不要求用户安装 Skill Runtime，不取代官方 H3 规则。

## 1. 最高铁律：一套事实、一个裁决、一个执行者

优先遵守根目录 AGENTS.md。开发时禁止用新检查解决检查过多、用新状态解决状态不一致、用新入口解决入口阻滞。

- 一套事实：素材 bindings / 已冻结 ShotSpec 与策略、模型变体 manifests、单实例 ComfyUI 台账、object_info 与资源收据；禁止为 Skill、统一工作流、四阶段再建镜像配置账本。
- 一个裁决：沿用 h3/mode_policy.py、foundation/h3.py、既有策略与资源能力裁决口；扩展原有权威实现，而不是叠加新的“统一检查器”。
- 一个执行者：已有 Worker / ComfyUI 调度 / ResourceLease / 受管渲染器 / Relay stage。新能力只有一条提交—回执—对账通道，UNKNOWN 禁止盲重投。
- 冻结和溯源：H3 规则包、编译器版本、策略 SHA、工作流图 SHA、素材 SHA 与历史镜头不可悄悄重写；版本变动必须升级身份，允许历史只读重放。
- 权限边界：不改动外部用户现有 ComfyUI 目录/自定义节点/权重；只读发现已安装能力，安装需要既有用户授权和既有整备执行入口。不得删除任何用户自定义文件。

## 2. 已核验 main 事实（不是计划中的新功能）

- backend/shine/comfy/graph_compiler.py 已有逐镜 Ref2VA 图编译；支持 native20、pdd8、可选二采；最多 9 image / 3 video / 3 audio，混合总数最多 12；目前 video_count 非零会以 VIDEO_REFERENCE_LOADER_UNVERIFIED 拒绝；不可臆造视频装载节点。
- backend/shine/foundation/h3.py 已是公用 H3 门面；manual4/comfy/h3_adapter.py 已委托此门面。不要建立第二个 H3 引擎或分叉四阶段。
- config/workflows 仍保有 9 个历史 Ref2VA 静态 JSON；backend/shine/comfyops/mode_capabilities.py 当前按历史基线 profile 推导核心节点；backend/shine/comfyops/variant_binding.py 与 variants.py 管理变体/配置绑定。
- backend/shine/strategy.py 已有 model_variant_id / accelerator_id / second_pass / upscale；RelayVSR X2 在 comfyops/relay_stage.py 是独立 GPU 阶段，同一 Worker 和资源租约。
- comfyops/setup_summary.py 是“设置 → 制作环境”的统一读视图；setup_reconcile.py、setup_preparation.py 已负责发现、收据、自动登记与整备。
- h3/compiler.py + h3/rulebook.py 是官方 Ref2VA 的唯一提示词编译与规则溯源；agents/crew_prompts.py 的影视角色已被增强；director_styles/ 下旧风格选择仍存在；h3/film_type.py 的真人/2D 动画模式是有效现有能力。
- docs/ACCEPTANCE_20261121_REF2VA_ART_QUALITY_V1.md 已完成工程与单镜 A/B，但未证明整体艺术质量改善；声音真机听审、VLM/ASR 等仍有证据缺口。

## 3. 执行顺序、边界和阶段交付

| 阶段 | 具体方案 | 应产出 | 阶段退出条件 |
|---|---|---|---|
| P1 单工作流收束 | SHINE_V3_PHASE1_SINGLE_REF2VA_WORKFLOW_20261121.md | 唯一权威工作流配方，旧 profile 兼容，七/四阶段共用编译器 | 默认1图和多图、两采样模式离线与本机验证，无新增设置门 |
| P2 选配节点/多模态 | SHINE_V3_PHASE2_OPTIONAL_NODES_MULTIMODAL_20261121.md | PDD/二采/X2开关统一、9图3视频3音频真实映射、按需资源闭包 | 0/边界/混合组合、开关关闭不加载依赖，视频参考有真机证据 |
| P3 整备和生产收束 | SHINE_V3_PHASE3_SETUP_PRODUCTION_CONVERGENCE_20261121.md | 唯一能力读视图和原整备动作贯通，错误可恢复，七/四阶段共用 | 基础能拍不被选配阻塞，已安装免下载，恢复/中断与回归全绿 |
| P4 原生电影 Skills | SHINE_V3_PHASE4_NATIVE_CINEMA_SKILLS_20261121.md | 受控 SKILL.md 目录、按需检索、五角色集成、旧风格新创作退出 | 真人/动画保持，官方 H3 不变，真机艺术 A/B 有实证 |

P1—P3 的工作流改造不得等待 P4 Skills；P4 不得通过偷偷插入额外检查链/同步队列达到“艺术提升”。每阶段验收后停下，不自动跨到下一阶段。

## 4. 不增加整备阻滞的严格合同

1. 启动和基础整备仅校验当前用户所选变体的最小可执行 Ref2VA 配方；可选模型、插件、视频输入、VLM/ASR、RelayVSR 等不得进入基础硬门。
2. 只有某镜明确需要的资源才进入该镜闭包；用户未启用的功能不检查、不下载、不加载、不参与生产拒绝。
3. 用户已选且该镜需要的功能若缺失，返回现有错误家族中的明确原因和既有“制作环境”修复目标，不允许静默换模型/降级/剔除参考。其他镜头照常推进。
4. 模型/节点/工作流已存在且经当前验证器核实的，复用现有安装与收据，自动登记，无须人工反复“找工作流/注册”。
5. 不以“所有可选组合都通过真机”为新机基础就绪条件；对未能实测的组合诚实标 NOT_VERIFIED，不虚构 READY。
6. 维持用户选择的每镜最长时长设置，不能将 5/8/10 秒固定为代码；CUT 的镜间与镜内语义各自保留。
7. 基础设定一次完成，两个流水线共享根目录和事实、只因调用方式不同而呈现不同 UI；不得增加第二份设置/下载/登记流程。

## 5. 版本/兼容/验收纪律

- P1 的唯一母版优先复用现有图编译器已审计配方，不强行删除 config/workflows 历史文件；将其退出新建生产候选集，但旧数据库、冻结单、手动已登记 profile、老请求仍可读可回放。
- 旧的 H3 prompt、rule pack、compiler 1.2.x/1.3.x/1.4.0 provenance 均保持可重放；不在工作流改造中修改对话、旁白/声音、历史 hash。
- 低显存 W6A8/NVFP4 变体只共享图“定义”，不假定所有变体支持同一节点配方；能力不足时明确候选/未知，禁止假冒用户4070真机结果。
- 每个阶段必须输出：BASE_SHA / AFTER_SHA、改动与删除项列表、三原则唯一责任映射、精确测试命令与日志、无新增阻滞矩阵、无法真机验证的项目、回滚办法、人工复核要点。
- 在本地 main 分阶段开发，不自动 push 或创建 PR，等待用户单独操作授权；文档分支为规划，不应直接合并方案到本地正在开发的 main。
- 所有回归按既有三层测试和 AGENTS.md 规定执行；可进行定向/完整后端分片、前端 TypeScript/Vitest/build、Ruff，真机需 RTX 5080 的真实 ComfyUI/真实权重/真实视频/真实日志证据。4070/5090vd2 未真机测试则明确未测试。

## 6. 本地 Agent 操作协议

先检查工作树、origin/main 与当前 HEAD；如本地 main 已有比文档基线更新的提交或未提交改动，以本地事实为准做 BASELINE_DIFF，不覆写用户更改。用 git fetch 获取文档分支，通过 git show origin/docs/shine-v3-four-stage-single-workflow-skills-20261121:docs/文件名读取总指导与本阶段子方案；不要 checkout/merge 文档分支进 main，不创建第二 worktree 除非用户明确指示。

按阶段实施、测试、验证、提交本地 commit、输出证据与遗留问题，然后停止。未通过证据禁止写“完成”。用户确认后再执行下一阶段。

## 7. 全局失败红线与回滚

任何出现：基础 Ref2VA 受可选资源阻断、两个流水线使用不同资源事实、镜头参考悄悄丢失、参考数 >12 被默许、历史请求重放哈希失效、重复启动/重复提交 ComfyUI、RelayVSR 与 H3 同时抢显存、AI Skill 执行未经授权代码、声音/外语旁白回归，都视为阶段未通过。首先回滚本阶段新路由或新创作选项，不重写历史台账、不删模型和用户资产；报告具体阻滞与下一步修复。

## 8. 外部参考边界

MiniMax-AI/MiniMax-H3 官方 H3 Prompt Writing Skill 是模型适配规范来源，已钉住的官方版本优先；社区 Skill 仅能作为审查后的一般电影方法来源。ComfyUI 的可选图节点与视频读取必须由实际已安装节点的 object_info、参数模式、真机输出验证，不能因 GitHub 示例有节点名就推定用户机器可运行。