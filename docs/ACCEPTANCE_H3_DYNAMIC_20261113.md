# H3 动态逐镜工作流 · 真机验收报告（含二采与 RelayVSR X2 分级）

- 日期：2026-11-13
- 分支：`feature/h3-dynamic-shot-workflow-20261113`
- 验收机：Windows 11 · RTX 5080 16GB · 32GB RAM
- 真实渲染器：本机 ComfyUI `0.39.0`（`F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base`，由
  `run_minimax_h3_int8_five.ps1` 受控启动，监听 127.0.0.1:8188）
- 权重：MiniMax-H3 Ref2VA **INT8 ConvRot**（官方 15,983,746,636B 主权重）+ nvfp4 AWQ 文本编码器
  + fp16 视频 VAE / fp32 音频 VAE + PDD 8 步加速文件 + `minimax_h3_latent_upscaler_3d_conv_v1_bf16`
- 证据目录：`.local-acceptance/h3dyn-20261113/`（Git 忽略，不入安装包）

## 1. 分级结论（三种状态不混用）

| # | 验收项 | 等级 | 依据 |
|---|---|---|---|
| 1 | 动态图节点合同（1/3/6/9 图、4图+2音、native20、二采 1.0×、二采 1.5×） | **REAL_PASS**（真实 object_info） | `node_contract_validation.json`：ComfyUI 0.39.0，7/7 组合 `missing_nodes=[]`、`missing_inputs=[]` |
| 2 | 单采真实出片（动态编译图 · INT8+PDD8 · 1 图 · 5s · 1344×768） | **REAL_H3_PASS**（技术层） | `render_evidence.json` case `single_pass_pdd8_1img`：prompt_id、产物 sha256/字节数、成片 `single_pass_pdd8_1img.mp4` |
| 3 | 二采真实出片（同配方 + 第二调度器尾部精修 denoise 0.25 · latent 1.0×） | **REAL_H3_PASS**（技术层） | `render_evidence.json` case `second_pass_tail_refine`；图内含两个真实采样段（53 一段 + 61/62/63 二段），音轨取自一段解码 |
| 4 | 二采 vs 单采对照（同 seed 同参考） | **可人工对照** | 两支成片同目录存放，`HUMAN_ART_REVIEW` 未签字（NOT_REVIEWED） |
| 5 | RelayVSR X2 真实超分 | **BLOCKED（如实）** | `node_contract_validation.json`：本机 object_info 无 `RelayVSRConditioning`/`VSRDecode`（钉定 revision `b0b74356…` 早于合并 `2f89433…`）；官方权重未下载、sha256 未钉定（`RELAYVSR_WEIGHTS_NOT_PINNED`） |
| 6 | RelayVSR 阻断的产品行为 | **REAL_PASS**（阻断路径真实执行） | `test_relay_upscale_stage.py`：BLOCKED 机器上条目具名失败、`prompt_count==0`、零提交；策略层 `UPSCALE_BACKEND_UNAVAILABLE` 拒绝开拍 |
| 7 | 统一调度层（H3/二采/Relay 同租约表串行） | **ENGINEERING_PASS + 真机渲染通过** | relay 条目走 `resource_slot("RELAYVSR")`（同表优先级 10，H3=100）；本次真机 H3 两次提交均在唯一实例串行执行 |

## 2. 真实渲染明细（render_evidence.json 摘要）

| case | prompt_id | 产物 | 字节 | 结果 |
|---|---|---|---|---|
| single_pass_pdd8_1img | `fdf70c93-d586-4739-991d-7a508afd9c3d` | `single_pass_pdd8_1img.mp4` | 1,997,902 | success / RENDERED |
| second_pass_tail_refine | `53d4de5f-d790-4843-9c70-7b0016ab26cd` | `second_pass_tail_refine.mp4` | 2,050,460 | success / RENDERED |

两案共同点：同 seed 20261113、同参考图（512×512 真实 PNG，上传后由动态图的
LoadImage 装载）、同 prompt 文本、同画幅 1344×768、5 秒（H3 17k+5 帧网格）。
差异仅在编译策略（无二采 vs 二采 denoise 0.25 / latent 1.0×）——同源对照。
完整身份（`prompt_graph_sha256` / `compiled_graph_sha256` / `output_sha256`）在
`render_evidence.json`。

## 3. ffprobe 全帧核验

对两支成片执行 `ffprobe`（流信息）与 `ffmpeg -f null -`（全帧解码）：

实测（`ffprobe_decode_summary.txt`）：

| 成片 | 流 | 尺寸 | fps | 时长 | 音轨 | 全帧解码 |
|---|---|---|---|---|---|---|
| 单采 | h264 High yuv420p | 1344×768 | 24.01 | 5.17s | AAC 32kHz stereo | **exit 0（0 错误）** |
| 二采 | h264 High yuv420p | **1344×768**（latent 1.0× 合同：尺寸不变） | 24.01 | 5.17s | AAC 32kHz stereo | **exit 0（0 错误）** |

二采成片的音轨来自一段 `VAEDecodeAudio`（图节点 55）——第二采样段未重合成声音，与图合同一致。

## 4. RelayVSR X2 解锁与复验步骤（BLOCKED → REAL 的路径）

1. 按上游审计纪律升级 ComfyUI 至含合并 `2f8943324e1eae89a5ee6c7db93602299246b5f1`
   （含 comfy-kitchen window_attention 依赖），钉 revision、回归基线 H3 图后再启用；
   或审计提供相同节点合同的兼容节点包（不默认引入）。
2. 从 `Comfy-Org/RelayVSR`（Apache-2.0）下载
   `relayvsr_x2_lightx2v_int8_convrot.safetensors` 与 `relayvsr_flash_vae_x2_fp32.safetensors`，
   计算 sha256 写入 `backend/shine/comfyops/relayvsr.py` 的 pin（当前为 None → BLOCKED）。
3. `relayvsr_readiness()` 自动转 AVAILABLE；策略 UI「高清视频」解锁；
   重跑 `backend/tests/test_relay_upscale_stage.py` 与一轮真实集生产即得
   `REAL_RELAY_X2_PASS`（合同：宽高精确 2×、同 fps、同时长±0.12s、音轨无损回灌）。

## 5. 已知边界

- 二采 `latent_scale=1.0`（尺寸不变尾部精修）真机合同与渲染均通过；`1.5×`（latent 放大）
  仅节点合同验证（REAL object_info），未做真实出片（时间预算）——如需 REAL_H3_PASS 补跑一镜即可。
- 画质收益判断（二采是否"更细"）保留给人工审片：`HUMAN_ART_REVIEW = NOT_REVIEWED`。
- 本机 RelayVSR 未解锁期间，任何策略组合都无法静默绕过（UI 禁用 + 服务端双闸）。
