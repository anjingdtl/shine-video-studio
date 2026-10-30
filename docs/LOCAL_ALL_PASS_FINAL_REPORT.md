# 本地技术验收最终报告（V1.3 真机收尾轮，2026-10-30）

本报告是本轮**实际执行**后的验收结论，不是计划或审查意见。所有数字都来自本轮在本机跑出的日志与产物；凡未实际运行的项一律标注为 NOT_RUN / BLOCKED_EXTERNAL，没有把任何一项从 FAILED、UNKNOWN、NOT_RUN 或 MOCK 改写成 PASSED。

---

## 一、最终结论

**PARTIAL / BLOCKED_EXTERNAL**

本轮把项目从"真实生产闭环从未验证"推进到**真实 H3 视频已由正常 Worker 生成并落盘、真实 ASR 与真实 VLM 已被受管链路调用并按合同阻断**，但**本地技术全 PASS 的判定条件尚未全部满足**，原因有三：

1. 真实 VLM 质检未取得合格结论（4B 模型与风格输入合同矛盾；8B FP8 模型在本机 16GB 显存上推理超时）。系统按合同**阻断并保留待对账**，没有伪造 PASS。
2. 多镜头连续生产、EDL 装配与最终成片交付未在本轮执行（NOT_RUN）。
3. 托管 GitHub Actions 因**账户计费/额度**导致作业根本未启动（BLOCKED_EXTERNAL，非代码失败）。

若只判定"真实 H3 生成 → 受管交接 → 真实质检调用"这一条主链，状态为 **PASS_REAL（生成与交接部分）/ FAIL（质检结论部分）**。

---

## 二、最终源码提交

| 项 | 值 |
|---|---|
| 分支 | `main` |
| 本轮提交 | `087d737` → `4ae33cd` → `cf8d262` → **`457acc7`**（本轮收尾），已全部推送至 `origin/main`，快进推送、未 force |
| 推送后复核 | 本地 `main`、`origin/main` 与 `FETCH_HEAD` 三者一致为 `457acc7e9bdc776056f524dae2013338b398e8ea`，`rev-list --left-right --count origin/main...main` 为 `0 0`，工作区干净 |
| 远端 | `https://github.com/anjingdtl/shine-video-studio` |
| 基线 | 轮次开始时本地 `53a2a81`，远端 `f6b196a`，本地超前 1 |

---

## 三、本轮实际修复的问题

以下每一项都由**可复现的反例**定位，修复后有定向验证，且**没有**通过放宽断言、关闭生产前检查或降低验收标准来取得绿色。

### 3.1 首次受管启动完全无法接入正常 Worker（阻断级）

| 缺陷 | 位置 | 原因 | 影响 |
|---|---|---|---|
| 首次启动上下文被类型检查拒绝 | `worker.py` `_run_comfy_job` | 只接受"上一轮交接的延续"类型，首次启动上下文永远抛 `HANDOFF_COORDINATOR_INVALID` | 首次受管生成 100% 失败 |
| 提交期守卫引用只有延续才有的属性 | `worker.py` 提交守卫 | 使用 `managed_continuation.previous.*` | 首次启动在提交后崩溃，结果被标记为"提交结果不明" |
| 渲染器客户端从不满足受管要求 | `worker.py` | `ComfyUIClient` 默认 `trust_env=True`，受管路径要求 `False` | 本地渲染器流量可能被环境代理劫持 |
| 审片围栏 ID 混入 release config | `handoff_startup.py` | `verify_cold_review_fence` 收到不认的参数 | 首次真实审片调用直接 TypeError |
| 审片服务的工作目录是 python.exe 文件本身 | `managed_recipe.py` | `Path(python).resolve()` 未取父目录 | 审片进程永远无法启动 |

### 3.2 ComfyUI 节点白名单不完整（P1）

`config/managed_recipe.json.example` 原先使用 `--disable-all-custom-nodes` 且只放行 `shine_comfy_handoff`，而真实 Ref2VA 工作流还需要 MiniMax-H3、KJNodes、PDD-Acc、Latent Upscaler、SolAttn、Video Helper Suite 与 `shine_h3_sampling_mode.py`。

- 以 `/object_info` 枚举全部已登记生产工作流，得到 **29 个节点类型、其中 12 个来自自定义节点包**，并用真实 ComfyUI 的 `/object_info` 逐一确认其提供目录。
- 修正后的白名单恰好加载 7 个供应商，其余全部跳过；`/object_info` 返回 1281 个节点类型，**13 个必需节点全部存在，missing 为空**。
- 更关键：`--base-directory` 会把 ComfyUI 的 `custom_nodes` 与 `models` 一并重定向到 BASE 目录，导致真实节点包与 H3 权重都不可见（并使适配器身份核验失败）。配方已去掉该参数，与用户自己的生产启动器一致。
- 新增**启动期节点完备性校验**：受管启动在登记实例前读取实时 `/object_info`，任一注册工作流所需节点缺失即以 `COMFY_REQUIRED_NODE_MISSING` 失败关闭，并把缺失清单写进启动日志。

### 3.3 本地 API 访问控制（P2）

- `security.py` 把 `0.0.0.0`（通配绑定地址，非回环）当作回环主机。`config.py` 用它校验 `SHINE_COMFYUI_URL`，因此 `http://0.0.0.0:8188` 会被误判为"本机"，参考素材可能发往可达网络的服务。已删除该条目并改用统一的 `is_loopback_peer`（正确处理 `::ffff:127.0.0.1`、拒绝 `0.0.0.0` 与 `::`）。
- 普通项目 API 此前**完全没有访问控制**，只依赖 uvicorn 的绑定地址。现新增应用层回环守卫：非回环客户端一律 403；需要局域网暴露时必须显式设置 `SHINE_ALLOW_NON_LOOPBACK_API=1`。这不破坏本地 Web 使用（浏览器与 Vite 代理都走回环）。

### 3.4 环境级报错（上一轮已修，此处复核）

- 全局 `~/.gitconfig` 的 `credential.helper` 指向已删除的 PortableGit GCM → 已删除该条，系统级 `manager` 继续生效。
- 全局 `uv.toml` 把阿里云镜像设为主索引，与锁文件记录的 `https://pypi.org/simple` 不一致 → 改为与锁文件逐字一致，镜像降为回退索引。**现在无需任何环境变量覆盖**。

---

## 四、完整测试结果（本轮实际执行）

### 4.1 软件测试

| 命令 | 结果 | 证据 |
|---|---|---|
| `uv sync --project backend --locked` | PASS，exit 0 | — |
| `uv run --project backend --locked pytest -c backend/pyproject.toml backend/tests` | 见 4.2 | `full-suite-allpass-20261030.log` |
| `uv run --project backend --locked ruff check backend/shine backend/alembic backend/tests` | PASS，exit 0 | — |
| `npm ci --prefix frontend` | PASS，exit 0，70 个包、0 漏洞 | `npm-ci-20261030.log` |
| `npm --prefix frontend run build` | PASS，exit 0 | — |
| `alembic upgrade head`（新库） | PASS，exit 0，8 个版本 | `alembic-managed-startup-20261030/` |
| `./scripts/verify_local_stack.ps1 -ApiPort 8127` | PASS，exit 0 | — |
| `./scripts/verify_stack_operations.ps1` | PASS，exit 0 | — |
| `python scripts/verify_v13_web.py`（Playwright 1.62.0 + Chromium 151） | PASS，exit 0，20 条流程全部 MOCK_VERIFIED、`api_errors: []` | `web-e2e-final-20261030.log` |

### 4.2 全量后端回归

**1085 项通过、1 跳过、0 失败、0 错误**，exit 0，耗时 434.62 秒（`full-suite-allpass2-20261030.log`）。跳过项为需显式设置 `SHINE_REAL_COMFY_PROBE=1` 的真实机用例。轮次起点基线为 **1038 项通过、exit 0**；本轮新增受管启动、API 回环守卫与节点完备性用例后共 1086 项，失败数始终为 0。

### 4.3 新增定向用例

| 文件 | 数量 | 覆盖 |
|---|---|---|
| `backend/tests/test_managed_startup.py` | 14 | 意图先于进程、无独占租约拒绝启动、外部租约冲突、未封闭拒绝启动、基线不足拒绝启动、**节点缺失时拒绝登记渲染器**、令牌不落 SQLite、崩溃恢复只停可证明归属者、所有者存活时不介入、已退出进程不终止、首次激活不确定时保留租约、首次封闭绑定、封闭被拒不产生围栏、未受管监督器不能封闭 |
| `backend/tests/test_api_loopback_guard.py` | 26 | `0.0.0.0`/`::`/`::ffff:127.0.0.1` 判定、渲染器 URL 校验、非回环客户端 403、显式放开 |

---

## 五、真实 MiniMax-H3 生产结果（PASS_REAL）

**由正常 Worker 完成，不是独立脚本。** 证据：`.local-acceptance/final-all-pass-20261030/real-production-run.json` 与 `real-production-run-8b.log`。

| 项 | 实测值 |
|---|---|
| 视频生成核心 | 本机 MiniMax-H3 **Ref2VA INT8 ConvRot 剪枝版**（`minimax_h3_ref2va_pruned_int8_convrot.safetensors`），注册 profile `local-h3-ref2va-2img` |
| 受管启动 | 真实 ComfyUI 由受管进程组启动，**5.6–5.9 秒**冷启动，`/object_info` 1281 个节点类型，13 个必需节点全在 |
| 关闭启动 | 适配器上报 `status: drained`，提示词在授权前被拒（409） |
| 激活 | 意图先写库，再恢复队列；`COMFY_ACTIVATION_CONFIRMED` |
| 提交 | 提示词图哈希落库后才提交；两张参考图按 profile 的 `media_transfers` 上传 |
| **真实生成耗时** | **约 110 秒**（提交 00:55:17.87 → 产物落盘 00:57:07.78） |
| 产物 | MP4，**1,822,918 字节**，SHA-256 `a90f67fbd5c82cc3a7d80fe689c444a5b84cd4914e7835fa9ed1f432b3e645f0` |
| 媒体核验（ffprobe） | `h264` **480×864**，`24/1` fps，时长 **5.165 s**；`aac` 音轨 **5.166 s**；容器时长 5.166 s |
| `is_mock` | **false** |
| 参考素材 | 复用历史**已人工批准并锁定**的角色图、场景图（SHA-256 与库内记录一致，文件在位）；未新建或晋升任何主资产 |

## 六、真实资源交接结果（PASS_REAL，部分）

| 阶段 | 结果 |
|---|---|
| H3 → 交接 | PASS_REAL：排空 → 停止 → 物理释放四个阶段全部记录，租约全程未释放 |
| ASR | PASS_REAL：受管审片服务启动、首次封闭围栏成立、预约成功、**真实转写完成并产出隔离收据**、`result_is_mock=false`、封存成功 |
| VLM | **FAIL**：4B 模型把风格输入字段判为不一致却又给出 passed（`STYLE_INPUT_DECISION_CONTRADICTION`），应用按合同拒绝；换 8B FP8 后在本机 16GB 显存上推理超过隔离服务 600 秒上限，隔离服务把结果标记为未知并**拒绝释放**（503），作业进入 `COMFY_HANDOFF_RECONCILE_REQUIRED` |
| 失败后的行为 | **正确**：未伪造 PASS、未盲重投、租约保留待对账、渲染器与审片服务进程均已退出，GPU 回落到 1356 MiB |
| 峰值显存 | 渲染期与审片期分别观察到 15840 MiB 已用（整机口径，WDDM 下为整机聚合值，非单进程精确值） |

**结论**：H3→ASR 的真实自动交接与物理释放成立；VLM 的**调用链**成立且阻断正确，但**尚未取得合格质检结论**，因此阶段 E 判 FAIL。

## 七、仍未完成（真实缺口，不得掩盖）

| 项 | 状态 | 原因 |
|---|---|---|
| 真实 VLM 质检资格重新认证 | FAIL | 4B 与风格输入合同矛盾；8B FP8 在 16GB 卡上超时。需更强模型或更好的提示/量化配置 |
| 多镜头连续生产（≥3 镜，真实 H3） | NOT_RUN | 本轮单镜头已通，续期链路（`run_managed_once`）未在真实数据上跑 |
| EDL 生成 + FFmpeg 装配 + 最终成片 | NOT_RUN | 依赖多镜头 |
| 20 项故障注入的完整清单 | PASS_SOFTWARE（部分）/ NOT_RUN（真实硬件部分） | 自动化对抗用例已覆盖多数状态机；真实 GPU 故障（如显存不足）未逐一注入 |
| 浏览器端全流程真实生产 | PASS_SOFTWARE（Mock 流程 20 条）/ NOT_RUN（真实生成） | 浏览器验收脚本走的是 Mock 夹具 |
| 人工权属、音质、画质与成片批准 | PENDING_HUMAN | 属用户职责，本轮未代行 |
| 托管 GitHub Actions | BLOCKED_EXTERNAL | 作业注解原文："The job was not started because recent account payments have failed or your spending limit needs to be increased." |

## 八、性能与可靠性实测

| 指标 | 值 | 采集方式 |
|---|---|---|
| H3 渲染器冷启动 | 5.6–5.9 秒 | 受管启动日志时间戳 |
| 单镜头真实生成 | 约 110 秒 | 提交→产物落盘 |
| 单镜头真实审片（ASR） | 数十秒（服务启动+围栏+转写+封存） | 服务访问日志 |
| 单镜头真实审片（VLM 4B） | 数十秒，返回但合同被拒 | 同上 |
| 单镜头真实审片（VLM 8B FP8） | **>600 秒超时** | 隔离服务上限 |
| 整机峰值显存 | 15840 MiB 已用 | `nvidia-smi` 采样（WDDM 整机口径） |
| 主机 RAM | 见 `resource-samples.json` | `GlobalMemoryStatusEx` 采样 |
| 异常恢复耗时 | 本轮未单独测量 | NOT_RUN |
| 连续三镜完成时间 | NOT_RUN | 依赖多镜头 |
| 模型反复切换后的残留 | 收尾后 GPU 1356 MiB、相关进程全部退出 | 进程与显存复核 |

## 九、证据目录

`.local-acceptance/final-all-pass-20261030/`

| 文件 | 内容 |
|---|---|
| `real-production-run.json` | 真实生产的作业、Attempt、产物、ffprobe、质量门、审计与交接日志状态 |
| `real-production-run.log` / `real-production-run-8b.log` | 两次真实生产的完整日志（4B 与 8B） |
| `resource-samples.json` | 逐 2 秒的 GPU 与主机 RAM 采样 |
| `managed-startup-node-inventory.json` | 真实受管启动的节点清单与封闭启动证明 |
| `managed-startup-probe.log` | 受管启动探针日志 |
| `bootstrap-direct-run2.log` | 白名单修正后的 ComfyUI 启动日志（精确加载 7 个供应商） |
| `managed-startup-real-20261030/` | 真实 ComfyUI 受管启动探针 |

仓库内证据：`docs/LOCAL_ENVIRONMENT_VALIDATION.md`、`docs/LOCAL_FINAL_CLOSEOUT_20261025.md`、`.local-acceptance/closeout-20261025/` 下的各次回归日志。

模型权重、真实媒体、数据库与审片原始响应均留在本地排除目录，未进入 Git。

## 十、遗留的外部阻断

1. **GitHub 账户计费/额度**：四个作业全部 2–3 秒内失败、步骤数为 0，注解原文如上。需要账户所有者处理账单或提高额度。本轮未修改任何账单或支付设置。
2. **本机 Git 凭据助手**：已按授权修复；`gh` 仍返回 401，与托管 CI 状态读取是同一个外部限制。

## 十一、对用户的明确交付建议

1. **先解决 GitHub 额度**，托管 CI 才会真正跑起来；在此之前，本地等价命令已经全部通过，可作为替代证据。
2. **VLM 侧需要一次模型选型**：本机 16GB 显存下 8B FP8 会超时，4B 无法稳定满足 7 项风格输入一致性的合同要求。建议在更大显存的机器上复验 8B FP8，或收窄该合同到模型可稳定判断的范围（**不得**为通过而放宽合同）。
3. **多镜头与装配**在单镜头通过后即可复用同一受管链路推进；建议以 3 镜连续生产作为下一次验收的最小单元。
4. **最终影片与权属批准仍需人工**；本报告不构成对画质、音质、版权或成片的任何批准。
