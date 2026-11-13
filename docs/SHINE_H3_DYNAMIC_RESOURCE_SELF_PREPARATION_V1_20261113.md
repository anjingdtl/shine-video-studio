# Shine H3 动态工作流资源自整备 V1.0｜增量建设与实施方案

> 日期：2026-11-13；状态：**设计和开发交接规范，不是功能已落地或真机验收声明**。
>
> 基线：`main` = `1a55c41509924b898a587901b0aa5aa5ac71cc70`（2026-11-13 12:09 UTC）。
>
> 上游在建文档：[SHINE_H3_DYNAMIC_SHOT_WORKFLOW_UNIFIED_ORCHESTRATOR_V1_20261113.md](https://github.com/anjingdtl/shine-video-studio/blob/docs/h3-dynamic-shot-orchestration-relayvsr-x2-second-pass-20261113/docs/SHINE_H3_DYNAMIC_SHOT_WORKFLOW_UNIFIED_ORCHESTRATOR_V1_20261113.md)；读取时 blob SHA `d94c4437b528770f9f711d56fb7138aaa2605abb`。
>
> 本增量的示例：`model_variant_id=ref2va-lowend-candidate`（W6A8），`download_source_policy=AUTO`。
>
> 实施隔离：本分支**只提交规范文档**。不得直接修改正在开发的 V1 工作分支或强推主分支；开发代理在 V1 registry 接口稳定后于独立 worktree/feature 分支实现，并经 PR 集成。
>
> 目标：导演模式普通用户选择模型/加速/二采/高清策略后，系统自动计算依赖、扫描已安装资源与可信缓存、显示确切缺项；用户明确授权后，从已核实的官方/镜像来源以现有受控下载器断点续传，核验并绑定到现有受管 ComfyUI；通过真实节点/运行时/试镜验证后供动态逐镜编译器使用。**不新建第二套下载器、第二套 ComfyUI、第二套模型库或 GPU 调度层。**

## 1. 与在建动态工作流 V1 的增量差异及边界

| 在建 V1 已规定 | 本增量补齐 | 唯一事实权威 |
| --- | --- | --- |
| `H3CapabilityRegistry`：量化底模、加速、二采、Relay、参考适配、相容矩阵 | 在每个能力注册项上引用固定的资源依赖 `component_id` 和运行配方；增加依赖闭包与资源就绪投影 | Registry + `config/deployment/*` |
| `WorkflowGraphCompiler`：按镜从合法版本化配方编译图 | 编译前只消费已就绪的能力，不在编译器里做下载/安装/磁盘全扫 | Compiler / provider |
| 模式级与策略级 `READY`，旧静态 Profile 仅 advisory | `PRESENT`、`HASH_VERIFIED`、`CONSUMABLE`、`NODE_ABI_VALID`、`HOST_TRIAL_PASS` 分层 | 现有 `variant_readiness` / receipts / service evidence |
| V1 WP1 允许已审物料一键安装/导入，禁止无授权未钉版网络安装 | 增加用户预览、来源选择、确认后已审候选下载，及失败恢复 | 现有 InstallPlan / Attempt / Downloader |
| V1 WP5 Relay X2 模型、节点、配方核实 | Relay、二采、PDD/Turbo 也复用同一依赖解析和下载流程 | 统一的资源组件目录 |
| V1 WP6 唯一 runtime control plane 和 GPU Lease | 安装/节点升级在受管实例空闲的维护窗口执行；下载文件本身不占用 GPU/不得乱停服务 | `runtime_orchestrator`、现有 mutex 与 Lease |
| V1 WP7 导演模式简明模型/加速/二采/高清设置 | 增加一块“本机资源检测和补齐”，不暴露 N 图 N 音 N 视频工程选项 | 现有导演设置 UI |

明确不做：修改 Ref2VA-only、变更用户已冻结风格/主素材、静默更换 W6A8 为 INT8、把二采/Relay 冒充 LoRA、生成任意未审核下载 URL、把权重写入 Git 仓库、另启独立 ComfyUI、下载后直接宣称生产 PASS。

## 2. 现有代码审计事实与必须修复的耦合

1. `backend/shine/api_setup.py` 中 `InstallPlanRequest.catalog` 默认 INT8；当前 `/install-plan` 对 `body.variant_id` 单独读取 manifest，但必须额外严检 `catalog.variant_id == body.variant_id`，即便未传 `variant_id` 也必须按选定 catalog 的身份查门禁；防止错配、漏检和因候选 `required_component_ids=[]` 导致的非预期路径。
2. `config/deployment/variants/ref2va-lowend-candidate.json` 的 `acceptance_status=CANDIDATE`、`auto_download_allowed=false`，属于“未准许**无感自动下载及生产**”，不等于固定 SHA 权重不允许用户自愿下载。应新增独立的“经审查+授权候选下载”权限，**保留这两个原字段，不改成 SUPPORTED/true 来解锁**。
3. `backend/shine/comfyops/variant_readiness.py` 的 `_required_components` 从历史固定图里提取模型文件名；它不解析动态能力的节点包/轮子/启动参数，且仅凭 basename 匹配可能歧义。本增量应以能力注册的 `component_id` 精确解析，图分析只做交叉核验，禁止错误复用。
4. `backend/shine/comfyops/existing_installation.py` 已有有界 ComfyUI 根目录发现、按大小初筛、哈希验证及已审模型导入；但默认扫描未覆盖全部候选、已授权外部模型路径和所有 Python/节点依赖。不得用无界全盘递归或跟随未经同意的 junction/network 路径“解决”。
5. `backend/shine/comfyops/installer.py` 已有 `ControlledDownloader`、固定 SHA 缓存、`.part`、分块 Range、重试及 HF 官方到 `hf-mirror.com` 回退。当前镜像偏向“主源连接失败后回退”，需扩充显式 `AUTO`/`MIRROR_FIRST`/`OFFICIAL_FIRST` 选择、记录每一文件实际来源与进度，仍使用**同一个下载器**。
6. `backend/shine/comfyops/deployment.py` 的 `generate_configuration` 仍从 `load_default_catalog()` 和 `default_variant_requirements()` 生成节点白名单、显存阈值/启动参数；现有 `argv` 未按候选变体添加/校验低显存专属配方；不能仅因用户下载 W6A8 就认为运行时已适配。
7. W6A8 当前固定范例 `config/workflows/ref2va-lowend-pdd-free-2img.json` 节点13为 `MiniMaxH3MemoryEfficientSageAttentionPatch`，节点14为 `MiniMaxLowVRAMAttention`；固定 ComfyUI wheel 目录没有已锁定的 `sageattention` wheel。RTX 4070 Ada/SM 8.9 用户曾在节点13报 `sageattention is not new enough version or could not determine CUDA architecture`。**必须核准兼容包/轮子，或另行审核不依赖 Sage 的合法图版本，严禁直接跳过节点并假定正确。**
8. NVFP4 历史候选图的 `required_nodes` 与实际图是否一致需顺手校正；NVFP4 在 RTX 4070 的 Ada 支持仍无真机证明，不因为可下载就标记生产可用。
9. ComfyUI 受管实例模型路径必须从真实绑定/`extra_model_paths` 和运行时 `object_info` 判定；缓存有文件≠已加载、图合法≠真实出片；安装后按确切受管实例校验。

## 3. 唯一资源模型：现有 Registry 与部署 catalog 之上的薄扩展

不建第二套数据库主表。能力注册表只**引用**部署目录的固定组件 ID 和 ABI/运行参数要求；权重的 URL、版本、许可、size、SHA-256 仍只在 `config/deployment/*` 维护，UI 不存另一个下载 URL 列表。

### 3.1 CapabilityResourceRequirements（建议合同，示例非现成 API）

~~~json
{
  "schema_version": "1",
  "capability_id": "h3.ref2va.w6a8",
  "variant_id": "ref2va-lowend-candidate",
  "catalog_id": "h3-ref2va-lowend-candidate-windows-x64",
  "required_components": [
    "minimax-h3-diffusion-w6a8",
    "minimax-h3-text-encoder-nvfp4",
    "minimax-h3-vae-int8",
    "minimax-h3-audio-vae-fp32"
  ],
  "runtime": {
    "managed_instance_only": true,
    "node_abi_required": ["MiniMaxH3ReferenceToVideo", "MiniMaxLowVRAMAttention"],
    "optional_or_alternative_node_abi": ["MiniMaxH3MemoryEfficientSageAttentionPatch"],
    "execution_preset_id": "candidate-w6a8-ada12gb-pending-audit"
  },
  "download_policy": "EXPLICIT_APPROVAL_CANDIDATE",
  "production_policy": "HOST_EVIDENCE_REQUIRED",
  "source_preference": "AUTO"
}
~~~

该结构只是接口示例：实际节点清单需由**经过审核的配方**完整声明，不可凭示例省略而生成 READY。Sage 节点属于当前固定图的**必需**节点，在审核出合法替代图之前不得当作可忽略的 optional node。

### 3.2 解析输入与输出（纯逻辑，不触网/不开进程）

输入：`project_id`、当前已冻结/待确认的 `ProjectProductionPolicy`（model_variant、accelerator、second_pass、Relay）、当前受管实例 identity、硬件事实、已审核 registry 版本。

执行：按“底模 + 选定加速 + 二采 + 选定高清后处理 + 该策略实际需要的节点/轮子/启动参数”计算**去重依赖闭包**；不要提前猜每一镜的图片/声音/视频数量，逐镜引用由 V1 WorkflowGraphCompiler 负责。所有资源项必须找到精确 component，找不到为 `UNPINNED_COMPONENT` 而非随机下载。

输出建议：

~~~json
{
  "policy_sha256": "<immutable-hash>",
  "registry_digest": "<immutable-hash>",
  "managed_instance_id": "<actual-instance>",
  "source_preference": "AUTO",
  "components": [
    {
      "component_id": "minimax-h3-diffusion-w6a8",
      "kind": "model",
      "state": "MISSING",
      "expected_bytes": 15983746636,
      "sha256": "<from-pinned-catalog>",
      "reuse_bytes": 0,
      "missing_bytes": 15983746636,
      "action": "DOWNLOAD_AFTER_APPROVAL"
    }
  ],
  "download_bytes": "<computed-from-real-scan>",
  "disk_required_bytes": "<computed-by-current-installer>",
  "blocking_reasons": ["SAGE_ATTENTION_ABI_NOT_VALIDATED"],
  "download_eligible": true,
  "experiment_eligible": false,
  "production_ready": false,
  "resolution_snapshot_sha256": "<immutable-hash>"
}
~~~

SHA、目录、运行时、依赖缺项必须使用真实 manifest 和本机检测值替换占位符。`download_eligible` 只说明“已审核源可供用户选择下载”，不覆盖 `experiment_eligible` / `production_ready`。

### 3.3 三个独立的授权/验收门禁

- **下载门禁**：每个需要下载的文件都已钉 revision + HTTPS URL + size + SHA-256 + 可审许可；用户明确同意项目/组件、来源策略、下载量和磁盘占用；不足则禁止建安装计划。`auto_download_allowed=false` 仍表示不允许无人授权启动下载。
- **实验门禁**：文件核验、工作流及启动参数静态合法、依赖版本/驱动/SM ABI 校验、受管 ComfyUI 处于可安全切换状态、用户同意一次受控短镜测试。必须使用已有 Worker/Lease/Attempt，不能创建第二套执行或绕过真实服务。无法兼容时显示 BLOCKED。
- **生产门禁**：已选精确组合在该主机/运行时/配方版本下完成真实 H3 试镜、必要的 Relay/二采分别验证与 QC；当前证据没有失效，才放行正式排产。下载完成不得影响生产验收状态。

## 4. 自动扫描算法及本机路径原则

扫描范围：① Shine content-addressed cache；② 当前受管 ComfyUI 实例的 `models`、`custom_nodes`、其真实使用的路径映射；③ 已发现且**用户授权读取**的本机 ComfyUI 模型目录；④ 用户显式加进来的本机资源目录；⑤ 当前受管 Python venv 的包版本/ wheel metadata。

- 不无界扫描整个磁盘，不读取 UNC/SMB 或追随意外 symlink/junction，不调用任意第三方安装脚本。
- 模型文件先按受审核的 relative path 与大小建立 candidate，再对候选进行完整 SHA-256 验证，校验期间显示明确进度；重名不同 SHA 一律不可复用。
- 组件内容以 digest 复用，但**可使用性**还需路径绑定到当前受管 ComfyUI；已哈希验证的文件可以先存在缓存，不能直接当 `CONSUMABLE`。
- 节点包按来源 commit、文件/目录 identity、受管实例白名单、实际 live `object_info` 对应 class/type/输入 ABI 联合核验；仅有同名文件夹不可当 READY。
- Python 依赖按当前真实 venv 的 dist-info/版本/兼容 wheel/Torch+CUDA/架构审查；不能因为宿主 Python 安装了 SageAttention 就把 ComfyUI venv 判为已安装。
- 已安装资源变化/路径移动/用户重启 ComfyUI/切换 GPU 后，只让受影响的能力证据变为 STALE；历史视频、哈希和旧 Job 只读。
- 输出状态建议 `MISSING` / `PRESENT_UNVERIFIED` / `HASH_VERIFIED_CACHE` / `INSTALLED_BOUND` / `NODE_ABI_VALIDATED` / `INCOMPATIBLE` / `STALE`；聚合为“已就绪、需要校验、缺失可下载、缺失不可下载、不兼容”。

## 5. 用户下载确认与 AUTO 来源路由

### 5.1 线路

`AUTO`（默认）：按被审核的每个文件的可访问性/延时做有界探测，选可信官方或可信镜像；下载中出现可恢复的 DNS/TLS/超时/429/503/断流时，以**同 SHA 固定字节**安全切换至批准的另一来源并续传，不允许在未知内容的 URL 间无校验切换。

`MIRROR_FIRST`：对于 Hugging Face 的官方固定 revision 的权重，优先尝试已白名单的 `hf-mirror.com`，失败再退官方；这只是“镜像优先”，不承诺所有网络环境可达，也不等于 Hugging Face 官方服务。

`OFFICIAL_FIRST`：先 `huggingface.co` 官方固定 revision；失败时按用户确认的回退规则考虑受审核镜像。

GitHub、PyPI、PyTorch CUDA wheel 等**不能使用 HF 域名替换**；没有经单独审计的镜像时标注“仅官方来源”。镜像主机、允许跳转主机、TLS、路径和来源需走现有 `check_url` 与 `TRUSTED_DOWNLOAD_HOSTS` 安全约束；不得新增任意 URL 输入框/通配白名单。镜像只改变传输，不改变组件 SHA、size、license 和 revision。

### 5.2 断点续传

继续使用 `ControlledDownloader` + `InstallationPlan` + `InstallationAttempt`：内容地址缓存 `<sha256>.part`、Range 分段、落盘后 fsync、最终完整 SHA、原子完成、暂停/恢复、崩溃后可证明阶段续跑。跨来源切换时若 Range 不被支持或响应 bytes 不一致，必须丢弃不可信部分/重新拉取，不可拼错内容。日志记录每一文件实际 origin、Range、确定已下载 bytes、重试原因，脱敏签名 URL，不将私有 token 写日志。可复用之前缓存必须复核摘要，不以同名文件代替。

### 5.3 同意范围与幂等

用户按钮“确认下载缺失资源”：先查看项目、变体、完整组件闭包、每个下载源策略、大小、磁盘预留与许可；创建与 `policy_sha256 + catalog/registry digest + scan_snapshot + source_preference + components/sha` 绑定的审批计划。点击一次才允许下载。配置变化必须重建计划并重新审批，重复点击用现有 target identity + intent 幂等，不重复下载。下载可与已有 H3 工作继续，但安装节点/改启动参数/变更实例白名单须等待真实 renderer 空闲的维护窗口，由现有 lifecycle control plane 执行。

## 6. W6A8 / AUTO 的首个实施样本

现有 W6A8 目录：`config/deployment/h3-ref2va-lowend-candidate-windows-x64.json`；
变体：`config/deployment/variants/ref2va-lowend-candidate.json`；
历史基准图：`config/workflows/ref2va-lowend-pdd-free-2img.json`。

真正基础 PDD-free 20-step 图至少使用如下资源：

| 资源 | 已钉 component | 文件字节数 |
| --- | --- | ---: |
| W6A8 Ref2VA DiT | `minimax-h3-diffusion-w6a8` | 15,983,746,636 |
| NVFP4/AWQ Qwen3-VL 文本编码器 | `minimax-h3-text-encoder-nvfp4` | 15,687,142,551 |
| INT8 ConvRot Video VAE | `minimax-h3-vae-int8` | 2,811,065,184 |
| FP32 Audio VAE | `minimax-h3-audio-vae-fp32` | 605,254,808 |

四件模型合计 **35,087,209,179 字节（约 32.68 GiB）**，仅为从空模型目录获取这四个文件的理论大小；未计 ComfyUI、节点包、Python wheels、解压、临时空间与其他可选策略。**只按真实缺项计算下载量**，已有部分可凭 SHA 复用。W6A8 默认图不启用 PDD/4-step turbo LoRA，不应多下载无用模型。

特别门槛：RTX 4070 12GB 属 Ada SM 8.9。W6A8 单 DiT 文件也大于显存，能否运行取决于 CPU/RAM offload、计算图峰值与节点实现，不能从“W6A8”名称推断可运行。当前 Sage patch 在示例 4070 上失败，先审查：
1. ComfyUI 本机真实 venv / Torch / CUDA / GPU capability；
2. 节点13的 SageAttention 可用性，能否有**固定来源、精确版本、适配 SM8.9 的经验证 wheel**；
3. 如果没有合法 wheel，则设计并验证无 Sage patch 的**新版、具名、hash 钉定图配方**，保留低显存 attention 与 H3 采样语义；**不直接删除节点13**，不修改历史固定图 hash；
4. 运行参数可探索 `--lowvram` / 分块 / offload，但需按候选 runtime preset 精确核定；现在 `generate_configuration` 用默认 INT8 显存门槛，必须解除这一硬耦合而不虚构新门槛；
5. 通过静态 + live ABI + 有界真机短镜后，登记该机该组合的真实峰值显存/内存、Job、artifact digest、节点版本、参数与错误日志；没有实体视频则标试渲染未通过。

**下载 W6A8 与用户授权试验是可实现的交付目标；“RTX 4070 必定跑通”不是本方案承诺。**

## 7. 与现有统一调度控制面的职责边界

- 资源 Resolver/Scanner：纯依赖解析与受限读盘，**不开 ComfyUI、不申请 GPU Lease**。
- 安装计划/下载：复用现有 `comfyops.deployment`、`installer`、`installation_worker`、计划审批与历史审计，不另设下载队列/第二张安装任务表。
- 资源激活：复用 `runtime_orchestrator`、`comfyops.instances`、`runtime_configuration` 的受管实例维护、互斥、lease、可验证 OS 进程归属。已有任务忙碌则下载可缓存，绑定/安装节点/重启等待安全空闲，绝不踢掉运行中的用户作业。
- 模式/策略资格：复用并扩展 `variant_readiness`、`profile_gate`、`service_validation` 的 v2 capability evidence，只验证当前选定策略的必需节点和资源，旧未选模板退为 advisory。
- 图编译与逐镜实际执行：V1 `WorkflowGraphCompiler` / provider / production Runner 原封不动消费同一能力版本及 host evidence，不在运行时偷偷下载/变更 checkpoint，历史工程继续按旧冻结快照重现。

## 8. API / 前端建议（在现有 setup router 扩展，不开第二服务）

建议合并到 `backend/shine/api_setup.py`（路由名字最终遵从 V1 已合并的代码）：

| 路由（建议） | 行为 | 副作用 |
| --- | --- | --- |
| `POST /api/v1/setup/resources/resolve` | 接收 project + ProjectProductionPolicy + instance identity；返回 pinned 依赖闭包及每项扫描状态、缺失量、门禁差异、snapshot | 无 |
| `POST /api/v1/setup/resources/install-preview` | 根据 resolve snapshot、用户的 AUTO/MIRROR_FIRST/OFFICIAL_FIRST 来源策略计算固定计划预览、licence /disk、拒绝原因 | 不下载；可准备审批 |
| `POST /api/v1/setup/resources/install-plan` | 用户明确提交 preview SHA 和 consent，复核 catalog/硬件/来源/快照；复用现有 create_install_plan 和审批 | 仅创建持久安装计划 |
| 现有安装 execute/progress/pause/resume/cancel | 执行受控下载及检验，可恢复和显示实际来源 | 原行为 |
| `POST /api/v1/setup/resources/activate` | 文件全部验证后请求安全维护窗口，绑定到受管实例、校验节点 ABI、重检能力证据 | 不创建第二 ComfyUI |
| `POST /api/v1/setup/resources/trial` | 用户选择一次隔离最小真实镜头试验，走已有 Job/Attempt/Lease | 不绕过生产 QC；不能假 PASS |

如果 V1 WP1 已有相同语义 API，则直接扩展，不重复发明路径。下载 preview 与用户授权不要复用 `production_ready` 结论作入口判据。所有 API 返回结构化错误，例如 `VARIANT_CATALOG_MISMATCH`、`UNPINNED_RESOURCE`、`LICENSE_ACK_REQUIRED`、`MIRROR_UNAVAILABLE`、`FILE_HASH_MISMATCH`、`NODE_ABI_INCOMPATIBLE`、`RENDERER_BUSY_DEFER_ACTIVATION`、`HOST_TRIAL_REQUIRED`。

导演 UI：只增加一个**“资源检查与下载”**区域，自动扫描、展示“已有 / 需下载 / 不兼容”，缺项有“查看并下载”；来源下拉默认“自动选择”，高级设置可选择国内镜像优先或国际源优先；展示逐文件进度、暂停/继续与失败原因；下载完显示“准备完成，正在验证”，**不提前显示“可以开拍”**。不添加 Profile/N图/N音/N视频选项。

## 9. 按最小冲突原则分阶段实施

- **S0：接口冻结与安全修复**。先核对 V1 WP1 的注册表接口，禁止变体与目录错配；补显式候选下载许可而不碰生产 READY；只修改 API 绑定与回归测试。
- **S1：ResourceResolver + 扫描**。在 `backend/shine/comfyops/` 增量模块实现精确组件闭包、已授权路径扫描、cache/receipts/venv/node/ABI 投影，优先读 V1 registry；UI 先展示缺件但不下载。
- **S2：AUTO 来源与批准下载**。扩展**原** `ControlledDownloader` 的有限来源策略；复用审批、Attempt 与 .part；先做纯离线断流/换源/坏哈希/不支持 Range 回归，再开放真实用户按钮。
- **S3：安全激活与可用性验证**。只经现有 control plane 维护实例；逐变体配置受管启动参数和节点白名单；Sage/wheel 或替代配方严格审查；真机 trial pass 只属于所测设备/参数与精确版本。
- **S4：导演 UI 和回归**。完成 V1 WP7 集成；不增加七阶段；两条主线路+AUTO 显示；进度/中断/恢复用户体验；相同模型跨作品复用。
- **S5：合并验收**。定向 Python / frontend / migration、V1 原有 WP1-WP8 回归、8 分片全量后端、Windows 本机真实 ComfyUI 试镜、4070 12GB 用户现场复验。创建独立 PR，主分支合并由现有流程决定。

### 所有权与冲突防护

V1 当前 Agent 继续独占 `H3CapabilityRegistry`、`WorkflowGraphCompiler`、production Runner、runtime_orchestrator 生命周期的主要改动。自整备 Agent 主要新增 `comfyops/resource_resolver.py`、`resource_inventory.py`、`resource_source_policy.py`（具体命名以 V1 实际代码为准），在 `api_setup.py`、`installer.py`、前端导演设置做小范围集成。对 `variant_binding.py`、`variant_readiness.py`、`deployment.py` 的共享修改按接口审计后集中集成，避免并行 Agent 互相覆盖。V1 若尚未合并则用 feature flag / adapter stub 对接，不为提前通过测试复制一份 Registry。

## 10. 验收矩阵（必须真实、可复现）

| 编号 | 场景 | PASS 必需 |
| --- | --- | --- |
| R01 | 4070 12GB，选 W6A8、AUTO，本机空模型目录 | 显示准确四项模型依赖、缺失文件/字节、Sage/运行风险；不假 READY |
| R02 | 4070 12GB，已存在同 SHA 文件、不同文件名 | 校验通过直接复用；只下载其余缺项；不重复占盘 |
| R03 | 同名错 SHA / 被篡改文件 | 不能复用，显示哈希错并提供批准重下 |
| R04 | 未确认下载，或取消确认 | 零新增网络下载，不能创建执行中的安装 Attempt |
| R05 | AUTO：官方超时，镜像可达；HTTP Range 断流 | 有界重试并跨可信源续传，最终大小+SHA 全部正确 |
| R06 | 镜像 URL 失效/恶意跳转/代理返回不同字节 | 明确报错，拒绝不可信重定向/错误文件，保留可证明缓存 |
| R07 | 同一用户重复点击/关闭浏览器后重开 | 继续唯一原 Attempt，进度可恢复，无重复下载 |
| R08 | 错 catalog_id 与 variant_id；未传 variant_id 但指向候选目录 | 一致门禁，不走默认 INT8，不允许漏判 |
| R09 | W6A8 安装到缓存但受管 instance 尚未绑定 | 只显示文件已验证，不能宣称生产可用 |
| R10 | SageAttention 不存在/不兼容 RTX4070 SM8.9 | 具名 BLOCKED；不得通过删除节点或运行全局 pip 假解锁 |
| R11 | 用户有活跃 H3/Relay 任务 | 允许批准下载缓存；不能抢占 GPU、停服务或热改节点；安全等待激活 |
| R12 | 一台已验收 5080 的 INT8 正常生产 | 原模型、图、启动参数、历史 Job、已交付视频均不回退 |
| R13 | 用户选择 W6A8 + 不相容 PDD/二采/Relay | 只阻断被选策略，说明资源与兼容缺项，不静默替底模 |
| R14 | 真机 W6A8 短镜成功与失败两种分支 | 真实 Job、gpu peak、artifact/hash 或根因有证据；失败不能变 SUPPORTED |
| R15 | 国内镜像无法覆盖 GitHub/PyTorch 等源 | UI 明说官方源/无可信镜像，不套用 HF 域名替换 |
| R16 | 出现安装版本/路径变更 | v2 capability evidence 精确失效与复验，不污染历史成片 |
| R17 | 新机打包与解压安装、非开发机 | 自动扫描、确认下载、依赖安装及维护窗口流程可由普通用户走通 |
| R18 | 未下载任何东西，只扫描 | 零 model load、零 GPU 推理、无新服务端口/后台线程、无敏感路径出站 |

验收区分：`DESIGN_DONE`（本文件）/ `ENGINEERING_PASS`（已编码+回归）/ `REAL_W6A8_HOST_PASS`（特定硬件真实试镜）/ `PRODUCTION_APPROVED_FOR_HOST`（精确组合生产资格）。绝不混用。

## 11. 开发 Agent 交接约束（实施前确认）

1. 先读 `AGENTS.md`、V1 在建文档、本增量文档，fetch 最新 main 与在建 PR；记录原始 SHA，创建独立 worktree，不能直接覆盖 V1 Agent 同时修改的文件。
2. 先输出真实调用链与冲突文件矩阵，明确 V1 能力注册字段、下载权限合同、镜像来源边界；接口变动要给新旧 API 兼容测试。
3. 优先 S0 与 S1 可落地能力；每一阶段实打实运行离线/集成测试，不允许“修改了 manifest 字段就通过”。
4. 保持 `auto_download_allowed=false` 和 `CANDIDATE` 的历史语义，新增允许用户审核下载的独立状态；批准时保留上游许可确认。
5. 本次示例 `W6A8 + AUTO` 必须真实走扫描、缺件报告、用户批准和原安装 Attempt；不要求 4070 真机必定出片；SM8.9 Sage 问题未解决前不得发布为生产可用。
6. API/Worker/ComfyUI 不出现第二套常驻进程；下载不需 GPU；激活遵从单一控制平面的 mutex/Lease，状态和错误由原台账输出。
7. 工程 PASS 只在全部相关测试真实跑过并记录原始退出码后签署；远端 CI 因额度不可运行应标记 `NOT_RUN`，不可代签。
8. 独立 PR 等待与动态 V1 功能分支对齐集成，保持主分支和其他 Agent 工作完全不受影响。

### 明确待核项

- 4070 12GB 的 RAM、pagefile、Torch、CUDA、node 版本和运行峰值尚未从用户设备取得；下载可用不推出渲染可用。
- W6A8 的 SageAttention 对 SM8.9 的兼容固定 wheel 或合法替代图尚未审定，不得自造版本号、下载地址或校验值。
- RelayVSR X2 上游权重/官方 core 节点配对需按 V1 WP5 审计并钉 SHA；本增量只复用同一资源安装通道，不能伪造验证完成。
- 第三方镜像可用性随网络变化，AUTO 只在受信且哈希一致的已审核源之间切换，不承诺固定速度或大陆可访问性。

---

**实施决策：** 该增量可与 V1 完整兼容。V1 做“选什么、怎么组图、怎么调度”，本增量做“依赖什么、本机有没有、如何经批准获得、能否真实执行”；三者共享同一注册表、安装器、受管实例与证据体系。