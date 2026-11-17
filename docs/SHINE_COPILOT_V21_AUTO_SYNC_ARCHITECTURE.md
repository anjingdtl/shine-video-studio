# Shine AI 管家 V2.1 — 自动同步知识与控制契约架构

- 日期：2026-11-17
- 分支：`feat/shine-copilot-v21-auto-sync`（基于 V2 `6fd3d8c`，V1/V2 均未合并 main）
- 应用版本：`2.1.0`；契约版本 `shine-copilot-contracts-2026.11.17-v21`
- 关联文档：`SHINE_COPILOT_V21_CONTRACT_GENERATION.md`、`SHINE_COPILOT_V21_COMPATIBILITY_MATRIX.md`、`SHINE_COPILOT_V21_ACCEPTANCE_REPORT.md`

## 0. 目标

让「Shine 正式业务能力」成为唯一维护点：开发者改业务代码与声明，构建期自动生成 AI 管家的知识索引与能力合同；用户升级后，AI 管家只加载当前安装版本打包的产物，不再手工维护第二份知识或参数白名单。

**知识自动更新，不等于权限自动升级。** 新增 API 会被发现、被说明，但只有通过路由、请求模型、唯一执行者、回读接口和审核合同之后，才能成为可执行动作。

## 1. 唯一事实来源

| 事实 | 唯一来源 | AI 管家如何使用 |
| --- | --- | --- |
| 业务能力 | FastAPI 路由 + Pydantic 请求模型 + 原服务/Worker | 声明中引用，构建期校验；运行期不重新定义 |
| 业务裁决 | `/api/v1/setup/verdict`、生产门禁、风格冻结、资产确认、质检/交付 | 原样读取；V2.1 不产生第二套 READY/PASS |
| 状态台账 | 项目、文档、Run、Job/Attempt、回执 | 回读接口与证据快照，不建第二本账 |
| 参数合同 | `backend/shine/copilot/declarations.py` 的 `ParamSpec` | 生成能力目录；服务端仍由 `_validate_params` 校验 |
| 知识内容 | 同一份 `FeatureDeclaration` | 生成版本化索引；不靠 LLM 猜测代码用途 |
| 构建身份 | `BUILD_INFO.json` / `SHINE_BUILD_COMMIT` + `knowledge_manifest.json` | 运行时比对；不一致精确降级 |
| 官方资料 | `support/official_docs.py` 的受审核摘要与链接 | 仅参考证据，永不授权、永不改变权限 |

## 2. 构建期与运行期职责

### 构建期（唯一生成者）

```
backend/shine/copilot/declarations.py       ← 业务声明（唯一维护点）
        │
        ├── scripts/generate_shine_copilot_contracts.py --write --commit <sha>
        │        ├── 校验路由 / 请求模型 / 执行者 / 回读 / 特征 / 审核状态
        │        ├── 对比上一版合同：参数删除、权限提升、GPU/付费、执行者变更
        │        └── 写出产物
        ↓
backend/shine/support/generated/copilot_contracts.json  （知识索引 + 能力目录 + 矩阵 + DISCOVERED）
backend/shine/support/knowledge_manifest.json            （构建身份：版本/指纹/提交号）
```

- 打包脚本 `scripts/build_test_package.ps1` 在写入 `BUILD_INFO.json` 后调用生成器，再 `--check` 验证。
- CI 在 L1 执行 `--check`：任何合同失配直接失败（见 §5）。
- 生成结果确定性：无时间戳、`source_commit` 不参与内容指纹；同一份代码生成同一指纹。
- 提交到仓库的产物 `source_commit` 为空：构建注入真实提交号，避免「清单包含自身提交」的自引用。

### 运行期（只加载，不生成）

- `support/contracts.py` 只读取打包产物 + 清单 + `BUILD_INFO.json`；不扫描源码、不访问网络、不调用生成器。
- `support/project_knowledge.py` / `support/capability_catalog.py` 从产物构建索引与目录。
- `support/actions.py` 在执行前调用 `execution_state(action_id)`：产物缺失/损坏/指纹不符/未审核 → 只拒绝该动作。
- 委托信封记录每一步当时的 `contract_digest`；升级后未执行步骤遇到不一致即暂停并要求重新确认。

## 3. 权限模型（三态分开）

| 维度 | 取值 | 谁决定 |
| --- | --- | --- |
| 发现状态 | `DISCOVERED` | 构建期路由扫描：未声明 API 只被列出，`executable=false` |
| 审核状态 | `AVAILABLE` / `REVIEW_REQUIRED` | 声明的 `availability` + `executor_bound` + `dispatch` 审核 |
| 运行时状态 | `AVAILABLE` / `CONTRACT_STALE` / `CONTRACT_REVIEW_REQUIRED` / `CONTRACT_NOT_DECLARED` / `CONTRACT_UNAVAILABLE` | `execution_state()` 对每个动作精确计算 |

- 只有运行时 `AVAILABLE` 才能进入 `preview_action`。
- 未声明动作即使被写进产物（被篡改），`live_contract()` 返回 `None` → `CONTRACT_NOT_DECLARED`，不可执行。
- `FORBIDDEN`（shell/git/源码写入/节点源码/通用 HTTP/自授权）在声明层就不存在对应动作。

## 4. 升级与兼容

1. 每次授权把 `contract_digest` 写入 preview；批准时重算，不一致 → `EXPIRED` + `CONTRACT_CHANGED`（HTTP 423）。
2. 每个委托步骤在创建时记录当时的 `contract_digest` / `contract_revision`。
3. 升级后：已提交步骤继续按原任务台账跟踪（`refresh` 只读原任务）；未执行步骤合同不一致 → `PAUSED` + `CONTRACT_CHANGED`，不重提。
4. 旧版委托（无合同字段）→ `PAUSED` + `CONTRACT_UNVERIFIED`，不得直接执行。
5. 已 `DONE` 的委托重复刷新不会重新提交。
6. 知识 `STALE` 只影响说明的可信度（回答标注过期），不阻断能力执行；能力合同失配才阻断该动作。

## 5. CI 契约检查（12 项）

| 检查 | 实现 |
| --- | --- |
| 注册 API 真实存在 | 路由表比对（方法 + 归一化路径，支持 `pause|resume|cancel`） |
| Schema 与 API 一致 | `body` 绑定参数必须在真实 Pydantic 模型字段中 |
| 唯一执行者存在 | 导入模块/属性 |
| 回读接口有效 | `authority` 为 GET/POST 路由时必须存在 |
| 权限等级变化 | 合同指纹变化必须提升 `contract_revision`，权限提高列为 BREAKING |
| 业务门禁仍在 | 声明的 `gates` 与执行链保持；V2.1 不新增裁决 |
| 删除的 API 仍被引用 | 路由不存在即失败 |
| 参数不兼容变化 | 删除参数 = BREAKING，需 `--allow-breaking` 显式接受 |
| 前端能力合同匹配 | 前端只读 `/support/capabilities/actions`，不保存参数副本 |
| 知识来源有效 | 每个 `sources` 路径必须存在 |
| 知识与目录匹配构建 | `manifest` 与产物指纹逐项比较 |
| 未审核动作获得执行权 | `AVAILABLE` 必须 `executor_bound` 且 `dispatch` 属于已绑定分派器 |

## 6. 四原则符合性审查

| 原则 | 落地 | 反例防线 |
| --- | --- | --- |
| 一套事实 | 声明引用真实路由/模型/执行者/回读；知识由声明生成 | 无第二份参数白名单：`ACTION_PARAMS` 由声明派生，`_ALLOWED_PARAMS` 再派生 |
| 一个裁决 | 生产/环境/风格/资产/交付读原门禁 | 生成器不写 READY；`DISCOVERED` 不产生执行权 |
| 一个执行者 | 每个写动作映射一个既有服务/路由/Worker | 无通用 HTTP 工具；无新队列、无新 GPU 管理器 |
| 运行时源码零修改 | 运行时只读打包产物 | 无 shell/git/patch；`contract_self_grant` 明确禁止；篡改产物 → `CONTRACT_*` 拒绝 |

## 7. 安全降级矩阵

| 异常 | 行为 |
| --- | --- |
| 产物缺失/损坏 | 知识回退到包内声明并标 UNKNOWN；所有写动作 `CONTRACT_UNAVAILABLE` 拒绝 |
| 清单与产物不一致 | `STALE`；只读回答标注过期；动作合同若指纹一致仍可执行 |
| 单个动作合同失配 | 该动作 `CONTRACT_STALE`，其余动作不受影响 |
| 动作未审核 | 该动作 `CONTRACT_REVIEW_REQUIRED`，并在原页面完成 |
| 旧授权遇合同变化 | `EXPIRED` + `CONTRACT_CHANGED`，重新预览 |
| 旧委托无合同 | `PAUSED` + `CONTRACT_UNVERIFIED` |
| 原任务 UNKNOWN | `NEEDS_REVIEW`/`PAUSED`，绝不自动重提 |

## 8. 明确不做

- 不新增 API 网关、任务调度器、GPU 管理器、业务状态中心、权限中心或第二套参数验证器。
- 不在运行时扫描源码或重新生成合同。
- 不让 LLM 猜测代码用途写入正式知识库；长文说明只引用受审核文档路径。
- 不因一次合同失配锁死整个 Shine 或全部 AI 功能。
