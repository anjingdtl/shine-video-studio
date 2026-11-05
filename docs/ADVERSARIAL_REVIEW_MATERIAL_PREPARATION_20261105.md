# 对抗性复核：导演模式 AI 素材准备建设

- 日期与时区：2026-11-05（北京时间，UTC+8）
- 复核对象：`docs/DIRECTOR_AI_MATERIAL_PREPARATION_PLAN_20261104.md`（计划 v1.0，基线 `main / bff25ad`）
- 复核基线：分支 `codex/director-ai-material-preparation`，`46b2f40` + `5c4842f` 之后
- 复核结论：**本轮建设未完成**（阶段 A 完成；B 代码与离线验证完成但真实出图未跑；C 仅契约接入；
  D/E 未开始）。复核过程中在既有实现里查出 **6 项缺陷**，其中 3 项使导演链根本无法走通，已全部修复。

## 1. 计划阶段完成度复核

| 阶段 | 计划退出条件 | 实际状态 | 判定 |
| --- | --- | --- | --- |
| A 契约与迁移 | 旧生产计划选择不变；新计划不能通过生产门禁；Schema 与迁移测试通过 | `purpose` 列与索引落地，生产侧读取点全部过滤，迁移 `f170a0010001` 可升可降 | **完成** |
| B 运行时与恢复 | 有持久化任务入口；崩溃窗口测试通过；真实配置缺失明确阻塞 | 三张台账表、条件认领、提交意图、UNKNOWN 语义、声明输出节点、字节校验均在位；但**真实 Qwen 出图与 live object_info 未跑** | **代码完成，真实出图 NOT_RUN** |
| C Agent | 未冻结可准备；旧 ProductionPlan 可用；长剧本及 UNKNOWN 测试通过 | 两个工具与两个提示词已接入路由选择；**Worker 真实 LLM brief 调用链未接**，正式批次以 `MATERIAL_BRIEF_MISSING` 具名拒绝 | **仅契约接入** |
| D 导演界面 | 刷新/切换/重复点击不重提；用户动作和候选晋升可从 UI 完成 | `frontend/` 无任何 material 代码 | **未开始** |
| E 验收与交付 | 完整回归、三类真实出图、人工晋升、真实 H3 引用、Windows 两链 | 仅离线回归；真实出图/晋升/H3 引用/入口与安装包均未跑 | **未开始** |

整体状态：**PARTIAL / NOT ACCEPTED**。不能以 Mock 替代真实出图与真实 H3 引用结论。

## 2. 对抗性审查发现（全部为复现后确认，非阅读推断）

审查方法：先用 `tools.grep` 枚举 purpose 隔离点、`AssetRequirement` 全部读写点、`PreProductionPlan` 全部读取点，
再以真实 HTTP（TestClient）驱动 `analyze → 确认 → 生成` 全链，用失败断言定位断点。
新增回归文件 `backend/tests/test_material_director_flow.py`（修复前 4 条全红，修复后全绿）。

### D1 [P0] 导演链在「确认清单」处断裂

- 证据：`analyze` 返回的需求对象没有 `requirement_id`；`plan_progress` 对草稿计划返回空需求表；
  `confirm` 要求每个 `decision.requirement_id` 命中一条 `AssetRequirement` 行，而该表在**草稿期根本不存在**
  （只有 `confirm` 成功之后才创建），于是首次确认必然 404 `MATERIAL_REQUIREMENT_NOT_FOUND`。
- 影响：用户永远无法确认清单，生成、更换、晋升以及阶段 C/D/E 全部不可达。
- 修复：草稿期即落 `AssetRequirement` 行并分配稳定 id；`confirm` **就地更新**这些行并把 `plan_id` 前移到新修订，
  id 跨修订不变（页面引用的 id 与批次引用的 id 始终同一个）；需求 id 现在也进入确认快照，改需求即改快照。

### D2 [P0] 确认快照无法获得

- 证据：`confirm` 比对 `graph_hash({requirements, visual_direction, scene_preparations})`，
  但 `analyze` 与 `plan_progress` 从未发布这个值，客户端无法算出它 → 任何确认都被判 `MATERIAL_PLAN_STALE`。
- 修复：新增 `service.confirmation_snapshot / confirmation_snapshot_hash` 作为唯一口径，
  `analyze` 与 `plan_progress` 都返回 `expected_snapshot`。

### D3 [P0] 场景契约未在 API 边界生效

- 证据：`AnalyzeBody.scene_preparations` 是 `list[dict]`，客户端任意字典直接入库；
  `family="voice"`、多余字段 `shots`、超长文本一律 202 通过（复现见修复前第 4 条测试的失败输出）。
- 影响：非法族类会一路进入计划载荷，再到图片提示词；已发布的 `ScriptPreparation` 契约形同虚设。
- 修复：字段类型化为 `list[ScriptPreparation]`（`extra=forbid`、逐项长度上限、`family` 白名单），
  服务层再校验一次并以 `MATERIAL_SCENE_PREPARATION_INVALID` 拒绝 → 422。

### D4 [P1] 许可证在渲染之后才检查

- 证据：`create_batch` 从不调用 `require_rights`；检查只发生在 `create_candidate` 里，即**GPU 渲染完成之后**。
  任意 `rights_id` 字符串都能先烧掉一次渲染再被拒。既有候选路径 `candidate-assets/generate` 是先校验再生成。
- 修复：在**真正创建批次前**校验（幂等重放不新建任何东西，故保留原答案，不重复校验）；
  `test_material_image_jobs.py` 的 10 处调用补上真实的已批准 rights 记录。

### D5 [P1] 变更类路由绕过 CSRF / Origin 边界

- 证据：`analyze`、`generate`、`cancel`、`reconcile`、`resume`、`regenerate`、`validate` 七个写路由未挂
  `Operator` 依赖；同一文件里的 `confirm` 与 `bind-image-runtime` 挂了。个人桌面约定要求同源 loopback
  动作带 CSRF 标记并记为 `local-user`。
- 修复：七个写路由统一挂 `Operator`（loopback + 同源 + `x-shine-local-action: 1` 标记 → `local-user`，不需密码）。
  后续阶段 D 的前端必须带该标记头，否则会收到 403。

### D6 [P2] 权重校验每次重算 16 GiB

- 证据：`verify_model_files` 无条件重算全部模型文件 SHA-256，`readiness()` 与 Worker 的 `resolve_runtime()` 每次调用都触发；
  本机权重合计 16.10 GiB。注释声称「调用方会缓存」，但调用方从未缓存。
- 附带缺陷：`/setup/image-readiness` 不带 `object_info` 调用，节点层永远停在 `FILES_VERIFIED`。
- 修复：按 size + mtime 缓存已验证摘要，变更即重算，缓存命中显式标记 `cached` 与 `verified_at`；
  就绪路由改为对已绑定实例做 3 秒只读 `object_info` 探测，探测失败只表示该层未获证，不拖住页面。

### D7 [P1] 重复分析会把已确认的计划顶掉

- 证据：`analyze` 完全忽略 `idempotency_key`，每次调用都新建一条草稿计划。而"当前素材计划"取的是最新一条，
  所以重复分析（双击、刷新重跑、确认后再点一次）会让**新的草稿成为当前计划**，用户刚确认的决定在页面上消失。
  计划 §6 明确要求"相同输入复用现有分析"。
- 修复：按 `{document_id, script_hash, scenes}` 的内容哈希判定；内容相同且当前计划就是该修订时直接复用，
  返回 `replayed: true`。确认后再次分析会返回**已确认修订**（当前修订），而不是新开草稿。
  注意：内容变化（新的分场分析）仍会开新修订，这是正确行为。

### D8 [P1] 中文角色名会让图片提示词必然被拒

- 证据：`image_prompts.compose()` 把需求名（原剧本里的中文角色名）直接当作 `subject:` 注入提示词，
  而同一函数对提示词语言强制 `_require_prompt_language()`（CJK 直接拒绝）。于是**任何中文剧本的素材都拼不出提示词**，
  报 `VISUAL_TEXT_MUST_BE_PROMPT_LANGUAGE`。
- 修复：剧本名是**来源身份**，不是提示词散文。它留在用户读到的需求上，由出品人的外观描述把身份带进画面；
  提示词只接受提示词语言。省略会写进证据（`subject_omitted: "not_prompt_language"`），不是静默丢弃。
  拉丁字母名字仍照旧成为 `subject:` 子句。

### D9 [P0] 计划的「素材完成条件」根本没有实现

- 证据：`PLAN_MATERIALS_READY` 在仓库里**只被读、从不被写**——`plan_progress` 用它决定页面阶段，
  但没有任何代码路径把计划推进到这个状态。结果是计划 §3.1 第三阶段（每个必需项绑定有效主资产）、
  §3.3 的完成条件与 §11.1「不误判齐备」三条要求同时落空：导演页永远不可能显示「素材齐备」。
- 修复：新增 `evaluate_materials()` 做**判定**（绑定本项目、已批准并锁定、族类与身份相符、
  文件哈希未变、授权已批准、`offline_only` 模拟素材一律不算），`POST /material-preparation/complete`
  把判定结果回写计划状态并逐条返回未绑定项与原因；`plan_progress` 每次读取都按实时判定给阶段，
  不依赖曾经写下的状态。与生产侧 readiness 用同一套原因码，两边含义一致。

### D10 [P1] 出图能力与权重安装在产品里没有入口

- 证据：`/setup/image-readiness` 与 `/setup/image-runtime/*` 三个接口存在，但**前端没有任何一处消费它们**，
  计划 §1.3 第 5 条要求的「基础配置能力卡」与 §8.3 的四层就绪度展示都落空；
  同时 `config/deployment/` 下只有 H3 与工具两个目录，**出图权重没有任何安装登记**——
  上一轮的权重是靠一次性脚本下载的，产品无法登记、无法按固定哈希安装，也无法在安装 UI 上展示
  下载量、占用、临时空间、许可证与目标盘（§8.2）。
- 修复：新增 `ImageCapabilityCard`（导演基础配置页）显示四层就绪度、阻塞原因、钉点与许可限制，
  并可登记三类真实小样；新增固定安装目录 `qwen-image-2.1-windows-x64.json`，3 个权重逐文件钉死
  SHA-256 与大小、标注研究用途，走既有受控安装路径。测试锁住「目录与工作流钉点逐字节一致」，
  防止目录漂移出审计描述。

### D11 [P1] Windows 启动器的 CI 闸门从 2026-11-04 起一直是坏的

- 证据：`36853e9`（"never mistake a half-dead stack for a running workbench"）把「已在运行」从
  `throw`（非零退出）改成友好 no-op（`exit 0`）——这是那一轮**有意**的行为（要活体 API + 活体页面 +
  属主身份才敢报「已在运行」）。但 `scripts/verify_stack_operations.ps1` 仍按旧契约断言
  「第二次启动必须失败」（非零退出），于是该脚本在「重复启动拒绝」这一步抛错，
  **CI 的 Windows 生命周期闸门在 main 上一直是红的**；文档里记录的 PASS 来自那次改动之前。
  本地各轮从未运行 PowerShell 脚本，所以没人发现。
- 修复：把断言改成**直接校验真正的不变量**——属主清单哈希不变、监听端口与属主进程不变、
  启动器明确输出「已在运行」。脚本现在在本机真实通过（见进度文档）。
- 附带纠正：凡早于 `36853e9` 的「Windows 启停 PASS」记录都不能再作为当前证据。

### D12 [P1] CI 的浏览器 E2E 闸门同样过期（本轮部分对齐，仍未转绿）

- 证据：`scripts/verify_v13_web.py` 按 2026-10-25 的单页界面编写：它填「操作员凭据」与「核验依据」
  （个人桌面简化轮已删除这两个字段，且前端测试明确断言它们不存在）、期望「批准」「锁定」两个按钮
  （现在是单个「确认使用」）、期望卡片显示英文状态码（现在渲染中文标签）、从不在页面间导航
  （风格与主资产面板已移到「素材与风格」页、制片准备面板在专家层），并且让 Playwright 默认 dismiss
  了「确认使用」前的原生确认框。实测在第一步即失败。
- 本轮已对齐十一处假设：按用户路径导航（`#d/assets` 与 `#v/production/preproduction`）、接管原生弹窗、
  删除已不存在的凭据字段、改用单按钮确认、改用真实中文状态标签、统一「结果 pre」访问器（原来一处取第一个、
  一处取最后一个，本身就矛盾）。
- **仍未转绿**：脚本现在能跑完导演层（风格冻结 + 主素材确认）并进入制片准备流程，但在「批准此哈希计划」
  一步超时。剩余部分需要同样地对着当前制片准备面板逐条核对，本轮不宣称修复。

## 3. 修复涉及的文件

| 文件 | 变更 |
| --- | --- |
| `backend/shine/materialprep/service.py` | 场景契约校验；草稿期需求行与稳定 id；快照口径与发布；rights 门禁；需求行跨修订前移；分析幂等 |
| `backend/shine/api_material.py` | `scene_preparations` 类型化；`analyze` 发布 `expected_snapshot`；七个写路由挂 `Operator`；就绪路由加 live 探测 |
| `backend/shine/materialprep/runtime.py` | 权重校验缓存与持久化；缓存命中标记 |
| `backend/shine/materialprep/execution.py` | Worker 门禁复用校验缓存 |
| `backend/tests/test_material_director_flow.py` | 新增：真实 HTTP 的 analyze→确认→生成 全链回归（5 条，含分析幂等） |
| `backend/tests/test_material_image_jobs.py` | 批次调用补已批准 rights 记录（10 处），提取 `_batch` / `_rights_id` 夹具 |

## 4. 验证结果

| 项目 | 命令 | 结果 |
| --- | --- | --- |
| 导演链回归（新增） | `pytest backend/tests/test_material_director_flow.py` | 修复前 **5 failed**，修复后 **5 passed** |
| 素材 + 分镜链回归 | `pytest test_material_preparation.py test_material_image_jobs.py test_material_director_flow.py test_crew_prompt_contract.py test_film_crew.py` | **passed** |
| 全量后端 | `pytest backend/tests` | **EXIT=0**，共 **1624** 个用例跑满 100%，无 F/E（1 个 skip）；日志 `.local-acceptance/full3.log` |
| 前端 | `npm.cmd run build`（含 `tsc -b`）/ `npm.cmd test`（frontend/） | **build 成功**；**16 个测试文件 / 115 个测试全部通过**（本轮未改任何前端文件，只做树健康度确认） |
| OpenAPI | `app.openapi()` | 场景体已按 `ScriptPreparation` 暴露，`family` 为 `character/scene/prop` 三值枚举；12 条素材/图片路由全部注册 |
| Ruff | `ruff check backend/shine backend/alembic backend/tests` | **All checks passed** |
| Alembic | `alembic -c backend/alembic.ini heads` / `upgrade head` / `check`（隔离 `SHINE_DATA_DIR`） | head `f170a0010001`；**No new upgrade operations detected** |

用户正式库未被升级或写入；迁移验证在隔离目录 `.local-acceptance/materialprep-adversarial` 完成。

## 5. 仍然存在的缺口（未实现，不是未验证）

以下项目**在 2026-11-05 续建轮已补上或仍需真实环境**，不得在任何交付结论中按已完成计：

1. ~~**阶段 C 的真实 LLM 调用链**~~ → **已补上**：`backend/shine/materialprep/brief.py` 让 Worker 在取 GPU 租约前
   真实调用出品人写外观描述，`backend/shine/materialprep/scenes.py` 让服务端按场调度前期编剧。
2. ~~**阶段 D 导演界面**~~ → **已补上首版**：`frontend/src/components/MaterialPreparationPanel.tsx`
   （读剧本 / 确认需求 / 选择素材），前端接入时带 `X-Shine-Local-Action: 1` 标记头（见 D5）。
3. **阶段 E 验收**：真实 Qwen 三类出图、live `object_info` 实测、人工晋升、真实 H3 引用新素材、
   Windows 入口与安装包验证，**仍然未跑**——这些需要真实渲染器与人的选择。
4. **源片段校验**：**已补上存在性校验**（`source_spans.py`：编造片段 → `MATERIAL_SOURCE_SPAN_UNKNOWN`），
   但只校验片段**存在**，尚未校验片段内容与需求描述的**语义一致性**。

## 6. 给下一位实施者的提醒

- `purpose` 隔离是这一轮最敏感的改动：任何新增的 `PreProductionPlan` 读取点都必须走
  `current_plan` / `current_plan_for_purpose` / `require_plan_for_purpose`，不能直接按项目取最新行。
- 新增 `AssetRequirement` 行时注意：生产侧所有读取都按 `plan_id` 过滤，素材行挂在素材计划上，不会串味；
  但**同表同项目**，任何按 `project_id` 全表扫描的新代码都会把素材需求算进去。
- 素材写路由现在需要 CSRF 标记头。若前端报 403，先看这个头，不要直接摘掉 `Operator` 依赖。
