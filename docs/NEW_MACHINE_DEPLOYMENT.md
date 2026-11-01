# 新机部署建设报告（2026-10-31）

> 最新修复与未完成项见 [2026-11-01 修复记录](NEW_MACHINE_DEPLOYMENT_FIX_20261101.md)。完整新机目标仍为 PARTIAL；以下保留原建设轮次记录，不作为最新验收通过声明。

范围：Windows x64 + NVIDIA（16GB 级显存）上，从"下载/解压项目"到"生产就绪 + 经用户批准后的无人值守制作"的完整工程链路。
本报告如实记录已实现、已验证与未验证边界；模拟证据不冒充真实 H3 验收。

## 0. 支持范围（第一套正式支持的配置）

| 项 | 固定值 |
|---|---|
| OS | Windows 10/11 x64（引导脚本在系统自带 PowerShell 5.1 下运行） |
| GPU | NVIDIA ≥ 15.3 GiB 显存（RTX 5080 16GB 级），驱动可用（nvidia-smi 可查） |
| RAM | ≥ 30 GiB（32GB 级机器实测 31.7GB 可用） |
| 磁盘 | ≥ 120 GB 空闲（下载 60.7GB × 2 预算） |
| 工具 | uv 0.9.5 / Node 20.19.5 / PowerShell 7.4.12 / FFmpeg 8.1.3（全部可由引导安装，固定版本+SHA-256） |
| Python | 3.12（由 uv 托管） |
| 生成路径 | MiniMax-H3 Ref2VA INT8 ConvRot（`local-h3-ref2va-pdd8-2img-1aud[-portrait]` 两个 profile），未更换模型/模式/量化 |
| 下载主机白名单 | huggingface.co、raw.githubusercontent.com、github.com、codeload.github.com、release-assets.githubusercontent.com、files.pythonhosted.org、download.pytorch.org（+HF CDN 重定向） |

**被覆盖的旧条款**：V1.0 §1.2.6「首版禁止自动下载模型权重」被本任务（"经用户授权的自动下载和部署能力"）覆盖；
授权形态 = 每张安装/实例计划的 5 分钟一次性人工 grant + 哈希绑定 + 30 分钟执行窗口（`shine.comfyops.approval` 既有机制，未改动）。

## 1. 链路总览

```
bootstrap.ps1（PS5.1 可跑，唯一系统入口）
  → 探测 uv/node/npm/pwsh/ffmpeg/ffprobe
  → 缺失工具按固定版本下载 → SHA-256+大小校验 → 用户级解压 → shim + 用户 PATH
  → start_shine.ps1 -Install（既有链路：uv sync → npm ci → Alembic → API/Worker/Web）
  → Web「新机设置」面板（/api/v1/setup/*）
      1) GET /setup/environment   只读环境检查（工具/GPU/RAM/磁盘/端口/可写性/路径说明）
      2) GET /setup/catalog       部署清单摘要（14 组件 60.7GB，全部 READY、全部有真实钉点）
      3) POST /setup/install-plan → 既有 grant → approve → execute（local）→ 受控下载+校验+隔离安装
      4) POST /setup/next-instance-plan → CREATE → INSTALL_NODE → BIND_MODELS → START（逐步，每步人工批准执行）
      5) POST /setup/configuration（操作员）→ 部署 handoff 适配器 + 收编采样节点 →
         生成受管配方/审片 manifest/.env（无个人路径依赖）
      6) POST /setup/profiles      登记两个 Ref2VA profile（幂等）
      首次生产前：scripts/seed_local_review_routes.py 绑定审片路由（既有脚本，幂等）
```

## 2. 分项建设内容

### A. 安装入口与环境检查
- `scripts/bootstrap.ps1`：PS 5.1 兼容（无 `&&`/三元/7 语法）；从 `config/deployment/tools-windows-x64.json`
  读固定版本清单；curl.exe 下载 → Get-FileHash 校验 → 大小校验 → Expand-Archive → `%LOCALAPPDATA%\Shine\bin` shim
  → 用户级 PATH。`-CheckOnly` 只报告；`-NoPathUpdate` 供测试。驱动/重启类阻断如实列出，不自动处理。
- `backend/shine/comfyops/host_env.py`：只读探测模块（命令/版本下限/GPU/RAM(ctypes)/磁盘/端口/可写性），
  runner 注入可测；`setup_blockers()` 输出诚实阻断清单（缺工具=可安装，驱动缺失=BLOCKED）。
- README 新增「新机部署」节 + 本报告。

### B. 版本化部署物料（全部真实钉点，无编造）
`config/deployment/_upstream-pins/`（4 文件）+ `scripts/generate_deployment_catalog.py` 装配 + loader 严格校验：
- 6 个源码归档（codeload zip 实测哈希）：ComfyUI @ b0b74356（v0.39.0, GPL-3.0-only）+ 5 个节点包
  （KJNodes/VHS/PDD-Acc/SolAttn/Upscaler，许可证逐一核实：GPL-3.0-only ×3、Apache-2.0 ×2、MIT ×1）。
- 93 个 Windows/py3.12 wheel（4.2GB）：uv pip compile（--python-platform windows --python-version 3.12）生成
  完整依赖闭包，逐包 PyPI JSON 取哈希并与锁文件哈希交叉验证；torch 2.7.1+cu128 三件套取自 cu128 索引锚点哈希。
- 7 个模型组件（56.5GB）：HF API 按 pinned revision 取 LFS oid/size；非 LFS 小文件实测哈希；
  许可证逐一核实（H3 系=minimax-h3-community-license，Qwen=Apache-2.0）。
- 4 个工具（uv/Node/pwsh/FFmpeg）：GitHub release API digest / nodejs SHASUMS256 真实值。
- 本机同日实测：6 个归档实际下载并计算 SHA-256；HF 小文件实际下载核验。
- **未下载**：大模型权重（未获执行授权，不下载 60GB 模型字节；下载算法由既有 ControlledDownloader
  测试 + 本次 37MB 归档实测覆盖）。

### C. 受控安装体系复用与补齐
- 复用不动：`ControlledDownloader`（断点续传/缓存/哈希）、`LocalInstaller`（journal/staging/恢复）、
  5 分钟一次性 grant、`execute_approved`、实例生命周期、离线导入通道。
- 补齐：PackageSpec 新增 `source`/`wheel` kind（source→NodePackage 表，wheel→NodePackage 表 + all-whl 不复制 custom_nodes）；
  `TRUSTED_DOWNLOAD_HOSTS` 共享常量（新增 codeload/release-assets/pythonhosted/pytorch 官方源，文档化扩宽）；
  PackageFile 重定向允许清单新增 codeload/release-assets（GitHub 归档与 release 资产）；
  model_bindings 家族 +`pdd_acc`（PDD 权重从 models/pdd_acc 加载——核实自上游 nodes.py 源码）。
- 审批/对账/保护语义未动：伪造 grant、重复消费、绕过审批检查全部仍被拒绝。

### D. 自动生成可迁移配置
`deployment.generate_configuration`（操作员端点）从部署结果生成（全部由安装根+探测结果推导）：
- handoff 适配器（integrations/shine_comfy_handoff，项目自有代码）部署到实例 custom_nodes（幂等，内容变化即拒绝）；
- 收编 `shine_h3_sampling_mode.py`（integrations/shine_comfy_sampling_mode/，与操作员原件逐字节一致已验证）——
  该节点此前只存在于个人机 custom_nodes，是真实生产路径的隐藏个人依赖，本轮消除；
- 受管配方（renderer+reviews，gpu_uuid 来自实时 nvidia-smi 探测）→ 所选部署目录下的 `setup/managed_recipe.json`；
- VLM/ASR 审片 manifest（模型根=隔离包目录，文件哈希=目录钉点）；
- .env 键值更新（SHINE_MANAGED_RECIPE/SHINE_RENDER_PROVIDER/SHINE_COMFYUI_URL/SHINE_ENABLE_LOCAL_EXECUTION 等）——
  密钥永远不经过此处；DPAPI/0600 文件机制不变，凭据由用户在设置页录入。
- 生成的配方直接通过 `load_managed_recipe`（Worker 侧同一加载器）验证；测试断言适配器身份与 GPU UUID 绑定。

### E. 工作流与生产就绪校验
- `/setup/profiles` 幂等登记两个受支持 profile（同名跳过；同名异图会被
  api_comfy 的 409 "A workflow profile with this name already exists" 拦住——重复登记按内容身份处理的要求由此满足）。
- `/setup/readiness` 六层就绪：L0 工具 / L1 目录 / L2 文件校验 / L3 环境 / L4 服务与契约 / L5 真实推理。
  **L5 恒为 NOT_VERIFIED**，只有真实（is_mock=false）生产证据能改变它——代码与测试双重保证 Mock/Fake 不能把它标 READY。
- profile 活校验（object_info 节点契约）沿用既有 validate 端点（需真实 ComfyUI 运行，属生产首启步骤）。

### F. 新机设置界面
`frontend/src/components/SetupPanel.tsx`（「新机设置」导航项，位于工作台顶部）：
环境检查 → 部署清单 → 计划与批准（生成安装计划/下一步实例计划/登记 profile/生成受管配置 + 每张计划"批准并执行"）→ 就绪检查。
高级 JSON 控件保留在 PreproductionPanel，向导不要求用户写任何 JSON。

2026-11-01 首启修复：实例准备在模型绑定后保持停止，不再生成普通 START。只有生产 Worker 在资源准入后执行受管启动。旧版已有进程先走自有进程 STOP 审批；配置生成按明确实例身份并拒绝活动/待对账资源。见 [首启交接修复](NEW_MACHINE_COLD_START_FIX_20261101.md)。常驻进程激活和审片首启仍待闭环。

## 3. 测试与验证矩阵

| 层 | 内容 | 结果 |
|---|---|---|
| 单元/合同 | `backend/tests/test_new_machine_setup.py`（11 项：目录合同/拒绝非白名单 URL/拒绝越界路径/计划链/磁盘预算/内容变更检测/状态机/配方生成+load_managed_recipe 验证/就绪诚实性/开放探针/环境探测） | 11 passed |
| 小文件真实算法 | `backend/tests/test_bootstrap_entry.py`：ZIP 下载/哈希/shim、钉点一致、操作员初始化与配置保留、无效凭据拒绝 | 定向通过，详见修复记录 |
| 本机实测 | 旧 PS5.1 全工具缺失结果来自探测嵌套数组错误，已修复，不能用作空白机证据；本机环境探测和小文件核验只证明各自范围 | 不代表新机验收 |
| Mock 全链 | v13 fake 链机制（CREATE/INSTALL_NODE/BIND_MODELS/START）由既有 test_v13_instances 覆盖；本轮状态机测试验证了下一步计划生成语义 | 通过 |
| 真实 SDK / CPU 契约 | 154 个发行包哈希核验、Windows 隔离环境安装、pip check、SDK 导入；固定节点归档的真实 ComfyUI CPU 启动及默认 profile 节点/参数名核验 | **PASS_SDK_ONLY / CPU_CONTRACT_ONLY**，详见修复记录 |
| 真实模型 / GPU | 全量约 60.8GB 物料、实际模型加载、真实 H3 推理 | **NOT_RUN** |
| 干净 Windows 验证 | 无 | **NOT_RUN**（本机非空白机） |

**判定**：新机目标为 **PARTIAL**。SDK 安装已单独验证，运行配置与服务综合验收仍待完成，见 `NEW_MACHINE_DEPLOYMENT_FIX_20261101.md`；不能把离线合同通过称为完整链路已经实现。

## 4. 用户仍需进行的人工作业（产品门禁，不可自动化）
1. 运行 bootstrap.ps1 并确认工具安装；驱动/重启类阻断人工处理。
2. 每张安装/实例计划的人工批准（5 分钟一次性 grant）。
3. 设置页录入 LLM provider 凭据（DPAPI/0600 文件存储，不入库不入 Git）。
4. 上传/制作候选资产 → 人工晋升主资产；音色冻结；风格冻结；确认生产。
5. 审片 needs_review 的最终人工裁决；异常对账（不确定外部结果）的人工处理。

## 5. 已知边界与后续
- 真实首启（下载+安装+启动+profile 活校验+seed 审片路由+一次真实 shot）是一次完整授权流程，本轮未执行。
- 干净 Windows 验证 NOT_RUN。中文/空格路径由设计支持（探测报告不拒绝只标注），
  但未在真实中文路径新机上实测。
- Qwen-ASR 审片 SDK 依赖闭包已接入并通过真实 Windows 隔离安装；sox 1.5.0 通过固定工具离线构建，SDK 导入不等于模型推理。Qwen-TTS voiceops 不属于本轮已验证范围。可选 upscale profile 在 CPU 节点契约核验中缺少 `MinimaxH3LatentUpscaler3DRefineHandoff`，不能视为可用。
- 未承诺"无人工资产审批、无风格确认、无异常对账的全自动最终艺术裁决"。
