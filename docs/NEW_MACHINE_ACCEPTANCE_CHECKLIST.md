# 完整新机验收清单

目标保持「完整新机 PASS」：从空白 Windows 下载项目，到选择安装盘、安装固定 ComfyUI/H3 环境、配置真实审片服务，再由 Web 工作台启动真实生产系列并产出通过全部必需质量门的影片。资产晋升、音色/风格冻结和不确定结果对账仍遵守项目的人工作业约束。

本表记录已取得的证据及缺口，不将分层通过合并成完整验收通过。

| 要求 | 当前证据 | 判定 |
|---|---|---|
| 首次安装前选择目录，避免默认占用 C 盘 | bootstrap `-StorageRoot`、系统盘默认拒绝、本地 ZIP 合同；设置页保存/刷新、API/Worker 同一偏好、实际 F 盘应用依赖与缓存 | 已实现；干净 OS 全套工具首装仍待验收 |
| 足够的目标盘空间，保留恢复快照 | 154 个发行包实测展开大小及 venv 大小，版本 1.1 安装计划加入实例/wheelhouse/三份快照预算；实际目标盘不足的反例 | 已实现和定向验证 |
| 操作员、逐计划授权、不可重放审批 | 安全密码输入与哈希、已有配置保留、审批后新哈希、刷新恢复已批准计划、换盘作废旧计划 | 合同及浏览器验证通过 |
| 固定依赖可真实安装 | 153 个 wheel 与一个 sdist，4,317,653,223 字节大小/哈希核验；sox 离线构建；pip check；ASR/VLM/音频库导入 | PASS_SDK_ONLY |
| 源码产物身份对应实际安装代码 | 独立自编小包同版本 A→B 反例，修复前仍运行 A，修复后运行 B；仅源码产物强制哈希锁重装 | 已真实复验 |
| 固定 ComfyUI 与节点可启动 | 6 个固定源码归档，25,943,309 字节核验；Windows 受控进程组、关闭队列、CPU 启动、真实 object_info；确认退出后释放 CPU 测试租约 | CPU_CONTRACT_OBSERVED；不是 GPU/模型验收 |
| 默认工作流的节点与参数契约 | 两个默认 PDD8 profile 对真实 CPU object_info 的节点/参数名验证均无缺失 | 结构层通过；模型文件选择和推理未验证 |
| 完整模型物料在新目录可用 | 60.8GB 完整清单有固定身份，实际仅完成 Python 发行包和源码归档下载 | 权重下载、加载 NOT_RUN |
| Web 设置交接给受管冷启动 | 已移除准备链普通 START；明确实例身份；候选配方经操作员确认后在同一 SQLite 激活，API/常驻 Worker 每轮采用，身份变化即阻断；真实 OS Worker 热读取合同通过 | 准备与持久激活合同通过；真实服务首启仍待闭环 |
| 已安装实例交给首次及后续生产启动 | 安装身份与每次运行身份已分离；`test_managed_run_binding.py` 15 项覆盖首次、后续、安装证据变化和继续运行复验 | 合同通过；真实服务首启仍待实机 |
| 审片路由由真实身份探测建立 | 新增 `/setup/services/validate`：短暂启动已安装 VLM/ASR，读取 `/v1/models` 和严格协议，自动创建 8B VLM + 0.6B ASR 路由并停止服务；种子脚本改为从服务发现模型 ID | 合同通过；真实 60.8GB 实机待验证 |
| L4 真实服务/节点/路由就绪 | L4 不再固定 PENDING；只有当前配置身份、live object_info 工作流契约、审片路由/能力哈希全部匹配时才 READY，任一变化即失效 | 合同通过；真实 clean Windows 待验证 |
| 真实生产系列、恢复、最终交付 | 后端回归覆盖离线系列、幂等、租约、修复及交付；本轮没有真实 H3 生产或模型质量证明 | 代码/合同通过；完整目标仍 PARTIAL |

可选 `local-h3-ref2va-pdd8-upscale-2img` 在这套干净节点安装中缺少 `MinimaxH3LatentUpscaler3DRefineHandoff`，没有把它登记为默认已支持工作流。该缺口单独保留，不能用上游基本 upscaler 的存在替代该自定义 Handoff 节点。

## 证据位置

- `NEW_MACHINE_DEPLOYMENT_FIX_20261101.md`、`NEW_MACHINE_DEPLOYMENT_TESTLOG.md`：本地回归与边界。
- `LOCAL_STORAGE_SETTINGS.md`：安装盘设置、首次引导及已有文件保护。
- `.local-acceptance/new-machine-goal-20261101/runtime-verification/`：固定包、源码构建、pip check、导入与磁盘测量。
- 同目录 `source-reinstall-before/` 与 `source-reinstall-after/`：A/B 源码重装反例及修复后实装结果。
- 同目录 `storage-stack-verification/`：真实应用依赖安装、Mock 栈接口验证与按出生身份关停。
- 同目录 `comfy-cpu-contract-after-name-fix/`：真实 CPU 节点/参数契约及私有 SQLite 租约记录。首次脚本因 Job Object 名称不符合接口约束而在创建进程前被拒绝，原失败记录保留；修正调用后重验。

后续先修复受管冷启动交接和审片自动配置，再进行完整物料与真实生产验收；所有未完成项保留原要求，不以 Mock、CPU 或元数据证据替代。
