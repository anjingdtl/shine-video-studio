# P2 建设方案：按需节点开关、真实多模态 Ref2VA 与 RelayVSR 2X

> 状态：待实施；前置：P1 已提交和验收；后续：P3 整备收束、P4 Skill  
> 本阶段不新增 ComfyUI 调度器，不修改旧 H3 编译器历史提示词身份。

## 1. 用户需求的工程翻译

仅一个 Ref2VA 生产方案，用户可选择模型变体、PDD 8步加速是否启用、二次采样是否启用、2X 高清是否启用；每镜参考素材由已确认分镜和素材 bindings 自行挑选，图片、视频、音频数量动态确定。UI 可以显示三个简单开关，但后台必须生成每镜的最小资源闭包，不能将所有功能节点永久挂在一个启动必备的庞大执行图上。

限定容量：图片≤9，视频≤3，音频≤3，总参考素材≤12，并遵守至少1张图的当前 Ref2VA-only 模式政策。示例：9图+3视频合法，9图+3视频+3音频不合法；6图+3视频+3音频合法。上限是容量，不是每镜一定用满。上一镜导出作参考的图或视频同样占实际对应参考预算，不能隐藏超限。

## 2. 事实基线

- comfy/graph_compiler.py 的 ACCELERATORS 已有 pdd8/native20，turbo4 未验收；SecondPassSpec 与正经第二采样链已存在；IMAGE/AUDIO/VIDEO/TOTAL = 9/3/3/12；图片/音频装载已按数量编译；video_count 非零直接报 VIDEO_REFERENCE_LOADER_UNVERIFIED。
- foundation/h3.py 已对 H3ReferenceInput 的 image/audio/video 做准备及 SHA 核验；但编译器不具备真实视频 loader 的 media_transfers 和位置映射证据。
- strategy.py 已定义 accelerator_id、second_pass.enabled、upscale.enabled。首选沿用策略字段，不加一组新布尔状态和数据库状态机。
- comfyops/relay_stage.py 已有 RelayVSR 独立 GPU 租约、提交意图/对账/验收与音轨保留；二采在 H3 图内，X2 是 H3 之后的可选阶段，不能误认为 LoRA 或同时驻留。
- H3 官方规则由 h3/rulebook.py 钉版本，最终 Prompt 由 h3/compiler.py 独占输出；不要在此阶段为了视频 loader 重写提示词规则。

## 3. 有效策略决策（唯一）

### 3.1 选择和运行时的关系

- 作品/批次用户选择的 GenerationStrategy 是唯一上游用户事实；镜头的冻结参考 bindings 和分镜 ShotSpec 决定实际素材数量。
- 每镜生成 effective recipe：选中的变体 + 加速配方 + 可选二采 + 可选 X2 + image/video/audio manifest；冻结图编译身份与资源列表。不新建第二套“镜头策略配置表”。
- PDD 开=既有 pdd8；PDD 关=经审计 native20；二采关不生成二采模型补丁/第二采样/3D handoff；二采开使用已审计配方和真正第二次 sampling。
- X2 关则不入 RelayVSR 队列、模型无需就绪；X2 开则 H3 当前 shot 通过后，经同一 Worker ResourceLease 将该 shot 的已验收视频送 RelayVSR，回灌原音轨，核验输出严格 2 倍尺寸且 fps/时长/来源不变。
- 全部可选能力只查选中的本镜闭包；如选了不支持的组合，明确返回现有错误/缺能力事实，不能自动删某一素材或换模型、强改模式。
- 若用户原本明确选定“全片高清”，使用现有批次策略派生每镜 X2，不另造每镜新 UI 或第二份开关；将来允许逐镜例外时必须写回原 ShotSpec/strategy 的受控修订而非隐藏状态。

### 3.2 资源闭包示例

| 镜头 | 策略 | 参考 | 本镜需要的资源 |
|---|---|---|---|
| A 对话 | 8步开、二采关、X2关 | 2图1音 | 选中 H3 变体+PDD+图/音装载，不要求二采、视频 loader、RelayVSR |
| B 纯画面 | 8步关、二采关、X2关 | 1图 | H3+native20，不加载 PDD 权重或可选音视频节点 |
| C 动作延续 | 8步开、二采开、X2开 | 3图1视频 | H3/二采按序，释放 H3 GPU 租约后 X2；需视频 loader 实证 |
| D 复杂参考 | 8步开、二采关、X2关 | 6图3视频3音 | 总12合法，真实上传与映射都可追溯；按内存预算评估 |
| E 超限 | 任意 | 9图3视频3音 | 总15，必须在现有单一裁决口具名拒绝，仅影响本镜 |

默认不能让所有参考槽都有空白 placeholder 节点；只生成实际使用的 Loader / MiniMaxH3ReferenceToVideo 的 autogrow input 和绑定表。

## 4. 9图/3视频/3音频落地开发

1. 读已钉住 MiniMax-H3 官方规则与本机实际 ComfyUI 的 object_info，识别 MiniMaxH3ReferenceToVideo 的 ref_images、ref_videos、ref_video_audios、ref_audios 数据类型、索引及 required/optional 合同；提供真实证据截图或脱敏 JSON 摘要。
2. 针对视频参考明确 loader 节点、原生文件输入、视频帧/时长/fps 处理、必要的 audio sidecar 关联及 media_transfers；不得凭名称自行创造节点类或假设 VHS_LoadVideo 一定存在。
3. 将视频参考映射到原有 reference_manifest、媒体上传及 ComfyUI graph 编译；对应参考类型、标签、序号与本镜输入一致。视频用于动作参考还是“续接前镜”，按已有 continuation 事实区分；不能把动作示例冒充视频续拍。
4. 每镜输入按有序 manifest 创建真实 slot：图片 1..N、视频 1..M、音频 1..K，所有资源执行前验证 SHA、路径、时长和类型。编译器需要输出参考族的参数映射、upload adapter、graph SHA、recipe SHA、slot identity。
5. 仍使用已有按镜资产引用规则，声线音频只由用户认可的素材绑定；用户未授权的旁白、外语、人声背景素材不能被自动加入。二采只复用第一采样音轨，RelayVSR 不能重新生成对白。
6. 处理插帧、时间对齐、视频音频侧链需求时只在确认的 node contract 内实现，不能用未验证的“能连线”替代真机参考有效性证明。

## 5. 真实可选节点，不是伪开关

- 使用裁剪编译：未选节点不出现在最终提交 graph 及 required_nodes/required_components，权重不会下载或加载；优先于把所有模型节点保留在图里再猜测 bypass 是否真能卸载。
- 对激活的重型分支落实条件依赖：PDD 模型/节点，二采调度器+3D latent upscaler，RelayVSR x2 模型+专用VAE+本机节点，以及视频 loader。
- 先使用现有 variant compatibility 审查合法组合；INT8 验收不能作为 W6A8、NVFP4 自动支持相同 LoRA/二采/X2 的证据。任何新变体支持须独立记录真实权重/节点/显存证据。
- 24fps、目标帧格、用户可选每镜最大时长和镜内 CUT 时间线沿用现有模型/编译器契约，不能引入 10 秒硬编码。
- 第三方 ComfyUI 自定义节点若不可用，基础1图能力不受影响；需要该节点的镜头给出具体资源名和原有整备入口。

## 6. 同调度资源安全

X2 不并行占用 H3 模型显存；保留现有 relay_stage.py、ResourceLease、Job/Run/Series/Attempt 与 EDL 产物优先级规则。一次镜头生成的 H3/二采/超分身份需独立留痕但共同归属于同一个 shot，不复制调度器、资源账本或最终交付流水线。断电/断网/不确定提交结果走既有对账，不允许重发造成重复计费/重复出片。

## 7. 分层验收清单

- 离线编译：PDD 开/关，二采开/关，X2 开/关，1/3/6/9 图，0/1/3 视频，0/1/3 音频及12项边界；确保未开启资源完全不在图/必需列表。
- 强约束反例：0图、>9图、>3视频、>3音、总>12、无 loader、不匹配上传映射、未知 checkpoint/弱证据变体，均具名拒绝且不会影响其他镜。
- 真机正例：至少1个真实视频参考参与 H3 生成，检查图中上传、节点输入和最终产物；如本机没有可验证 loader，记录 BLOCKED/NOT_VERIFIED，绝不声称 P2 完成。
- X2：实测 2× width/height、原 fps、同 duration、音轨保留；H3 GPU 资源释放后 X2 才启动，复用 Worker/租约。
- 二采：真实第二采样确有运行，音频不重复合成。
- 低显存：只在开发机5080做实测；4070/5090vd2 必须明确 NOT_TESTED，给出用户端复核指导而非虚构通过。
- 历史与双线：冻结旧包、两个流水线的参考限额与行为不回归；外语乱入/旁白乱入负例，编译/声音控制必须回归通过。

## 8. 交付与失败退出

输出 live object_info 探测证据、激活组合真值表、总/各族参考边界矩阵、有效图差异与资源闭包、真实 H3/PDD/二采/X2 输出，P2 acceptance MD。若视频参考不可行，P2 只能标 PARTIAL/BLOCKED，不得以“UI 显示 3 视频参考槽位”冒充成功；回滚该选配能力，新基础路径继续可用。

禁止修改 P3 设置/整备体系或 P4 Skills；本阶段只调整已经存在的策略、动态图、上传适配器与可选后处理接线，必要的原有裁决调用可做最小变更。阶段结束在本地提交，停下待指示。