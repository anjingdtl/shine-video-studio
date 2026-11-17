# Shine Assets 素材整备中心——独立工作台全量建设规范（V1.0）

> 规范类型：产品决策 + 架构 ADR + API/数据合同 + 云端 Codex 实施与验收规范  
> 制定：2026-11-08  
> 对应仓库：anjingdtl/shine-video-studio  
> 核对基线：main @ 631d9a07c6be5911a88e138bb037da3d369f3d79（2026-11-08 04:56:44 UTC）。实施时必须重新 fetch/rebase 核对最新 HEAD。  
> 实施方式：一个独立 feature 分支 + 一个 Draft PR；不得直接在 main 或用户生产数据库上改造。  
> 本文件为**待建设规范**，不代表功能已经实现，也不代表本机 GPU 已验证。

## 0. 一页执行摘要

**唯一正式产品决策：Shine Assets（素材整备中心）与 Shine Director（导演工作台）是同一级别的两个独立工作空间，不是导演模式内的标签页或第七个制作阶段。**

- **UI 独立**：入口、路由、导航、画布、任务视图、素材库和管理层级独立。可以在没有任何影视项目时使用全部素材中心工作流。
- **业务独立**：用户可自由创建图片和声音，不必须导入剧本或生成项目内 AssetRequirement。
- **数据互通**：全局素材库与导演项目双向流动；项目可挑选和绑定特定素材版本，项目优秀素材可经人工授权收藏到全局库。
- **底层复用**：同一个 FastAPI、SQLite、Worker、资源租约、ComfyUI 实例/管理器、Qwen-Image-2.1 模型、Voice/TTS 提供者、工作流能力注册、技术校验、授权审批、哈希媒体存储及审计账本。
- **图片完整创作链**：文生图、图生图、局部修订/遮罩、多图合成（最多 10 张参考图，以真实 workflow 声明为准）、人物穿戴合成；原生透明背景/抠图、RGBA 和支持时 2K 输出列入同一能力体系。
- **声音链**：音色设计、经授权克隆、试听/冻结、对白配音、录音与音效/音乐导入管理；不得虚构尚未安装的音效/音乐生成模型。
- **严格真实验收**：云端 Mock/Fake 只能证明代码与状态机；不能证明 Windows RTX 5080、Qwen 图片编辑、ComfyUI 实际工作流或 H3 成片质量。真实能力逐项 fail closed。
- **无破坏性迁移**：既有导演六阶段、已确认素材、已冻结导演包、四阶段 AI 编导流水线、真实 H3 资源占用与未知外部执行不得回写或重放。

## 1. 权威资料与当前远端状态

### 1.1 开工前阅读顺序

1. `AGENTS.md`：全部持久项目不变量及最新补充。
2. `docs/SHINE_ASSETS_CENTER_FULL_BUILD_PLAN_20261108.md`（本文件）。
3. `docs/adr/0006-director-ai-material-preparation.md`、`docs/DIRECTOR_AI_MATERIAL_PREPARATION_PLAN_20261104.md`。
4. `docs/AI_DIRECTOR_FOUR_STAGE_LONGFORM_PLAN_20261107.md`、`docs/FOUR_STAGE_CLOSEOUT_EVIDENCE_20261108.md`、`docs/ADVERSARIAL_REVIEW_FOUR_STAGE_PIPELINE_20261108.md`。
5. 与各服务配套的源文件、测试和当前 main HEAD；不得以旧报告替代实时代码审查。

### 1.2 已确认的工程落点（基于上述 SHA 的代码，不是完成承诺）

| 领域 | 现有代码入口 | 现状 / 本轮责任 |
|---|---|---|
| Web 壳、模式与路由 | `frontend/src/App.tsx`、`frontend/src/useStudio.ts`、`frontend/src/api.ts` | 现有 `ui='director'|'expert'`、`#d/*`、`#v/*`；新增独立 assets workspace，不能破坏旧路由。 |
| 导演项目素材 UI | `frontend/src/components/AssetPanel.tsx`、`MaterialPreparationPanel.tsx` | 现有素材上传/确认/绑定及从剧本驱动的生成；双向联动须复用这些合同。 |
| 四阶段创作流水线 | `frontend/src/components/FourStagePanel.tsx`、`backend/shine/api_pipeline.py`、`backend/shine/director/pipeline.py` | 2026-11-08 新合入，保留其确认、文档、长片交接及真实状态语义。 |
| 项目 Asset | `backend/shine/models.py` 中 `Asset` | `project_id` 非空；项目+SHA 唯一；approved/locked、entity_id、role、tags、provenance。 |
| 候选/音色/授权 | `backend/shine/comfyops/models.py`、`backend/shine/comfyops/assets.py`、`backend/shine/api_preparation.py` | CandidateAsset/VoiceProfile/RightsRecord 等当前按 project_id 归属；不能伪造 dummy project，也不能直接跨项目引用。 |
| 导演素材任务 | `backend/shine/api_material.py`、`backend/shine/materialprep/{service,execution,models}.py` | MaterialRun/Item/ImageAttempt + 202/Worker + UNKNOWN 对账；设计应提取可复用的执行内核，不拷贝一套。 |
| Qwen 出图 | `backend/shine/materialprep/qwen_workflow.py`、`runtime.py`、`image_prompts.py` | 现有审计过的 Qwen-Image-2.1 **T2I** 模板/输出节点/运行时指纹；不可将它误宣称为 I2I/Inpaint/MultiRef 已验证。 |
| ComfyUI 注册 | `backend/shine/api_preparation.py` 的 workflow-capabilities、`comfyops/registry.py`、`comfyops/image_renderer.py` | 复用 revision、dependency hash、live object_info、审批及模型托管。 |
| Worker/GPU | `backend/shine/worker.py`、`comfyops/scheduler.py`、`comfyops/renderer_release.py`、`comfyops/handoff_*` | 单一资源账本；不可影响在跑 H3 series 或强制释放外部未核实占用。 |
| 存储/媒体 | `backend/shine/comfyops/storage.py`、`backend/shine/media/{artifacts,thumbnails,probe}.py` | 继续按内容哈希存储；新增全局引用计数/可达性保护，不做第二份素材仓。 |
| 数据库迁移 | `backend/alembic/versions/` | 基线迁移头为 `f180b0020001`；本轮从真实最新 head 扩展，保证单 head、幂等和旧库升级。 |
| 现有验收 | `backend/tests/`、`frontend/src/test/` | 必须新增中心用例并保留旧全量回归。 |

### 1.3 外部模型能力依据（不是本机可用性证明）

- Qwen 官方模型仓库：https://github.com/QwenLM/Qwen-Image-2.1
- Qwen 官方发布介绍：https://qwen.ai/blog?id=qwen-image-2.1
- ComfyUI 官方 Qwen-Image-2.1 编辑示例：https://comfy.org/hub/workflows/qwen-image-2-1-image-edit-5fbe136ba6f9/
- Comfy 官方总览：https://comfy.org/qwen-image-2.1/

官方描述确认统一文生图与编辑、最多 10 参考图、多区域圈画/标注或单独遮罩、人物/产品一致性、原生 RGBA/抠图、2K 等能力。但**每一能力必须以本地 ComfyUI 实际安装的精确权重、节点、图、输入槽位、输出槽位、显存与真实小样为 READY 准入标准**。不得把模型支持能力、Comfy 社区样例、云端编译通过等同于安装实例已实现。严格校核 Qwen 及第三方权重的许可证；不得默认具有商业视频作品的使用许可。

## 2. 不可更改的产品边界（P0 不变量）

1. 在应用顶层增加三个并列空间：**Shine Director 导演工作台**、**Shine Assets 素材整备中心**、**专业设置**（设置服务于两者，不另建 ComfyUI）。导演六阶段保持原顺序。
2. 素材中心无 project_id 也可创建、查看、生成和版本管理；不要创建“全局虚拟项目”以规避当前外键。
3. 图片引擎仅指定 Qwen-Image-2.1 本地 ComfyUI 家族，禁止偷偷退回旧 Qwen、SD、远程付费图像 API 或 Fake provider。
4. 视频唯一生成核心仍为本机 MiniMax-H3/已允许的 Ref2VA 路径；素材中心不得替换视频核心或让图像任务篡改 H3 状态。
5. 一个 SQLite WAL 数据库、一个受管 Worker/任务仲裁器、一个 GPU lease/renderer 账本、一份内容寻址文件仓、一份审计记录。
6. 全局素材**候选 → QC → 用户人工确认/晋升 → 正式版本**；生成绝不自动晋升。用户明确选择“分配到项目”后，才创建项目授权的独立 Asset 身份快照。
7. 任何项目已有 Asset/VoiceProfile、镜头绑定、导演包、预制作 manifest 和生成记录不得因全局版本更新而隐式替换。
8. 已提交外部任务若状态 UNKNOWN，必须按外部 prompt_id/账本进行对账；不能无条件 retry/重新出图/再次扣费。
9. 目前单机 RTX 5080 16GB VRAM / 32GB RAM 为资源设计下限参考；2K、10 图并发的**质量、耗时、显存**均须实测，不能设定臆造 SLA。
10. 保持 localhost 与敏感信息保护；不得自行向云端上传用户图片、音频、声音克隆参考、剧本文字。
11. 单次 PR 允许多个按阶段提交，但其 diff 应只含本模块相关变更与必要共用抽象，不得顺手重写四阶段导演流水线。

## 3. 目标交互：真正独立的 Shine Assets

### 3.1 应用入口和 URL

顶层空间切换器：`导演工作台 | 素材整备中心 | 专业设置`。默认仍打开导演首页，记住用户最近使用的空间偏好但合法深链优先。

建议规范路由（可在同 PR 的 ADR 中微调具体语法，但不得破坏旧入口）：

- `#d/projects`、`#d/assets`、`#d/run` 等现有导演入口继续有效。
- `#a/home`：素材中心首页。
- `#a/images`：图片工坊。
- `#a/voices`：声音工坊。
- `#a/library`：全局素材库。
- `#a/assignments`：项目分配与使用记录。
- `#v/...`：专业模式保持有效。
- 进入/离开任一空间，未保存的遮罩与输入须明确提醒，不能静默丢失；项目变化应清理过期跨项目选择与异步响应。

素材中心必须有自成体系的布局，而不是复用整个导演素材准备长页面。移动窄屏有折叠操作栏；桌面画布可全屏。

### 3.2 首页

“开始创作”入口（文生图、编辑图片、局部修订、多图合成、人物穿戴、声音）、最近素材、最近任务、缺少哪些真实能力的诊断。无项目时也正常显示。不向用户暴露 model hashes、lease owner 等工程词，专业详情才看。

### 3.3 图片工坊：完整功能矩阵

| 用户模式 | 输入 | 预期输出 | 必须满足的后端合同 |
|---|---|---|---|
| 文生图 T2I | 文本、风格/构图、宽高比、尺寸、seed 可选、候选数量 | 一组独立候选版本 | QWEN_IMAGE_T2I 已审计 graph + live 验证，实际输出与所选尺寸一致或明确说明转换。 |
| 图生图 I2I | 原图、修改提示词、可选额外风格参考、保真控制（仅在工作流确有参数时显示） | 新候选，原图只读保留 | 实际图像输入传输 + Qwen image-edit graph；不能用 T2I prompt 文本假装输入了原图。 |
| 局部修订 INPAINT | 原图、画布遮罩、修订指令、可选强制保留条件 | 修改区域候选并可对比 | 真实遮罩或官方允许的圈画/图像标注接口映射；输入与遮罩坐标/像素对齐。 |
| 多图合成 MULTIREF | 1–10 张参考图，每图有语义角色/顺序、整体提示词 | 多主体合成候选 | 参考槽位有序、逐图传输与 hash 验证；只开放当前图确实支持的槽数，超限明确拒绝。 |
| 人物穿戴 WARDROBE | 人物基底 + 服装 + 鞋帽/配饰/道具等可选槽位 + 保持面部一致性指令 | 新造型候选、可进一步局部修订 | 作为 MULTIREF 的用户场景预设，不单独假造物理模型；角色槽位固定映射可审计。 |
| 透明背景 / 抠图 RGBA | 文字生成或现有图片 + 透明背景指令/编辑参考 | 真正 alpha PNG 或可保存 RGBA | 必须验证 PNG alpha 通道与非不透明像素；不是背景涂成棋盘格/纯白。 |
| 高分辨率 2K | 选择模型/工作流支持的高分辨率档 | 图片及实际尺寸元数据 | 工作流声明 + local GPU 实测，OOM 明确报错且保留任务状态。 |

**共通体验**：拖拽/粘贴导入本地授权参考图、预览实际来源、生成 1–N 候选、候选比对、放大检查、导出、版本回退、选择入库、重复上次参数、保存命名模板。默认最多 1 张候选以兼顾 16GB GPU，批量图片按队列逐个提交，不强制多图并行。

**图像质量约束**：人物身份一致性是“用户视觉验收目标”，不是保证 100% 相同；不保证从一张照片推导精确三视图。底图、构图、视角、服装变化和脸部保留要求都进入可追踪 manifest。用户可对比并决定是否晋升。

### 3.4 局部修订画布

- 画布最小能力：画笔、橡皮、矩形/自由选区（至少一种）、缩放/平移、笔刷大小、撤销/重做、清空选区、原图/修订后并排比较。
- 前端触控/鼠标绘画映射到**原图像素坐标**；导出遮罩为与输入图尺寸匹配的单通道或 RGBA PNG，语义固定（白=改、黑=保留；如实际图相反在适配层显式转换）。
- 不允许只有 CSS 视觉蒙层而未上传真实 mask 文件；不允许重画“看起来有变”的整张图后伪装局部修订。
- 遮罩空、全图、尺寸不一致、原图变更、引用版本 stale、超出合法像素尺寸均有明确提示。
- 工作流若原生要求圈画/标记图，服务器转换遮罩为符合官方节点语义的标注图，并记录转换器版本/哈希；未经验证不发布。
- 原始参考图、mask、提示词、生成工作流与结果图关联不可变版本；原图不覆盖。

### 3.5 多图合成与穿戴预设

- 每张图有 `reference_id`、`sha256`、`order`、`slot_role`、`rights_snapshot`。角色例：`subject`、`face`、`garment`、`hair`、`shoe`、`bag`、`hat`、`prop`、`scene`、`style`。
- 预览区展示每张图实际可点击来源和角色；拖动调整顺序后编译映射必须同步变化。
- 如果底层工作流提供 2 张槽位，UI 显示“此工作流目前仅支持 2 张”，不能吞掉第三张；需要注册/验证 10 槽位图后才开放 10 张。
- 人物穿戴的首选主图应含人物；服装参考不默认变成身份主图，保真提示词与规则版本固定，可进一步使用局部修订。
- 使用输出作为下一轮输入时创建 lineage，而非覆盖原版本；合成失败的中间产物不会误成为正式主素材。

### 3.6 声音工坊

- **音色设计**：描述、试听文本、生成/试听、用户冻结、版本；复用 Voice Provider、Voice Profile 规范，但全局主体必须允许没有 project_id。
- **声音克隆**：必须有来源和相应主体授权、用途与期限证明；不得因全局共用而扩大原许可范围。
- **对白生成**：选已冻结音色、输入中文普通话文本、语速、情绪、停顿（以实际能力为准）、试听、生成音频、记录 ASR/对齐与审核结果。
- **音效与配乐**：首版允许用户上传、分类、试听、标签、分配；没有真实音效/音乐生成引擎时不提供虚假生成按钮。
- **权限**：声音/形象的可重用权必须逐项目重新校核；过期、撤销、非商业许可限制触发阻塞，不得自动借用他人项目的 RightsRecord。
- **音色跨项目**：全局声音母版被授权绑定后，项目生成自己的冻结引用/使用快照；不能直接将其他项目 VoiceProfile.id 作为项目级已冻结凭据。

### 3.7 全局素材库与分类

**一级**：人物、场景、道具、服装、音色、对白、音效/音乐、其他；**二级**用户可设置；支持标签、收藏夹、来源、尺寸、时间、复用次数、项目、状态筛选，以及名称/编号/标签搜索、分页或虚拟列表。

卡片显示图片缩略图或音频播放控制、素材编号（如 R001/S001/D001/V001）、当前选定版本、来源/审核状态和“分配到作品”。素材详情含原始生成参数、参考图清单、缩放预览、版本树、使用项目、授权范围、导出、归档。

“归档”是软删除；已被引用的任何版本不得从磁盘物理清除。禁止对外暴露本机绝对路径。

### 3.8 双向项目联动

A. 中心 → 导演：选择目标项目 + 固定已晋升版本 + 对应人物/场景/道具身份 + 明确确认，创建项目 Asset 引用快照（保留原先项目 Asset/shot/contracts 行为），提示刷新项目素材。再次分配相同版本+相同身份应幂等；不同人物身份必须显式确认。

B. 导演 → 中心：在项目素材页“收藏到素材中心”，先校验项目资产是有效批准、locked、真实文件 hash/适用授权，再人工设定全局名称/分类并创建全局元数据快照。禁止把 offline-only Mock 当成真实素材导出并标为 production-ready。

C. 导演素材需求匹配：`MaterialPreparationPanel` 中增加“从素材中心选择”，用户选中的 LibraryVersion 经过项目内授权与身份确认后，服务端创建本项目 Asset；原有 `reuse_master` 继续选择这个新的项目 Asset.id，而不是绕过 `project_id` 校验。

D. 一次创作的“返回项目”上下文需含 `project_id` + `requirement_id` + `requirement_revision/hash`，返回时重新验证需求仍是当前版本。旧需求/旧项目切换后不得自动回填。

E. 全局版本从 V1 更新到 V2 **绝不自动修改**已绑定的项目 Asset 或冻结导演包；升级只能新建项目内素材修订，执行依赖影响分析，按当前项目门禁人工确认。

## 4. 统一数据设计与迁移规范

### 4.1 设计原则

沿用**全局内容寻址文件**和**项目独立身份/授权**。全局素材元数据不等于某个项目中的主资产；只有显式 assignment 才生成项目可消费 Asset。项目已用素材通过 SHA 复用同一物理字节，但保留单独 Asset.id、role、entity_id、provenance、approved/locked 以及原工作流绑定。

**不得**直接把现有 `assets.project_id` 改为 nullable，或让现有 `candidate_assets.project_id`/`voice_profiles.project_id` 空置而破坏旧 ORM、测试与全链授权。应新增全局素材领域数据，再从共用媒体服务复用校验/晋升逻辑。具体类名可替换，必须在 PR 的 ADR 写出字段映射。

### 4.2 建议新表（名称可经代码调研优化）

| 领域对象 | 主要字段（除 id/created_at/updated_at） | 约束 |
|---|---|---|
| `LibraryAsset` | title、kind、category、tags、collection、cover_version_id、status、created_by | 全局稳定身份；归档而非立即删；无 project FK。 |
| `LibraryVersion` | library_asset_id、ordinal、parent_version_id、content_sha256、storage_path、media_type、size、width/height/duration、source_mode、prompt_hash、workflow_revision_hash、references_manifest_hash、rights_id、provenance、qc、status、approved_by/at | 不可变媒介与来源；一个素材的版本编号唯一，current pointer 以事务更新；候选不能作可分配版本。 |
| `LibrarySourceReference` | version_id、source_version_id 或受控上传 snapshot、order、slot_role、sha256、mask_sha256、consent_snapshot | 1–10 有序；无悬空引用；不把任意客户端路径写入 manifest。 |
| `LibraryRenderTask` | request_hash、idempotency_key、kind、mode、workflow_revision_id/hash、runtime_fingerprint、state_version、status、submit_intent_at、external_prompt_id、external_outcome、active_attempt、lease_owner、timestamps | 单个生成意图最多一个活动任务，UNKNOWN 只核对；执行复用 Worker。 |
| `LibraryTaskAttempt` | task_id、attempt_no、snapshot_hash、submit_stage、response_evidence_hash、candidate_version_id、error_code、reconciliation_state | attempt 不覆盖、失败与重试可追溯。 |
| `LibraryProjectLink` | project_id、library_asset_id、library_version_id、project_asset_id、role、entity_id、rights_snapshot_hash、created_by | UNIQUE 针对明确身份+版本，项目完整性和哈希必须复验。 |
| `LibraryRights`（或通用 Rights 扩展） | scope、license_kind、provenance、recorded_actor、expires_at、revoked_at、project_allowed_scope | 区分用户来源授权、模型许可证、项目使用用途；受控授权不能仅是 boolean。 |

**状态**可参考：`DRAFT → QUEUED → RUNNING → CANDIDATE → QC_PASSED/REVIEW_REQUIRED/QC_FAILED → PROMOTED → ARCHIVED`；提交状态必须额外记录 `SUBMIT_INTENT/EXTERNAL_UNKNOWN/RECONCILE_REQUIRED`，严禁 UNKNOWN 当 FAILED 自动再次提交。可根据现有项目常量调整，但“候选/正式、技术结果/人工结果、已提交/未知结果”不可合并。

重要索引：content_sha256、asset+version、type/category/tags、project+identity、task idempotency key、attempt external_prompt_id、未完成任务的唯一性、外键以及时间逆序检索。SQLite JSON 大字段不能代替需要原子条件更新的状态列。

### 4.3 文件生命周期与去重

- 现有 `store_asset_file` / safe path / media probe 做统一保存；内容哈希相同只存储一份，不因两个项目分配重复复制大文件。
- 如果文件存储实现需要新增 metadata/refcount，按可达性查询或可重建引用登记；跨库、全局、项目和任务引用必须被保护。
- 删除项目**不能清理**全局素材仍引用的物理文件；全局资产归档不能清理项目仍在使用的文件；永久清理只针对无任何引用的字节且需人工确认/备份。
- 原有资产仍保持原绝对字节内容与哈希，不允许“提升质量”直接覆盖或将旧文件搬迁到失效路径。
- PNG alpha 保留、不转成 JPEG；缩略图独立缓存，不能替代正式文件；尺寸与遮罩必须从真正解码数据获得。
- 数据库升级须“空库升级 + 当前旧库**拷贝**升级 + 重跑升级 + 读回旧快照核对”；生产数据库禁止破坏性 downgrade 演练。新功能可通过入口和 capability flags 关闭，历史数据不删除。

### 4.4 权利、冻结与审批

- 全局库 candidate 到正式 LibraryVersion 必须消费与 **candidate ID、内容 SHA、来源/参数、操作人、rights snapshot** 绑定的一次性人工 action/grant；可复用现有 ApprovalGrant 的合同，若底层绑定仅支持 project record，新增等效全局 scope，不把 global 行“冒充项目”。
- 项目 assignment 时再验证源版本 approved、rights 有效、license 能用于目标作品及当前生产用途、sha 校验通过、无 mock/offline_only；必要时向用户展示受限用途并拒绝生产级使用。
- 全局素材 approval 只代表素材中心选择，不自动允许进入任意项目的成片；项目 Asset 的确认/锁定需按既有门禁完成，或通过等价显式确认流程生成并锁定同样完整的合法快照。
- 版权不由模型自动授予。Qwen-Image-2.1 的实际权重使用条款、二次分发/商用范围由运行时明确展示和记录；未知授权默认限制高风险生产用途。
- 音色克隆保留声音持有人同意、可撤销与授权时效；声纹/音频样本不随日志和远端 PR 上传。

## 5. 工作流与模型：复用 ComfyUI，但独立能力合同

### 5.1 统一能力目录

建议逻辑 capability IDs（非强制创建 7 个物理图）：

| capability | 正式能力 | 当前基线 | 合法发布条件 |
|---|---|---|---|
| `QWEN_IMAGE_T2I` | 文生图 | 存在审计 T2I graph | 当前实际图 + live 验证 + 真出图样品。 |
| `QWEN_IMAGE_I2I` | 图生图 | 待新建/核验 edit 适配 | 真实图像输入 slot + 正确变化输出。 |
| `QWEN_IMAGE_INPAINT` | 局部修订 | 待核验 | mask/mark 实际输入与坐标、真编辑对比。 |
| `QWEN_IMAGE_MULTIREF` | 多参考 | 待核验 | 参考图按声明数量/顺序完整到达 graph；正反例。 |
| `QWEN_IMAGE_WARDROBE` | 穿戴合成预设 | 业务层复用 MULTIREF | 人物/服饰/道具槽位真实可用，用户审核一致性。 |
| `QWEN_IMAGE_RGBA` | 透明生成/抠图 | 待核验 | 解码验证真实 alpha；不能将 checkerboard 当结果。 |
| `QWEN_IMAGE_2K` | 2K 图片输出档 | 待核验 | 当前模型/显存和真实输出探测证明。 |

**固定原则**：category 只表达逻辑能力，workflow_revision 记录 API graph、节点版本、模型精确身份/许可证、源模板 commit + hash、输入 slot 映射、output node、可用 reference_count、分辨率界限、mask semantics、memory estimate、local_verified_at、运行时指纹。支持“一个图注册多 capability”，但校验独立，修改图后原真实证据过期。

### 5.2 原生 ComfyUI 验证阶梯

- `UNREGISTERED`：前端只显示“未配置”。
- `REGISTERED`：合法 JSON/映射已登记。
- `STATIC_VALIDATED`：本次本机 live object_info 证明节点和输入类型；仅能显示“图结构已校验”，不可显示“真实可用”。
- `LOCAL_SAMPLE_PASSED`：真实模型实际推理、输出节点/文件 SHA/解码、至少一条该模式的正样例和反例通过。
- `READY`：依赖未漂移、运行时指纹一致、许可证/本机资源满足约束、样品确认。
- `DEGRADED/INVALIDATED`：缺模型/图漂移/OOM/权限撤回/本机验证过期，展示具体阻塞与操作。
- `MOCK_VERIFIED` 是另一个**绝不升级为 READY**的证据轴；Fake 仅用于云端回归。

任何能力在云端未做本机真实推理都只能交付 IMPLEMENTED 或 TESTED_OFFLINE。UI 不能硬编码“全部已就绪”。

### 5.3 图像执行请求标准（建议）

以下是概念合同，由 Codex 落地为 Pydantic + TypeScript 类型，字段名可等价调整：

```json
{
  "mode": "MULTIREF",
  "prompt": "Keep the face of the first person and apply the outfit from the second image.",
  "output": {"width": 1024, "height": 1536, "transparent": false},
  "seed": 12345,
  "workflow_revision_id": "uuid",
  "input_assets": [
    {"reference_id": "uuid-1", "order": 0, "role": "subject", "sha256": "64char"},
    {"reference_id": "uuid-2", "order": 1, "role": "garment", "sha256": "64char"}
  ],
  "mask_asset_id": null,
  "rights_snapshot": "64char",
  "idempotency_key": "user-request-key",
  "execution_mode": "real"
}
```

服务端只能从受控上传/已授权媒体引用取字节；所有客户端上传都校验 MIME、真实解码、大小、路径安全、hash、权限和 slot 数量；工作流映射不得静默丢 input / mask / reference / negative prompt。只有被真实节点映射支持的参数才发送，其他明确报 `WORKFLOW_PARAMETER_UNSUPPORTED`。

真实输出必须从声明的 output_node_id 精确领取；图 history 的其它预览节点、历史生成结果和不同 prompt_id 的文件不可混入本次任务。

### 5.4 GPU/调度合同

- 复用 `ResourceLease/ResourceScheduler`，所有图片和声音任务均通过现有受控 Worker claim 与资源仲裁；不新建第二套后台守护进程或另起未受管 ComfyUI。
- H3 production series 跨镜头独占 GPU lease；素材任务**排队**而非抢占。只有真实安全边界才允许 renderer handoff/卸载。
- 16GB 情况下小批量排队、可选低显存模式由已验证参数决定；用户取消只置受控 `cancel_requested`，不强杀正在交付 H3 的进程。
- 图片生成适配器不得持有 SQLite 写锁跨越 `/prompt`、上传或 history 轮询；必须先持久化 submit intent、随后外呼、再绑定外部 prompt_id，UNKNOWN 专门对账。
- 资源租约不因超时自动认定 GPU 已空闲；release 必须遵守当前 PID/birth/ownership 证明，不可改写 `renderer_release` 既有语义。
- 多图/连续修订不得绕开入队与幂等；排队提示“等待视频生产释放显卡”优于虚构进度百分比。

## 6. API 设计（建议路径，实施前产出 OpenAPI）

所有路径前缀 `/api/v1`，不会侵入现有 ``/projects/{id}/material-preparation`、`/candidate-assets`、`/voice-profiles` 路由。建议独立 `backend/shine/api_asset_center.py`，通用底层 service 单独模块，不直接复用项目绑定 API 假装无项目。

| HTTP | 路径 | 语义 |
|---|---|---|
| GET | `/asset-center/capabilities` | 真实能力状态、可用操作、声明的图槽位上限与阻塞原因。 |
| GET/POST | `/asset-center/assets` | 分页/筛选/创建全局素材容器或从受控上传创建候选。 |
| GET/PATCH | `/asset-center/assets/{id}` | 详情、标签、收藏夹、封面等可变元数据；expected_version 防丢更新。 |
| GET | `/asset-center/assets/{id}/versions` | 版本谱系与使用情况。 |
| POST | `/asset-center/uploads` | 授权图片/音频/遮罩原始字节导入，返回受控媒体 ID。 |
| POST | `/asset-center/image-jobs` | 一个结构化入口，mode=T2I/I2I/INPAINT/MULTIREF/WARDROBE/RGBA；严格能力路由。 |
| GET | `/asset-center/image-jobs/{id}` | 持久 job/attempt/status/output/candidate/progress/action 列表。 |
| POST | `/asset-center/image-jobs/{id}/cancel` | 安全取消。 |
| POST | `/asset-center/image-jobs/{id}/reconcile` | 查询已有外部任务，不重新提交。 |
| POST | `/asset-center/image-jobs/{id}/new-attempt` | 仅明确合法的新物理执行意图，需人工确认+新 identity。 |
| POST | `/asset-center/versions/{id}/qc` | 媒体验证、可选 VLM，保留证据及能力状态。 |
| POST | `/asset-center/versions/{id}/promote` | 原子消费一次性确认快照，候选→可分配正式版本。 |
| POST | `/asset-center/assets/{id}/select-version` | 选择默认已批准版本，历史项目不受影响。 |
| POST | `/asset-center/assignments` | 全局版本 → 项目身份映射/项目资产快照（幂等、授权、强校验）。 |
| GET | `/asset-center/assignments` | 按素材/项目/版本查询使用记录。 |
| POST | `/projects/{pid}/assets/{asset_id}/save-to-library` | 项目锁定素材 → 全局库；人审+版权+sha。 |
| GET | `/projects/{pid}/asset-center/search` | 导演按角色/场景/道具查全局已批准版本。 |
| POST | `/asset-center/voice-jobs` | 设计/克隆/TTS 统一调用现有声音服务的独立业务入口。 |
| GET | `/asset-center/voice-jobs/{id}` | 持久结果/试听/质量/人工动作。 |
| POST | `/asset-center/voices/{id}/freeze` | 人工试听+授权冻结版本。 |

返回的错误必须有稳定 `code`、中文可读说明、`next_action`，并区分能力缺失、权限不足、资源等待、输出未定、任务未知、版本过期。GET 与 preview 不会产生 GPU/外部付费调用；所有实质生成一律显式 POST + idempotency_key；刷新页面只 GET，不自动二次提交。

### 6.1 API 的原子一致性

- `POST /assignments`：校验、project Asset 建立、LibraryProjectLink 建立、审计写入同事务；成功后返已绑定 project_asset_id。失败全部回滚，不能孤儿引用。
- `POST /promote`：version status + 权限 grant 消费 + hash/rights 验证 + 审计同事务；并发双击唯一一个胜出。
- 每个 read/modify/write 用 `state_version` / expected hash 或条件更新；对不同请求身份不可复用相同 idempotency_key。
- 任务本地结果确认后不可覆盖；同哈希去重允许多个元数据版本引用同字节，但必须保留各自来源/rights。
- 跨项目查询必须限定当前可见项目及适用授权；不得凭猜中的 UUID 读取别的项目敏感素材。

## 7. 实现目录建议与边界

建议**新增**（以实际结构为准）：

- `frontend/src/asset-center/`：独立 Layout、Routes、Home、ImageWorkshop、ImageCanvas、ReferenceSlots、VoiceWorkshop、LibraryBrowser、AssetDetails、Assignments、GenerationJobs、API hooks。
- `backend/shine/asset_center/`：models、contracts、service、generation、workflow_adapters、media_validation、assignments、rights、voice_bridge、audit 等。
- `backend/shine/api_asset_center.py`：独立公共 API。
- `backend/alembic/versions/<next>_asset_center.py`：增量迁移（不能改旧迁移历史）。
- `backend/tests/test_asset_center_*.py`、`frontend/src/test/assetCenter*.test.tsx`：单元、API、E2E/UI 回归。
- `docs/adr/0008-asset-center-shared-kernel.md`：在代码落地时固定最终 schema、组件依赖图和兼容理由。
- `docs/ASSET_CENTER_IMPLEMENTATION_REPORT_20261108.md`：云端实施结果、能力矩阵和验收证据，日期可随实际提交更新。

受控修改既有入口：`frontend/src/App.tsx`、`frontend/src/api.ts`、`backend/shine/main.py`、`backend/shine/worker.py`、`backend/shine/comfyops/{assets,registry,scheduler}.py` 等，仅在最小必要面修改；先提取可复用服务后再接入，不复制已有核心执行代码。已有六阶段/四阶段流水线不得被大面积重排。

## 8. 实施步骤（单一 feature PR、按可验收提交）

### Phase 0：代码勘察与 ADR（先于功能代码）

- 从最新 origin/main 独立创建 `feature/shine-assets-center`（若同名存在则选唯一后缀），禁止在 main 工作。
- 锁定 `git rev-parse HEAD`、`git status --short`、当前数据库迁移 head、所有在跑实例安全边界。
- 画出数据血缘和资源时序，逐个列出复用对象、需要新增表和严禁触碰的模块；提交 ADR。
- 调查 Qwen image-edit 原生 ComfyUI 最新官方示例；记录精确 commit、workflow hash、模型 hash/许可证、Node 输入映射，不声称云端装了 2.1 权重。

### Phase 1：素材库底座和工作空间

- 建立全局库模型与非破坏性迁移、统一文件引用、资产列表/详情/收藏/版本。
- 增加并列工作空间、独立导航、四页主要 UI 的真实数据联动与空态，不要求先创建项目。
- 打通项目↔全局库双向分配及明确授权；冻结旧项目无副作用。
- 持久 API 类型/断言，独立项目切换后清除旧请求结果。

### Phase 2：Qwen 图片完整生成

- 提取现有 T2I 执行到可复用 service，维护旧 project material-preparation 兼容性。
- 添加 I2I、局部编辑、多图合成、穿戴预设、RGBA/2K 的能力 adapter/slot contract 与真实验证门禁。
- 实现独立画布、引用图管理、遮罩导出、候选列表与版本树。
- Worker 新队列项复用现有 submit-intent / external-UNKNOWN / GPU lease，状态持久、刷新后恢复。
- 将模型支持却未通过本机推理的能力标记 `PENDING_LOCAL_VALIDATION`，界面不可误报 READY。

### Phase 3：声音工坊

- 复用 Voice Provider 的设计、克隆、TTS、ASR、对齐、授权；全局声音资产与项目 VoiceProfile 的绑定桥接。
- 加入试听/版本/删除与多项目复用，声音克隆授权保持收敛；音效/音乐限导入。
- GPU 和模型调用不得影响 H3 series。

### Phase 4：对抗测试、UI 真人路径、PR 收口

- 执行第 9 节全部场景，修复根因、重跑回归，提交结果证据与截图/文字描述。
- 前后端 API 实际联通，不能只提交 mock demo 页面。
- 更新 README 的双工作台入口简述，但不改旧产品事实；不要给主线新增未验证“已完成”标签。
- Draft PR 对准 main，列出云端可验证/本机未验证/许可证待确认的边界。禁止 merge PR，交由用户审阅。

## 9. 验收矩阵（硬性 PASS / BLOCKED / NOT_RUN）

### 9.1 P0 用户场景（必须由代码与 Fake/小文件测试证明）

| ID | 操作 | 预期 |
|---|---|---|
| AC-01 | 新机尚无任何作品时进入 `#a/home` → 导入图片 → 建立素材 | 无 project 也能成功入库，刷新后仍存在。 |
| AC-02 | 从导演任意页面进入 Assets 再返回原项目 | 导演页/路由、六阶段状态与作品不丢。 |
| AC-03 | T2I 创建候选并执行审核、人工晋升 | 未批准不可项目使用；已批准 version 不可篡改。 |
| AC-04 | I2I 输入实图生成新版本 | 真实输入字节在请求图中，原图仍在，V2 lineage 完整。 |
| AC-05 | Inpaint 画布编辑 → 导出遮罩 → 重启/刷新恢复 | mask 像素坐标/尺寸/语义正确；空遮罩拒绝。 |
| AC-06 | MultiRef 2–10 图按角色重新排序 | 图执行 manifest 每图顺序正确；输入 slot 不够必须拒绝而非静默少传。 |
| AC-07 | 人物+服装+鞋帽合成，之后局部修订 | Wardrobe 使用 MultiRef 真实合成 adapter，生成两个不可变候选版本。 |
| AC-08 | RGBA 结果透明度检查 | 非零透明像素正确写入 PNG；没有 alpha 明确失败。 |
| AC-09 | 声音设计 → 试听 → 冻结 → 配音导入项目 | 声音/项目授权快照与 hash 均正确，不能跨项目借用原 voice ID。 |
| AC-10 | 全局版 V1 分配项目 A 和 B，两项目赋不同实体名称 | 同物理 SHA、不同 project Asset.id/身份；不同用途互不串扰。 |
| AC-11 | 项目 A 使用 V1 后全局选 V2，删除项目 B | A 的冻结绑定仍为 V1；全局版和真实文件未丢失。 |
| AC-12 | 导演“从素材中心选择”进入当前素材需求 | 生成项目 Asset 后原 `reuse_master` 合法；过期 requirement 拒绝回填。 |
| AC-13 | 同时点击生成/晋升/分配，或刷新与网络断开 | 幂等、事务一致，不重复 GPU 执行、不产生双重主素材。 |
| AC-14 | Comfy 提交后超时且 outcome UNKNOWN | 不能自动重发；只显示核对/经授权的新尝试，保留旧记录。 |
| AC-15 | H3 正在连续生成时提交图片与声音任务 | 被正确排队，不能抢租约或中断 H3 renderer。 |
| AC-16 | 模型/节点/工作流图改变 | `READY` 自动失效，报告缺失依赖；不悄悄回退到 Fake。 |
| AC-17 | 项目已有旧素材/旧冻结 style/新四阶段长片包 | 新迁移前后旧合同数据不变，所有导演回归通过。 |
| AC-18 | 恶意 MIME、超大文件、非法路径、错 SHA、错项目 ID | 4xx 稳定错误，无路径穿越、跨项目泄漏、部分提交。 |
| AC-19 | 冻结声音使用许可撤销/过期 | 新生成与新项目分配被阻止，历史审计保留。 |
| AC-20 | 普通用户不打开专业设置完成图片工坊典型任务 | 界面少工程语言，能看到中文当前进度/下一步/错误行动。 |

### 9.2 必须单独记录的本机真实验证（云端不可假装完成）

真实 Windows 机器上的每一种图片模式分别执行：至少一次实际模型推理；逐输入文件验证 sha/有效加载；读取当前 live `object_info` 与 graph hash；记录 prompt_id、declared output_node_id、实际输出文件 SHA、图像解码、PNG alpha/遮罩坐标等；对人物面容、衣着保留、多图合成与局部编辑给人工可复核结果。验证 1024 及条件允许下的 2K，并记录 RTX 5080 16GB 的实际峰值显存、OOM 与释放结果。

真实 H3 多镜头生产与素材任务互斥检查必须在**不会伤害用户正在执行的作品**的测试目录/隔离实例中进行。未做的写 `NOT_RUN`；失败写 `BLOCKED + code + next_action`。本次云端 PR 的目标是“完整可执行的软件实现 + 诚实的本地验收门禁”，绝不可将未运行的硬件测试写成 PASS。

### 9.3 测试指令（以当前 pyproject/package scripts 为准）

```bash
uv run --project backend --locked pytest -c backend/pyproject.toml backend/tests
uv run --project backend --locked ruff check backend/shine backend/alembic backend/tests
uv run --project backend alembic -c backend/alembic.ini heads
npm --prefix frontend test -- --run
npm --prefix frontend exec tsc -- --noEmit
npm --prefix frontend run build
```

结合仓库测试环境实际参数调整；记录命令、退出码、通过/跳过/失败数。若云端安装缺依赖、网络、GPU、ComfyUI 或 Windows PowerShell，单项写未运行及原因，不得从旧测试报告借用 PASS。尤其保留：`test_offline_e2e.py`、`test_v13_e2e.py`、`test_v13_migrations.py`、`test_film_crew.py`、`test_pipeline.py`、`test_four_stage_closeout.py`、导演运行/素材准备/工作流注册/frontend 路由测试。

迁移必须具备 fresh + copied-old-db 两种测试；没有旧库文件可用时用真实旧迁移生成历史 schema 的 fixture，不允许对正式库试错。

## 10. 安全、合规与错误处理

1. **来源安全**：只接收授权本地上传或已授权的库内 version，不能接受任意服务器 file://、磁盘绝对路径、任意 HTTP 地址作为执行端输入；恶意文件限制尺寸、格式、真实内容、解码、metadata。
2. **跨项目权限**：当前权限边界是本机单人应用 + loopback/CSRF，并非面向互联网的多用户身份服务；“跨项目引用”仍需检查具体项目和合法用途。
3. **模型许可证**：Qwen-Image-2.1 实际采用版本条款必须核验并保留项目使用范围；不得把 Hugging Face“可下载”视为允许商用/转卖/二次分发。未知一律给出清楚提示并保护制作出口。
4. **生成安全**：侵权来源、未授权真实人物声音克隆不得通过自动复用绕过确认；有争议时人工审核。
5. **日志限制**：图片/声音字节、全文提示词中私人信息、API key/令牌及本地绝对路径不写远端仓、CI artifact、云端日志；测试只用可公开的合成或授权样本。
6. **版本保护**：hash/rights/status stale 以 409 返回，禁止在客户端“继续生成”时放宽服务端条件。
7. **可恢复**：任务取消、失败、未知外部结果、真实释放状态均需要明确的用户下一步。
8. **假与真**：Mock/Fake 结果不能晋升为真实生产用资产；不得为了通过验收直接设置 `verified=true` / `READY`。
9. **无隐式下载**：新增模型/节点只登记版本锁定计划，下载或安装必须经用户显式确认；不改动现有隔离 ComfyUI 安装，不影响主线装机成功路径。
10. **可回滚**：关闭素材中心入口后旧导演模式可继续运行；旧数据和文件不因迁移回滚被删。

## 11. 云端 Codex PR 交付物清单

PR 必须提供：

- [ ] 完整实际代码：不是只有 PRD、假接口、纯静态界面或只在浏览器状态中保存数据。
- [ ] 两个并列独立工作空间，旧深链仍有效，不增导演第七阶段。
- [ ] 全局 LibraryAsset/Version/ProjectLink 和必要事务式任务表，增量迁移与索引。
- [ ] 图像 T2I、I2I、局部 mask 编辑、MultiRef、Wardrobe、RGBA 及支持时 2K 的能力适配、前端页面和 fail-closed 验证。
- [ ] 每项图片能力的真实工作流注册/映射证据合同及本机验收脚本；不以 cloud mock 认定 ready。
- [ ] 音色设计/克隆/TTS 工坊，声音授权、人工冻结及跨项目映射；音效/音乐导入。
- [ ] 素材库分类、标签、版本历史、收藏、检索、项目分配、导演反向入库。
- [ ] 复用现有 Worker、Lease、ComfyUI、内容寻址与候选 QC，不引入第二调度器。
- [ ] 旧导演素材准备、四阶段创作与 H3 生产回归全部通过或有明确 blocker。
- [ ] 安全与对抗测试：权限、UNKNOWN、幂等、并发、hash、mask、引用数量、GPU、旧库兼容。
- [ ] 形成 ADR、OpenAPI/API 类型、用户使用说明、可复制本机验收步骤与实施证据报告。
- [ ] Draft PR 说明包括：base SHA、变更文件、阶段提交、测试实际执行/未执行、真实 GPU 验证清单、授权/许可证风险、已知缺口与操作建议。

## 12. 严禁误判的 STOP 条件

发生以下任何情况，应停止相应能力/保持未就绪，而不是造假放行：

- 无本地真实图像编辑节点/参数/完整 reference input 映射；模型或 Node 不匹配。
- 只看到模型名称或只运行 mock，没有真实 `history`/output_node 文件证明。
- 多图参考实际少传、遮罩未进入 graph、PNG alpha 不存在、生成任务不持久或换项目串数据。
- 尝试绕过现有 `project_id` / `RightsRecord` / `ApprovalGrant` / `preproduction.enforce` 保护。
- 外部执行状态 UNKNOWN 或 renderer ownership/lease 不明确却要自动 retry/kill。
- 当前 main 在执行中发生竞争改动且涉及相同模块，需要 rebase 复核而非覆盖他人提交。
- 许可范围不能确认却拟默认开放正式商用出品。

**最终验收定义**：云端交付为“软件实现完成、离线/伪服务通过、真实能力门禁与本机验证路径齐备”，本机每种 Qwen 能力未经真实验证都必须继续显示未验证。目标是最终完成全能力，但不牺牲真实证据、用户素材与 H3 主线稳定性。

## 13. PR 操作规范

- 基于最新 `origin/main` 建唯一分支 `feature/shine-assets-center-20261108`（如冲突加短后缀）。
- 保证是**一个独立业务功能 PR**；不要让本文件的文档 PR 与 feature PR 的 commit 交叉污染。
- 若本文件仍在独立 docs PR 分支、尚未合入 main：先查看该 docs PR 链接，从 docs 分支读取规范但实现分支仍从最新 main 切出；如果需要将规范纳入实现分支，请以 cherry-pick 单文档提交方式操作，并在 PR 中说明待去重方案。推荐先由用户合并 docs PR，feature 分支再 rebase main。
- 实现分为至少四次可读提交：schema/ADR → 资产库与双向桥接 → 图片/声音工坊与运行时 → 对抗测试/文档。
- Draft PR 先开放审查，**不可自动合并、不可强推 main、不可跑用户真实 H3 项目**。
- 允许在云端自主执行可回滚的代码修改、依赖安装、测试和 PR 更新；不允许进行涉及用户个人文件、正式数据库、API 密钥、生产 GPU 任务的破坏性或未授权操作。
- Cloud 任务不应引用 Windows F: 路径作为云端存在的磁盘；仅把它用于用户日后本机验收说明。

---

**完成判定**：两套平级、独立且互通的工作空间；一套共享 AI/数据底座；文生图+图生图+局部修订+最多 10 图合成+人物穿戴+透明图+声音工具均有真实软件实现与严格能力门禁；项目引用既可复用又不串扰；所有旧导演生产与四阶段流水线保持兼容；Cloud/Local 证据分开陈述。
