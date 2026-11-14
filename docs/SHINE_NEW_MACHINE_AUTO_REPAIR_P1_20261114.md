# Shine 新机环境自整备 P1（2026-11-14）

分支：`fix/p1-new-machine-self-heal-20261114`
基准：`main@1c7cfd3205724fdb6514cdb602a459e7d545b869`

## 1. 当前架构

```
Web 向导 / 制作环境卡片
        │  GET  /setup/environment（只读）
        ▼
shine.comfyops.host_env           主机事实、工具探测、内存、端口
        │
        ├─ tool_inventory        固定工具包（manifest 唯一事实来源）
        ├─ environment_repair    只读修复计划 + 受控执行器
        └─ variants/recommendation  变体分档与准入
        │
        ▼
POST /setup/tool-repair/execute   （仅回环操作员，固定 argv）
        │
        ▼
scripts/bootstrap.ps1             Windows PowerShell 5.1 自举
        │
        ├─ 固定 URL + SHA-256 + 尺寸校验
        ├─ 已完整目录复用 / 半成品保留
        └─ 输出 JSON 报告 → 后端重新探测受管目录
```

ComfyUI 生命周期仍只由既有 `instances` / `renderer_supervisor` /
`runtime_orchestrator` 拥有；本轮没有新增第二套调度器。

## 2. 故障复现

现场数据（RTX 4070 / Windows x64）：

| 项目 | 实际值 |
| --- | --- |
| 工具 | ffmpeg、ffprobe、pwsh 缺失；node 22.15.0、npm 10.9.2、uv 0.13.7 达标 |
| 显存 | 12282 MB | 
| 内存 | 系统报告 31525 MiB |
| 端口 | 8188 被占用 |
| 磁盘 | 503.06 GB 可用 |

旧链路：

1. `/setup/environment` 返回混合 `blockers`；
2. `RealProductionWizard` 发现任意 blocker → `stopWith('hardware')`；
3. 后续 ComfyUI / 模型 / 工作流 / 激活全部不可达；
4. 页面提供 `pwsh -File bootstrap.ps1`，而 `pwsh` 恰好是缺失项；
5. SetupCenter 的 `envBlocked` 同时禁用自动准备按钮；
6. 工具安装到用户 PATH 后，运行中的 API 进程读不到新 PATH。

## 3. 根因

| # | 根因 | 位置 |
| --- | --- | --- |
| R1 | 检测、可修复性、阻断范围混为一层，任何 blocker 终止整条链 | `host_env.setup_blockers` / `RealProductionWizard.run` |
| R2 | 工具以“命令”而不是“安装包”为单位报告，ffmpeg/ffprobe 被算成两个缺失项 | `host_env.BOOTSTRAP_INSTALLABLE` |
| R3 | Web 引导入口硬编码 `pwsh`，与缺失项形成循环依赖 | `api_setup.environment` |
| R4 | 检测只看进程继承的 PATH，安装完成后仍报缺失 | `host_env.probe_command` |
| R5 | 默认 INT8 的 15360 MB 显存门槛与 32768 MB 内存门槛被当作整机门槛 | `setup_blockers` |
| R6 | 32768 MiB 门槛用固定 1024 MiB 容差对照系统报告值，31525 MiB 落空 | `variants.effective_floor_mb` |
| R7 | 端口只判断 bind 失败，不判断占用者身份 | `host_env.probe_port` |
| R8 | 向导硬编码 `port: 8188`，会把服务重新绑回被占用端口 | `RealProductionWizard` |
| R9 | 向导只传组件，不传用户选定的变体，存在隐式默认 INT8 风险 | `RealProductionWizard.runTail` |

## 4. 新旧行为对比

| 场景 | 旧行为 | 新行为 |
| --- | --- | --- |
| ffmpeg/pwsh 缺失 | 立即终止 | 生成补装计划；一次确认后自动补装并复查 |
| Node/npm/uv 已达标 | 仍出现在缺失提示里 | `ALREADY_AVAILABLE`，不会重复安装 |
| 8188 被外部进程占用 | `PORT_OCCUPIED` 阻断全部 | 识别身份；外部进程不杀，自动选合法空闲端口 |
| RTX 4070 | 默认 INT8 门槛阻断整条链 | 显存只阻断该变体资格；候选方案可选、可准备 |
| 31525 MiB 内存 | `RAM_BELOW_FLOOR` | 硬件清单可达 32768 时按实装容量判定；读不到时 `DETECTION_UNKNOWN` |
| 安装后 PATH 未刷新 | 仍报缺失 | 受管目录前置探测，无需重启整机 |
| 页面重复点击 | 可能重复提交 | 计划 ID 内容绑定 + 持久执行状态；成功/无缺失时直接返回 |
| 用户取消 | 可能仍有副作用 | 取消路径不产生任何下载或安装 |

## 5. 数据合同

### 5.1 工具包

`GET /setup/tool-packages` 的语义并入 `GET /setup/environment`：

```json
{
  "package_id": "ffmpeg",
  "label": "FFmpeg + FFprobe",
  "tools": [{"tool": "ffmpeg", "present": false, "version": "", "min_version": "6", "at_floor": false}],
  "status": "MISSING",
  "auto_repairable": true,
  "download": {"version": "8.1.3", "sha256": "...", "size": 194182300},
  "requires_environment_refresh": true
}
```

工具包固定为 `node(node,npm)`、`uv(uv)`、`ffmpeg(ffmpeg,ffprobe)`、
`pwsh(pwsh)`；来源、版本、哈希全部读取
`config/deployment/tools-windows-x64.json`。

### 5.2 分级 findings

```json
{
  "code": "GPU_VRAM_BELOW_FLOOR",
  "repairability": "HARDWARE_INCOMPATIBLE",
  "scopes": ["VARIANT_QUALIFICATION", "PRODUCTION_ACTIVATION"],
  "actual": 12282,
  "required": 15360,
  "next_action": "SELECT_LOWER_TIER_VARIANT",
  "message": "…"
}
```

枚举：

- `repairability`: `AUTO_REPAIRABLE | USER_ACTION_REQUIRED | HARDWARE_INCOMPATIBLE | DETECTION_UNKNOWN | ALREADY_AVAILABLE`
- `scopes`: `BOOTSTRAP | RESOURCE_PREPARATION | VARIANT_QUALIFICATION | SERVICE_CONFIGURATION | PRODUCTION_ACTIVATION`

`blockers` 字段保留，由 findings 派生并附带相同分类，旧调用方不破。

### 5.3 修复计划

```json
{
  "plan_id": "toolrepair-<sha256前32位>",
  "status": "COMPLETE | NOTHING_TO_REPAIR",
  "packages": [...],
  "download_bytes": 306208785,
  "install_root": "<data_dir>/tools/bin",
  "tools_root": "<data_dir>/tools",
  "source": {"kind": "PINNED_TOOLS_MANIFEST", "manifest_sha256": "..."},
  "bootstrap": {"executable": "powershell.exe", "requires_administrator": false},
  "authorization": {"status": "AWAITING_OPERATOR_CONFIRMATION"},
  "execution": {"status": "NOT_STARTED"},
  "side_effects": false
}
```

`plan_id` 只由 manifest 哈希、包身份和受管根目录计算；重复 GET 稳定。
GET 端点不下载、不建目录、不改 PATH、不启动进程。

### 5.4 内存

`ram` 同时给出：

- `ram_mb` 操作系统报告可用量；
- `installed_mb` 硬件清单实装量（Windows `Win32_PhysicalMemory`，不可读时为 null）；
- `floor_mb` 依据声明门槛换算后的判定线；
- `at_floor` / `at_floor_basis` / `note` / `deficit_mb`。

`HARDWARE_RESERVED_MEMORY` 只在实装量 ≥ 声明门槛时出现，是实测解释，
不是扩大容差；实装量读不到时结论为 UNKNOWN / 需要复核。

### 5.5 端口

```json
{
  "port": 8188, "selected": 8189,
  "identity": "FREE | OWNED_COMFYUI | ADOPTABLE_COMFYUI | FOREIGN_PROCESS | UNKNOWN_IDENTITY",
  "reusable": false,
  "preferred_available": false,
  "evidence": {"identity_rule": "http_responder"}
}
```

ComfyUI 身份只认 `/system_stats` 的 `system + devices` 结构；HTTP 200 本身
不构成识别。`selected` 在 `next-instance-plan` 与 `configuration` 两个时刻
由服务端重新探测，绑定失败不会被假定为仍然空闲。

## 6. 状态机变更

新增（与既有 readiness 不冲突）：

```
NOT_STARTED → AWAITING_OPERATOR_CONFIRMATION → RUNNING
   → SUCCEEDED | PARTIAL | FAILED
   → ALREADY_AVAILABLE（无缺失）
   → PLATFORM_UNSUPPORTED（非 Windows，未执行）
```

变体就绪仍沿用既有
`RESOURCE_PREPARED → FILES_VERIFIED → WORKFLOW_REGISTERED →
RUNTIME_CONTRACT_VERIFIED → TRIAL_READY → PRODUCTION_READY`；
下载完成不会推进到 `PRODUCTION_READY`。

## 7. 安全边界

- 执行端点仅限回环操作员（既有 `Operator` 依赖）。
- 请求体只有 `plan_id`，`extra="forbid"`；没有命令、URL、路径、Shell 参数。
- 执行器 argv 完全由服务端生成：`powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -ToolsManifest … -InstallRoot … -ToolsRoot … -NoLaunch`。
- `subprocess.run(shell=False)`，参数数组，无字符串拼接。
- 安装根目录必须位于 `settings.data_dir` 下，`..` 与目录外路径被拒绝。
- 不提升管理员权限；不安装/升级 NVIDIA 驱动；不改系统级 PATH（沿用
  bootstrap 既有的用户级 bin 目录行为）。
- 计划 ID 内容绑定，伪造/过期 ID 返回 409，不执行任何命令。
- 审计事件 `TOOL_REPAIR_EXECUTED` 只记录包名、哈希、状态；不含令牌/密码。
- 外部进程占用端口一律不杀、不接管；只换端口。
- 既有下载白名单、SHA-256、缓存、断点续传与持久安装任务未被绕过。

## 8. 兼容性

- `GET /setup/environment` 新增字段，`blockers` 结构向后兼容。
- `POST /setup/next-instance-plan` 新增可选 `variant_id`，不传时行为不变。
- `scripts/bootstrap.ps1` 新增可选 `-ToolsRoot`，不传时使用历史路径。
- `setupExecution.ts` / 安装队列 / 审批 grants 未改动。
- Launcher PR #17：未修改 launcher 目录、端口探测所有权或进程身份实现；
  本轮的端口选择只读取 `ComfyInstance` 记录，不写 launcher 状态。

## 9. 测试矩阵

| 层级 | 文件 | 覆盖 |
| --- | --- | --- |
| L1 | `backend/tests/test_new_machine_self_heal.py` | 工具包去重、版本、计划 ID、授权状态、失败/部分成功、哈希错误、未知硬件、内存边界 31525/31744/32768/16GB/不可读、端口五态、重复执行、拒绝伪造计划、固定 argv |
| L1 | `frontend/src/test/realWizard.test.tsx` | 4070 图标补装、取消无副作用、补装后不循环、诊断模式不下发安装、变体/端口贯穿 |
| L1 | `frontend/src/test/setupCenter.test.tsx` | 可修复工具不阻断准备入口、真实资源阻断仍生效、端口显示 |
| L2 | `backend/tests/test_new_machine_setup.py` | 既有安装计划、实例链、配置生成、readiness |
| L2 | `backend/tests/test_bootstrap_entry.py` | Windows 隔离启动器（仅 Windows CI 运行） |
| L3 | 本机 | Python/React 全量静态、单元、集成与构建；Windows 真机标注 `PENDING_WINDOWS_VALIDATION` |

## 10. 未验证边界

- Windows 真机下载与 PATH 刷新：本沙箱为 Linux，仅通过隔离测试和固定
  argv 合同验证，标注 `PENDING_WINDOWS_VALIDATION`。
- RTX 4070 真机推理：不在本 PR 门槛内；所有 4070 结论均来自环境事实模拟。
- 断点续传的字节级行为由既有 `bootstrap.ps1` 单测覆盖，本轮未改动其算法。

## 11. 补充修复：已有 ComfyUI 与模型检索（2026-11-14）

现场反馈：用户已经下载 ComfyUI 和模型，但「检测已有 ComfyUI」找不到。

根因不是文件不存在，而是检索范围被写死：

- `discover()` 只在固定盘根目录下看名字含 `comfy` 的目录，或 `ai/tools/apps/comfy`
  的一层子目录。`D:\Downloads\AIGC\ComfyUI_windows_portable\ComfyUI` 这类
  常见下载层级完全不在覆盖范围内。
- 配置的 `SHINE_COMFY_PROBE_ROOTS` 只检查根目录本身和 `root/ComfyUI`，不会
  向下搜索。
- `inspect()` 只扫描 `<ComfyUI>/models`。权重放在外部目录、由
  `extra_model_paths.yaml` 声明时，模型同样会被漏掉。

修复后的合同：

- 配置根目录按有界深度 6 搜索，固定盘按深度 4 搜索；优先访问名字含
  `comfy`、`download`、`ai` 等目录。
- 每个种子目录有 12,000 个目录的访问预算；跳过 `Windows`、`Program Files`、
  `AppData`、`$Recycle.Bin`、`node_modules`、`.git`、虚拟环境等系统/缓存目录；
  不跟随符号链接、junction 和 UNC 网络路径。
- 返回值新增 `coverage: BOUNDED_RECURSIVE_SCAN` 与 `scan`（深度、访问数、
  预算、是否截断、种子列表），不再把“浅层没找到”表述成“整盘没有”。
- `GET /setup/existing-installations` 支持可选 `root` 参数；桌面向导会把
  用户选定的安装目录一并传入，下载到该目录内部时不会被漏检。
- `inspect()` 在标准 `<ComfyUI>/models` 之外，只读跟随 `extra_model_paths.yaml`
  声明的本机目录；UNC、链接、junction 与不存在的目录一律忽略。
  返回 `model_roots` 说明实际读取了哪些目录。
- 扫描仍然是只读的：不复制、不修改、不执行 ComfyUI 的 Python 代码，
  仍以文件大小为候选、哈希为最终证据。

测试：

- `test_discovery_finds_deep_download_layout`
- `test_discovery_skips_system_and_cache_trees`
- `test_discovery_endpoint_searches_the_chosen_root`
- `test_inspection_scans_model_roots_declared_by_the_installation`
- `test_inspection_ignores_declared_network_roots`
- `test_discovery_finds_portable_at_common_depth_with_bounded_scan`
