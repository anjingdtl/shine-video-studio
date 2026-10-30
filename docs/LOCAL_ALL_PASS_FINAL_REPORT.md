# 本地技术验收最终报告（V1.3 真机收尾轮 · 第二轮，2026-10-30）

本报告是本轮**实际执行**后的验收结论，不是计划或审查意见。所有数字都来自本轮在本机跑出的日志与产物；凡未实际运行的项一律标注为 NOT_RUN / BLOCKED_EXTERNAL / PENDING_HUMAN，没有把任何一项从 FAILED、UNKNOWN、NOT_RUN 或 MOCK 改写成 PASSED。

---

## 一、最终结论

**PARTIAL / BLOCKED**

本轮把上一轮剩下的三个真实缺口中的两个**在当前代码上真正关闭**了：

1. **真实 VLM 质检结论**由 FAIL 变为 **PASS_REAL**：4B 本地执行器在风格一致的真实镜头与带中文对白的真实镜头上返回了合同有效的 `passed` 结论，五道质量门全部通过；能力修订也已在本轮当前代码下用**真实正反例**重新资格化并发布为 `PRODUCTION_APPROVED`。
2. **多镜头连续生产 + EDL + FFmpeg 装配 + 最终成片**由 NOT_RUN 变为 **PASS_REAL**：三个有先后依赖关系的真实镜头在**同一次受管会话**中由正常 Worker 依次生成、自动质检，EDL 装配出的 15.02 秒成片通过全帧解码，`review_state = all_passed`。
3. **受管模式下的无人值守多镜头生产**此前根本不存在（见 3.4），本轮以产品功能的形式补齐并由真实数据跑通。

仍然不能判定 `LOCAL_TECH_ALL_PASS` 的原因只剩三类，且都不是本机代码问题：

- **托管 GitHub Actions**：账户计费/额度导致作业根本未启动（`BLOCKED_EXTERNAL`，非代码失败）。
- **浏览器端真实生产链路未跑**：真实生成链路已由正常 Worker 侧跑通，但尚未在浏览器里点选触发（`NOT_RUN`）。
- **人工验收**：权属、音质、画质与成片批准仍属用户职责（`PENDING_HUMAN`）。

若只判定"真实 H3 生成 → 受管交接 → 真实质检 → 多镜头 → 装配 → 能力资格化"这一条主链，状态为 **PASS_REAL**。

---

## 二、最终源码提交

| 项 | 值 |
|---|---|
| 分支 | `main` |
| 本轮提交 | 见文末"提交与推送"小节（提交后回填） |
| 远端 | `https://github.com/anjingdtl/shine-video-studio` |
| 轮次起点 | `50dd583916b4827ae9b058c2c4e0010bea1fa989`（上一轮收尾提交，本轮全部变更在其之上） |

---

## 三、本轮实际修复的问题

以下每一项都由**可复现的反例**定位，修复后有定向验证，且**没有**通过放宽断言、关闭生产前检查或降低验收标准来取得绿色。

### 3.1 真实 VLM 质检失败的根因：是验收数据与冻结风格冲突，不是模型（结论修正）

上一轮把 4B 的失败记为"模型与风格输入合同矛盾"、把 8B 的失败记为"超时"。本轮用真实数据把两件事都查清了：

**（1）4B 的"合同矛盾"是模型判对了。** 逐字比对冻结风格与验收镜头文本：

| 来源 | 文本 |
|---|---|
| 冻结风格 `5380cca5`（v2，项目当前冻结版本） `shot_blocking` | "The character **sits** centre-left, gaze directed into the depth of the frame" |
| 验收镜头 story_beat | "<Subject 1> **walks toward the camera** …" |

这是两份**书面输入**互相矛盾。8B 的原始响应逐字写明："Style requires character seated centre-left, but character is walking toward camera"，同时给出 `verdict: passed` 与 `findings: []`——模型自己承认冲突却仍判通过，属于自相矛盾。应用的 `require_style_input_checks` 因此按合同拒绝（`STYLE_INPUT_DECISION_CONTRADICTION`），**行为正确**。

> 上一轮的验收脚本沿用了旧风格草稿（v1 中文版）的剧本写法，与项目已冻结的 v2 不一致。**镜头必须服从已冻结的风格**，因此修的是镜头数据，不是合同，也不是放宽断言。

**（2）8B 在本机 16GB 显存上确实不可用，但不是"加载慢"。** 实测：

| 模型 | 单次真实审片 | 完成 token | 结论 |
|---|---|---|---|
| 8B FP8 | **370.66 秒** | 429 / 2048 | 结构化解码下每 token 成本极高，加隔离服务的冷启动与全量哈希后**超过 600 秒上限** |
| 4B bnb-4bit | **43.67 秒** | 572 / 2048 | 合同有效，可在本机无人值守运行 |

排除了三个常见误判：模型加载仅 5.1 秒、显存占用 12.06 GB（未超预算）；结构化解码回调经 `cProfile` 实测仅 **2.33 ms/token**（不是瓶颈）；全量模型清单哈希仅 5.4 秒（不是瓶颈）。瓶颈是 8B 权重本身在本机的生成吞吐。

**选型结论**：本机的生产审片执行器固定为 **Qwen3-VL-4B-Instruct（bnb-4bit，SHA-256 校验过的本地包）**。8B 保留登记但不再作为生产绑定。

### 3.2 验收镜头数据修正为与冻结风格一致

三支真实镜头全部重写为与冻结风格七项字段一致（坐姿居中偏左、视线入画深处、中近景、轻微手持呼吸、青蓝霓虹对暖黄路灯、霓虹逆光加路面反射、35mm 纪实颗粒浅景深、单个长镜头、夜雨环境声），并把 `camera` 与 `sound` 的自由文本字段同步对齐风格原文。中文对白只出现在 `dialogue` 字段（首次尝试把中文写进 story_beat，被 H3 规则包 `H3_ENGLISH` 正确拦截）。

### 3.3 本地 ASR 的能力边界被如实测出

真实 ASR（`whisper-tiny`）把台词「**雨**还没停，我们得走了」转写成「**月**还没停,我们得走了了」——同音字错误导致音频门硬失败（`WRONG_LINE`）。这是 tiny 模型的真实能力边界，**不是**把门放宽的理由。改用更短、无同音风险且已被本项目验证过的台词「我们走吧。」后，音频门真实通过。

### 3.4 受管模式下的多镜头生产此前根本跑不通（产品缺口，P1）

上一轮遗留的"续期链路（`run_managed_once`）未在真实数据上跑"，本轮定位到**两个真实产品缺口**：

| 缺陷 | 位置 | 原因 | 影响 |
|---|---|---|---|
| 正常 Worker 入口无法续接第二镜 | `worker.py` `run_one` | 受管交接完成后，idle keeper 继续持有独占租约，而 `run_one` 不返回协调器；下一镜重新申请租约时 `ResourceScheduler.admit` 必然失败并直接返回 `None` | **第一镜成功后第二个镜头永远排不上队**，即指令点名的"因资源残留而永久卡住" |
| 受管序列没有干净的收尾 | `worker.py` / `handoff_idle.py` | 渲染器被停下后没有释放独占租约、idle keeper 只能以"失败待对账"收场；同一实例也无法再次受管启动（`COMFY_STARTUP_ALREADY_RECORDED`） | 序列结束后留下一条 `RECONCILE_REQUIRED` 租约，后续所有作业被阻塞 |

**修复**（新增产品能力，非测试脚手架）：

- `worker.py` 新增 `run_managed_series(...)`：首镜持受管启动，后续每镜通过 idle keeper 的**租约转移**续接同一台已封闭的渲染器；序列之间**不释放独占资源、不重启渲染器**，因此两个镜头的串行模型阶段不可能重叠；`on_shot_complete` 回调让调用方在"源作业已 PASSED"时排入依赖镜头，而围栏渲染器在此期间持续持有独占准入。
- `worker.py` 新增 `release_managed_series(...)`：序列结束时先停止并证明自有进程组为空、再实测 RAM/VRAM 基线、最后释放独占租约，并停止 idle keeper 的续租——任一步失败都保留租约待对账，绝不假装硬件已释放。
- `comfyops/renderer_supervisor.py` 新增 `RendererSupervisor.stop_closed()`：只停止仍处于封闭状态的自有进程组。
- `comfyops/handoff_idle.py` 新增 `IdleHandoffKeeper.finalize()`：在已验证释放之后结束续租，且**不**把交接标记为失败。

### 3.5 上一轮已修、本轮复核仍成立的项

受管首次启动接线、提交期守卫、渲染器客户端 `trust_env=False`、围栏归属、审片工作目录、ComfyUI 节点白名单与启动期节点完备性校验、API 回环守卫、`uv`/`git`/`gh` 环境级修复——本轮全部复核通过，未出现回归。

---

## 四、完整测试结果（本轮实际执行）

### 4.1 软件测试

| 命令 | 结果 | 证据 |
|---|---|---|
| `uv sync --project backend --locked` | PASS，exit 0 | — |
| `uv run --project backend --locked pytest -c backend/pyproject.toml backend/tests` | 见 4.2 | `multishot-20261031/full-suite-final.out` |
| `uv run --project backend --locked ruff check backend/shine backend/alembic backend/tests` | PASS，exit 0 | — |
| `npm ci --prefix frontend` | PASS，exit 0，70 个包、0 漏洞 | `multishot-20261031/npm-ci.out` |
| `npm --prefix frontend run build` | PASS，exit 0（tsc -b + vite build，1591 模块） | — |
| `alembic upgrade head`（全新库） | PASS，exit 0，40 张表 | `multishot-20261031/alembic-new-20261031.db` |

### 4.2 全量后端回归

**1094 项通过、1 跳过、0 失败、0 错误**，exit 0（`multishot-20261031/full-suite-final.out`）。跳过项为需显式设置 `SHINE_REAL_COMFY_PROBE=1` 的真实机用例。上一轮基线为 1085 项通过、exit 0；本轮新增 9 项受管序列用例后共 1094 项，失败数始终为 0。

### 4.3 新增定向用例

| 文件 | 数量 | 覆盖 |
|---|---|---|
| `backend/tests/test_managed_series.py` | 9 | 序列参数校验（配方/审片人/长度）、无排队作业时返回空、渲染器非封闭启动拒绝 `stop_closed`、封闭自有进程组停止后证明为空且不可重复停止、idle keeper 的 `finalize()` 结束续租且不再判失败、未释放时 `close()` 仍判失败、收尾协调器非法时拒绝、渲染器无法停止时保留租约 |

---

## 五、真实 MiniMax-H3 多镜头生产结果（PASS_REAL）

**由正常 Worker 完成，不是独立脚本。** 证据：`.local-acceptance/multishot-20261031/multishot-run.json`、`run7.out` / `run7.err`、`resource-samples.json`。

| 项 | 实测值 |
|---|---|
| 视频生成核心 | 本机 MiniMax-H3 **Ref2VA INT8 ConvRot 剪枝版**，注册 profile `local-h3-ref2va-2img`（对白镜用 `local-h3-ref2va-2img-1aud`） |
| 会话形态 | **三镜在同一次受管会话内连续完成**，同一台受管渲染器（实例 `shine-local-h3`），独占租约全程未释放 |
| 受管启动 | 真实 ComfyUI 冷启动约 6 秒，`/object_info` 节点完备性校验通过，适配器以 `drained` 封闭状态起来，授权前提示词被拒（409） |
| 镜头 A | `a97c0d0f-afbf-4464-b014-5654d8c4bb45` **PASSED**（cut，独立镜头） |
| 镜头 B | `19e4eb79-9c8e-4e2d-a34d-94387e69aaa7` **PASSED**（中文对白 + 已批准音色资产，真实 ASR 音频门通过） |
| 镜头 C | `d99daf2d-1681-464d-86d6-9528275982ba` **PASSED**（`continuity.mode = last_frame`，软参考连续性，指向镜头 B 的 shot/job） |
| 三镜合计耗时 | **627.55 秒**（约 209 秒/镜，含真实 H3 生成约 110 秒、真实 ASR、真实 VLM 与渲染器围栏重启） |
| 每镜质量门 | technical / semantic / audio / style / final **全部 passed**，`is_mock = false`，`quality_verification = real` |
| 参考素材 | 复用历史**已人工批准并锁定**的角色图与场景图（SHA-256 与库内记录一致）；未新建或晋升任何主资产 |
| 连续性口径 | `last_frame` 是**软参考连续性**，不是硬视频锚定；本轮如实按软连续性标注 |

---

## 六、真实资源交接与质检结果（PASS_REAL）

| 阶段 | 结果 |
|---|---|
| H3 → 交接 | PASS_REAL：排空 → 停止 → 物理释放四个阶段全部记录，租约在镜头之间保持 ACTIVE，渲染器不重启 |
| ASR | PASS_REAL：受管审片服务启动、首次封闭围栏成立、预约成功、真实转写完成并产出隔离收据、`result_is_mock=false`、封存成功；对白镜的音频门真实通过 |
| VLM | PASS_REAL：4B 执行器返回**合同有效**的 `passed`（`verdict=passed`、`findings=[]`、`overall_confidence` 达 1.0、`style_input_checks` 七项齐全），隔离收据与执行器身份齐备，`result_is_mock=false` |
| 真实 VLM 单镜耗时 | **43.67 秒**（同镜头用 8B FP8 为 370.66 秒） |
| 失败后的行为 | **正确**：冲突/矛盾/超时的响应一律按合同拒绝并保留待对账，未伪造 PASS、未盲重投 |
| 峰值显存 | 渲染期整机 15400–15840 MiB 已用（WDDM 整机口径，非单进程精确值）；收尾后回落到约 1300 MiB，相关进程全部退出 |

**负向对照的真实结论**：负向对照镜头要求"亮黄雨衣 + 鲜红头发"，与已批准的黑色战术装、深色头发角色参考不符。4B 执行器**正确检出**该违例，给出合同有效的 `failed` 结论与硬 finding（`STYLE_INPUT_COSTUME`，confidence 0.95），风格门与 final 门均判 failed，系统未把它放行。

> 过程中的两处真实产品约束（均按设计生效，未绕过）：同一实例 id 与同一审片服务 id 的受管启动只能登记一次；第二次受管序列必须使用独立的渲染器实例名与审片服务名，但渲染器仍只认自己的进程组，因此上一进程退出后由下一进程接管同一端口。

## 六之一、VLM 能力重新资格化（PASS_REAL）

上一轮登记的 VLM 能力修订因**受钉住的审片客户端源码指纹变化**而按设计失效（`NATIVE_REVIEW_CLIENT_CHANGED`）。本轮在当前代码下重新完成资格化：

| 步骤 | 结果 |
|---|---|
| 新建 VLM_REVIEW 能力修订（`WorkflowRevision`） | 200，绑定 `local-qwen3-vl-4b`、`response_protocol=local-review-json-schema-v1`、当前 `client_review_sha256`、当前执行器身份与解码器哈希 |
| 活体执行器身份校验 `validate-native` | 200，`errors: []` → **STATIC_VALIDATED** |
| 绑定真实正反例调用 `native-execution-evidence` | 200 → **REAL_TESTED**（正向样本 `6da53379…` verdict=passed；负向样本 `9aef8d1c…` verdict=failed 且含硬 finding） |
| 发布 `publish` | 200 → **PRODUCTION_APPROVED** |

绑定的是两次**真实完成**的模型调用（`result_is_mock=false`、执行器身份与结构化输出证明齐备、租约已释放、输入哈希互不相同）；产品端点重新核验了结果摘要，没有接受任何外部声明。

---

## 七、真实 EDL 装配与最终成片（PASS_REAL）

| 项 | 实测值 |
|---|---|
| EDL | `schema_version 1.1`，3 个 clip，均为 `in_s=0.0 / out_s=5.0`、`transition=CUT`，逐条绑定 job/attempt/artifact 三元身份 |
| 装配实现 | 产品自身 `DeliveryAssembler`（FFmpeg 归一化 + concat），经 `POST /api/v1/projects/{id}/deliveries` |
| 成片 | `dacee60fed517075ad244bfbc697e9deb5bb8c8b20a4a4e0791c4e47bee660b6`，**3,718,157 字节** |
| 媒体核验 | `h264` **480×864**、`24/1` fps、**15.000 s**、360 帧；`aac` **15.021 s**；容器时长 15.021333 s |
| 全帧解码 | `ffmpeg -xerror -i … -f null -` **returncode 0，stderr 为空** |
| `review_state` | **all_passed**（三支源作业的 technical/semantic/audio/style/final 五门均真实通过且非 mock） |
| 交付记录 | `MediaArtifact(kind=delivery)` 带完整 `clips` 来源清单（job/attempt/artifact/sha256/时长），并写入审计 `DELIVERY_ASSEMBLED` |

---

## 八、仍未完成（真实缺口，不得掩盖）

| 项 | 状态 | 原因 |
|---|---|---|
| VLM 能力发布为 `PRODUCTION_APPROVED` | **PASS_REAL** | 正负两个真实样本均已绑定并发布（见第六之一节） |
| VLM 负向对照的合同合规 | **PASS_REAL** | 外观不符对照给出合同有效的 `failed` + 硬 finding `STYLE_INPUT_COSTUME`（confidence 0.95）；风格冲突类对照仍会被合同拒绝，这是正确行为 |
| 本地 ASR 的中文识别能力 | PASS_SOFTWARE（有边界） | `whisper-tiny` 存在同音字错误；已用可稳定核验的台词通过音频门，但更复杂中文台词的核验能力不足 |
| 8B FP8 的生产可用性 | BLOCKED_EXTERNAL（本机硬件） | 单次真实审片 370.66 秒，叠加隔离服务开销后超过客户端超时预算；需更大显存或更快的量化配置 |
| 20 项故障注入的完整清单 | PASS_SOFTWARE（部分）/ NOT_RUN（真实硬件部分） | 自动化对抗用例已覆盖多数状态机；真实 GPU 故障（如显存不足）未逐一注入 |
| 浏览器端全流程真实生产 | PASS_SOFTWARE（Mock 流程 20 条）/ NOT_RUN（真实生成） | 浏览器验收脚本走的是 Mock 夹具；真实生成链路已由 Worker 侧跑通，但未在浏览器内点选触发 |
| 人工权属、音质、画质与成片批准 | PENDING_HUMAN | 属用户职责，本轮未代行；本报告不构成任何艺术质量批准 |
| 托管 GitHub Actions | BLOCKED_EXTERNAL | 作业注解原文："The job was not started because recent account payments have failed or your spending limit needs to be increased." |

---

## 九、性能与可靠性实测

| 指标 | 值 | 采集方式 |
|---|---|---|
| H3 渲染器冷启动 | 约 6 秒 | 受管启动日志时间戳 |
| 单镜头真实生成 | 约 110 秒 | 提交 → 产物落盘 |
| 三镜连续生产合计 | **627.55 秒**（约 209 秒/镜） | 序列计时 |
| 单镜头真实审片（VLM 4B） | **43.67 秒** | 真实执行器计时 |
| 单镜头真实审片（VLM 8B FP8） | **370.66 秒**（隔离服务内超时） | 真实执行器计时 |
| 单镜头真实审片（ASR whisper-tiny） | 数十秒 | 服务访问日志 |
| 整机峰值显存 | 15400–15840 MiB 已用 | `nvidia-smi` 采样（WDDM 整机口径） |
| 模型切换后的残留 | 收尾后 GPU 约 1300 MiB，渲染器与审片服务进程全部退出 | 进程与显存复核 |
| 异常恢复耗时 | 本轮未单独测量 | NOT_RUN |

---

## 十、证据目录

`.local-acceptance/multishot-20261031/`

| 文件 | 内容 |
|---|---|
| `multishot-run.json` | 三镜真实生产的作业、Attempt、产物、ffprobe、质量门、模型调用与审计 |
| `run7.out` / `run7.err` | 真实多镜头序列的完整日志（受管启动、围栏、审片、租约转移） |
| `resource-samples.json` | 逐 2 秒的 GPU 与主机 RAM 采样 |
| `delivery.json` | EDL、交付响应、成片 ffprobe 与全帧解码结果 |
| `full-suite-final.out` | 最终提交对应的全量后端回归日志 |
| `asr-probe.json` | 真实 ASR 对两支成片的逐字转写原文 |
| `managed_recipe.local.json` | 本机受管配方（渲染器与两支审片服务） |

`.local-acceptance/vlm-qualification-20261031/`
`calibration-run.json`、`neg2.out` / `neg2.err`：正负对照镜头的真实生产与审片证据。
`vlm-qualification.json`：能力修订的登记、活体身份校验、执行证据绑定与发布结果。

`.local-acceptance/final-all-pass-20261030/`
`real-production-run.json`、`real-production-run*.log`、`resource-samples.json`、`managed-startup-node-inventory.json`：上一轮单镜头真实生产与节点清单。

仓库内证据：`docs/LOCAL_ENVIRONMENT_VALIDATION.md`、`docs/LOCAL_FINAL_CLOSEOUT_20261025.md`。

模型权重、真实媒体、数据库与审片原始响应均留在本地排除目录，未进入 Git。

---

## 十一、遗留的外部阻断

1. **GitHub 账户计费/额度**：四个托管作业全部 2–3 秒内失败、步骤数为 0，注解原文如上。需要账户所有者处理账单或提高额度。本轮未修改任何账单或支付设置。
2. **本机 16GB 显存**：8B FP8 结构化审片在本机需要 370 秒以上，叠加隔离服务开销会超过客户端超时预算，因此生产绑定固定为 4B。若要复核 8B，需要更大显存的机器或更快的量化配置。

---

## 十二、对用户的明确交付建议

1. **成片可直接查看**：`.local-acceptance/multishot-20261031/acceptance/artifacts/1f743836-a00c-4787-8343-4f1a24ed9135/delivery/dacee60fed517075ad244bfbc697e9deb5bb8c8b20a4a4e0791c4e47bee660b6.mp4`（15.02 秒，480×864@24fps，三镜 CUT 串联，`review_state=all_passed`）。
2. **先解决 GitHub 额度**，托管 CI 才会真正跑起来；在此之前本地等价命令已全部通过。
3. **VLM 侧建议**：若需要在生产中保留"严格风格冲突即失败"的硬判定，本机 16GB 上的 4B/8B 都不足以稳定满足响应合同；建议在更大显存的机器上复核 8B FP8，或把响应合同拆成"检出冲突"与"判定失败"两个可独立签收的阶段（**不得**为通过而放宽既有判定）。
4. **最终影片与权属批准仍需人工**；本报告不构成对画质、音质、版权或成片的任何批准。
