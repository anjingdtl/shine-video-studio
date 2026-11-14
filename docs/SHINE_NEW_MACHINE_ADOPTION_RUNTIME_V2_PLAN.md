# Shine 新机整备、外部 ComfyUI 接管与安装调度 V2 —— 根因与实施计划

- 基准主线：`21f3e728e5f0e34fefe7b22bb84efc3265540aff`（`origin/main`）
- 开发分支：`fix/shine-new-machine-adoption-runtime-v2-20261114`
- 审计样本：Windows 11 / Ryzen 7 9800X3D / RTX 4070 12282 MiB / 32GB
  / `I:\Trae\ComfyUI`（0.39.0，非 Git 检出）/ H3 权重约 43.97GB

## 1. 真实故障与复现条件

| 编号 | 用户看到的事实 | 复现条件 | 真实状态 |
|---|---|---|---|
| F1 | FFmpeg/pwsh 已由 bootstrap 安装，界面仍报缺失 | 工具写进受管 `bin`，但 API 进程 PATH 未刷新 | 子进程 PATH 前缀对 Windows `CreateProcess` 解析程序名无效；`.cmd` shim 不能直接作为可执行映像 |
| F2 | 审片下载中新建出图计划 → `INSTALL_TARGET_CONFLICT` | 两个计划共享同一 `storage_root`、组件集合不同 | `queue_attempt()` 把“同目录不同内容”当作不可恢复冲突抛出 |
| F3 | 已有 43.97GB H3 权重核验成功，前端仍提示不存在并准备重新下载 | 权重在用户自己的 ComfyUI `models/<family>`，不在 Shine 隔离包目录 | 就绪度只认“隔离包目录或 extra_model_paths 的 shine 段”，外部实例的文件路径没有被记录成可复用证据 |
| F4 | `bound=false` / `consumable=false` | 上述外部安装 | 核验回执只记录一个 `directory`（ComfyUI 根），而文件在 `models/unet` 等子目录；路径级证据缺失 |
| F5 | 非 Git 安装报 `INSTALLED_VERSION_UNKNOWN`，采用被拒 | ComfyUI 由整合包安装，无 `.git` | 版本证据只有 Git revision；没有本地版本声明与运行服务 `/system_stats` 两个来源 |
| F6 | 自定义节点无登记、无版本来源 | `custom_nodes` 未纳入检测输出 | 节点只输出“目录存在/不存在”，没有包级证据 |

## 2. 当前架构事实（不改动的部分）

- 唯一视频核心：MiniMax-H3；唯一调度层：`runtime_orchestrator` + 现有 ComfyUI 调度。
- 唯一安装器：`LocalInstaller` / `ControlledDownloader` / `bootstrap.ps1`；本轮不新增下载器。
- 安装任务身份：`InstallationPlan` + `InstallationAttempt` + 常驻 Worker（租约、心跳、暂停、取消、对账）。
- 采用关系：`EXISTING_COMFY_ADOPTION` 计划 + `ComfyInstance(payload.adopted=true)`，不改用户安装文件。
- 生产门禁：`variant_readiness` 五层 + `variant-availability` 的 `available` 字段，保持历史语义。

## 3. 根因

1. **可执行解析**：`_run_with_path()` 只向子进程环境注入 PATH。Windows 的 `CreateProcess`
   用父进程 PATH 解析 application name，因此受管 shim 永远不会被找到；`.cmd`
   还必须通过 `cmd.exe /d /s /c` 执行。
2. **目录锁**：物理目标锁被实现为“同根目录即冲突”，没有“等待 + 自动接续”状态。
3. **证据粒度**：就地核验的回执以单一目录为锚点，无法描述一个组件分散在
   `models/unet`、`models/clip` 等不同 family 目录的真实布局。
4. **绑定判定**：`bound_to_instance` 只比较 `extra_model_paths.yaml` 的 `shine`
   段；外部安装的默认 family 目录与用户声明目录不参与判定。
5. **版本证据单一**：只把 Git revision 当唯一可信身份，且把“未知”与“不匹配”
   混在一个门禁里。
6. **前后端事实源**：`SetupCenter` 用 `existing-installations/inspect` 的文件计数
   推断“已安装/已核验”，与后端 `variant-availability` 的核验/绑定事实脱节。

## 4. 计划修改文件

后端：

- `backend/shine/comfyops/host_env.py`：工具白名单、绝对路径解析、Windows 脚本调用。
- `backend/shine/comfyops/installation.py`：`WAITING_FOR_TARGET`、目标持有者判定、按序认领。
- `backend/shine/comfyops/installation_worker.py`：不新增状态机，沿用既有阶段。
- `backend/shine/comfyops/existing_installation.py`：版本多源证据、节点包登记、
  声明的 family 目录、路径级核验回执。
- `backend/shine/comfyops/verification_receipts.py`：路径级回执与兼容旧回执。
- `backend/shine/comfyops/adoption.py`：接受“可观测版本”，记录服务身份与
  路径级回执，不复制/不下载/不改用户目录。
- `backend/shine/comfyops/variant_readiness.py`：实例绑定事实、七个解耦事实。
- `backend/shine/api_setup.py`：`variant-availability` 输出 facts / binding /
  blocking_reasons / next_step。
- `backend/shine/api_preparation.py`：排队应答暴露 `waiting_for_target`。

前端：

- `frontend/src/api.ts`：类型与错误文案（含旧冲突码的兼容说明）。
- `frontend/src/components/SetupCenter.tsx`：以后端事实决定是否动权重，
  显示“等待前一项安装”和“还差一步”。
- `frontend/src/setupExecution.ts`：尝试视图增加等待字段。

测试：

- 新增 `backend/tests/test_host_tool_resolution.py`、`test_installation_queue.py`、
  `test_existing_binding.py`；补充 `test_existing_installation.py`、
  `test_new_machine_setup.py`、`frontend/src/test/setupCenter.test.tsx`。

## 5. 目标数据合同

### 5.1 工具探测

```json
{
  "tool": "ffmpeg",
  "present": true,
  "version": "8.0.1",
  "at_floor": true,
  "resolved_path": "I:\\...\\shine\\tools\\bin\\ffmpeg.cmd",
  "resolution": "MANAGED_TOOL_DIR",
  "resolution_kind": "COMMAND_SCRIPT",
  "search_scope": "MANAGED_THEN_PROCESS_PATH"
}
```

只接受固定白名单（uv/node/npm/pwsh/ffmpeg/ffprobe）；未知名称与路径字符串直接拒绝。

### 5.2 安装尝试

- 新阶段 `WAITING_FOR_TARGET`：仍拥有计划身份，不持有目录写权。
- 目录写权由“未过期的租约 + 目标持有阶段”证明；暂停/可重试不持有写权。
- 认领顺序按创建时间；被占用者标记等待并跳过，目标释放后自动接续。

### 5.3 权重回执

```json
{
  "identity": "<revision+sha256+size>",
  "root": "I:\\Trae\\ComfyUI",
  "allowed_roots": ["I:\\Trae\\ComfyUI", "J:\\weights"],
  "files": [
    {"relative_path": "h3.safetensors", "sha256": "...", "size": 0,
     "path": "I:\\Trae\\ComfyUI\\models\\unet\\h3.safetensors", "mtime_ns": 0}
  ]
}
```

旧回执（无 `path`）继续按 `directory + relative_path` 校验，不会因升级失效。

### 5.4 实例绑定

```json
{
  "instance_id": "local-8188",
  "mode": "ADOPTED_EXTERNAL_INSTALLATION",
  "source": "ADOPTED_REGISTRATION",
  "root": "I:\\Trae\\ComfyUI",
  "families": {"unet": ["J:\\weights\\unet"]}
}
```

`consumable = receipt.verified && (in_place || 每个已核验文件都位于该实例的 family 目录)`。

### 5.5 七个事实

`FILES_FOUND / FILES_VERIFIED / RESOURCE_REGISTERED / INSTANCE_BOUND /
WORKFLOW_VALIDATED / TRIAL_READY / PRODUCTION_READY`，与历史 `available`
（开拍闸门）分开输出。

## 6. 任务状态机（兼容既有命名）

```text
QUEUED / WAITING_FOR_TARGET
        ↓ (Worker 认领，目录写权)
DOWNLOADING → RETRY_WAIT → VERIFYING → INSTALLING
        ↓
FILES_VERIFIED_PENDING_RUNTIME → VALIDATING_RUNTIME_CONTRACT → RUNTIME_CONTRACT_READY
```

失败沿用 `FAILED_RETRYABLE / NEEDS_REVIEW / PAUSED / CANCELLED`。
`WAITING_FOR_TARGET` 不是失败，不进 `TERMINAL_STAGES`。

## 7. 兼容性风险

| 风险 | 处理 |
|---|---|
| 旧回执无路径 | 保留 `directory + relative_path` 校验分支 |
| 旧前端只读 `available` | 字段含义不变，只新增字段 |
| 两个 Worker 同时认领 | `BEGIN IMMEDIATE` + 租约 + 目标持有判定 |
| 外部安装被误认为受管 | `mode` 显式区分；不写用户目录，不杀外部进程 |
| 版本号被当作 commit | `version_is_not_a_commit: true`，来源与置信度单列 |
| 测试期望与既有语义冲突 | 只修改本轮明确改变的合同（同目录冲突 → 等待），其余保持 |

## 8. 回归矩阵

| 用例 | 覆盖 |
|---|---|
| T01–T02 | 受管目录内 FFmpeg / pwsh 绝对路径解析、`.cmd` 调用 |
| T03 | 已核验外部权重不重新下载（采用计划 `downloaded_bytes=0`） |
| T04–T05 | 自定义路径发现、采用不覆盖原安装 |
| T06 | 仅当回执路径位于实例 family 目录时 `bound=true` |
| T07–T08 | 同目录不同内容安全排队、同计划幂等复用 |
| T09–T10 | 暂停不持有写权、过期租约不形成永久锁 |
| T12–T14 | INT8 门槛不降低；低显存资源可准备；图片资源不被 H3 门禁阻断 |
| T15 | 刷新后以后端事实恢复，不丢核验状态 |
| T16 | 外部实例仅登记，不终止/不覆盖 |
| T18 | 错误对象不会再渲染成 `[object Object]` |
| T19 | 未知工具名/路径直接拒绝 |
| T20 | 与导演模式、素材中心、Launcher 相关的既有测试 |

真实 Windows 安装、真实 ComfyUI 绑定与 RTX 4070 出片验证不在云端伪造，
在验收报告中单列 `PENDING_REAL_HARDWARE_VALIDATION`。
