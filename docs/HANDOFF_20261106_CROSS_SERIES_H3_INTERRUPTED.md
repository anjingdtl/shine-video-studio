# 交接：跨 series 续接与真实 H3 全旅程（2026-11-06 中止）

## 结论和原始目标

用户要求自主完成：①真实 VLM 正负校准、发布新 revision、弃用旧 revision；②跨 series 任意媒体续接；③以导演模式 Web 用户实操完成从剧本生成到真实多集无人值守 H3 交付，至少一部成片拼接 20 段视频。用户随后要求中止任务、编写进度文档并交给另一个 agent。**三项目标均未完成；本轮没有真实 H3 新出片，不得宣布验收通过。**

## Git 与未完成代码

- 工作目录：`F:\ClaudeWorkSpace\projects\shine-video-studio`；分支 `codex/cross-series-continuity-20261106`，HEAD `32cf192`。本轮改动未提交；接手前先执行 `git status --short --branch`、审阅 `git diff` 和未跟踪文件，保留现有工作，不要 reset/clean。
- `backend/shine/media/continuity.py`：新增校验源 job/shot/attempt/artifact 身份、源 SHA-256，从源视频提取末帧、视频或音频窗口并保存带 lineage 的 `MediaArtifact`。提取在数据库写事务之外，提交前重查来源。**尚未端到端接入**，需独立复核并发、来源有效性、媒体时间窗、ffmpeg 行为和文件完整性。
- `backend/shine/comfyops/production_series.py`：增加来源查询、可推迟首镜、在创建依赖 job 前准备派生媒体的部分实现。`_run_source_job` 只从本 Run 已通过的 series item 找源，并拒绝其他生产的较新 job；跨 episode、重复 shot、非相邻 source、重启与并发 fencing 等仍需补齐并实测。`first_source_job_id` / `defer_first` 与调用方的完整集成尚未结束。
- `backend/shine/production/runner.py`：`_batch_plan(..., split_continuity=True)` 支持按限制切分连续链，但**生产调用仍使用默认旧策略**；新旧 Run 的计划版本固定、跨 batch 调度与交付一致性尚未接通。不得声称跨 series 已实现。
- `backend/shine/services.py`、`backend/shine/h3/compiler.py`、`backend/shine/comfy/provider.py`：开始将派生引用加入 manifest、在 prompt 中指明标签、在上传前核验派生物与源文件。真实 ComfyUI 的映射、视频/音频 adapter、所有引用数量和真实能力验证尚未完成。对于正式非 CUT 请求，现在没有派生物会拒绝，避免静默声称媒体续接；这可能改变既有路径，必须完成集成后回归。
- **关键缺口**：`worker.py` 的 `_load_comfy_context` 仍只加载 `Asset`，不能加载新 `ContinuityReference`；`comfyops/preproduction.py` 仍要求 manifest 与人工绑定完全一致，尚不识别经过来源验证的技术派生物。不能绕过这些门禁，也不能把派生物自动晋升为已批准 master。补齐后要验证 job 依赖失效、源 revision/文件变动、重启、不同项目、资源与 workflow slot 映射。视频、音频和硬帧模式的真实 workflow 能力不得伪造。
- `backend/shine/comfyops/review_runtime.py`、`backend/shine/api_setup.py`：正在修复可选审片模型已安装但 wheel 没被部署到运行 overlay 的问题，新增从已固定并核验的 wheel 解包到空目录的实现；已有非空目录不覆盖。需进一步审查并发、安全、实际安装器和旧目录兼容性，测试真实 wheel、配置生命周期和 Windows CI。`backend/shine/comfyops/service_validation.py` 对确定性 import 失败新增快速结束的部分处理，避免审片探测反复等满超时；服务验证仍须实测。
- `backend/tests/test_continuity_media.py`、`test_cross_series_source.py`、`test_review_runtime_preparation.py` 是新增测试；`test_production_series.py` 一处测试明确以 `offline=True` 表示其虚构 job/artifact。请检查该调整是否恰当并补充真实边界的反例和端到端测试，不要用虚构 artifact 证明真实媒体传递。

## 本轮校准与本机状态

- 当前 Web 数据目录是 `.local-acceptance/live-browser`，API `http://127.0.0.1:8000`、Vite `http://127.0.0.1:5173`；后端 schema `f180b0010001`。本轮为验证服务受控停止了原启动器记录的空闲栈，并用 `scripts/start_shine.ps1 ... -NoWorker` 重启：**当前 Worker 离线**。恢复前核对运行中动作、进程身份、队列、GPU 租约和运行配置；不要把这个状态误当成生产可用。
- 中止时 API 和 Web 在线；查询到无非 `RELEASED` 的 resource lease、无运行/排队 job。服务验证的短时 VLM 进程 `17916` 随后自行退出，API 记录请求 `200 OK`，`8116`、`8117`、`8188` 未见监听。请重新核对，勿依赖此快照；**不要盲目终止进程、重投验证或释放不明租约**。
- 当前库起初没有 `vlm_reviewer` 路由，也没有 `workflow_revisions`；最终服务验证已报告 VLM READY 并登记本地路由，尚无本轮正负校准或新 revision。另一套历史正式库 `.local-acceptance/real-user-journey-final/data/shine.db` 存在旧 VLM revision `03f32432-4c94-4476-8580-e1a14e9fc37f` 和不同的审片路由；两个目录是不同的 SQLite 资源台账。不能在两个独立台账并行使用同一 GPU，也不能拿历史证据冒充当前库的校准。先弄清目标库及旧 revision 的处置，再按真实输入和实际执行器完成本轮校准。
- 本轮首次补装审片模型的计划 `2d9bae23-4c7d-466f-8068-b1e9b34a2afc` 因 `DOWNLOAD_STREAM_FAILED:ReadTimeout` 失败，尝试 `480f7a2b-5a81-4cef-9c74-1abe801de518` 已通过现有授权 API 对账为 `RECONCILED_NO_RETRY`；没有盲目重投。
- 从本机历史目录将 catalog 所钉住的 23 个 VLM/ASR 模型文件和 122 个 wheel 缓存文件逐文件核验 SHA-256 后复用，不改源文件。新计划 `8a1ae9c2-e5b0-412a-8946-cbb54b5eda26`、尝试 `4fc10727-5d80-4393-827c-68012fbd975d` 已达到 `FILES_VERIFIED` / `FILES_VERIFIED_PENDING_RUNTIME`，并非推理验证。
- 导演模式先前补齐了当前项目 `1b0dcb51-62b3-472d-b804-a4ade1b71092` 的四个标准 workflow profile；服务验证依赖这些 profile。VLM/ASR 的小模型文件虽齐，设置流程原本没有解压 runtime overlay。本轮以本地已校验 wheel 临时构建 overlay，恢复 wheel 内原始 decoder `RECORD` 后，`decoder_runtime_identity()` 核验通过。相关临时辅助脚本位于 `.local-acceptance/_reuse_review_cache_20261106.py`、`_reuse_review_wheels_after_reconcile_20261106.py`、`_provision_review_runtime_20261106.py`；它们不是正式安装入口。
- 真实导入检查显示本机 ComfyUI 嵌入 Python 是 **3.13.11**，已安装 `nagisa` wheel 是 **cp312**，`qwen_asr` 导入失败 `ModuleNotFoundError: No module named 'nagisa_utils'`。ASR 是 advisory，不能因此阻止正式 H3；不得伪报 READY。中止后确认最后一次服务验证于 16:32:59 结束：VLM `READY`、型号 `qwen3-vl-8b-fp8`，ASR `UNAVAILABLE / SERVICE_VALIDATION_HTTP_503`。这是文件、服务及导入探测，不是正负校准或实际审片通过。
- 旧校准脚本 `.local-acceptance/_recalibrate_vlm_20261103.py` 绑定历史项目、job 和样本；其副本 `_recalibrate_vlm_20261106.py` 在当前库因 `VLM_ROUTE_MISSING` 于调用模型前退出，**未产生新校准记录**。旧出版脚本硬编码另一 API/项目/旧 revision，不能直接运行。
- MiniMax 文本服务的导演模式连接检查通过，但仅证明连接/模型列表，**不是**真实剧本生成验收。当前已有项目和素材属于用户数据，勿删除或覆盖；未知远程 LLM 请求不得盲重发。

## 测试证据与未完成验证

- 执行过 `backend\.venv\Scripts\python.exe -m pytest tests/test_continuity_media.py tests/test_cross_series_source.py tests/test_production_series.py -q`，当时 37 项通过；随后又新增了部分测试和修改，需重新运行。`test_review_runtime_preparation.py` 与 `test_service_validation.py` 合计 11 项通过，随后亦有后续工作，最终完整回归未做。
- 对相关修改文件执行过 `ruff check` 通过；`ruff format --check` 指出原有 `production_series.py` 存在大量既有格式差异，不要误以为格式检查全绿。曾误调用不存在的 `tests/test_setup_service_validation.py`，随后以实际 `tests/test_service_validation.py` 执行成功。
- 尚未执行完整 backend suite、迁移验证、frontend build/test、真实正负 VLM 校准、revision 发布/弃用、重新分析和冻结计划、最终真实 H3 journey、20 段及多集无人值守交付、人工艺术验收。所有模拟媒体仅为测试图样。
- 后续若修改 `worker.py`、`preproduction.py` 或其他 `client_review_identity.py` 钉住的文件，必须在最终源码下**再**做真实正负校准、发布新 revision、弃用对应旧版、重分析并重新冻结正式计划；校准中不要修改钉住的文件。Worker/quality 变更后按 `AGENTS.md` 执行指定 E2E、series、handoff、V1.3、H3 compiler 与全量回归。

## 推荐接手顺序

1. 核对上述服务验证是否已结束，保留所有未提交变更和用户数据；检查本机资源与数据库，恢复或修复审片能力，区分历史正式库与当前工作台库。完成真实正负校准及真实 revision 生命周期，不能以文件就绪或 mock 代替。
2. 审阅并完成当前未闭环的续接实现：生产策略版本固定、跨 series/episode 的来源与顺序、媒体派生和 lineage、`preproduction`/Worker/ComfyUI 引用传递、输入数量与实际 profile 能力、失败/取消/恢复/失效。加真实媒体边界反例及从 Worker 调度入口驱动的回归，再按最终源码重校准。
3. 确保安装器自动构建安全且 hash 固定的审片 overlay；单独记录 ASR 的 Python ABI 问题，不能弱化质量门禁或把 optional 模型失败变成 H3 阻断。
4. 通过内置浏览器以**导演模式**进行新的用户旅程，生成合适的虚构剧本，显式处理项目文字的远程服务同意与素材/风格确认；持久化所有身份、证据和失败原因。按真实作品完整流程运行多集与至少 20 段真实 H3 片段的成片，核验每段来源、产物 SHA、ffprobe/解码、EDL 顺序和最终交付；区分自动技术状态与用户艺术验收。
