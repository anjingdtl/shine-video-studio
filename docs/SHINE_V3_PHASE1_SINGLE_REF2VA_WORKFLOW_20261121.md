# P1 建设方案：单 Ref2VA 工作流定义与逐镜最小执行图

> 状态：待本地 Agent 实施；前置：阅读总指导；后续 P2 才开放视频参考/更多可选节点  
> 开工审计基线：origin/main 13595adee3884ae22c33b82d0d8961124eb16291，实际开发以本地 main 最新事实差异为准。

## 1. 目的和非目标

目标：新生产用户只选择 H3 模型变体和制作策略，不选 2img、6img、1aud 等工作流名称；一份受审计母版/配方由现有图编译器根据每镜 N 图/N 音、采样选项生成最小 API 图。七阶段与四阶段共用同一个 H3 公共门面，旧配置不再是启动和生产必须齐备的多模板矩阵。

本阶段不实现未验证视频 loader、不重写 RelayVSR、不接电影 Skills、不改变用户上传素材规则，不删除历史工作流、数据库 profile 或任意冻结制片请求。

## 2. 基线实情和权威入口

- backend/shine/comfy/graph_compiler.py：compile_ref2va_workflow、CompiledWorkflow、图哈希、required_nodes、variant/accelerator 与第二采样配方。已具备动态图而非空白开发。
- backend/shine/foundation/h3.py：compile_h3_ref2va_graph 和统一执行门面；backend/shine/manual4/comfy/h3_adapter.py 已调用相同门面。
- backend/shine/strategy.py：Production Strategy 已代替七阶段工作流选择器；不要重造用户参数表。
- backend/shine/comfyops/mode_capabilities.py：现在仍依赖 config/workflows 静态名称推导基础节点；variant_binding.py、variant_readiness.py、setup_preparation.py 仍有与历史 profiles 关联的登记/核验路径。
- config/workflows：多个旧版 Ref2VA JSON 保留用于兼容、历史配方审计和低显存来源。
- models/variants：不能把低显存候选型号强行映射到 INT8 的一套权重/节点链。

## 3. 权威设计

### 3.1 单母版的准确含义

选一个现有权威代码出口：foundation/h3.py::compile_h3_ref2va_graph 委托 comfy/graph_compiler.py::compile_ref2va_workflow。只允许一个持续维护的 Ref2VA 图配方及版本身份；变体是该配方的受审核模型/节点参数化，不允许各变体另维护一整套独立、互相漂移的图生成器。若特定变体缺少已审计配方，保持明确不支持，不能用默认 INT8 顶替。

模板可用代码化分段 recipes；并不强制交付一个包含所有可选依赖的巨大 .json。默认 1 图、N 图自动仅存在所需 loader、H3 生成、解码和保存节点，图外选项绝不参加 node/weight readiness。保留 graph_sha256/recipe_sha256/参数映射及媒体上传适配器。

### 3.2 现有调用链收束

- 七阶段：导演包/冻结策略 → 当前镜头 bindings → RenderRequest → foundation/h3 的唯一编译入口 → 既有 provider/Worker。
- 四阶段：manual4 adapter → 同一个 foundation/h3 编译入口 → 既有 ComfyUI Worker / 提交回执。
- 不新建第二个模态路由、HTTP ComfyUI 客户端、状态机或数据库 profile 注册器；避免复制编译器到 manual4。
- 静态 JSON 改为历史/证据兼容来源；取消它们作为“新制作必须齐备”的全局要求，旧历史仍按冻结身份 replay，不能删除文件让回放失效。

### 3.3 统一选择与冻结

输入包含 variant_id、accelerator_id、second_pass、实际 reference manifest 和 ShotSpec；输出 CompiledWorkflow.graph / parameter_map / media_transfers / required_nodes / execution_identity，提交前保存 prompt/graph/strategy/asset SHA。工作流编译 deterministic；顺序稳定，重复同一冻结输入同一哈希。无参考图必须遵从现有 Ref2VA-only 拒绝，不回退 T2VA。

同一个“可用”判定用于新生产和设置 UI；旧 profile 漂移是 advisory，不能成为新拍基础门。当前变体的真实节点能力与权重仍有硬事实。

## 4. 具体开发任务

1. 建立 baseline diff：查 9 个静态 JSON 与编译器/变体的真正使用处，列调用关系，标注历史兼容与新生产必须的入口。
2. 选择并收束一套权威配方，删除新生产对静态 profile 名称的分支选择和重复参数拼装；仅保留证据/历史回放所需路径。不得让新动态编译器读多个不同静态图拼凑“统一”。
3. 把模式级核心节点从权威基本配方派生，裁决口与 setup_summary / mode_capabilities 一致；关闭音频、二采、PDD 时不得要求其专属节点。
4. 融合 VariantBinding、variant_readiness 对新动态配方的要求；旧数据库 profile 只用于冻结旧产物、只读兼容与用户现有已注册配置。
5. 核对七阶段、四阶段的调用、任务身份、资源租约、提交/UNKNOWN 恢复与输出 QC，确保不是 UI 显示同名但后台仍分叉。
6. 为默认 INT8 pdd8、native20，1/2/6/9 张参考图、0/1/3 音的合法组合提供确定性编译测试；P2 未解决的视频参考返回现有具名不可用，而非全局阻滞。
7. 保持历史 1.2.x—1.4.0 H3 提示词溯源不变；此阶段尽量不编辑 h3/compiler.py。

## 5. 测试矩阵

- 图编译：1图/2图/6图/9图；0音/1音/3音；13+ 张非法组合、无图片非法组合，输入顺序与 SHA 冻结；未用 loader 不存在，不留占位文件。
- 采样：pdd8 / native20 使用各自经验证节点，禁用 PDD 不出现 PDD 权重或节点。
- 变体：INT8 正向、候选 W6A8/NVFP4 没有已审计配方时准确返回不可用，绝不偷换 checkpoint。
- 双线：手动四阶段与自动七阶段对相同镜头得到相同核心图/身份，并使用同一 ComfyUI 事实。
- 历史：旧 profile、旧包、旧 job/attempt 与历史 hash 仍可读，旧 profile 文件在位可追溯。
- 真机：5080+现有 H3，实际 1图、N图、pdd8、native20 能完成非 mock 视频，技术 QC 通过；若部分能力未安装，标明 NOT_VERIFIED，不强行阻断其余能力。
- 防阻滞：缺一个没有被选中的 legacy profile、二采、RelayVSR 依赖时，基础新生产仍能开始。

## 6. 交付与验收

必须交付路径调用图（改前/改后）、唯一配方入口、被废弃的重复入口清单、旧兼容测试、图 SHA/节点列表比较、真机证据及剩余限制。更新用户可见的技术选择文案但不添加新设置屏或工作流下拉。

阶段通过标准：新镜头不需要注册一堆历史 JSON 就能投产；两个流水线共用 foundation/h3；没有第二检查器/执行器；基础 INT8 一镜真实可出；对低显存没有虚假通过。形成 P1 acceptance MD 与本地 commit，停下等待下一阶段指示。

## 7. 回滚

任何失效时优先还原新生产分流开关和单母版裁决，保留旧 profile 兼容；绝不删除历史资源或重置数据库。不得把 P2 视频参考功能和 P3 下载重构偷塞入本阶段。