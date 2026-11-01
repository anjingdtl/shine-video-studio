# 部署清单说明（config/deployment/）

## 文件结构
- `h3-ref2va-windows-x64.json`：部署清单（14 组件）。由 `scripts/generate_deployment_catalog.py` 从
  `_upstream-pins/`（archives/tools/wheels/models 四份实测钉点）装配生成；backend loader
  （`shine.comfyops.deployment.load_deployment_catalog`）以严格 Pydantic 模型逐项校验
  （哈希格式/URL 白名单/路径安全/大小/许可证/usage）。
- `tools-windows-x64.json`：bootstrap 工具清单（uv/Node/pwsh/FFmpeg，固定版本+SHA-256+size+解压后入口文件）。
- `_upstream-pins/`：实测钉点存档（2026-10-31 自上游发布元数据读取，程序化获取，零手工输入哈希）。

## 组件一览（全部 READY，60.8GB）
| 组件 | kind | 固定版本 | 许可证（已核实） |
|---|---|---|---|
| comfyui | source | b0b74356…（v0.39.0） | GPL-3.0-only |
| comfy-runtime-wheels | wheel | cu128-2026-10-31 | mixed-pinned-wheel-licenses |
| ComfyUI-KJNodes / comfyui-videohelpersuite | node | ab72eda7… / 4d907bee… | GPL-3.0-only ×2 |
| ComfyUI-MiniMax-H3-PDD-Acc / ComfyUI-SolAttn_triton | node | 311a65dd… / 26d816eb… | Apache-2.0 ×2 |
| Comfyui_Minimax_h3_latent_Upscaler | node | 40316cf0… | MIT |
| minimax-h3-diffusion | model | e5eb578a… | minimax-h3-community-license |
| minimax-h3-text-encoder | model | e5eb578a… | minimax-h3-community-license |
| minimax-h3-vae | model | e5eb578a… | minimax-h3-community-license |
| minimax-h3-lora | model | 039cc857…（fal/MiniMax-H3-Realism-People-LoRA） | minimax-h3-community-license |
| minimax-h3-pdd | model | 335001fb…（alibaba-pai/MiniMax-H3-Acc-LoRAs） | minimax-h3-community-license-agreement |
| qwen3-vl-8b-fp8 | review-model | 9cdc6310… | Apache-2.0 |
| qwen3-asr-0.6b | review-model | 5eb14417… | Apache-2.0 |

## 目录里的三个绑定家族细节
- 模型组件的 `relative_path` 复刻生产布局（如 `Minimax-H3/…`、`MiniMax-H3(1)/…` 子目录前缀），
  保证 ComfyUI 列出的模型名与已注册工作流图引用的名字逐字一致。
- PDD 权重经 BIND_MODELS 绑定为第 6 个家族 `pdd_acc`（节点从 `models/pdd_acc` 加载——已对上游
  `ComfyUI-MiniMax-H3-PDD-Acc/nodes.py` 源码核实）。
- 审片模型（review-model）不进 ComfyUI 绑定；它们装到 `comfyops/isolated-packages/<component_id>`，
  配方生成时写入审片 manifest（文件哈希=目录钉点），由 `local_review_isolated_server.py` 加载。

## 重复登记语义
- 同名同图（内容身份一致）→ 幂等复用；同名异图 → `CATALOG_COMPONENT_CHANGED` / 同名 profile → 409。
- profile 重复登记由 api_comfy 的名称唯一性（409）+ register 脚本幂等（同名 skip）共同保证。

## 已知不在清单内的项目（如实记录）
- qwen-asr / qwen-tts SDK：sdist-only 依赖（sox/gradio）不适配 `--no-index` wheelhouse，
  保持既有手动 voiceops 流程（voice-role-runtimes.json.example）。
- Myang GPL 插件：维持"用户自行安装的独立插件"边界，不入清单。
