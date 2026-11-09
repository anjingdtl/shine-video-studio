# Shine Assets 本机 Qwen-Image-2.1 能力验收规程

云端代码和 Mock 测试不证明 Windows 本机模型可运行。每个能力单独记录工作流/运行时指纹、输入和输出 SHA、ComfyUI prompt ID、节点、分辨率、显存峰值和人工检查结果。没有通过实测的能力继续显示 `PENDING_LOCAL_VALIDATION`。

## 安全前提

1. 使用隔离的 Shine 测试数据目录和独立测试项目/素材，不使用真实生产作品、用户私有素材或正在运行的 H3 任务。
2. 确认 H3 和其它 GPU 任务均空闲，先在本机 Shine 设置页查看任务状态与 ComfyUI 实例。素材任务通过原 Worker 和 ResourceLease 排队；不得手动启动第二个 ComfyUI 或释放 H3 租约。
3. 只使用你有权处理的合成图片/声音。Qwen-Image-2.1 的上游条款为 2026-09-20 research license；下载不代表商业使用许可。项目图片、肖像、语音样本与模型许可分别核对。
4. 验收期间不要修改模型文件、工作流、节点包或运行时。若修改，先前证据失效，重新验收。

## 0. 只读预检

在 Windows PowerShell 5.1 或更新版本，从仓库根目录运行：

```powershell
./scripts/verify_shine_assets_qwen.ps1 -Action preflight
```

默认只读取 `http://127.0.0.1:8000/api/v1`，不发起生成。若启动脚本分配了其它本机 API 端口，使用 `-ApiBase http://127.0.0.1:<端口>/api/v1`。预检会记录 system status、Qwen T2I 和编辑图能力、官方工作流 pin、十个总图槽、mask 合同及所需证据字段。

## 1. T2I 单样本

先准备隔离证据目录，例如 `D:\shine-assets-validation\run-001`。确认本机能力允许验证且没有 H3 正在运行，再显式提交一次：

```powershell
./scripts/verify_shine_assets_qwen.ps1 `
  -Action t2i-sample `
  -Category prop `
  -Prompt 'A red paper lantern on a plain neutral background.' `
  -Width 1024 -Height 1024 `
  -EvidencePath 'D:\shine-assets-validation\run-001\t2i.json' `
  -ConfirmLocalGpuUse
```

脚本只提交一个 `validation_only` 样本，通过任务 API 轮询已存在的任务，不会在超时后创建第二次尝试。成功后会下载候选 PNG，核对服务端版本 SHA 与本地 SHA、解码尺寸、非 Mock 身份、任务 prompt ID、版本工作流 hash 和声明输出节点 `461`。任何不匹配都会返回失败；UNKNOWN 保留原账本并要求先对账。

此脚本只自动化 T2I 的本机字节与尺寸证据。它不会自动批准候选、判定画面质量、更新模型 READY 状态或授予商业许可。对候选还需人工审核、通过素材库 QC/晋升流程。

## 2. I2I、INPAINT、MULTIREF 与 WARDROBE

在 `#a/images` 使用隔离素材：

1. 导入源图并记录媒体 ID、SHA、格式和解码尺寸。编辑图底图使用 `image_1`，其它参考依次是 `image_2` 到 `image_10`。
2. I2I 使用与源图不同但可识别的编辑指令，核对该源图字节确实上传、prompt ID 和版本 `references_manifest` 中 SHA/顺序/角色完全对应。源版本应保持不变。
3. INPAINT 在源图像素坐标绘制遮罩；白色表示采用生成像素、黑色表示逐像素保留源图。实际 mask PNG 会作为最后一个 `images.image_N` 图输入上传给 ComfyUI，prompt 明确标注白/黑语义；收获后再用同一 mask 精确合成。测试空遮罩、全白遮罩、尺寸不符、遮罩文件 hash 错误、超过十张总槽均被拒绝。核对 `mask_sha256`、原图/遮罩/结果的宽高及输出中黑区字节与原图一致。上游没有专用 mask 节点，必须人工检查模型是否按 mask 标注编辑，未通过不得开放正式生成。
4. MULTIREF 用至少 2 张、再用 10 张图分别测槽位映射；核对上传内容和 SHA 与 manifest 一致。确认第 11 张被拒绝，不得静默丢图。
5. WARDROBE 分别标注人物、服装、鞋帽/配饰/道具槽位，并逐一验证主体顺序和提示词语义。此模式是多图工作流的预设，不是单独模型。

每种模式至少完成一个正样本和一个反例。记录 graph hash、模板 commit/hash、模型文件 SHA、ComfyUI `object_info` 报告、prompt ID、输出 node、输出 hash 和 decoded dimensions。画面人物一致性、服装变化与局部边界由人工对照；不能写成模型保证。

## 3. RGBA 与 2K

- 手工抠图：在 PNG 底图上用白色圈出保留主体、黑色清除背景。导出 PNG 后逐像素验证透明像素数量、保留区域 alpha=255、背景区域 alpha=0。此路径是确定性像素处理，不是 AI 分割。
- Qwen 原生/提示词透明输出：检查解码后的透明像素大于 0；纯色棋盘格或只有 alpha 声明不通过。
- 2K：分别提交系统支持的实际目标尺寸，验证 PNG 解码尺寸与请求完全相同；记录 `ResourceLease`、运行时指纹、峰值显存、耗时、OOM 状态以及资源释放证据。OOM 不得换成 Fake 输出或误标成功。

## 4. 声音和 H3 共存

声音设计、声音克隆、普通话 TTS 分开验证。克隆使用单声道 16-bit PCM WAV、样本原文和角色名。监听 SDK 子进程退出，核对输出解码与 GPU 租约释放。试听后才能冻结音色；TTS 使用已冻结版本，并记录源音色 hash、ASR/对齐与 hard-error 结果。

图片与声音请求连续入队后逐项验收：检查 Worker 每个 tick 只取一项，输出完成后先确认模型卸载/推理子进程退出和 `ResourceLease=RELEASED`，再允许下一个图像或声音任务开始。TTS 还需检查合成后未确认释放时 ASR 不启动，ASR 后未确认释放时词级对齐不启动；失败应留下 `RECONCILE_REQUIRED`，不能因 lease timeout 直接启动下一模型。

H3 lease 共存只在隔离实例和测试工程中验证：先让测试 H3 series 持有 GPU，再提交图片和声音任务；确认它们保持排队，H3 lease owner、renderer PID/birth、输出和冻结 manifest 均未变化。完成后确认队列顺序及所有 lease 安全释放。禁止用真实在制任务做中断实验。

## 5. 证据归档

每个能力单独保存：

- 软件分支和提交 SHA、日期、Windows 版本、RTX 5080/显存容量、驱动与 ComfyUI 版本；
- T2I/edit 模型文件 hash/许可、工作流和节点版本、模板 commit/hash、完整图 hash、live `object_info`；
- 每个 input/reference/mask 的 SHA、尺寸、用途、语义槽位和授权快照；
- attempt/task ID、ComfyUI prompt ID、输出节点、输出 SHA、解码格式/尺寸/alpha；
- 最高显存、生成耗时、OOM/异常、lease 状态、人工审核结论和复核者；
- 每项状态只能为 `PASS`、`BLOCKED + code + next_action` 或 `NOT_RUN`。只有真实模型结果可进入本机能力证据；Fake/Mock 证据另记且永不升级为 READY。

保存证据前确认不含 API key、私有媒体字节、用户 prompt、绝对路径或个人信息。商业用途在核实模型和素材各自授权之前保持关闭。

## 2026-11-09 本机技术验证

RTX 5080 上使用隔离 SQLite 数据库、受管 ComfyUI 和已有锁定模型。以下结果均为真实推理。主观画面效果与音色相似度仍待人工验收。

| 项目 | 验证结果 | 台账定位 |
| --- | --- | --- |
| 全局文生图 | 512×512 PNG，技术检查通过。候选完成晋升和项目分配。 | task `fe504685`，版本 `2b553609` |
| 全局图生图 | 用已晋升的文生图作底图，将蓝纸灯笼改成红色，得到 640×448 PNG。参考清单包含底图，技术检查通过。 | task `c79857ac`，版本 `7fbf09e1` |
| 项目备料与逐镜绑定 | MiniMax 使用 1,800 字节选返回 10 项需求。为一个角色补充测试外观描述后生成 1024×1024 PNG，晋升并绑定到一镜，镜头 revision 2。 | 候选 `6c9419a6`，主素材 `843167ca`，镜头 `96d9d13e` |
| 声音克隆 | 从合成 WAV 样本生成 6.24 秒 WAV，技术检查通过。SDK 子进程正常退出，声音租约已释放。 | task `806783fe`，版本 `cf09e15a` |
| 专业模式声音克隆 | 项目导入 WAV 后直接克隆，得到 6.32 秒待试听样音。技术检查通过，子进程退出码为 0，声音租约已释放。 | voice `da7d352f` |

图片受管进程已停止。该轮覆盖单个项目角色需求、单图编辑与克隆技术链路，声音保留待试听状态。
