# 全 PASS 收尾核对表（2026-10-26）

Actual seven-field continuation (2026-10-26): **VLM qualification remains FAILED / withheld**. The real positive and masked-face negative passed. The empty-street call returned an unusable received response (`FAILED`, released lease, no retry); its initial cleanup hit a missing executor-identity field, after which an explicit continuation verified the exact owner and physically exited the process. The remaining walking control ran once separately and still falsely passed: its checklist calls walking toward camera consistent with the unchanged frozen sitting requirement. Re-reading both source requests confirmed the same frozen Style snapshot; this is not a substituted-style control. Coverage alone therefore does not establish correct input reasoning. No VLM capability was published, no production gate/history was rewritten, and no failed H3 task was replayed. All stages are released and ComfyUI has been restored idle. Current ASR qualification is unchanged. See [actual controls and failure evidence](evidence/local-closeout-20261025/style-input-real-calibration.json). Next work must separate text input consistency from pixel review; full-product acceptance remains **PARTIAL**.

Current protocol hardening (2026-10-26): **639 backend tests passed**, zero failures/errors/skips; Ruff and a fresh TypeScript/Vite build passed. A red wire counterexample reproduced acceptance of a positive response with no explicit frozen-style input comparison. New VLM responses must cover all seven Style fields, and any declared input conflict/uncertainty must have a corresponding style finding and non-pass verdict. Missing/duplicate/inconsistent coverage is a received failure with a response digest and no automatic retry. Native qualification and production evidence check the same contract; immutable historical records remain readable. This is software verification only: the four unchanged real-media controls still require new serial calls, and VLM production qualification remains withheld. Current ASR qualification is unchanged. See [protocol evidence](evidence/local-closeout-20261025/style-input-contract.json).

Latest actual continuation: 8B installation is complete (13 checked files, 10,605,572,552 bytes). Five serial calibration calls completed and released resources. Current-runtime ASR is PRODUCTION_APPROVED from actual CER=0 evidence; 8B VLM is withheld after a walking-versus-sitting style-conflict false pass. The application correctly rejects the separate empty-street style control; the private observer capitalization error is documented separately. ComfyUI is restored idle with no render replay. Full-product status remains PARTIAL. See [pinned calibration evidence](evidence/local-closeout-20261025/vlm8b-actual-calibration.json).

**当前未达全 PASS。** 该表按原建设方案核对完成条件，不以新增回归数量缩小验收范围。
工程、Mock、真实模型和人的最终验收分别记录；完整回复失败、资源归零或 HTTP 200 均不等同于影片通过。
最新本机完整后端为 625 项通过；浏览器 17 类流程为 MOCK_VERIFIED，详见
[当前机器记录](evidence/local-closeout-20261025/native-review-capabilities.json)。

来源：[V1.0 第 11 节](Shine视频工作台_V1.0_项目建设方案.md#11-交付里程碑与验收)、
[V1.3 C1–C7](Shine视频工作台_V1.3_ComfyOps与全资产预制作系统建设方案.md)、
[本机执行清单](V1.3_LOCAL_INTEGRATION_CHECKLIST.md)、[当前收尾报告](LOCAL_FINAL_CLOSEOUT_20261025.md)。

## 原始 M0–M4 验收

| 要求 | 已有证据及实际范围 | 未关闭项及所需证据 |
| --- | --- | --- |
| M0：指定文档、配置样例、CI、API/Web、迁移及离线 Mock | README/AGENTS/LICENSE、architecture/contracts/upstream-audit/ADR、根 `.env.example` 和 `config/voice-role-runtimes.json.example`、CI/启动脚本均存在；本机测试、构建、迁移与隔离浏览器通过 | 当前代码的远端 CI 因账户付款/额度未启动；须账户恢复后对届时 head 完成四项 CI |
| M1：CRUD、资产/风格冻结、DAG/租约、暂停、恢复、失败、去重及 10 个独立 Mock 任务 | `test_workflow.py::test_ten_independent_mock_jobs_complete_and_remain_separate` 实际提交/消费 10 个任务并断言独立 ID、幂等键和状态；其余 workflow/bindings/style/submission/worker/安全测试均进入全量回归 | 该项是软件验收；后续真实项目的人类批准仍须逐项成立，不继承测试操作员签名 |
| M2：编译器、Gateway、Profile、媒体入库；真实 Windows T2VA 和至少一种参考生成 | 编译器/Gateway/媒体路径软件回归通过；真实 Ref2VA/PDD8/超分已有样本与校验记录；Web 已按明确选定 Profile 提交实际模式 | T2VA 固定 Base 权重已校验，真实 Profile/Job/Attempt/Artifact 已产生；20 步完整解码通过，但两个独立样本技术通过，但内容终审仍失败；审片误判反例保留，尚待可靠审片及合格样本 |
| M3：引用错误、错误对白、坏视频、风格越界、缺资产；阻断/限次返工/局部挂起 | offline E2E、technical/semantic/repair/quality/reference 对抗性软件回归通过；正常 Worker 已接本地媒体审片 | 新真实反例的 JSON 截断已拒绝；旧调用已 ABANDONED；显式 verdict 补丁后，真实错误故事反例与正常对照均正确阻断/通过；真实 Qwen 新对白 CER=0，原始零长度对齐拒绝、同句降速副本严格对齐通过；其余实际引用、生产对白和限次返工仍待验证，权利与人工声音质量判断未关闭 |
| M4：人工提供约 60 秒、至少 2 人对白，从冻结到 MP4 并记录介入/合格/返工/GPU 时间 | 正式交付 API/Web、来源审查/哈希检查和旧单人镜头装配已验；不能替代 M4 场景 | 尚无满足此原始要求的完整真实验收证据；需要人工提供/确认场景，完成必要资产/音色/风格/工作流/清单冻结，真实排产/QC/返工/导出与最终观看试听 |

## V1.3 C1–C7 与本机执行边界

| 项目 | 工程证据 | 真实闭环状态 |
| --- | --- | --- |
| C1 目录/环境盘点 | preparation/instances/adversarial 测试及隔离 Web 扫描/范围控制 | 既有设备/进程已实际核对；完整操作员限定范围、已有实例只读备份与实际运行验收仍按本机清单完成 |
| C2 固定安装、许可、依赖、预算、批准、受控执行及恢复 | 真实下载/文件安装算法由 Fake HTTP 注入测试；实际固定小元数据下载成功；失败对账、OS 锁、篡改/未知状态阻断和页面恢复流程均已验证 | 旧 TLS/读取失败已按明确委托正式对账；四包 42 文件均已校验；真实 SDK 调用、退出及同句降速副本对齐完成；T2VA 已安装并完成首个真实渲染（内容 QC 失败），音色/身份和完整声音质量待确认；不能以缓存元数据或 Mock 安装计通过 |
| C3 Workflow Registry/Factory | factory/registry/reference/profile/弃用/权限及原生审片/ASR 登记、发布和路由/结果篡改回归通过；实际 Ref2VA 旧 Profile 保留 | 原生 VLM/ASR 均完成真实固定指纹校准和认证委托发布；后续对抗性反例失败后 VLM 已 DEPRECATED，ASR 保持发布，旧记录保留；节点名通过不能冒充检查点运行、硬连续性或视频引用传输通过 |
| C4 候选资产与来源/QC/人工晋升 | fake image/voice 候选、权限和单调 Mock 来源、下载/解码/晋升拒绝路径通过 | 当前验收项目没有真实候选晋升记录，四条 RightsRecord 仍 PENDING；需要本人权利证据和逐项批准 |
| C5 VoiceOps | 设计/克隆/合成/ASR/对齐/字幕软件接口与 Fake E2E；TTS/ASR 固定依赖隔离导入通过 | 当前项目 VoiceProfile 为空；已有真实设计/克隆/新对白、CER=0 与同句降速副本字词对齐；原始零长度时间仍拒绝，尚无真人试听冻结、声音身份/口型和完整字幕验收 |
| C6 预制作 DAG/资源/开机前门禁 | 分析、依赖、并发排斥、释放故障/过期阻断、manifest pins 及权限测试通过；真实准备检查与 Worker 审片前后均核对原生角色和执行器绑定 | 当前验收项目没有冻结 PreProductionPlan；真实就绪门禁不得通过 Fake、改状态或禁用 enforce 关闭 |
| C7 Web、迁移、E2E、Windows CI 和报告 | 本机完整后端、Ruff、前端构建、迁移、隔离浏览器通过；安装恢复 UI 已接真实 API；文档契约与权限说明已更新 | 新代码远端 CI 待账户恢复后重新验证；真人操作与真实媒体验收仍各自待完成 |

以上项目中，Mock 协议已实现不意味着所有可选音乐/SFX/未知模型都已受支持。
未注册能力继续明确阻断；不为满足“全 PASS”自动替换模型、丢弃引用或假造验证等级。

## 当前具体阻断与执行顺序

1. 用户明确表示“我不在电脑前，我授权你全权自主推进”。后续操作以 `user-delegated-codex` 记录授权来源；
   不冒充用户本人在场，也不虚构试听、权属或美学判断事实。原技术恢复/安装的等待本人操作项已由此授权接续。
2. 截断审片调用 `ff6d7832-52ed-442d-af75-4d03ee8f5d1b` 已 ABANDONED，租约 RELEASED。
   已终止的旧安装尝试均已正式对账，不重放旧尝试；新计划 Qwen 计划 `9a844b71-c4e1-48f8-9e1a-41ddb3bbf635`
   已 FILES_VERIFIED；其运行验证及限制见当前机器记录。每段最多 64MiB，完整验证一段前最多 3 次流中断；收到整段后才重置预算。
   只续传固定哈希 GET，最终大小和 SHA-256 都须匹配；不重放安装或模型尝试。
3. 四个 Qwen 包已安装并配置固定 SDK/运行时映射，实际串行设计/克隆/合成/识别/对齐及进程退出已有证据。
   12000 MiB 是 ESTIMATED 准入上限，不能冒充实际消耗或质量通过。
4. T2VA 首次计划连接失败已对账；相同固定范围的新计划 `bb6c1560-6f98-4cd8-b11e-fea7c603d43b` 已完成文件校验。
   原模型目录保留，隔离路径通过自有 ComfyUI 实例加载；首例 T2VA 技术通过、内容 QC 失败，等待独立正例。
5. 新真实资产、音色、Rights、Workflow 发布、预制作冻结及最终试听/美学验收按真实证据推进。
   自主授权不提供缺失的第三方权属事实；AI 编写场景也不能冒充 M4 的人工提供场景或本人试听。
6. GitHub Actions 账户包含额度已耗尽；未改付款方式、预算或产生新付费。
   额度可用后须对届时 head 验证 backend、backend-windows、frontend、web-e2e；旧 SHA 绿色和本机测试不替代。

独立审片/ASR 的指纹、原生登记/发布、准备检查及实际审片前后绑定软件路径已补齐；新增 48 项契约/对抗性用例在隔离替身上验证。真实部署、固定包安装、新校准和原生能力发布已完成；VLM 后续可靠性反例未通过，已撤下其生产资格，当前项目预制作冻结仍被阻断，不能用软件通过冒充硬件或人工验收。

每个模型阶段开始前及结束后核验释放，任一未知调用或资源状态保留待对账；审片、生成和多个 Agent 不并行。
完成结论必须以对应层的当前证据逐项关闭本表，不能仅凭本表存在或软件测试通过宣告完成。

真实 VLM 可靠性仍未 PASS：同一失败视频曾被误判通过，匿名虚构人物被错误要求无五官/服装，单长镜头被误解为跨镜头连续性。所有反例留账，VLM 能力正式 DEPRECATED；不能用软件回归或基础正反例掩盖。两个新 T2VA 样本均技术通过、内容验收未通过。详见当前实机机器记录。

官方 8B FP8 候选的固定文件与分片校验支持已准备，完整后端 604 项通过；尚未安装、推理或获得生产资格，不能替代原 VLM 可靠性未通过的状态。

较大模型预算与实际租约的 7 个反例修复后，全量 611 项通过。官方 8B 固定隔离安装正在执行，尚无推理或生产资格；新适配器下的 ASR 也须重新校准，旧发布状态不能替代当前运行身份。

校准设计复核确认旧 A 行走/镜头要求与冻结坐姿/镜头文案存在未消解差异，不能作为严格冻结风格的无争议正例。旧结果保留，8B 校准改用一致输入并区分真实生成失败和模型误判，不靠更改风格或预设通过关闭验收。

加载闭包反例修复后全量 625 项通过；已减少重复 CI 触发，未改四项 job/权限、未恢复账户额度。8B 两片缓存已校验，但隔离安装终态、真实推理和完整质量校准仍须另证，不据接收字节数关闭验收。
