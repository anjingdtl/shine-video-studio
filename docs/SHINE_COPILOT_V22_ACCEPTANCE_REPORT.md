# Shine AI 管家 V2.2 验收报告

- 功能：个性化配置（昵称/称呼/启动预读）与本地长期记忆
- 分支：`feat/shine-copilot-v22-persona-local-memory`
- 基线：V2.1 PR #59 HEAD `ead511b95c62122d2f7047c4e049d634890e89ed`
- 最新 main：`a1d05498e5fd654656981f3e2ad364b561bb2462`
- 迁移 head：`f180f0010006`

## 1. 结论摘要

| 项目 | 状态 |
| --- | --- |
| 功能实现 | **已实现** |
| 后端 V2.2 专项自动化 | **30/30 通过** |
| 前端 V2.2 专项自动化 | **8/8 通过（含导航、刷新、昵称同步、记忆管理、主题）** |
| 前端全量 | **422/422 通过，80/80 文件** |
| V1/V2/V2.1/V3 Support 回归 | **147/147 通过** |
| Alembic 空库/增量升级 | **8/8 通过** |
| Ruff / TypeScript / Vite Build | **通过** |
| V2.1 契约生成检查 | **通过（app 2.2.0）** |
| 真实浏览器端到端 | **通过（本机 mock 文本模型，见 §4）** |
| 最新 main 前端集成 | **tsc/build 通过；395/395 前端测试通过** |
| Windows 真机安装包 | **NOT_RUN（无 Windows 设备）** |
| RTX/H3 真机生产 | **NOT_RUN（无 GPU/H3 设备）** |
| 语义 Embedding 模型（bge 等） | **NOT_CONFIGURED / NOT_RUN** |
| sqlite-vec | **未采用、NOT_RUN** |

**无代码阻塞项。** 唯一流程依赖：PR #55/#56/#59 仍为 OPEN，V2.2 PR 必须以 V2.1 为 base；
待前置 PR 合并后再改 base 到 main。

## 2. 已实现

1. 左侧导航「AI 管家配置」，路由 `#d/copilot`；素材中心也有同一入口。
2. 昵称/称呼：默认 `AI 管家`；1..20 字符；HTML/指令/密钥拒绝；全局浮窗实时同步。
3. 启动预读：0..4000 字符；保存/清空/恢复默认/字符数；不产生额外 LLM 请求。
4. 统一配置：`AppSetting("copilot_user_profile")`；原 `web_search` key 兼容。
5. 长期记忆：`copilot_memories` + FTS5 + 本地 256 维哈希向量；可重建。
6. 项目隔离：召回范围固定 `GLOBAL + 当前 SupportSession.project_id`。
7. 自动提炼：6 条用户消息批量一次；后台执行；远端需显式授权。
8. 管理：查看/按项目筛选/搜索/来源/新增/编辑/删除/清项目/清自动/重建索引/状态。
9. 删除：正文清空 + FTS/向量立即删除 + 哈希墓碑阻止自动重建。
10. 上下文预算：12,000 字符上限；业务事实 > 启动预读 > 记忆 > 普通资料；记录裁剪原因。
11. 数据库迁移：V2.1 → V2.2 增量升级；保留会话/消息/委托。
12. V2.1 契约：新增 2 个 READ 能力 + 1 个 feature；记忆写操作保持 HUMAN_REQUIRED。

## 3. 自动化验收

### 3.1 后端 V2.2 专项（30 项）

`backend/tests/test_support_memory_v22.py`

覆盖：

- 配置默认值、修订号、web_search 兼容；
- 昵称 HTML/指令注入拒绝、长度拒绝、称呼拒绝；
- 启动预读保存/注入、无额外 LLM 调用；
- 预算裁剪且保留 verdict/task/diagnosis；
- 记忆 CRUD、搜索、去重、编辑后旧向量失效；
- 删除后不可召回、墓碑阻止自动重建；
- 项目 A/B 隔离、全局记忆跨项目、无项目只全局；
- 按项目清除不影响其它项目；
- FTS/向量删除后 LIKE 降级、重建恢复；
- 自动提炼阈值、关闭后不再新增；
- 远端提炼授权、远端上下文授权与撤回；
- 配置编辑下一条消息生效；
- 自然语言不能删除/编辑记忆；
- 重启后配置/记忆保留；
- 密钥/脚本标签拒绝；
- 记忆不能授予 shell/git 工具；
- 远端 prompt 无授权时不包含召回记忆；
- 预算单元测试。

执行：

```bash
CODEBUDDY_SAFE_DELETE_ENABLED=0 backend/.venv/bin/python -m pytest \
  backend/tests/test_support_memory_v22.py -q -p no:cacheprovider
```

结果：`30 passed`。

### 3.2 Alembic

```bash
CODEBUDDY_SAFE_DELETE_ENABLED=0 backend/.venv/bin/python -m pytest \
  backend/tests/test_alembic.py -q -p no:cacheprovider
```

结果：`8 passed`，包括：

- 空库升级到 `f180f0010006`；
- V2.1 库升级保留 project/session/message；
- 老 M1 库升级不丢行；
- `alembic check` 无漂移（FTS5 影子表被显式排除）；
- 降级/再升级。

### 3.3 V1/V2/V2.1/V3 Support 回归

```bash
CODEBUDDY_SAFE_DELETE_ENABLED=0 backend/.venv/bin/python -m pytest \
  backend/tests/test_support_agent.py \
  backend/tests/test_support_copilot_v2.py \
  backend/tests/test_support_copilot_v21.py \
  backend/tests/test_support_copilot_v3.py -q -p no:cacheprovider
```

结果：`147 passed`（V1 55、V2 24、V2.1 41、V3 27）。V2.1 的 41 项专项全绿。

### 3.4 前端

```bash
cd frontend
npx tsc -b --pretty false
npm run build
npx vitest run --no-file-parallelism
```

结果：

- TypeScript：通过；
- Vite Build：通过；
- Vitest：`422 passed / 80 files`。

V2.2 新增 `src/test/copilotSettings.test.tsx` 8 项：

- 四个区域渲染；
- 昵称/称呼保存并同步共享配置；
- 启动预读保存/清空/字符数；
- 搜索/筛选/编辑/删除/清项目/重建索引；
- 自动提炼与远端授权开关；
- 浅色/深色主题；
- 全局浮窗昵称同步；
- 左侧导航 + 刷新恢复 + 素材中心入口。

### 3.5 后端八分片

执行方式按 AGENTS.md：测试文件 `split -n r/8`，8 个进程并行。

结果（最新代码，最终 8/8 exit 0）：

| 分片 | 用例数 | 最终结果 |
| --- | ---: | --- |
| chunk-aa | 324 | exit 0 |
| chunk-ab | 393 | 首轮 2 项迁移 head 断言过期 → 修正后复跑 exit 0 |
| chunk-ac | 365 | exit 0 |
| chunk-ad | 543 | 首轮与其他全量任务并行时超长；单独复跑 exit 0 |
| chunk-ae | 317 | 首轮 2 项审计码表未同步 → 补齐后复跑 exit 0 |
| chunk-af | 390 | exit 0 |
| chunk-ag | 442 | exit 0 |
| chunk-ah | 323 | 首轮 1 项迁移 head 断言过期 → 修正后复跑 exit 0 |
| **合计** | **3097** | **8 个分片全部 exit 0，复跑日志无 FAILED/ERROR** |

首轮失败均为**本轮新增迁移 head 与新增审计事件码**导致的预期同步项，不是产品逻辑失败；
修正对应测试常量/前端码表后，相关分片复跑全绿。`chunk-ad` 在 8 个分片同时运行时超过
30 分钟，单独复跑退出码 0；这是调度/重负载抖动，不是用例失败，未伪造任何 PASS。

### 3.6 契约生成

```bash
PYTHONPATH=backend backend/.venv/bin/python \
  backend/scripts/generate_shine_copilot_contracts.py --check
```

结果：

```
contracts ok: app=2.2.0 actions=14 knowledge=22 discovered=425
```

## 4. 真实浏览器端到端

环境：

- FastAPI 真实服务（本机 SQLite，迁移到 head）；
- Vite 真实前端；
- Playwright + Chromium 真实浏览器（1440×1050）；
- 文本模型：**本机 loopback OpenAI-compatible mock provider（仅验收用，不随产品发布）**。
  它让真实的 LLM Gateway → 提炼 → 审批/授权 → 索引链路运行，但不代表真实模型质量。

场景与结果：

1. 打开 `#d/copilot`，设置昵称 `小映`、称呼 `导演`、启动预读“我主要制作 AI 影视短剧，喜欢电影化叙事…”。
   **通过**。
2. 浅色 / 深色主题切换，配置页均可读。**通过**。
3. 打开项目 A，与管家对话 6 轮。后台自动提炼创建 1 条项目记忆：
   `项目镜头偏好:手持跟拍与自然光,避免过度滤镜。`，来源 `AUTO`，项目 A。**通过**。
4. 配置页按项目 A 可见该记忆；按项目 B 不可见。**通过**。
5. 手动新增全局记忆“片尾使用黑场与雨声”。**通过**。
6. 停止并重新启动后端（模拟关闭/重新打开 Shine），重新打开浏览器：
   - 昵称 `小映`、称呼 `导演`、启动预读均保留；
   - 手动记忆保留；
   - 项目 A 首次追问时，后端 memory evidence 显示 `retrieved=1`，
     召回 id 为该 AUTO 记忆；**跨重启召回通过**。
7. 项目 B 追问同一问题：`retrieved=0`；**项目隔离通过**。
8. 在配置页删除该 AUTO 记忆后，项目 A 再次追问：`retrieved=0`；**删除后不再召回通过**。
9. 删除后，数据库仅保留：
   - 1 条 `DELETED` 墓碑（正文为空、哈希保留）；
   - 1 条 ACTIVE 手动全局记忆；
   - 原 Support 会话/消息未删除。
10. 删除后再次执行自动提炼：`suppressed=1, created=0`；**墓碑抑制通过**。

截图（验收环境本地，不提交用户数据）：

- `/tmp/v22-e2e/final-settings-light.png`
- `/tmp/v22-e2e/final-settings-dark.png`
- `/tmp/v22-e2e/final-project-a-recall.png`
- `/tmp/v22-e2e/after-delete.png`

浏览器无未捕获 `pageerror`。资源请求中出现的连接失败来自本机未启动的 ComfyUI/端口探测与
不存在的静态资源，不是页面异常。

旧业务代办：V1/V2/V2.1/V3 的 147 项 Support 测试全绿；`/support/capabilities`、
`/support/capabilities/actions`、计划预览与委托链路未改动。

## 5. 安全验收

| 检查 | 结果 |
| --- | --- |
| 昵称 HTML/脚本注入 | 400 `HTML_NOT_ALLOWED` |
| 昵称系统指令改写 | 400 `INSTRUCTION_NOT_ALLOWED` |
| 启动预读/记忆密钥 | 400 `SECRET_DETECTED` |
| 记忆脚本标签 | 400 `HTML_NOT_ALLOWED` |
| 自然语言删除/编辑记忆 | 无对应能力；记忆仍存在 |
| 记忆触发任意 API/shell/git | 工具清单无 shell/git；记忆只进入 prompt 文本 |
| 跨项目自动召回 | 项目 B 测试 `retrieved=0` |
| 远端提炼未授权 | 自动提炼 `REMOTE_CONSENT_REQUIRED`，不发送 |
| 远端上下文未授权 | prompt `long_term_memories=[]` |
| 删除后残余向量召回 | FTS/向量同步删除；复测 0 |
| 业务事实被记忆覆盖 | 系统规则 + prompt `memory_notice` + 现有 verdict/task 证据优先 |
| Git 泄漏 | 未提交数据库、密钥、对话、模型或用户偏好文件 |

## 6. 记忆引擎与资源要求

| 项目 | 结论 |
| --- | --- |
| 存储 | SQLite（与 Shine 同一 metadata DB） |
| 关键词索引 | FTS5（`unicode61` + CJK 二元组增强） |
| 向量 | 本地 256 维 signed hashing / CJK n-gram + ASCII 词 |
| 向量大小 | 1,024 bytes/条 |
| 性能（本机容器） | 1,000 次 embed 约 0.80s（约 0.8ms/次，240 字文本）；10,000 次 cosine 约 0.17s |
| CPU | 任意 x86_64；无 GPU、无 CUDA、无 ONNX、无模型下载 |
| 内存 | 单次召回最多 800 候选 × 256 维；无模型常驻 |
| 启动成本 | 不加载模型；索引按需初始化 |
| 外部服务 | 不依赖 Chroma/Qdrant/Docker/云端向量库 |
| 语义模型 | bge-small-zh-v1.5 等 **未打包/未验证**，状态显示 `NOT_CONFIGURED` |
| sqlite-vec | 未采用；Windows 扩展加载兼容性未验证 |
| 显卡冲突 | 不进入 ComfyUI/H3 资源租约；默认不占显存 |

## 7. 明确未验证 / 降级

| 项目 | 状态 | 说明 |
| --- | --- | --- |
| Windows 10/11 真机安装包 | NOT_RUN | 无 Windows 设备；静态审查无新增依赖/二进制/模型 |
| RTX 4070/5080/5090 + H3 真机生产 | NOT_RUN | 无 GPU/H3 设备；未改生产链路 |
| bge/ONNX 语义向量 | NOT_RUN | 未下载、未验证许可证与权重；使用哈希向量降级 |
| sqlite-vec | NOT_RUN | 未引入额外扩展依赖 |
| 真实付费/远端 LLM 质量 | NOT_RUN | 浏览器 E2E 使用 loopback mock 证明链路 |
| 真实 Windows SQLite FTS5 | NOT_RUN | Linux 容器 FTS5 可用；Windows 需真机复核 |
| 跨项目显式引用 | 未实现 | 聊天链路永不自动跨项目；配置页可显式筛选查看 |

## 8. 工程验收结论

V2.2 达到任务定义的功能与安全目标：

**给 AI 管家起名字 → 设置称呼 → 编写启动预读 → 在不同时间/项目持续交流 → 自动记住经过筛选的
长期信息 → 按当前项目召回 → 随时查看/修订/删除记忆。**

且：

- 未增加第二套 Agent；
- 未复制正式业务事实；
- 未绕过原有授权；
- 未影响 H3 真实生产；
- 未引入独立向量数据库服务；
- 记忆模型缺失时聊天/生产不被阻塞；
- 历史记忆不覆盖实时业务状态；
- 用户不需要额外工程配置。

## 9. 最终八分片更新

最终结果：**8/8 分片 exit 0，合计 3,097 个后端用例全部通过**。

- `chunk-aa / ac / af / ag`：首轮即通过；
- `chunk-ab / ae / ah`：首轮仅因新增迁移 head 与新增审计码表未同步而失败，修正测试常量/
  前端码表后复跑 exit 0；
- `chunk-ad`：首轮 8 分片并行时超过 30 分钟被人工终止（无失败输出）；单独复跑 exit 0，
  543 个用例通过。

复跑日志位于 `/tmp/v22-shards/chunk-*-rerun.log`（验收环境本地，不提交仓库）。
