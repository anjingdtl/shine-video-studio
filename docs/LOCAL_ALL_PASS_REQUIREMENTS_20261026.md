# 全 PASS 收尾核对表（2026-10-26）

**当前未达全 PASS。** 该表按原建设方案核对完成条件，不以新增回归数量缩小验收范围。
工程、Mock、真实模型和人的最终验收分别记录；完整回复失败、资源归零或 HTTP 200 均不等同于影片通过。
最新本机完整后端为 541 项通过；浏览器 15 类流程为 MOCK_VERIFIED，详见
[当前机器记录](evidence/local-closeout-20261025/installation-recovery-web.json)。

来源：[V1.0 第 11 节](Shine视频工作台_V1.0_项目建设方案.md#11-交付里程碑与验收)、
[V1.3 C1–C7](Shine视频工作台_V1.3_ComfyOps与全资产预制作系统建设方案.md)、
[本机执行清单](V1.3_LOCAL_INTEGRATION_CHECKLIST.md)、[当前收尾报告](LOCAL_FINAL_CLOSEOUT_20261025.md)。

## 原始 M0–M4 验收

| 要求 | 已有证据及实际范围 | 未关闭项及所需证据 |
| --- | --- | --- |
| M0：指定文档、配置样例、CI、API/Web、迁移及离线 Mock | README/AGENTS/LICENSE、architecture/contracts/upstream-audit/ADR、根 `.env.example` 和 `config/voice-role-runtimes.json.example`、CI/启动脚本均存在；本机测试、构建、迁移与隔离浏览器通过 | 当前代码的远端 CI 因账户付款/额度未启动；须账户恢复后对届时 head 完成四项 CI |
| M1：CRUD、资产/风格冻结、DAG/租约、暂停、恢复、失败、去重及 10 个独立 Mock 任务 | `test_workflow.py::test_ten_independent_mock_jobs_complete_and_remain_separate` 实际提交/消费 10 个任务并断言独立 ID、幂等键和状态；其余 workflow/bindings/style/submission/worker/安全测试均进入全量回归 | 该项是软件验收；后续真实项目的人类批准仍须逐项成立，不继承测试操作员签名 |
| M2：编译器、Gateway、Profile、媒体入库；真实 Windows T2VA 和至少一种参考生成 | 编译器/Gateway/媒体路径软件回归通过；真实 Ref2VA/PDD8/超分已有样本与校验记录；Web 已按明确选定 Profile 提交实际模式 | T2VA 已有官方 20 步结构草稿、固定 Base 权重安装批准及受控续传；仍缺文件全部校验、真实 Profile 和实际 Job/Attempt/Artifact/QC 证据 |
| M3：引用错误、错误对白、坏视频、风格越界、缺资产；阻断/限次返工/局部挂起 | offline E2E、technical/semantic/repair/quality/reference 对抗性软件回归通过；正常 Worker 已接本地媒体审片 | 新真实反例的 JSON 截断已拒绝；旧调用已 ABANDONED；显式 verdict 补丁后，真实错误故事反例与正常对照均正确阻断/通过；真实 Qwen 新对白 CER=0，原始零长度对齐拒绝、同句降速副本严格对齐通过；其余实际引用、生产对白和限次返工仍待验证，权利与人工声音质量判断未关闭 |
| M4：人工提供约 60 秒、至少 2 人对白，从冻结到 MP4 并记录介入/合格/返工/GPU 时间 | 正式交付 API/Web、来源审查/哈希检查和旧单人镜头装配已验；不能替代 M4 场景 | 尚无满足此原始要求的完整真实验收证据；需要人工提供/确认场景，完成必要资产/音色/风格/工作流/清单冻结，真实排产/QC/返工/导出与最终观看试听 |

## V1.3 C1–C7 与本机执行边界

| 项目 | 工程证据 | 真实闭环状态 |
| --- | --- | --- |
| C1 目录/环境盘点 | preparation/instances/adversarial 测试及隔离 Web 扫描/范围控制 | 既有设备/进程已实际核对；完整操作员限定范围、已有实例只读备份与实际运行验收仍按本机清单完成 |
| C2 固定安装、许可、依赖、预算、批准、受控执行及恢复 | 真实下载/文件安装算法由 Fake HTTP 注入测试；实际固定小元数据下载成功；失败对账、OS 锁、篡改/未知状态阻断和页面恢复流程均已验证 | 旧 TLS/读取失败已按明确委托正式对账；四包 42 文件均已校验；真实 SDK 调用、退出及同句降速副本对齐完成；T2VA 仍在安装，音色/身份和完整声音质量待确认；不能以缓存元数据或 Mock 安装计通过 |
| C3 Workflow Registry/Factory | factory/registry/reference/profile/弃用/权限测试通过；实际 Ref2VA 旧 Profile 保留 | 当前新工作流记录为 DRAFT/FAKE_EXECUTED，未有新的真实发布闭环；节点名通过不能冒充检查点运行、硬连续性或视频引用传输通过 |
| C4 候选资产与来源/QC/人工晋升 | fake image/voice 候选、权限和单调 Mock 来源、下载/解码/晋升拒绝路径通过 | 当前验收项目没有真实候选晋升记录，四条 RightsRecord 仍 PENDING；需要本人权利证据和逐项批准 |
| C5 VoiceOps | 设计/克隆/合成/ASR/对齐/字幕软件接口与 Fake E2E；TTS/ASR 固定依赖隔离导入通过 | 当前项目 VoiceProfile 为空；已有真实设计/克隆/新对白、CER=0 与同句降速副本字词对齐；原始零长度时间仍拒绝，尚无真人试听冻结、声音身份/口型和完整字幕验收 |
| C6 预制作 DAG/资源/开机前门禁 | 分析、依赖、并发排斥、释放故障/过期阻断、manifest pins 及权限测试通过 | 当前验收项目没有冻结 PreProductionPlan；真实就绪门禁不得通过 Fake、改状态或禁用 enforce 关闭 |
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
4. T2VA 首次计划连接失败已对账；相同固定范围的新计划 `bb6c1560-6f98-4cd8-b11e-fea7c603d43b` 在续传。
   未变更原 ComfyUI 模型目录，原 Ref2VA 路径保留，尚无 T2VA 生成。
5. 新真实资产、音色、Rights、Workflow 发布、预制作冻结及最终试听/美学验收按真实证据推进。
   自主授权不提供缺失的第三方权属事实；AI 编写场景也不能冒充 M4 的人工提供场景或本人试听。
6. GitHub Actions 账户包含额度已耗尽；未改付款方式、预算或产生新付费。
   额度可用后须对届时 head 验证 backend、backend-windows、frontend、web-e2e；旧 SHA 绿色和本机测试不替代。

独立审片/ASR 的真实模型指纹已补入执行器响应和调用账本软件路径；实时部署与原生能力登记、预制作绑定仍待完成，不能用 ComfyUI 假图补足。

每个模型阶段开始前及结束后核验释放，任一未知调用或资源状态保留待对账；审片、生成和多个 Agent 不并行。
完成结论必须以对应层的当前证据逐项关闭本表，不能仅凭本表存在或软件测试通过宣告完成。
