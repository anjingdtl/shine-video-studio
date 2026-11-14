# Shine 新机整备、外部 ComfyUI 接管与安装调度 V2 —— 工程验收报告

- 基准主线：`21f3e728e5f0e34fefe7b22bb84efc3265540aff`
- 开发分支：`fix/shine-new-machine-adoption-runtime-v2-20261114`
- 验证环境：Linux 云端沙箱（无 Windows、无 NVIDIA GPU、无真实 ComfyUI）
- 审计样本：Windows 11 / RTX 4070 12282 MiB / ComfyUI 0.39.0 / 43.97GB H3 权重

## 1. 分层结论

| 层级 | 结论 | 依据 |
|---|---|---|
| ENGINEERING_PASS | **通过** | L1 quick：817 passed / 24 skipped / 0 failed；定向集成测试全绿；前端 332 tests + production build；ruff 全绿 |
| WINDOWS_INSTALL_PASS | **PENDING_REAL_HARDWARE_VALIDATION** | 云端无 Windows；Windows-only 测试按既有标记跳过。宿主逻辑已用跨平台合同测试覆盖 |
| RUNTIME_INTEGRATION_PASS | **PENDING_REAL_HARDWARE_VALIDATION** | 云端无真实 ComfyUI 服务；绑定合同用合成目录 + 真实回执逻辑验证 |
| REAL_RENDER_PASS | **PENDING_REAL_HARDWARE_VALIDATION** | 未执行任何真实 GPU 出片，不声称 PASS |
| PRODUCTION_READY | **未改变** | 仍由既有生产门禁决定；本轮没有降低任何门槛 |

## 2. P0 缺陷关闭情况

### P0-01 Windows 已安装工具仍报缺失 —— 已修复

- 根因：子进程 PATH 前缀无法让 Windows `CreateProcess` 解析程序名；`.cmd` shim 不是可执行映像。
- 修复：`host_env.resolve_tool_executable()` 只接受固定白名单，先搜受管目录，
  再用绝对路径执行；`.cmd/.bat` 经 `cmd.exe /d /s /c` 且整体加引号。
- 证据：
  - `backend/tests/test_host_tool_resolution.py`（10 例，全绿）：
    受管目录优先、FFmpeg/FFprobe/pwsh/Node/npm/uv、安装后立即重探、
    未知名称/路径拒绝、`.exe` 优先于 `.cmd`、含空格路径的脚本调用、相对 PATH 条目拒绝。
  - `environment_repair.execute_repair()` 安装后立即用同一受管目录重探，判定以真实执行与版本为准。
- 未验证：真实 Windows 上的 `.cmd` 实际执行（需 Windows 宿主）。

### P0-02 安装计划冲突导致 `INSTALL_TARGET_CONFLICT` —— 已修复

- 根因：同目录不同组件集合被当作不可恢复冲突。
- 修复：新增兼容阶段 `WAITING_FOR_TARGET`；目录写权由“未过期租约 + 持有阶段”证明；
  Worker 按创建顺序认领，被占用者标记等待并跳过，目标释放后自动接续。
- 证据：`backend/tests/test_installation_queue.py`（7 例，全绿）：
  同目录不同内容排队、目标释放后自动认领、无关目录不被阻塞、
  暂停不持有写权、过期租约不是永久锁、同物理工作幂等复用、取消等待任务立即生效。
- 更新：`test_new_machine_setup.py` 中原“409 冲突”合同改为“202 + 等待”。

### P0-03 外部 ComfyUI 已核验权重仍 `bound=false` —— 已修复

- 根因：回执只记录单一目录；外部安装的权重分散在 `models/<family>`，
  且绑定只认 `extra_model_paths.yaml` 的 `shine` 段。
- 修复：
  - `save_receipt()` 支持逐文件绝对路径 + `allowed_roots`；旧回执保持兼容。
  - `register_verified_packages()` / 采用流程写入路径级回执（不重新读取字节）。
  - `variant_readiness.current_instance()` 区分受管/采用实例，绑定判定使用
    `models/<family>`、family 别名与用户声明的目录。
- 证据：`backend/tests/test_existing_binding.py`（4 例，全绿）：
  采用实例内已核验文件 `consumable=true`；同一回执对另一实例不绑定；
  `extra_model_paths` 外部目录可绑定；没有实例时明确不绑定。
- 采用流程仍不复制、不下载、不修改用户安装：`test_existing_comfy_adoption.py` 7 例全绿。

## 3. P1 缺陷关闭情况

### P1-01 ComfyUI 版本与节点登记 —— 已完成（工程级）

- 新增本地版本证据（`comfyui_version.py` / `version.py` / `pyproject.toml`）与
  运行服务 `/system_stats` 证据；记录 `version_source`、`version_confidence`，
  并显式标注 `version_is_not_a_commit=true`。
- 采用门禁不再要求必须等于固定 Git revision：`MATCH / MODIFIED / MISMATCH /
  OBSERVED` 均记录来源；完全无法确认时才拒绝（`EXISTING_COMFY_VERSION_UNVERIFIED`）。
- 新增 `node_packages` 登记：包名、真实路径、版本/来源、Git commit（可读时）、
  目录事实与“节点类映射需要运行服务”的明确区分。
- 证据：`test_existing_installation.py` 新增 2 例；节点无版本时固定为 `VERSION_UNKNOWN`。

### P1-02 前后端统一事实源 —— 已完成

- `variant-availability` 新增 `facts / binding / blocking_reasons / next_step`；
  `available` 历史语义保持不变。
- `SetupCenter` 以后端事实决定“是否需要动权重”：已安装+已核验时不再规划下载；
  扫描结果只在后端不可读时作为目录级回退。
- 证据：`frontend/src/test/setupCenter.test.tsx` 新增“后端已核验不重复下载”用例；
  前端全量 332 tests 通过。

### P1-03 低显存与生产门禁 —— 保持红线

- 未修改 INT8 15360 MiB 门槛；候选变体仍为 CANDIDATE。
- 未执行 RTX 4070 真机测试，`PRODUCTION_READY` 仍为 `PENDING_REAL_HARDWARE_VALIDATION`。

### P1-04 下载重定向与恢复 —— 已具备，本轮回归确认

- `installer.py` 已允许 301/302/303/307/308，并对重定向后的域名再次做白名单校验。
- 证据：`backend/tests/test_installer_redirects.py` 55 例全绿（含 `.NET` 308 场景与非法来源拒绝）。
- 本轮不新增第二套下载器，不承诺速率提升。

### P1-05 独立能力不被 H3 门禁阻断 —— 保持并回归

- 图片/审片/ASR 计划走各自 catalog，不继承 H3 变体资格。
- 既有 `test_install_plan_qualification.py::test_a_component_only_plan_is_not_gated_by_the_video_variant`
  与图片独立计划测试保持通过。
- 说明：`qualification=UNKNOWN` 的设计是“允许准备资源、所有生产门禁保持关闭”，
  与审计要求一致；该文件里另有一条断言 UNKNOWN 也返回 409 的旧用例，
  在基准 `21f3e72` 上同样失败（已用独立 worktree 复核），本轮未改变该语义。

### P1-06 真实任务闭环 —— 状态如实

- 软件状态机到交付的模拟闭环由既有 `test_offline_e2e` / `test_v13_e2e` 覆盖，
  L1 quick 全绿；本轮未新增并行链路。
- 真实出片入口仍为 `scripts/verify_real_production.py`（需要真实栈与 GPU）。
- `REAL_RENDER_VERIFIED` 未置位；未伪造任何 PASS。

## 4. 测试执行汇总

| 套件 | 结果 |
|---|---|
| `uv run --project backend python scripts/run_tests.py quick` | 817 passed / 24 skipped / 0 failed（13m10s，pytest 阶段） |
| 新增/定向后端测试 | `test_host_tool_resolution` 10、`test_installation_queue` 7、`test_existing_binding` 4、`test_existing_installation` 21、`test_existing_comfy_adoption` 7 |
| 相关回归 | `test_installation_persistence` 26、`test_runtime_activation` 16、`test_runtime_orchestrator` 22、`test_setup_cold_start` 14、`test_new_machine_setup` 15、`test_installer_redirects` 55、`test_resource_resolver_v2` 19 |
| 前端 | 56 files / 332 tests passed；`npm run build` 通过；`tsc --noEmit` 通过 |
| lint | `ruff check backend/shine ...` 全部通过 |

Windows-only 测试（真实 bootstrap、`.cmd` 执行、受管实例启停）在本环境跳过，
需在用户 Windows 机器或 Windows CI 上复跑。

## 5. 安全与兼容审查

- 未新增视频模型、未新建调度器、未新增加下载器、未重写 Launcher。
- 工具执行只接受白名单名称；不接受客户端提供的可执行路径。
- 外部安装：只读扫描 + 只登记；不写用户目录、不删除模型、不结束外部进程。
- 排队机制不删除目标锁：写权仍由租约证明，两个 Worker 不会同时写同一目录。
- 回执升级向后兼容：无路径的旧回执继续按目录校验；路径必须位于登记的安装根
  或用户声明的模型目录内。
- 数据库：无 schema 迁移；只新增阶段字符串与 JSON 字段，旧行可读。
- `WAITING_FOR_TARGET` 是新增可读状态，旧前端会把它当作非终态显示。

## 6. 回滚与升级

- 回滚：回退本分支提交即可；数据库无迁移，已写入的 `WAITING_FOR_TARGET`
  会被旧代码视作未知阶段，需人工对账（建议在无运行任务时回滚）。
- 升级：旧安装首次检测后，若历史回执缺少路径，重新执行一次“核验已有权重”
  即可生成路径级回执；不会重新下载，也不会删除旧回执。
- 用户本地补丁对账（审计样本四处）：
  1. `frontend/src/main.tsx` 超时兼容 —— 分支未改动该行为，升级后按 diff 对账。
  2. `frontend/src/App.tsx` 失联重连 —— 同上。
  3. `scripts/stop_shine.py` WAL checkpoint —— 本轮未改动，保留用户版本。
  4. `backend/shine/comfyops/host_env.py` 工具路径解析 —— 本轮已用白名单 +
     绝对路径 + `.cmd` 调用重新实现，语义覆盖该补丁；合并时以主线实现为准。

## 7. 尚需真实环境验证

1. Windows 11 上 bootstrap 安装 FFmpeg/pwsh/npm 后的即时检测（`.cmd` 实际执行）。
2. `I:\Trae\ComfyUI`（0.39.0，非 Git）采用后的 `bound=true` 与
   `variant-availability` 一致。
3. RTX 4070 12282 MiB 上 INT8 仍保持“低于门槛”的真实结论；
   低显存候选的资源准备与试镜。
4. 真实单镜头出片、任务落盘、质检与交付（`REAL_RENDER_VERIFIED`）。
