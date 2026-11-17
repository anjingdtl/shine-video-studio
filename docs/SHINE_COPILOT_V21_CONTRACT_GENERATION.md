# Shine AI 管家 V2.1 — 契约生成规范

- 日期：2026-11-17
- 生成器：`backend/scripts/generate_shine_copilot_contracts.py`
- 声明文件：`backend/shine/copilot/declarations.py`
- 产物：`backend/shine/support/generated/copilot_contracts.json`、`backend/shine/support/knowledge_manifest.json`

## 1. 正式业务能力声明

每个可被 AI 管家引用或执行的能力，只在 `declarations.py` 声明一次。

### 1.1 写能力 `CapabilityDeclaration`

| 字段 | 含义 | 生成器的检查 |
| --- | --- | --- |
| `action_id` | 稳定动作标识 | 唯一；`AVAILABLE` 时必须属于已绑定分派器 |
| `feature_id` | 关联知识条目 | 必须存在于 `FEATURES` |
| `label` / `description` | 面向用户的说明 | 非空 |
| `params: tuple[ParamSpec, ...]` | AI 参数合同 | 顺序即目录顺序；`body` 参数必须在真实 Pydantic 模型字段中；`path` 参数必须在路由模板中 |
| `api_route` | 真实路由（`暂停|继续|取消` 可写成 `a|b|c`） | 路由表必须存在 |
| `request_schema` | 真实请求模型（`module:Class`） | 必须可导入；`body` 参数逐一比对 |
| `response_schema` | 响应模型（可选） | 可导入（若声明） |
| `executor` | 唯一既有执行者 | 模块/属性必须可导入 |
| `authority` | 回读接口或台账 | 路由形式必须真实存在 |
| `permission_tier` | `PREVIEW` / `EXECUTE_GUARDED` | 写动作只能这两种 |
| `project_scope` | `PROJECT` / `MACHINE` / `OPTIONAL` / `RUN` | 运行时据此决定是否继承会话项目 |
| `preconditions` / `gates` | 原业务门禁名字 | 只作说明与计划展示，不产生第二套裁决 |
| `occupancy_gate` | `FULL` / `NONE` / `RELEASE` | 运行时占用门禁与委托自动步骤集合由它派生 |
| `gpu` / `paid` | 资源成本 | 目录展示；委托预算据此分类 |
| `idempotency_policy` / `readback_contract` / `rollback_policy` | 幂等、回读、回滚说明 | 必填（可默认） |
| `availability` | `AVAILABLE` / `REVIEW_REQUIRED` / ... | `AVAILABLE` 必须有执行者、真实路由、已绑定分派器 |
| `dispatch` | 已审核分派键（默认自身） | 目标必须是一个已存在的写动作 |
| `executor_bound` | 是否已审核绑定 | `AVAILABLE` 必须为真 |
| `introduced_version` | 引入版本 | 展示与兼容报告 |
| `contract_revision` | 合同版本号 | 合同指纹变化时必须提升 |

### 1.2 只读能力

`CapabilityDeclaration` 的只读形式：`permission_tier="READ"`、`status="READ_ONLY"`、`executor` 指向既有只读函数、`api_route`/`authority` 指向真实 GET 路由。

### 1.3 功能声明 `FeatureDeclaration`

| 字段 | 含义 |
| --- | --- |
| `feature_id` | 稳定功能标识 |
| `module` / `title` / `description` | 功能说明 |
| `status` | 只能 `IMPLEMENTED` / `PARTIAL` / `PLANNED` / `BLOCKED` / `NOT_SUPPORTED` |
| `verification` | `AUTOMATED` / `HARDWARE_REQUIRED` / `NOT_RUN` / `BLOCKED` |
| `sources` | 真实仓库路径，必须存在 |
| `apis` | 真实路由，必须存在 |
| `evidence` | 自动化测试/证据路径（说明性） |
| `not_supported` | 已知不支持边界的明确说明 |
| `docs` | 受审核长文的文档路径 |

**状态来自受审核的工程证据**：路由存在不等于 `IMPLEMENTED`；单测通过不等于真机 GPU/H3 通过；方案文档存在不等于已上线。

## 2. 自动生成规则

```
声明（写 + 读 + 功能 + 人工 + 不支持 + 矩阵 + 禁止项）
  → 路由表（真实 FastAPI app）
  → 校验（12 项）
  → 对比上一版合同（参数/权限/成本/执行者/路由）
  → 产物（确定性 JSON）+ 清单（构建身份）
```

- 知识条目字段：`knowledge_id`、`feature_id`、`module`、`title`、`description`、`source_path`、`source_revision`、`api_contract`、`implementation_status`、`verification_status`、`app_version`、`knowledge_revision`、`content_hash`。
- `content_hash` 只覆盖内容，不含 `source_revision`：同一代码同一指纹，避免构建时间戳造成虚假过期。
- `DISCOVERED`：未声明但真实存在的 `/api/v1` 路由，写入产物 `discovered`，恒为 `executable=false`。
- 参数合同同时输出 `param_contract`（类型/必填/长度/枚举/绑定方式），前端与自然语言规划器读同一份。

## 3. API 变更处理

| 变更 | 分类 | 处理 |
| --- | --- | --- |
| 新增可选参数 | 兼容 | 提升 `contract_revision`；目录与前端自动更新 |
| 新增必填参数 | BREAKING | 必须 `--allow-breaking` 并提升版本；旧授权失效 |
| 删除参数 | BREAKING | 同上；旧动作不能误执行 |
| 权限等级提高 | BREAKING | 同上 |
| GPU/付费成本增加 | BREAKING | 同上 |
| 执行者/路由变化 | BREAKING | 同上 |
| 动作从 `AVAILABLE` 撤回 | BREAKING | 旧授权与新委托均被拒绝 |
| 新增路由未声明 | DISCOVERED | `--check` 报告差异并失败，要求重新生成与人工审核 |
| 删除路由 | 失败 | 声明引用不存在路由 → CI FAIL |

**禁止静默把旧授权应用到新合同**：preview 记录 `contract_digest`，批准时重算；不一致 → `EXPIRED` + `CONTRACT_CHANGED`。

## 4. 后续开发者的固定流程

1. 完成原业务实现与测试。
2. 维护原 Pydantic Schema / 服务签名。
3. 在 `declarations.py` 声明一次（新动作还需在 `support/actions.py` 绑定分派器）。
4. 运行 `python -m scripts.generate_shine_copilot_contracts --write`。
5. 运行 `--check`（CI 也会运行）。
6. 若合同变更：按分类提升 `contract_revision`；BREAKING 需显式 `--allow-breaking` 并在 PR 中说明。

不需要再修改：`capability_catalog.py` 的静态列表、`actions.py` 的参数白名单、`tools.py` 的动作表、前端参数副本、`project_knowledge.py` 的静态条目。

## 5. 常用命令

```bash
# 生成（发布构建由 build_test_package.ps1 在 staging 内自动执行）
PYTHONPATH=backend uv run --project backend python backend/scripts/generate_shine_copilot_contracts.py --write --commit "$(git rev-parse HEAD)"

# CI / 本地校验
PYTHONPATH=backend uv run --project backend python backend/scripts/generate_shine_copilot_contracts.py --check

# 兼容旧命令（仅写单个清单文件）
python backend/scripts/generate_copilot_knowledge.py --commit <sha> --output /tmp/knowledge_manifest.json
```

## 6. 审核边界（必须保持）

- 生成器只派生，不执行、不裁决、不联网、不读写模型。
- LLM 不得生成 `action_id`、参数或 API 路径；`nlu.enrich_plan` 只补已注册动作的 `title`/`description`/`language`。
- 新增动作默认不可执行：`availability` 必须是经审核的 `AVAILABLE`，且 `dispatch` 必须指向已绑定分派器。
- 人工确认类（大纲、风格冻结、镜头、主资产、备料、审片、删除、安装授权）不因自动化而开放。
