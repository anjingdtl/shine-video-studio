# 本地环境验证报告（V1.3 真机集成轮）

10-26 授权续轮：完整后端 **520 passed、零 skipped/failures/errors**；Ruff 通过，前端未改且构建已通过。
四个固定 Qwen 包的 42 文件已全部校验；真实声音设计、克隆、新台词合成、识别与对齐均实际运行并释放。
新台词识别 CER=0，但原始对齐有一个零长度字，保留为真实质量失败；同句 0.75 倍速技术副本的真实复验
满足完整原文和严格正长度时间段，未改写任何模型时间戳。说话人/ASR 置信度及真人试听仍待确认。
真实 VLM 正反例及既有生产数据保持上轮边界；T2VA Base 安装在首次连接错误后已对账并进入新固定计划续传，
尚未渲染。以 `user-delegated-codex` 记录授权，未冒充本人在场或批准资产/音色。全产品仍 **PARTIAL**。
见 [真实 Qwen 与对齐记录](evidence/local-closeout-20261025/real-qwen-sdk-and-alignment.json)。下文保留历轮状态。

前一续轮记录：完整后端 **474 passed，零 skipped/failures/errors**；隔离浏览器 **15 类
MOCK_VERIFIED，api_errors=[]**。安装恢复页面、项目过滤和跨项目迟到响应反例通过。
当前全产品仍 PARTIAL，真人恢复/模型安装、真实反例复验、M2/M4 等尚未完成；
见 [逐项需求核对](LOCAL_ALL_PASS_REQUIREMENTS_20261026.md) 和
[最新页面证据](evidence/local-closeout-20261025/installation-recovery-web.json)。下文保留历轮结果。

最新串行执行与本地审片补充见 [10-25 继续收尾第 7 节](LOCAL_FINAL_CLOSEOUT_20261025.md#7-继续推进真实本地审片与严格串行)。
历史真机轮结果保留。新 PDD8 超分样本及仅重跑机器 QC 通过；后续卸载异常已由真人完成指定调用对账，
cuBLAS 清理后的新真实 VLM 推理/卸载闭环通过，模型退出后 allocated/reserved 均为 0。
当前全产品状态仍为 PARTIAL。
本阶段最终工程复验为后端 427 项通过、Ruff/TypeScript/Vite/锁文件/Windows 启停通过；
浏览器回归仅为 MOCK_VERIFIED。命令、历次失败和最新统计见
[验证记录](evidence/local-closeout-20261025/verification.json)。
后续 SDK 隔离轮更新为后端 **437 项通过**；两个官方 Qwen SDK 在不同依赖目录导入成功，
仅验证导入和进程退出，没有加载语音模型。四个模型的固定文件安装提案已创建但未批准。
当前详细边界见 [SDK 轮记录](LOCAL_FINAL_CLOSEOUT_20261025.md#75-qwen-sdk-依赖隔离与模型进程退出)。
`68af13d` 的四项远端 CI 均为 SUCCESS。安装前又发现 Hugging Face CDN 跳转被旧下载器拒绝，
已补受包/计划审批快照约束的逐文件跳转策略；68 项定向回归和真实 142 字节元数据下载通过。
四个模型的新安装计划仍待真人批准，未下载权重、未运行语音模型；
见 [当前安装通道记录](LOCAL_FINAL_CLOSEOUT_20261025.md#77-固定模型安装的下载通道修复)。
本阶段完整后端更新为 **455 passed，0 skipped / failures / errors**，Ruff 与显式 PyPI 锁文件核验通过。
10-26 续：真人安装在首包权重连接时出现 TLS EOF，五份元数据缓存保留、完成模型包为零；
旧失败尝试保持待对账，没有自动重试。已补响应建立前有界连接重试与正式人工失败对账入口。
Web 模式不再写死 Ref2VA；隔离浏览器 13 类流程通过，T2VA/Ref2VA 请求被拦截验证，真实新增任务为零。
T2VA 官方原生 20 步 API 草稿结构核对通过，但缺失 INT8 Base 权重、未注册真实档案、未提交生成。
当前详情见 [续轮对账](LOCAL_FINAL_CLOSEOUT_20261025.md#78-安装-tls-失败后的正式对账与连接容错10-26-续)
和 [T2VA 准备](LOCAL_FINAL_CLOSEOUT_20261025.md#79-t2va-页面入口与官方草稿准备)。
随后真实 VLM 反例返回截断 JSON，被正确拒绝；释放核验通过，生产镜头/门禁保持不变。
已补输出预算、明确 finish_reason、响应哈希和确定响应失败分类；软件定向回归通过，
补丁后的真实反例尚未复验，旧截断调用需本人对账。服务离线后已核验旧进程退出并恢复 API/Web/ComfyUI，
验收 Worker 暂停启动；见 [反例实测与修复](LOCAL_FINAL_CLOSEOUT_20261025.md#710-真实-vlm-反例发现截断输出未将其算作通过)。
本续轮最终完整后端 **473 passed，0 skipped / failures / errors**；安装恢复定向 80 项、
审片响应与旧 E2E 定向 30 项通过，Ruff/前端构建/锁文件核验通过。
命令和当前真实边界见 [续轮验证记录](evidence/local-closeout-20261025/recovery-and-review.json)。
`0a35891` 的当前远端 CI 四项均因 GitHub 账户付款/额度问题未启动，已逐一读取 Check annotations
确认；不是当前代码的测试通过证据。本机 473 项与旧提交 CI 通过分别保留，
见 [账户阻断记录](evidence/local-closeout-20261025/ci-account-block.json)。

日期：2026-10-25（Asia/Shanghai）。范围：本机 Windows 11 工作站在本轮 V1.3 本地
H3 集成工作中被实际检查过的硬件、运行时、ComfyUI 实例、模型文件、工作流档案、
LLM 网关与落盘产物。本报告只写实际执行过的检查及其真实输出；没有执行的项目一律
标注为未覆盖。

关联文档：[真实 H3 验收报告](LOCAL_REAL_H3_ACCEPTANCE.md)、
[V1.3 收尾报告](LOCAL_V1.3_CLOSEOUT_REPORT.md)、[操作员指南](operator-guide.md)。

证据文件（本机本地路径，不进入 Git）：
`.local-acceptance/evidence/`（`acceptance-evidence.json`、`h3-model-hashes.json`、
`comfyui-process.json`、`comfyui-preflight.json`、`comfyui-object_info-full.json`、
`db-backup-inventory.json`、`v13-feature-verification.json/.md`）。

---

## 1. 结论摘要

- 本机是一台满足本地 H3 Ref2VA 推理条件的 Windows 11 + RTX 5080 工作站，本轮在其中
  完成了五次真实 ComfyUI 渲染（A、B 各两版，C 一版）、一次真实交付样片装配，并修复了
  三个在真机路径上才暴露的缺陷；收尾审片阶段又定位并修复了两个新缺陷（LLM 网关未下发
  JSON 契约、启动脚本记错了监听 pid），并复验了陈旧租约清扫（见
  [V1.3 收尾报告 7.2](LOCAL_V1.3_CLOSEOUT_REPORT.md#72-收尾审片阶段新发现并修复的缺陷)）。
- 工程回归、静态检查、前端构建在交付提交上全部通过。
- 唯一被本轮证伪的环境级结论是 `uv lock --check` 的失败原因：它与 `UV_DEFAULT_INDEX`
  导出与否无关，真正原因是本机全局 uv 配置指向阿里云镜像（详见 2.4）。
- 语义、风格、终审三道门与 B 的音频门在本轮**没有**模型覆盖，但由操作员
  2026-10-25 11:30（UTC）的逐项人工审阅关闭；三个真实镜头的最终状态是 `PASSED`、
  `quality_verification = manual_verified`。

## 2. 硬件与运行时

### 2.1 主机事实

| 项 | 实测值 | 取证方式 |
| --- | --- | --- |
| 操作系统 | Microsoft Windows 11 专业版 10.0.26200 64 位 | `Get-CimInstance Win32_OperatingSystem` |
| CPU | AMD Ryzen 7 9800X3D，8 核 16 线程 | `Get-CimInstance Win32_Processor` |
| 内存 | 31.66 GiB | `Get-CimInstance Win32_ComputerSystem` |
| GPU | NVIDIA GeForce RTX 5080，16303 MiB 显存，驱动 621.01 | `nvidia-smi` / `Win32_VideoController` |
| F 盘 | 总量 1907.7 GiB，剩余 534.6 GiB | `Get-Volume -DriveLetter F` |

```powershell
Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, OSArchitecture
Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors
nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv,noheader
Get-Volume -DriveLetter F | Select-Object Size, SizeRemaining
```

实测输出：

```
OS: Microsoft Windows 11 专业版 10.0.26200 64 位
CPU: AMD Ryzen 7 9800X3D 8-Core Processor    cores=8 logical=16
RAM GiB: 31.66
NVIDIA GeForce RTX 5080, 16303 MiB, 1497 MiB, 621.01
F: SizeGiB=1907.7 FreeGiB=534.6
```

### 2.2 工具链

| 工具 | 版本 | 备注 |
| --- | --- | --- |
| Shine Python | CPython 3.12.13 | uv 管理的 `backend/.venv` |
| ComfyUI Python | CPython 3.13.11 | ComfyUI 自带 `python_embeded` |
| ComfyUI torch | 2.10.0+cu130 | 随 ComfyUI 内置 |
| FFmpeg / FFprobe | 8.1 full build (gyan.dev) | 在 PATH 上 |
| Node / npm | v24.14.1 / 11.14.1 | 前端构建 |
| uv | 0.11.2 | 依赖与锁文件 |

```powershell
& 'backend\.venv\Scripts\python.exe' --version      # Python 3.12.13
ffprobe -version | Select-Object -First 1             # ffprobe version 8.1-full_build-www.gyan.dev
node --version; npm --version; uv --version
```

两套 Python 环境完全分离，本轮没有互相污染：Shine 只用自己的 `backend/.venv`，
ComfyUI 只用自己的 `python_embeded`。

### 2.3 仓库与提交状态

| 项 | 值 |
| --- | --- |
| 仓库 | `anjingdtl/shine-video-studio`（private） |
| 本轮基线 | `63929f49c98e205776da5e2287cfe368cf87de75`（与 `origin/main` 同一 SHA） |
| 本轮提交 | `fc75693d9aabb44cee92ca40eb97838e1ebd674c`，分支 `feature/v13-local-h3-integration`，2026-10-25 17:49:36 +08:00 |
| 后续提交 | `2df8aff9f62209b6202bbd839fd914989499d5b2`（2026-10-25 18:01:13）：`backend/shine/llm/gateway.py` 修复 `LLM_FORMAT` 根因，并加入本轮四份文档 |
| 收尾审片轮证据快照 | `8bf956fc527c9865fdbf779409d8c1d683064f64`（`.local-acceptance/evidence/acceptance-evidence.json` 记录的 HEAD，采集于 2026-10-25 11:31:09 UTC） |
| 工作区 | 提交前后均干净 |

```powershell
git log --oneline -3
git branch -vv
git status --short
```

`2df8aff` 改的是 LLM 网关：以前只把 schema 交给服务商做校验、没下发给模型，而剧组
指令要求模型"只返回所给的 JSON 契约"——契约却从未被提供。修复把 schema 追加到请求
末尾。详见 [V1.3 收尾报告 5.1](LOCAL_V1.3_CLOSEOUT_REPORT.md#51-llm_format-的根因与修复本轮后续提交)。

本轮改动相对基线共 17 个文件、+3004/−40 行：

| 类别 | 文件 |
| --- | --- |
| 交付装配（新增） | `backend/shine/media/assembly.py`、`backend/shine/api_delivery.py` |
| 三个真机缺陷修复 | `backend/shine/worker.py`、`backend/shine/comfy/client.py` |
| 工作流档案生成器 | `scripts/build_h3_workflow_profiles.py`、`config/workflows/ref2va-pdd8-*.json`（3 个） |
| 进程管理 | `scripts/start_shine.ps1`、`scripts/stop_shine.py` |
| 回归测试 | `backend/tests/test_comfy_completion_window.py`、`test_gpu_release_confirmation.py`、`test_delivery_assembly.py`、`backend/tests/fake_http.py` |
| 收尾审片阶段 | `backend/shine/llm/gateway.py`（把 JSON 契约下发模型，提交 `2df8aff`）、`scripts/start_shine.ps1`（拒绝占用端口 + 记录 `listener_pid`）、`scripts/stop_shine.py`（结束监听者 + 端口仍被占用告警） |
| 其他 | `backend/shine/main.py`（路由挂载）、`scripts/register_ref2va_profiles.py`、`.gitignore` |

`git fetch` 在本轮因网络连接重置失败，远端状态无法刷新。写文档时用只读的
`git ls-remote` 重新核对过：远端 `refs/heads/main` 仍是
`63929f49c98e205776da5e2287cfe368cf87de75`，与本地 `origin/main` 同一 SHA，远端 main
没有移动；本轮提交 `fc75693` 与后续的 `2df8aff` 都在本地分支
`feature/v13-local-h3-integration` 上，尚未推送。因此本轮不对远端 CI 结果做任何断言。

```powershell
git ls-remote origin refs/heads/main   # 63929f49…（只读，不改本地引用）
git rev-parse origin/main               # 63929f49…
git rev-parse HEAD                      # 2df8aff…
```

### 2.4 依赖锁文件（本轮实测纠正了先前的归因）

本机全局 uv 配置把默认索引指向阿里云镜像：

```toml
# C:\Users\Administrator\AppData\Roaming\uv\uv.toml
index-url = "https://mirrors.aliyun.com/pypi/simple/"
```

而 `backend/uv.lock` 的来源是 `https://pypi.org/simple`。实测：

```powershell
# 默认（走阿里云镜像）
uv lock --check --project backend
# The lockfile at `uv.lock` needs to be updated, but `--check` was provided.
# exit=1

# 覆盖为 pypi.org 后
$env:UV_DEFAULT_INDEX = 'https://pypi.org/simple'
uv lock --check --project backend
# Resolved 42 packages in 1ms
# exit=0
```

`uv lock --check -v` 的关键行直接指出了原因：

```
DEBUG Resolving despite existing lockfile due to missing remote index: `alembic` `1.16.2` from `https://pypi.org/simple`
```

结论：失败与 `UV_DEFAULT_INDEX` **是否导出无关**，与镜像指向有关。覆盖索引后退出码为
0，说明锁文件本身有效、没有被改动。这是本机索引/镜像配置问题，不是仓库缺陷。报告中
不再沿用"导出该变量才会失败"的旧说法。

## 3. 工程回归（本轮在交付提交上亲自重跑）

```powershell
& 'backend\.venv\Scripts\python.exe' -m pytest backend/tests -q
& 'backend\.venv\Scripts\ruff.exe' check backend/shine backend/alembic backend/tests
npm --prefix frontend run build
npm --prefix frontend audit
```

| 检查 | 结果 | 类别 |
| --- | --- | --- |
| 后端全量测试 | exit 0，**389 项全部通过**（36 个测试文件，基线 375 + 本轮新增 14） | `TESTED_OFFLINE` |
| 三个新增回归文件 | 14 项全通过（2 + 6 + 6） | `TESTED_OFFLINE` |
| Ruff | `All checks passed!`，exit 0 | `TESTED_OFFLINE` |
| 前端构建 | `tsc -b && vite build`，Vite 6.4.5，1590 modules，exit 0 | `TESTED_OFFLINE` |
| `npm audit` | `found 0 vulnerabilities`，exit 0 | `TESTED_OFFLINE` |

前端构建实测输出片段：

```
vite v6.4.5 building for production...
✓ 1590 modules transformed.
dist/assets/index-DVA6zgt_.js   289.69 kB │ gzip: 89.26 kB
✓ built in 1.06s
```

## 4. ComfyUI 实例

### 4.1 实例身份

| 项 | 值 | 来源 |
| --- | --- | --- |
| 安装根目录 | `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base` | `comfyui-preflight.json` |
| ComfyUI 版本 | 0.39.0 | 运行日志 / `/system_stats` |
| ComfyUI commit | `b0b743566f65daafc423b4fea8a2fbda94b3384a` | `comfyui-preflight.json` |
| git 工作区 | 干净（`comfyui_git_dirty: 0`） | `comfyui-preflight.json` |
| 监听地址 | `127.0.0.1:8188`（仅回环） | `comfyui-process.json` |
| 进程 PID / 启动时间 | 10184 / 2026-10-25T17:02:15+08:00 | `comfyui-process.json` |
| 启动前状态 | 监听器为空、无 ComfyUI 进程、队列与历史为空 | `comfyui-preflight.json` |

本轮**没有**升级、修补、清理或停止该安装的任何内容；只按操作员已验证的参数另起了一个
实例。

启动参数（`comfyui-process.json` 的 `argv`，Python 内嵌解释器 + ComfyUI 入口）：

```
F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\python_embeded\python.exe
  -X utf8 -s F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI\main.py
  --windows-standalone-build
  --listen 127.0.0.1 --port 8188
  --disable-async-offload --disable-pinned-memory
  --reserve-vram 1 --cache-ram 6 10 --fast fp16_accumulation
```

这是操作员既有的 INT8 ConvRot 配置（profile 标识 `int8_convrot_int8five`）。

### 4.2 运行时自述

```
[INFO] Total VRAM 16276 MB, total RAM 32418 MB
[INFO] pytorch version: 2.10.0+cu130
[INFO] Device: cuda:0 NVIDIA GeForce RTX 5080 : cudaMallocAsync
[INFO] Python version: 3.13.11 (tags/v3.13.11:6278944, Dec  5 2025, 16:26:58) [MSC v.1944 64 bit (AMD64)]
[INFO] ComfyUI version: 0.39.0
```

### 4.3 自定义节点

运行日志 `comfyui-stderr.log` 记录本轮实际加载的节点，其中与 H3 链路直接相关的是：

- 核心 `comfy_extras/nodes_minimax_h3.py`
- `ComfyUI-KJNodes`、`comfyui-videohelpersuite`、`ComfyMath`
- `comfyui-SolAttn_triton`
- `ComfyUI-MiniMax-H3-PDD-Acc`
- `comfyui-dev-utils`
- 操作员自建 `custom_nodes/shine_h3_sampling_mode.py`（只把枚举转成 PDD nfe/enabled，
  不改模型、采样或缓存）

其余为该安装中原有的节点包，本轮未改动。

### 4.4 节点类型数量

```powershell
Invoke-WebRequest 'http://127.0.0.1:8188/object_info' -UseBasicParsing
```

实测：**3091** 个节点类型，快照存于
`.local-acceptance/evidence/comfyui-object_info-full.json`（7.4 MB）。

写文档时又用 Python 直接取了一次实时接口（未改动实例）：

```python
import json, urllib.request
oi = json.load(urllib.request.urlopen('http://127.0.0.1:8188/object_info', timeout=120))
print(len(oi))                     # 3091
ss = json.load(urllib.request.urlopen('http://127.0.0.1:8188/system_stats', timeout=60))
print(ss['system']['comfyui_version'], ss['system']['python_version'], ss['system']['pytorch_version'])
```

```
object_info node classes: 3091
comfyui_version: 0.39.0
python: 3.13.11 (tags/v3.13.11:6278944, Dec  5 2025, 16:26:58) [MSC v.1944 64 bit (AMD64)]
pytorch: 2.10.0+cu130
device: cuda:0 NVIDIA GeForce RTX 5080 : cudaMallocAsync vram_total= 17066360832 vram_free= 15560602112 torch_vram_total= 67108864 torch_vram_free= 49017344
queue running: 0 pending: 0
```

### 4.5 0.39 的显存口径（与第 8.2 节的缺陷直接相关）

上面这行实时读数就是活证据：`torch_vram_total = 67,108,864`、
`torch_vram_free = 49,017,344`，**空闲部分比总量小**，差值
18,091,520 字节（17.25 MiB）。这说明 0.39 报的是"分配器已保留池"与"已保留但未分配的
部分"，两者不可能满足原先的 `total <= free` 判定。这条读数同时也是 GPU 释放确认修复
（`MAX_LIVE_TENSOR_BYTES = 512 MiB`）在本机的实测依据：差值远低于阈值，说明渲染端
当前确实没有驻留模型。

## 5. 模型文件

来源：`.local-acceptance/evidence/h3-model-hashes.json`（SHA-256 与字节数）。

| 路径（相对 ComfyUI 根） | 字节 | SHA-256 |
| --- | --- | --- |
| `models/diffusion_models/Minimax-H3/minimax_h3_ref2va_pruned_int8_convrot.safetensors` | 20,970,379,616 | `9255f52b6677845ad238f20dfaafa94727053694127ab7f255c048f0f9365779` |
| `models/clip/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | 15,687,142,551 | `35a88d51044231fe332301d7a62aa81e3f2cba62febeb446e2c1e3e0ef76f2c6` |
| `models/vae/MiniMax-H3(1)/minimax_h3_video_vae_fp16.safetensors` | 5,207,808,496 | `7c1f131492e7eddacaac9069a61b81bdd39de5cc96561e677c5eab1cdce5e522` |
| `models/vae/MiniMax-H3(1)/minimax_h3_audio_vae_fp32.safetensors` | 605,254,808 | `8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48` |
| `models/clip/qwen3vl_32b_minimax_h3_int8_convrot.safetensors` | 27,141,342,152 | `bc2ced0fbea64757fa9acddccfc0b3f4819d1dcf1da6c124d690d368be283923` |
| `models/vae/MiniMax-H3(1)/minimax_h3_video_vae_int8_convrot.safetensors` | 3,171,670,912 | `9bb2d96f218c76babd85e0611b85ca8fb330a90546c01a0005e8a58a59593410` |

实际被本轮渲染加载的是前四个（INT8 ConvRot 主干 + NVFP4 文本编码器 + fp16 视频 VAE +
fp32 音频 VAE），日志中对应：

```
[INFO] Model storage policy: ... models/vae/MiniMax-H3(1)/minimax_h3_audio_vae_fp32.safetensors
[INFO] VAE load device: cuda:0, offload device: cpu, dtype: torch.float32
[INFO] Model storage policy: ... models/vae/MiniMax-H3(1)/minimax_h3_video_vae_fp16.safetensors
[INFO] Model storage policy: ... models/clip/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
[INFO] Model storage policy: ... models/diffusion_models/Minimax-H3/minimax_h3_ref2va_pruned_int8_convrot.safetensors
```

PDD 加速文件 `models/pdd_acc/MiniMax-H3-Ref2VA-Acc-8Step.safetensors` 由插件在运行时加载，
日志为：

```
[INFO] MiniMaxH3PDDAccApply: MiniMax-H3-Ref2VA-Acc-8Step.safetensors (original) steps=8 blocks=4,4,4,4,4,4,4,4, 258 lora modules, heads fused
```

该文件**不在** `h3-model-hashes.json` 中，因此本报告不给出它的 SHA-256，只保留运行日志
里的文件名与参数行。模型权重只留在这台机器上，没有进入 Git。

## 6. 工作流档案与实时校验

五个档案全部登记在验收库中，且每一个都用**实时** `object_info` 校验过，结果统一为
`{"missing_nodes": [], "missing_inputs": []}`：

| 档案 ID | 名称 | 图 SHA-256 | 校验时间 (UTC) |
| --- | --- | --- | --- |
| `3a307aba-15fd-4edb-9ea8-50ce08def73a` | `local-h3-ref2va-2img` | `6f7a5fb19c548ca945f8a2169e05c836d7c06f03466cf2b893b3be599505d112` | 2026-10-25T09:14:08Z |
| `ddfa53df-3af7-494b-a1de-43c74344bea1` | `local-h3-ref2va-2img-1aud` | `7d86f7b6d994b0b38cc379a4fe0268f68776c4a43e80fb0f7651a3005f57dcd6` | 2026-10-25T09:14:08Z |
| `18e2f8d3-ca1d-4f0d-83ae-0bffc1b4b5f2` | `local-h3-ref2va-pdd8-2img` | `86a7624e6eaeccd3a3c54cf5024317698cac64cbe083ed10dd158bcaa1e6eaa0` | 2026-10-25T09:14:09Z |
| `01510286-83e7-4bde-ac2b-09332aeb1435` | `local-h3-ref2va-pdd8-2img-1aud` | `ea979fba2d3ad02493ee28d7126a624ca8afde06b1895a0e81a6bd50a193ca64` | 2026-10-25T09:14:09Z |
| `e8aa2bb8-6f11-478b-ad4e-9e05a478b1fa` | `local-h3-ref2va-pdd8-upscale-2img` | `ca3f6f70611b13923f682ce965504666b948082af8df61aed93571eb97353490` | 2026-10-25T09:14:10Z |

前两个是 V1.2 的历史档案（图 hash 未变）；后三个是本轮新增，由
`scripts/build_h3_workflow_profiles.py` 可重复生成：

```powershell
uv run --project backend python scripts/build_h3_workflow_profiles.py
uv run --project backend python scripts/build_h3_workflow_profiles.py --validate http://127.0.0.1:8188
```

PDD8 档案（`config/workflows/ref2va-pdd8-2img.json`，25 个节点）的固定参数，逐节点实测：

| 节点 | 关键输入 |
| --- | --- |
| `MiniMaxH3ReferenceToVideo` | `width: 1344`、`height: 768`（模型原生） |
| `LoraLoaderModelOnly` | `h3-realism-people-t2v-i2v-r2v.safetensors`，`strength_model: 0.65` |
| `MiniMaxH3SigmaShift` | `shift_video: 12.0`、`shift_audio: 3.0` |
| `SolAttnPatch` | `tau 1.5`、`start_percent 0.2`、`end_percent 0.9` |
| `BasicScheduler` | `scheduler: simple`、`steps: 20` |
| `ShineH3SamplingMode` | `mode: "PDD 8 steps"` |
| `MiniMaxH3PDDAccApply` | `pdd_file: MiniMax-H3-Ref2VA-Acc-8Step.safetensors`、`lora_strength 1.0`、`head_strength 1.0` |
| `ComfyMathExpression` | `max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17`（H3 的 17k+5 帧格点） |
| `KSamplerSelect` | `sampler_name: euler` |
| `RandomNoise` | `noise_seed: 77` |
| `CreateVideo` | `fps: 24.0`、`codec: h264` |
| `LoadImage` × 2 | `shine-subject.png`、`shine-picture.png` —— 恰好一个 subject 槽与一个 picture 槽 |

采样步数由 `ShineH3SamplingMode` 把枚举转成 PDD 的 nfe/enabled，PDD 权重是
`MiniMax-H3-Ref2VA-Acc-8Step.safetensors`；基础调度器仍出 20 步 sigma，PDD 节点据此
分组成 8 步。超分档案 `ref2va-pdd8-upscale-2img.json`（29 个节点）在其上追加
`MinimaxH3LatentUpscaler3DRefineHandoff`（`scale: 1.5`、`cfg: 1.0`）与
`MiniMaxH3PDDAccScheduler`（`nfe: "8"`、`denoise: 0.25`）。

每个档案都声明了 `parameter_map`（`positive_prompt`/`duration`/`seed`/
`reference_subject_1`/`reference_picture_1`/`reference_audio_1`）与 `media_transfers`
（image 走 `/upload/image`；audio 额外声明 `VHS_LoadAudioUpload`）。未映射的槽位会
以 `PENDING_WORKFLOW_MAPPING` 阻断渲染，不会被静默丢弃。

档案身份中还固定记录了连续性边界：

```
"continuity_modes": ["SOFT_REFERENCE_CONTINUITY"],
"hard_continuity": "PENDING_HARD_CONTINUITY_TEST",
"video_reference": "SUPPORTED_NODE_UNVERIFIED_TRANSFER"
```

## 7. LLM 网关与密钥边界

| 项 | 值 |
| --- | --- |
| 档案 ID | `ffbf39c1-1a30-459b-8e4e-cca7ba10b3cd` |
| 名称 / 类型 | `minimax-openai-compatible` / `openai_compatible` |
| 端点 | `https://api.minimax.cn/v1`（操作员授权的本地测试端点） |
| 模型 | `MiniMax-M3.1-flash` |
| 密钥存储 | Windows DPAPI 秘密存储（`secret_storage: dpapi`） |
| 连接测试 | `{"ok": true, "message": "Connection succeeded"}` |

能力探测返回 `available: true`，并列出服务商模型表：
`MiniMax-M3.1-Flash`、`MiniMax-M3`、`MiniMax-M2.7`、`MiniMax-M2.5`、`MiniMax-M2.1`、
`MiniMax-M2` 等。探测同时标注 `model_listed: false` 与
`"warning": "The configured model was not listed by the provider"` —— 配置里写的是小写
拼写 `MiniMax-M3.1-flash`，服务商返回的是 `MiniMax-M3.1-Flash`。连接本身成功。

12 个 Agent 角色全部路由到该档案：`scriptwriter`、`director`、`cinematographer`、
`editor`、`continuity_supervisor`、`sound_director`、`producer`、`comfyops`、
`script_planner`、`shot_planner`、`repair_planner`、`qc_reviewer`。

**密钥处理声明**：本轮创建的密钥只写入 Windows DPAPI 秘密存储，API 从不返回它，
它没有进入 SQLite、没有进入 Git、没有进入日志、没有进入前端响应。本文档不包含、
不引用、不复述任何密钥内容。

**JSON 契约下发**：剧组的角色指令要求模型"只返回所给的 JSON 契约"。提交 `2df8aff`
之后，`LLMGateway.complete_for_role` 会把该角色的 schema 作为请求的**最后一段**
追加给模型 —— 服务商收到的请求里因此确实带着这份契约。对授权的 MiniMax 端点实跑，
`scriptwriter` 返回 HTTP **201** 并产出有效的 `ScriptBreakdown`。修复前的失败是
`LLM_FORMAT`（HTTP 422），根因见
[V1.3 收尾报告 5.1](LOCAL_V1.3_CLOSEOUT_REPORT.md#51-llm_format-的根因与修复本轮后续提交)。

## 8. 数据目录与数据库备份

本轮使用隔离的验收数据目录 `.local-acceptance/h3run/`，原 `.local-data-v12` 只读。
数据库用 SQLite 在线备份 API（WAL 感知）复制，媒体复制进隔离目录，因此没有任何写回。

| 数据库 | 来源 | 备份 | 完整性 |
| --- | --- | --- | --- |
| V1.2 验收库 | `.local-data-v12/shine.db` | `.local-acceptance/db/v12_acceptance-backup.db`（483,328 B） | `ok` |
| V1.2.1 审计副本 | `.local-data-v121-audit/v12-upgrade-copy.db` | `.local-acceptance/db/v121_audit-backup.db`（483,328 B） | `ok` |
| 默认本地库 | `.local-data/shine.db` | `.local-acceptance/db/default_local-backup.db`（4,096 B） | `ok` |

验收库本身：`.local-acceptance/h3run/shine.db`，Alembic head `f130a0020001`。

## 9. 落盘产物校验

验收库中全部 12 个媒体产物的落盘文件 SHA-256 与登记值逐一相等（`hash_matches: true`），
字节数也相符：

| 产物 | 字节 | SHA-256 与登记值一致 |
| --- | --- | --- |
| V1.2 A（480x864） | 2,540,146 | 是 |
| V1.2 B（480x864） | 1,228,496 | 是 |
| V1.2 C（480x864） | 1,899,829 | 是 |
| 本轮 Shot A 第一版（1344x768） | 6,660,241 | 是 |
| 本轮 Shot B 第一版（1344x768） | 4,589,710 | 是 |
| 本轮 Shot C（1344x768） | 4,414,119 | 是 |
| 本轮 Shot A 当前版（1344x768） | 4,249,108 | 是 |
| 本轮 Shot B 当前版（1344x768） | 3,390,919 | 是 |
| 交付样片（错误顺序版） | 14,085,974 | 是 |
| 交付样片（A→B→C，第一版来源） | 14,085,974 | 是 |
| 交付样片（A→B→C，重渲染来源，`pending_review`） | 10,893,258 | 是 |
| 交付样片（最终，`all_passed`） | 10,893,258 | 是 |

最后两条登记的是**同一个内容寻址文件**（SHA-256 `4ce49654…`），区别只在来源 job 的
状态：第一条装配时三个来源 job 尚未被审片签署，第二条是审片之后重装的。已通过版本
的镜头产物与先前的成片都没有被覆盖。

## 10. 本轮未覆盖或受限的环境项

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 语义 / 风格 / 终审门的**模型**覆盖 | `BLOCKED / PENDING` | 没有模型覆盖；这三道门由操作员的人工逐项审阅关闭；`allow_remote_review = 0`，没有任何图像/音频/视频离开本机 |
| 自动 ASR | `BLOCKED / PENDING` | 未配置；音频流存在并可解码，中文台词未逐字**机器**核验（由人工试听逐字核对） |
| 严格首尾帧锚定 | `PENDING_HARD_CONTINUITY_TEST` | 未实现为可放行的连续性 |
| 视频参考真实传输 | `SUPPORTED_NODE_UNVERIFIED_TRANSFER` | 节点存在，传输未验证 |
| 多模型 GPU 争用 | 未覆盖 | 本轮只跑了一个本地模型家族 |
| ComfyUI 远程绑定 | 未启用 | `SHINE_ALLOW_REMOTE_COMFYUI` 未设置，渲染端始终是回环地址 |
| 远端 CI | 未确认 | `git fetch` 在本轮失败；已用只读 `git ls-remote` 核对远端 main 未移动，但仍不对远端 CI 结果做断言 |

## 11. 结论分类

| 结论 | 类别 |
| --- | --- |
| 本机硬件、磁盘、工具链满足本地 H3 Ref2VA 推理条件，实测值如第 2 节所列 | `VERIFIED_LOCAL_ENV` |
| ComfyUI 0.39.0 实例在 `127.0.0.1:8188` 上以操作员既有 INT8 ConvRot 参数启动，安装未被改动 | `VERIFIED_LOCAL_ENV` |
| 实时 `object_info` 含 3091 个节点类型，快照已留档 | `VERIFIED_LOCAL_ENV` |
| 实际加载的 4 个 H3 模型文件，以及另外 2 个未使用的 INT8 变体，SHA-256 与字节数均已留档（PDD 加速文件未留档，见第 5 节） | `VERIFIED_LOCAL_ENV` |
| 五个工作流档案全部用实时 `object_info` 校验通过（无缺失节点/输入） | `VERIFIED_LOCAL_ENV` |
| `uv lock --check` 失败源于本机全局 uv 镜像配置，覆盖索引后 exit 0；锁文件本身有效 | `VERIFIED_LOCAL_ENV` |
| 后端全量测试、Ruff、前端构建、`npm audit` 在交付提交上通过 | `TESTED_OFFLINE` |
| 交付装配能力、三个真机缺陷修复、进程清单启停脚本 | `IMPLEMENTED` / `TESTED_OFFLINE` |
| 本轮三个真实镜头为真实 ComfyUI 产物 | `VERIFIED_REAL_H3` |
| 交付样片为真实 FFmpeg 装配产物，可完整解码 | `VERIFIED_REAL_H3` |
| 操作员 `shine-owner` 逐项审阅并签署三个镜头，语义 / 风格 / 终审门与 B 的音频门由**人工**关闭 | `VERIFIED_MANUAL` |
| 语义 / 风格 / 终审门与音频门**没有模型覆盖**，只由人工关闭 | `BLOCKED / PENDING` |
| 严格连续性、视频参考传输、多模型 GPU 争用 | `BLOCKED / PENDING` |
