# 基础配置与运行时收敛验收

本轮在主仓 `F:/ClaudeWorkSpace/projects/shine-video-studio` 的 `main` 原地修复，
基于 `0384b97`。输入为基础配置截图及用户提供的夜间运行日志。

## 已修复链路

| 用户遇到的问题 | 权威事实、裁决与执行者的收敛 |
| --- | --- |
| 页面发现 ComfyUI，后台却当成裸机 | `resolve_installation` 同读实例、选择记录与持久检索索引。选定安装与端口随整备请求保留，Windows 路径按 Path 语义比较。 |
| 标准多行 YAML、外置权重和 aliases 漏识别 | `declared_model_family_roots` 统一解析分组 YAML / JSON，presence、H3 绑定和出图核验复用同一目录解析。 |
| 已核验仍无法采纳，H3 与出图证据互相覆盖 | 整备核验结果交给既有采纳执行者。机器级采纳可无作品，增加出图能力保留 H3 证据。已有目录与其文件保持原位。 |
| 候选方案被自动选中，又阻断出图 | 后端给出默认基线，候选由用户显式选择。出图整备不携带 H3 variant。原有候选启动证据要求继续由同一合同裁决。 |
| 运行配方被两项能力反复覆盖 | 一次整备生成一份物理渲染器配方，出图绑定消费同一份。Shine 节点从本安装数据目录通过 ComfyUI 既有配置加载。 |
| 空库整备停在项目门禁 | 机器级服务验证消费已登记规范模板，验证与激活使用同一执行链，无需虚构作品行。 |
| 页面上下一步、文件徽标与端口互相矛盾 | 删除额外的浏览器扫描徽标与重复下一步，选择区读 summary 文件事实，端口读选定安装，阶段投影按依赖顺序推进。 |
| 一个方案的成功出片让后面的阶段提前变绿 | 阶段投影要求前序事实成立，运行事实挂载机器合同或本能力真实证据。 |
| 长核验时显示上一已跳过步骤 | 任务领取时持久化本次 current 与 message，SKIPPED 计入已完成。取消任务不能被旧执行结果复活。 |
| 冷启动动态图先探测尚未启动的渲染器 | Worker 先执行原有受管 before_prepare，再读真实 object_info 编译和校验。 |
| 预提交失败租约永久占位 | 扩展既有 orphan 回收裁决。FAILED attempt 无 graph intent、ack、外部 id、产物或激活记录，且进程与端口证明成立时回收。已有外部提交标记继续保留对账。 |
| 人工升级被 repair 预算拦住 | 人工升级分支在自动重试预算判断之前，保留原 retry 计数。 |
| PDD8 触发 SageAttention ABI 错误 | 动态 PDD8 模型链移除该 patch，required_nodes 同步移除，native20 保留其原链。 |
| 实例参数错误误导用户重绑配置 | 原有 mismatch 拒绝同时给出 requested_instance_id 与 recipe_instance_id。 |

## 接手轮补充修复（2026-11-18，Codex 额度耗尽后接手）

Codex 留下的最终 8 分片全量跑完后只有 2 个分片不干净，共 3 个用例。接手后按三原则
逐个收敛，不新增平行链路：

| 失败用例 | 根因 | 收敛 |
| --- | --- | --- |
| `test_setup_preparation_instance.py::test_instance_step_skips_when_installation_exists`、`::test_enqueue_includes_instance_only_on_bare_machine` | 契约冲突：INSTANCE 步已改为「外部采纳（adopted）只读跳过、受管半成品交回同一架实例阶梯继续」，新测试 `test_setup_machine_reuse.py::test_existing_managed_installation_still_asks_the_same_instance_ladder` 已断言新契约；两个旧测试仍断言「有任何 instance 就跳过」 | 旧测试收敛到唯一裁决（adopted 才跳过），保留「外部复用安装不被改动」的保护意图；受管继续阶梯由新测试覆盖。11 例全过 |
| `test_existing_installation.py::test_verify_endpoint_requires_local_user_and_never_copies`（FAILED + teardown ERROR） | **真实产品缺陷**：`security.sanitize_payload` 的 `_ABSOLUTE_PATH` 正则 `[A-Za-z]:\\[^\s\"']+` 遇空格即停。验收路径含空格+中文（`F:\ShineTest\基础配置终验 20261118\…`），审计 root 被脱敏成残缺串 `[local-path] 20261118\…`，重复核验的 `{root, '[local-path]'}` 相等匹配 miss → 又哈希一遍模型字节。隔离跑路径无空格才侥幸通过 | 在唯一脱敏执行者里新增「整串即绝对路径 → 稳定输出 `[local-path]`」（`_ABSOLUTE_PATH_WHOLE`，覆盖空格/中文/UNC/正斜杠）；嵌入长文本的路径仍按原子串脱敏。含空格中文 basetemp 下 34 个受影响用例全过 |

## 验证记录

定向回归、完整前端和 8 分片后端日志保留在主仓 `.local-data/setup-repair-*.log`。
完整后端使用 `run_backend_shards.ps1` 按文件均分 8 片，并设置
`CODEBUDDY_SAFE_DELETE_ENABLED=0`。

- Codex 首轮最终全量（`shards-20261118-003651-665`）：chunk-0 / chunk-2 不干净，
  其余 6 片 clean；失败即上表 3 例。
- 接手修复后复验（`shards-20261118-011127-939`，日志
  `.local-data/setup-repair-backend-rerun.log`）：**8 分片全部 exit 0，
  `shards_with_failure_markers=0`，274 个测试文件零 FAILED / ERROR**。
- 前端沿用 Codex 最终全量（`.local-data/setup-repair-frontend-final2.log`）：
  83 文件 / 431 例全过；接手轮改动仅 backend，不影响前端。

已执行 TypeScript / Vite build、Ruff、Copilot 生成合同检查和
`uv --no-config sync --project backend --locked --dry-run`。接手轮另对
`shine/security.py` 与改动测试文件复跑 Ruff，干净。

## 真机与外部日志的范围

- 真机整备从仓库外调用正式启动入口，数据目录为
  `F:/ShineTest/真机整备 20261118`，API / Web 为 `8001 / 5174`，
  目标 ComfyUI 端口为 `8196`。源码仍从本主仓加载。
- 首轮真机发现同一 Windows 文件大小写被字符串比较误判，产生额外本地缓存复制。
  修复后新增了大小写复用回归，并用最新 Worker 重跑整备。
- 本机日常库 `D:/Shine/data` 的只读复核为 ACTIVE，服务核验有效。
  本轮真机整备使用独立数据目录。
- 用户提供的 RTX 4070 日志记录了真实 PDD8 产物及 PASSED，且
  `acceptance_status=pending_review`。这份证据的范围属于该用户机，
  本轮未将其改写为本机重新出片或人工验收。
- 日志中的 `start_real_comfyui.ps1` 与对应自定义快捷方式脚本未被当前主仓跟踪。
  当前正式启动链 `start_dev.ps1 → start_shine.ps1` 只启动工作栈，
  渲染器沿既有受管执行链启动。日志中的额外裸 ComfyUI 需要在用户机确认归属。
- 当前日常库未配置本地 VLM / ASR reviewer，两个审片包为 UNVERIFIED_ORIGIN。
  Worker 改动后的真实 reviewer 正负校准及真实新 H3 出片仍待相应本机能力验收。

## 最终结果

- **后端全量回归绿**：修复后 8 分片复验全 exit 0、零 FAILED/ERROR（274 文件）。
- **真实浏览器复核（本机日常库 `D:/Shine/data`，API 8000 / Web 5173，用户授权启动）**：
  基础配置页 `#d/setup` 机器裁决进度条 **100% · 整备完成 · 真实制作已就绪**；
  「使用的 ComfyUI」= `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI` · 端口 8188 ·
  **你自己的安装（只读复用）**；H3 视频生成「① 文件已就绪并核验 ② 已验证可运行 · 可试镜」；
  H3 能力卡「权重已逐字节核验，并已绑定到当前 ComfyUI 实例」；基线 INT8 ConvRot 选中
  「本机已核验 · 已绑定」，NVFP4 / w6a8 候选仅「候选 · 可下载」**不被自动选中**；
  主按钮「确认并完成配置（不下载）」与「真实试镜一次」可用，不强制下载、不卡项目门禁；
  可选审片 / 语音如实标「未准备」并列具名缺件，不阻塞出片。顶栏、机器卡、能力卡三处
  同读一份裁决。截图：`.local-data/setup-repair-live-setup-ready.png`（Git 忽略）。
  这正面闭环了用户报障：已有 ComfyUI + H3 权重现在能在 web 页面被判为就绪并进入生产。
- **未发起新的真实 H3 出片**：就绪裁决与「真实试镜一次」入口已验证可用，但真实渲染
  占用显存与较长时间，本轮未启动；「可试镜」如实表示尚需真实出一次片，不冒充已验收。
- **诚实边界**：本轮改了 `worker.py`（被 `client_review_identity` 钉住），旧 VLM revision
  按设计失效；真实 reviewer 正负校准与真实新 H3 出片仍待相应本机能力验收，不得声称已校准。
  本机为 RTX 5080（非用户 RTX 4070）；用户机 4070 的真实 PDD8 产物 PASSED 证据范围属于
  该用户机，本轮未改写为本机重新出片或人工验收。
- **工作栈状态**：日常栈按用户授权启动用于复核，复核后保持运行（用户日常工作台，
  渲染器空闲）；停止入口 `scripts\stop_shine.ps1 -DataDir "D:\Shine\data"`。
- **临时产物**：分片 `bt-*` basetemp 与复现目录留在 `F:/ShineTest/`（非系统盘），
  `chunk-*.log` 按 AGENTS.md 留存为证据；本轮未做批量删除。
