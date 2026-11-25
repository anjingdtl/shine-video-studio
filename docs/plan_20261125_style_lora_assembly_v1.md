# Shine Video Studio — 风格化 LoRA 装配能力 V1 技术规格书（三原则审查定稿）

**日期**：2026-11-25  
**适用范围**：统一 H3 Ref2VA 工作流基座（`graph_compiler.py` / `foundation/h3.py`）、七阶导演流水线（⑤ 导演包）、四阶独立流水线（`manual4` 排产页）  
**最高工程准则**：`AGENTS.md` 项目三原则 —— **一套事实、一个裁决、一个执行者**

## 1. 三原则审查与收敛结论

对照 `AGENTS.md` 的「一套事实、一个裁决、一个执行者」及三条禁令，本方案对初稿进行了 5 项关键收敛，消除平行状态与分裂链路：

| 维度 | 初稿潜在分裂风险 | 三原则收敛后设计 |
| :--- | :--- | :--- |
| **一套事实（ComfyUI 可见性）** | 数据库缓存 `comfy_lora_name` 并额外提供 `POST /api/loras/refresh` 同步，易与实时 ComfyUI 实例状态漂移。 | **不落库缓存 `comfy_lora_name` 与可用状态**。`style_lora_assets` 仅存不可变文件身份（`id/path/sha256/default_strength/trigger_word/origin`）；可见性与精确名称由唯一裁决口实时结合磁盘与 `/object_info/LoraLoaderModelOnly` 投影，删除冗余 `refresh` 写接口。 |
| **一套事实（托管目录）** | 区分「托管实例目录」与「接管实例目录」两套落盘位置。 | 统一落盘到 Shine 唯一托管目录 `<comfyops_model_root>/loras/shine_user/`；接管既有 ComfyUI 时统一通过 `extra_model_paths.yaml` 的 `loras` 映射暴露，不维护双目录副本。 |
| **一个裁决口（装配门禁）** | 前端、七阶导演包、四阶排产、编译器各自写一套数量/权重/W6A8/可见性校验。 | 建立唯一裁决口 `evaluate_style_lora_selection()`（位于 `comfyops/loras.py` 并由 `foundation/h3.py` 统一导出）。七阶预检、四阶快照、Worker 提交与编译器前置均调用同一裁决口；前端仅消费后端投影（`status`、`lora_supported`）。 |
| **一个执行者（触发词注入）** | 七阶与四阶各自拼接 `trigger_word`，易造成双线不一致或引入非 ASCII 破坏 `H3PromptCompiler` 规则包。 | 触发词入口强校验（仅 ASCII 安全短语，禁止 CJK 与 H3 保留标签），由唯一函数 `apply_style_lora_triggers()` 在冻结 Prompt 与计算 `prompt_sha256` 前统一注入，确保「冻结文本哈希 = 实际提交文本哈希」。 |
| **一个执行者（导入与路由）** | 路由前缀未统一 `/api/v1`，上传与本地路径导入容易写两套校验逻辑。 | 路由统一挂载在 `/api/v1/loras`；所有导入方式统一汇入唯一执行者 `import_style_lora()`（校验 `.safetensors`、解析 header、流式 `sha256` 去重、原子落盘、入库）。 |

## 2. 目标与 V1 范围边界

### 2.1 核心目标
在保持统一 H3 Ref2VA 单一基座工作流与「只编译已审计配方」原则的前提下，新增用户可控的风格化 LoRA 导入与装配能力，使七阶导演流水线和四阶排产流水线共用同一套 LoRA 资产台账、唯一裁决口、图编译逻辑与共享前端装配组件。

### 2.2 V1 明确约束
- **电影化 LoRA 外移去硬编码**：移除 `backend/shine/comfy/graph_compiler.py` 中对 `REALISM_LORA`（`MiniMax-H3\h3-realism-people-t2v-i2v-r2v.safetensors`）的硬编码挂载，将其外移为普通可配置独立 LoRA 条目（通过自动发现纳管，用户可自由勾选、调权重或移除）。
- **零 LoRA 纯净基座**：当 `style_loras` 为空时，基座图直连 `UNETLoader ("10")` → `MiniMaxH3SigmaShift ("12")`。
- **独立风格 LoRA 串联上限**：V1 仅支持单个 `LoraLoaderModelOnly` 节点形态的独立风格化 LoRA，最大串联 **3 个**（`0 ~ 3` 个按顺序串联），**不支持多节点 LoRA 或自定义子图导入**。
- **双流水线同步**：七阶导演线（`DirectorShootPanel.tsx` ⑤ 导演包）与四阶独立线（`ScheduleProduceStagePage.tsx` 排产页）同步接入同一个共享装配组件与后端基座契约。
- **低显存变体门禁**：V1 对 `W6A8`（`ref2va-lowend-candidate`）变体禁止挂载 LoRA，仅在已验收的 `INT8`（`h3-ref2va-int8-convrot`）主线启用；由 `strategy/options` 投影 `lora_supported` 字段，唯一裁决口与编译器兜底拦截。

## 3. 核心数据契约

### 3.1 本地 LoRA 资产唯一台账（`style_lora_assets`）
在平台 SQLite 数据库中新增表 `style_lora_assets`（附 Alembic 迁移），**仅存储持久化的文件与元数据身份，不缓存易漂移的 ComfyUI 运行时状态**：

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `id` | `str` (PK) | 唯一 ID（用户导入 `lora_<short_uuid>`；外移发现的电影化 LoRA 使用稳定 ID `h3-realism-people`） |
| `display_name` | `str` | 前端展示名（默认去后缀文件名，用户可改名） |
| `filename` | `str` | 原始 `.safetensors` 文件名 |
| `storage_path` | `str` | 实际落盘绝对路径（受管目录或既有 H3 部署发现路径） |
| `sha256` | `str` (64 hex, unique) | 文件内容哈希，用于去重与冻结防篡改核验 |
| `file_size_bytes` | `int` | 文件字节数 |
| `default_strength` | `float` | 默认权重，范围 `[0.0, 1.5]`，默认 `0.65` |
| `trigger_word` | `str` | 可选触发词（最长 160 字符，纯 ASCII，禁止 CJK 与 H3 保留标签，默认空） |
| `origin` | `Literal["imported", "discovered"]` | `imported` 为用户导入；`discovered` 为从既有 H3 部署目录扫描纳管 |
| `created_at` / `updated_at` | `datetime` | 时间戳 |

实时投影字段（仅在 API 响应与裁决结果中动态计算，不落库）：
- `comfy_lora_name: str | None`：ComfyUI `/object_info/LoraLoaderModelOnly` 枚举中的精确匹配项
- `status: Literal["READY", "MISSING_FILE", "HASH_MISMATCH", "NEEDS_COMFY_REFRESH", "COMFY_UNREACHABLE"]`

### 3.2 编译器与基座不可变规格（`StyleLoraSpec`）
在 `backend/shine/comfy/graph_compiler.py` 中定义冻结 dataclass，并由 `backend/shine/foundation/h3.py` 导出：

```python
MAX_STYLE_LORAS = 3

@dataclass(frozen=True)
class StyleLoraSpec:
    lora_id: str
    lora_name: str           # 必须来自唯一裁决口解析的 ComfyUI 精确枚举名
    sha256: str              # 64位小写十六进制
    strength_model: float = 0.65
    trigger_word: str = ""

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "lora_id": self.lora_id,
            "lora_name": self.lora_name,
            "sha256": self.sha256,
            "strength_model": round(float(self.strength_model), 4),
            "trigger_word": self.trigger_word.strip(),
        }
```

### 3.3 策略、快照与执行请求扩展
1. **统一 Pydantic 契约（`backend/shine/strategy.py`）**：
   - 新增 `StyleLoraConfig(BaseModel)`（`extra="forbid"`）：
     - `lora_id: str`
     - `lora_name: str`
     - `sha256: str`（64 位小写 hex）
     - `strength_model: float = Field(default=0.65, ge=0.0, le=1.5)`
     - `trigger_word: str = Field(default="", max_length=160)`（通过统一校验器拒绝 CJK、控制字符与 `<Subject>`/`<Audio>`/`<Video>`/`<Picture>` 等保留标签）
   - 在 `GenerationStrategy` 中新增 `style_loras: list[StyleLoraConfig] = Field(default_factory=list, max_length=3)`。`strategy_sha256()` 自动包含其顺序、权重、哈希与触发词。
   - 在 `model_variant_options()` / `/api/v1/strategy/options` 的每个变体条目中投影 `lora_supported: bool`（`INT8` 为 `True`，`W6A8` 为 `False`），前端直接读该字段。
2. **统一执行请求（`backend/shine/foundation/h3.py`）**：
   - `H3ExecutionRequest` 新增 `style_loras: tuple[StyleLoraSpec, ...] = ()`。
   - 提供唯一触发词合成函数 `apply_style_lora_triggers(prompt_text: str, style_loras: Iterable[StyleLoraSpec]) -> str`。
3. **四阶独立线（`backend/shine/manual4/`）**：
   - `M4SnapshotConfigBody`（`manual4/api/production_routes.py`）直接复用 `StyleLoraConfig`，新增 `style_loras: list[StyleLoraConfig] = Field(default_factory=list, max_length=3)`。
   - `compile_m4_ref2va_workflow()`（`manual4/comfy/h3_adapter.py`）与 `manual4/production/service.py` 全程透传 `style_loras` 至 `compile_h3_ref2va_graph()` 与 `H3ExecutionRequest`，固化进 `snapshot_sha256` 与 Run config。

## 4. 唯一执行者与唯一裁决口（`backend/shine/comfyops/loras.py`）

### 4.1 唯一导入执行者：`import_style_lora()`
1. **文件格式与安全校验**：
   - 仅允许 `.safetensors` 后缀（否则抛 `LORA_INVALID_EXTENSION`）。
   - 解析 safetensors 文件头（8 字节小端长度 + UTF-8 JSON header，限制 header 大小上限防 DoS），校验结构合法（否则抛 `LORA_CORRUPT_HEADER`）。
   - 流式计算 `sha256`；若 `style_lora_assets` 已存在同哈希且磁盘文件完好，直接幂等返回既有记录。
2. **唯一托管落盘**：
   - 统一落盘至 `<comfyops_model_root>/loras/shine_user/<sanitized_filename>`（先写临时文件再原子替换；文件名冲突时附加短哈希后缀）。
   - 同步确保 `extra_model_paths.yaml` 的 `loras` 条目包含 `<comfyops_model_root>/loras`（对托管与接管既有 ComfyUI 均成立）。

### 4.2 原电影化写实 LoRA 的外移发现：`sync_discovered_loras()`
- 在 `GET /api/v1/loras` 查询时幂等扫描既有部署路径下的 `Minimax-H3/h3-realism-people-t2v-i2v-r2v.safetensors`。
- 若存在且未入库（按 `sha256` 去重），登记为 `id="h3-realism-people"`、`display_name="电影化写实 LoRA (H3 Realism)"`、`default_strength=0.65`、`origin="discovered"`。
- 删除策略：`imported` 条目删除时清理托管文件；`discovered` 条目仅删除台账记录，不删原始部署模型。正在运行中的 Run 引用该 LoRA 时拒绝删除（HTTP 409）。

### 4.3 唯一裁决口：`evaluate_style_lora_selection()`
七阶导演包预检、七阶开拍提交、四阶快照预览、四阶建 Run、Worker 执行前校验**全部调用此唯一函数**：
1. 校验数量 `<= 3`（`STYLE_LORA_LIMIT_EXCEEDED`）、无重复 `lora_id`/`sha256`（`STYLE_LORA_DUPLICATE`）、权重在 `[0.0, 1.5]`（`STYLE_LORA_STRENGTH_OUT_OF_RANGE`）。
2. 校验变体兼容性：`variant_id == W6A8_VARIANT_ID` 且非空时拒绝（`VARIANT_LORA_UNSUPPORTED`）。
3. 核对磁盘文件存在（`LORA_FILE_MISSING`）且实时 `sha256` 与冻结值一致（`LORA_HASH_MISMATCH`）。
4. 若提供实时 `object_info`（来自 ComfyUI `/object_info/LoraLoaderModelOnly`），按相对路径后缀精确匹配枚举项，返回规范化的 `tuple[StyleLoraSpec, ...]`；未命中则阻断（`LORA_NOT_VISIBLE_IN_COMFYUI`）。

## 5. 图编译器改造与兼容性（`backend/shine/comfy/graph_compiler.py`）

### 5.1 `_model_stack()` 动态串联
将 `_model_stack()` 扩展为接收 `style_loras: tuple[StyleLoraSpec, ...] = ()`：
- **0 个 LoRA**：不生成 LoRA 节点，`"12"`（`MiniMaxH3SigmaShift`）直接接收 `["10", 0]`。
- **1 ~ 3 个 LoRA**：节点 ID 依次使用 `"11"`, `"111"`, `"112"`：
  - `"11"` 接 `["10", 0]`；`"111"` 接 `["11", 0]`；`"112"` 接 `["111", 0]`；
  - `"12"` 接收最后一个 LoRA 节点的 `[last_lora_id, 0]`。
- **二采联动**：二采节点 `"62"`（`MiniMaxH3PDDAccApply`）继续复用 `model_out_ref`，风格 LoRA 链自然覆盖一采和二采。
- **编译器兜底校验**：即使跳过上层直接调用 `compile_ref2va_workflow()`，仍对数量、去重、权重与 `lowend` 变体做硬拦截。

### 5.2 哈希版本与历史兼容
- `recipe_payload` 新增 `"style_loras": [spec.canonical_dict() for spec in style_loras]`，`model_identity` 同步记录 `style_loras` 摘要。
- `_recipe_version()` 升级新编译路径为 `"shine.graph_compiler.v3"`（`W6A8` 仍保持 `"shine.graph_compiler.v2"`）。
- `low_vram_attention_form_of_graph()` 和 `is_low_vram_guard_variant()` 均新增 `style_loras: tuple[StyleLoraSpec, ...] = ()` 参数并透传。
- **在途历史快照兼容**：当 `style_loras == ()` 且新基座（0 LoRA）反推未命中旧 `frozen_graph_sha256` 时，反推函数兜底比对一次升级前的 legacy 形态（单挂 `REALISM_LORA @ 0.65`），确保升级后历史未完结任务不卡死。

### 5.3 触发词与 `prompt_sha256` 铁律
- 冻结镜头 Prompt 时统一经 `apply_style_lora_triggers()` 生成最终提交文本并计算 `prompt_sha256`；ComfyUI 提交前仍严格执行 `PROMPT_HASH_MISMATCH` 校验，绝不在提交阶段偷偷篡改已冻结的文本。

## 6. API 接口与错误码（`backend/shine/api_loras.py`）

统一挂载在 `/api/v1/loras`（遵循现有个人桌面 loopback + CSRF 中间件）：

| 方法与路径 | 用途 | 请求 / 响应要点 |
| :--- | :--- | :--- |
| `GET /api/v1/loras` | 获取全部可用风格 LoRA 列表 | 自动执行幂等发现 + 实时探测 ComfyUI `/object_info/LoraLoaderModelOnly`，返回 `items[]`（含实时 `status`、`comfy_lora_name`）及 `comfy_reachable` |
| `POST /api/v1/loras/import` | 导入 `.safetensors` | 支持 `multipart/form-data` 上传或 JSON `local_path`；可选 `display_name`、`default_strength`、`trigger_word`；统一由 `import_style_lora()` 执行 |
| `PATCH /api/v1/loras/{lora_id}` | 更新展示名、默认权重、触发词 | 校验权重 `[0.0, 1.5]` 与触发词安全规则 |
| `DELETE /api/v1/loras/{lora_id}` | 删除条目 | `imported` 同步删托管文件；`discovered` 仅删记录；运行中 Run 引用返回 `409 LORA_IN_USE` |

标准错误码（同步注册到 `frontend/src/api.ts` 与 `frontend/src/errorActions.ts`）：
- `LORA_INVALID_EXTENSION`、`LORA_CORRUPT_HEADER`、`LORA_FILE_MISSING`、`LORA_HASH_MISMATCH`
- `LORA_NOT_VISIBLE_IN_COMFYUI`、`LORA_IN_USE`、`STYLE_LORA_LIMIT_EXCEEDED`、`STYLE_LORA_DUPLICATE`、`STYLE_LORA_STRENGTH_OUT_OF_RANGE`、`STYLE_LORA_INVALID_TRIGGER`、`VARIANT_LORA_UNSUPPORTED`

## 7. 前端共享装配组件与双流水线接入

新建共享组件 `frontend/src/shared/StyleLoraAssemblyPanel.tsx`，被两条流水线直接复用：

1. **共享组件职责（只读后端投影，不自造规则）**
   - 列表与状态：展示当前已装配的风格 LoRA（`0/3 ~ 3/3`），支持勾选添加（满 3 个置灰）、上移/下移排序、权重滑块与数值输入（`0.00 ~ 1.50`，步长 `0.05`）、触发词覆盖、单条移除。
   - 导入与刷新：内置「导入 LoRA（`.safetensors`）」操作与「重新检测 ComfyUI」按钮（直接重新拉取 `GET /api/v1/loras`）。
   - 门禁反馈：直接依据后端返回的 `lora_supported`（变体不支持时禁用并提示）与条目 `status`（`MISSING_FILE`/`NEEDS_COMFY_REFRESH` 标红阻断）。
2. **七阶导演线（`frontend/src/components/DirectorShootPanel.tsx`）**
   - 嵌入 **⑤ 导演包** 拍摄方案区，随 `ProductionStrategy` 持久化至 `storedShootStrategy`，随导演包冻结并在版本历史中展示已冻结 LoRA 摘要。
3. **四阶独立线（`frontend/src/manual4/pages/ScheduleProduceStagePage.tsx`）**
   - 嵌入排产配置区，同步写入 `M4ScheduleConfigSnapshot`（`localStorage`）并在 `previewM4Snapshot` 与 `createM4Run` 中透传 `style_loras`。

## 8. 验收矩阵与回归纪律

### 8.1 后端单元测试（假权重严格使用 `tests.conftest.make_sparse_file`）
- **图编译**：0/1/2/3 个 LoRA 拓扑与哈希变化；4 个/重复/权重越界/W6A8 拒绝；`low_vram_attention_form_of_graph` 与 `is_low_vram_guard_variant` 覆盖新形态与 legacy 形态。
- **唯一执行者与裁决口**：合法 `.safetensors` 导入与幂等去重；非法后缀/坏 header 拒绝；电影化 LoRA 自动发现；磁盘篡改触发 `LORA_HASH_MISMATCH`；ComfyUI 未扫到触发 `LORA_NOT_VISIBLE_IN_COMFYUI`；触发词 CJK/注入拦截。
- **双流水线透传**：七阶导演包冻结→Runner→ComfyUI 提交图一致；四阶 `M4SnapshotConfigBody` 预览→建 Run→执行图一致；既有测试按需更新以适配 0-LoRA 默认基座与新版本哈希。

### 8.2 全量回归命令（严格遵守 `AGENTS.md`）
- **后端全量（必须 8 分片并行，禁止单进程串行）**：
  - `.\run_backend_shards.ps1 -WorkRoot <非系统盘固定测试目录>`（或带 `CODEBUDDY_SAFE_DELETE_ENABLED=0` 的 8 分片脚本）
  - 全部 8 个分片退出码为 `0` 且无 `FAILED`/`ERROR` 行
- **代码静态检查与迁移**：`ruff` 检查、Alembic 升级/降级校验
- **前端全量**：`npx vitest run --no-file-parallelism` 与 `npm run build`

### 8.3 真机 H3 验收（不可用 Mock/离线测试替代）
- 在具备真实 ComfyUI + MiniMax-H3 的本机上，分别通过七阶导演包与四阶排产页实跑：
  1. 0 LoRA 纯净基座出片 1 镜；
  2. 外移后的电影化写实 LoRA（调权 `0.4` vs `0.85`）出片对比；
  3. 电影化 LoRA + 用户自导入第三方 H3 风格 LoRA 双 LoRA 串联出片；
  4. 触发 LowVRAM 降级时带 LoRA 执行图通过哈希核验并成功出片。