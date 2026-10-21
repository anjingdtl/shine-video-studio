# H3 V4 可选采样与超分：本地实测报告

更新日期：2026-10-21。范围：用户指定 ComfyUI 基准另存、参数选择和单段实测。本轮未修改 Shine 视频工作台代码或生产数据库，也未声称完成工作台端到端验收。

## 最新用户验收与项目判断

用户观看本次测试片后明确反馈：“生成的影片效果不错，达到基本预期了”。因此，本样本状态更新为 **VERIFIED_REAL_H3 + VERIFIED_MANUAL_BASIC_VISUAL**，不再将基本画质列为等待反馈。

该认可限于本次PDD8＋1.5倍超分短片，不代表全部人物/镜头语义、三段拼接、严格连续性或工作台完整生产流程通过；不回写原始机器verification.json中的历史PENDING，也不自动批准任何生产任务。

当前项目整体为 **PARTIAL**：后续优先接入工作台新Profile与参数映射，完成工作台端到端短镜头、完整三段及缓存/拼接实测，再由授权操作员逐项审片。PDD4、Native20只有结构/调度校验，真实GPU出片尚未运行。自动ASR为未配置的可选增强，已有用户确认的旧样本声音/对白/口型结论保留。

本报告作为当前态文档已同步进入Shine仓库`docs/H3_REF2VA_V4_LOCAL_TEST_20261021.md`；完整机器证据与媒体继续仅保存在本机，不进入Git。用户已授权将V1.2.1修复和两份报告通过普通Git快进推送main。

## 交付文件

- 三段正式副本：`F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI\user\default\workflows\MiniMax-H3_3Segment_V4_Ref2VA_4步8步20步_可选超分_20261021.json`
- 单段5秒复现副本：`F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI\user\default\workflows\MiniMax-H3_V4_标准化5秒实测_PDD8_超分开_20261021.json`
- 唯一新增节点：`ComfyUI/custom_nodes/shine_h3_sampling_mode.py`，只把枚举转换成 PDD nfe/enabled，不改模型、采样或缓存实现。
- 原始工作流 SHA256：`bd83d198933033ab6dc47656a586e702333c4abd6f324a4cf00937992c9c1a78`，实测后重新计算一致。

## 使用

1. 当前已加载新节点的实例：http://127.0.0.1:8189 。8188 原进程保留；它启动早于新节点，加载新工作流前需在方便时重启。
2. 上方“01 采样模式”选择 PDD 4 steps / PDD 8 steps / Native 20 steps。
3. 上方“02 超分开关”：false 输出原片，true 输出 3D 潜空间超分+PDD尾部2步细化。缩放倍数仍用原有节点471设置，默认1.5。
4. “09 按超分开关拼接”默认开启。“08 高清拼接”保留为高级强制高清旁路，默认关闭，开启会执行超分。
5. 三段原片备份始终保存；另有三段选定版本输出。正式副本保留1344×768、7秒、种子77/78/79、逐段素材和提示词。
6. 原生20步指生成阶段禁用PDD，保持原模型+原有电影写实LoRA/注意力配置，使用simple20调度；若开启超分，细化阶段独立使用PDD8模型与尾部2步。

PDD4复用现有Ref2VA PDD权重并按4步分组，不是FL2V Turbo4。现有插件和模型已足够，本轮没有下载安装新引擎、改依赖或替换INT8检查点。上游依据：[Alibaba PAI官方模型卡](https://huggingface.co/alibaba-pai/MiniMax-H3-Acc-LoRAs) 与 [已安装PDD插件的上游说明](https://github.com/Jalen-Brunson/ComfyUI-MiniMax-H3-PDD-Acc)。

## 实测记录

- ComfyUI prompt_id：`210cfa24-1356-4616-9838-b4db6b4f037e`；仅提交一次，提交前保存intent和payload哈希，收到prompt_id后立即保存ack。
- 状态：execution_success；总耗时401.239秒，约6分41秒。
- 配置：S1、PDD8、Euler、CFG1、shift12/3、PDD强度1/1、原有SolAttn、原有写实LoRA0.65、种子77。
- 5秒按基准17k+5帧格点映射为124帧，名义24fps，即5.1667秒；MP4容器时长5.166秒。
- 提示词仅在测试副本调整时长和四镜头时间轴；保留原S1素材与其余内容。该段没有对白，不能作为新一轮对白/口型验收。
- 原片：1344×768，124帧，全片解码通过。
- 超分：2016×1152，124帧，全片解码通过。
- 两版PTS严格递增，相对24fps格点最大误差0.333毫秒。缓存重封装使用毫秒精度，平均帧率标签24800/1033约24.0077，名义帧率24。
- 两版音频包SHA256一致，解码PCM SHA256一致，32kHz。音频未被超分改变。
- 测试缓存和输出位于`output/MiniMax_H3/Selectable_20261021/Test5s_PDD8`，未写入旧缓存目录。
- GPU测试结束，队列为空；已通过ComfyUI的/free释放模型，实例继续可用。

## 验证及边界

`python_embeded/python.exe tools/validate_h3_selectable_20261021.py`：三种模式均通过ComfyUI原生validate_prompt；另验证4/8步sigma长度、原生旁路保持模型和20步sigmas、超分独立PDD8、切换超分不改变原片缓存标识。

`python_embeded/python.exe tools/verify_h3_selectable_20261021.py`：原片/超分分辨率、124帧、PTS、完整解码、音频包/PCM哈希、原始工作流哈希检查通过。新连线的输入输出元数据亦已双向核对。

- PASS：PDD8+超分的真实本机GPU单段流程。
- PASS：PDD4 / Native20的结构、调度和旁路合同校验。
- NOT RUN：PDD4与Native20真实GPU生成；正式三段完整生成和拼接；Shine工作台集成流程。
- VERIFIED_MANUAL_BASIC_VISUAL：用户已认可本次影片达到基本预期。
- PENDING：逐项人物一致性、四镜头语义、完整三段和工作台正式验收，未以聊天反馈代写生产审批。
- NOT TESTED：新一轮对白/口型。用户对之前三段的声音、逐字对白、口型通过结论保留。

完整证据：本目录的intent.json、payload.json、ack.json、history.json、verification.json、抽帧及workflow_controls.png。运行日志在`tools/h3_selectable_20261021_runtime`。

重启原8188进程曾被自动审批拦截（blocked by policy），未执行停止；因此使用独立8189实例完成本轮测试。

## 提交前状态复核

8188和8189均在线、队列均为空；8189的`210cfa24-1356-4616-9838-b4db6b4f037e` History仍为success。原始验证JSON和媒体未改写。

主分支交付包含已验证的V1.2.1代码修复与本次两份文档；本次不再运行GPU、不修改模型、不导入私人素材、视频、数据库或密钥到仓库。版本交付不等于产品全项验收。

本次交付前已重新运行Shine完整后端测试（exit 0）、Ruff（全通过）及TypeScript/Vite构建（通过）；仅有既有Starlette/AnyIO弃用警告。该工程回归不增加GPU实测覆盖范围。GitHub API认证401，不能据此确认新提交的远端CI；主分支推送结果另以Git SHA核对。
