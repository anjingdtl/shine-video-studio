# H3 Ref2VA V1.2.1 收尾报告

日期：2026-10-21（Asia/Shanghai）。本轮不新增 GPU 生成，不下载或切换模型。

## 1. 基线与交付

- 仓库：`anjingdtl/shine-video-studio`，private，默认分支 main。
- 起始提交/已刷新远端 main：`32ebffb12006fdeefda4b0a2e15e4736e2f6564b`。
- 开发分支：`fix/v1.2.1-ref2va-final-hardening`，开始时工作区干净。
- main 不改写、不合并、不强推；实现提交 `ffc36e74d5a12e45ed5004e8229c448c05c644f2`；交付见下方 PR 与 CI 记录。
- **整体结论：BLOCKED（正式画质/严格接续与生产审批尚未闭合），不能宣称正式短剧质量验收完成。**

## 2. CI 与工程修复

起始基线：205 passed；Ruff 两处 E501；TypeScript/Vite 构建成功。
编译器字符串只拆成相邻 f-string，字节内容、标签次序、工作流语义不变；测试保留原断言。
本机 uv 默认镜像与锁文件 PyPI 来源冲突；设置当前进程
`UV_DEFAULT_INDEX=https://pypi.org/simple` 后 `--locked` 正常，无需改锁文件或 Python 依赖。

全量测试还暴露 Windows 时钟分辨率下影视 Agent 记录同时间戳排序不稳定。
SQLite 查询增加 rowid 次序，质量门也按相同原则稳定排序；不修改历史记录。
前端只显示当前最高 attempt_no + 当前 Artifact 的门禁，不从旧 Attempt 借用通过状态。

`npm ci` 的审计发现 Vite 6.3.5 存在高危本地文件读取等漏洞；补丁升级至 6.4.5，
`npm audit` 归零。首次 npm 源连接 ECONNRESET，重试镜像安装成功；未更换模型组件。

## 3. 人工审片安全（IMPLEMENTED / TESTED）

新增 `operator_review.py` 业务服务、`api_operator.py`、Web 分项表单。
服务端配置命名操作员和高熵令牌 SHA-256，常量时间比较；只接受回环客户端、同源浏览器请求。
令牌不进入数据库、Git、日志、URL 或浏览器长期存储。未配置默认拒绝。
详细配置见 [operator-review.md](operator-review.md)。

最高 attempt_no 为当前 Attempt；拒绝旧媒体串轮次，校验原请求指纹、当前修订、风格、
规则包及锁、媒体归属/重新计算的 SHA-256、Prompt ID、完成 History 和提交图 hash。
技术 QC 必须已有真实通过记录且提交时重新通过。文件缺失、替换或损坏不能放行。

快照覆盖任务版本、Attempt 全部字段、策略及质量记录。外部只读检查之后取得 SQLite
写锁，重新读取全部对象，再条件更新 state_version。重复 snapshot 和 request_id、
并发审批、期间 Worker/策略/资产记录变化均不能产生第二次有效迁移。
人工结论追加独立 QCReport/门禁/审计，原自动证据不覆盖。

身份、服装、场景道具、动作运镜、风格、对白、音色、口型、连续性、其他异常逐项记录。
有对白时，明确人工观看+试听及逐项证据可补齐 audio；音轨存在、能量或泛泛备注不能补齐。
任一分项 FAIL 均保持 NEEDS_REVIEW（包括无剧本对白镜头中的音频项失败）；必需未审项也保持 NEEDS_REVIEW。维护脚本复用 API，已移除数据库直写和 attempts[0]。

## 4. 连续性边界（IMPLEMENTED / TESTED）

| 契约 | 旧字段兼容 | 当前两个 Profile |
| --- | --- | --- |
| SOFT_REFERENCE_CONTINUITY | last_frame | 图片槽位普通参考；不保证首帧相等 |
| HARD_FRAME_ANCHOR_CONTINUITY | hard_frame_anchor | PENDING_HARD_CONTINUITY_TEST，拒绝生成 |
| VIDEO_REFERENCE_CONTINUITY | video_reference | SUPPORTED_NODE_UNVERIFIED_TRANSFER，拒绝自动接续 |

API 接受长名称并规范化为兼容字段；已有请求和历史证据不重写。
排产与提交前双重检查能力，不能通过绕过 UI 或重启 Worker 绕过。
已有依赖链对 passed/current 上游的要求、下游失效及独立镜头隔离仍受原回归覆盖。

A 最后帧与 C 第一帧已从原视频重新提取并查看：A 为城市道路人物中近景，C 为商铺街道
人物远景，构图明显不同。原图保存在本地 `.local-data-v121-audit/A-last.png` 和
`C-first.png`，未上传 GitHub。历史 C 的“continues exactly”只是提示词意图，不是验收证据。

只读分析本机 ComfyUI `b0b743566f65daafc423b4fea8a2fbda94b3384a` 的
`comfy_extras/nodes_minimax_h3.py`：`MiniMaxH3AddGuide` 有 image/vae/latent/positive/frame_idx，
未来可在独立副本中把已通过上游末帧接入 image、frame_idx=0、正确 VAE/AV latent，
正向 conditioning 再到 guider。还须验证实际 Ref2VA/INT8 支持和预先约定的解码帧容差；
节点文案不证明硬接续。此次未改该文件、未复制上游实现、未运行新采样。

## 5. Workflow 模型身份（IMPLEMENTED / TESTED）

模型家族 MiniMax-H3；模式 Ref2VA；变体 Ref2VA INT8 ConvRot；实际 UNETLoader 文件：
`Minimax-H3\minimax_h3_ref2va_pruned_int8_convrot.safetensors`。
家族、模式、变体、精度、权重文件、图版本/hash 分开显示并保存。
新注册的真实图自动推导身份，冲突 checkpoint 元数据拒绝；旧通用标识兼容迁移。
旧 RenderRequest 的模型标识保留，未来请求使用实际 checkpoint 身份，无任何下载路径。

完整节点图 hash 不变：

| Profile | SHA-256 |
| --- | --- |
| local-h3-ref2va-2img | 6f7a5fb19c548ca945f8a2169e05c836d7c06f03466cf2b893b3be599505d112 |
| local-h3-ref2va-2img-1aud | 7d86f7b6d994b0b38cc379a4fe0268f68776c4a43e80fb0f7651a3005f57dcd6 |

两张图片容量保持不变，含音频 Profile 额外一音频；不丢弃第三张图。
静态 autogrow 路径检查要求节点真正声明 COMFY_AUTOGROW_V3，并核验本机 Ref2VA
ref_image_0..8、video/audio_0..2 的前缀和界限。动态扩图始终关闭。
视频节点源码存在与真实媒体传输验收严格分开。

## 6. Web 与 API（IMPLEMENTED / TESTED）

Web：实时 API/Worker/ComfyUI/provider/队列/错误；Profile 的真实身份、参考槽位、映射、
验证时间和来源；软/硬/视频连续性显示；当前 Attempt 哈希、视频预览、技术 QC、
逐项人工审片与审计。Mock 默认；选择真实 Ref2VA 与 Profile 后每次提交再确认。
本轮从未点击真实提交，也未点击人工审批通过。

`/health` 是进程存活，新增 `connection_probe=not_run_use_system_status`，兼容保留旧字段。
`/api/v1/system/status` 才执行实际连接探测。连接在线不等于 H3 模型或电影质量通过。
`quality_verification` 在 UI 明确称证据来源，最终结果必须看 final 门。
本地登录预览已在数据库副本上通过；截图只保存在本地
`.local-data-v121-audit/operator-review-ui.png`。没有生产凭据或审批事件由 Agent 创建。

## 7. 数据库迁移（TESTED）

Alembic `e8f1a2b3c4d5 → f121a0010001` 增加 `workflow_profiles.model_identity`，
按原图填写身份并纠正真实 INT8 Profile 的 checkpoint 元数据。
全新库、旧版本往返、含旧 Profile 的升级均有测试。
还用 SQLite 只读连接 backup 实际 V1.2 验收库，再只迁移副本：
jobs、job_attempts、media_artifacts、qc_reports、quality_gates、audit_events 全部逐行相等。
原生产/验收数据库未升级、未清理、未改审批状态。

## 8. 新增回归与验证命令

新增人工审片及 Ref2VA 加固专项：未授权/伪用户名/跨源、媒体篡改/丢失、旧修订、
stale、租约、错误 Attempt/Prompt、技术门缺失、History 不符、策略快照失效、
并发只提交一次、request_id 重放、两轮返工、未试听阻断/明确试听补齐、FAIL 不误放行、
模型身份和图 hash、autogrow 边界、第三张图片阻断、未验证硬/视频连续性、旧数据迁移。
全部原有测试保留；迁移 head 和健康响应的新字段断言同步更新。

```powershell
$env:UV_DEFAULT_INDEX='https://pypi.org/simple' # 仅本机镜像配置冲突时
uv sync --project backend --locked
uv run --project backend --locked pytest -c backend/pyproject.toml backend/tests
uv run --project backend --locked ruff check backend/shine backend/alembic backend/tests
npm ci --prefix frontend
npm --prefix frontend run build
npm audit --prefix frontend
```

完整回归包含 `test_offline_e2e.py`、`test_film_crew.py`、Fake ComfyUI 媒体传输、
Worker 重启、提交不明不重投、请求修订返工预算和冻结风格保护。
Windows PowerShell `verify_local_stack.ps1 -ApiPort 8127 -KeepData` 在全新临时目录运行通过：
API 健康、Worker 在线、Mock PASSED、video_created=false、7 条审计；MOCK_VERIFIED。
本机最终完整回归：254 passed，1 条既有 Starlette/AnyIO DeprecationWarning（83.80s）；
相较基线净增 49 项，同时间戳排序合同反例、非字符串连续性输入及十项 FAIL 不误放行反例均通过。Ruff 全绿。
`npm ci`、TypeScript/Vite 6.4.5 build 全绿；`npm audit` 0 vulnerabilities。
一次 npm ci 遇到测试 Vite 占用 esbuild 的 EPERM；停止本轮记录的隔离进程后已重跑通过。
远端三项 CI 的最终数量和结果见交付记录。

## 9. 原三个真实样本复验

`scripts/verify_existing_ref2va.py` 只读打开原库、重算哈希并运行 FFprobe/FFmpeg 全帧解码。
原视频不重命名、不覆盖、不重新生成。

| 样本 | Job / 当前 Attempt | SHA-256 | 本轮客观结果 |
| --- | --- | --- | --- |
| A | b9670cca-8b08-40c3-8dd5-ea33ca50eaca / 47f94c59-53c9-4c00-8b9f-e1e611425389 | c15c399ef118708a9a6c61254f61ffb7fc23ae1c079a3e78ddc80005274a3841 | VERIFIED_REAL_H3：既有产物完整性/全帧解码；保留历史 PASSED |
| B | 83580f76-cbc9-4933-afd7-5d3d7293f16b / 678a67fb-04e6-4066-85e0-bded98e47d17 | 1c6e146403de2e59303f982b7a2f9ed68d6be4aa7900dad59943f8f14f5a9746 | VERIFIED_REAL_H3：既有产物完整性/全帧解码；原状态 NEEDS_REVIEW |
| C | a5dd68b7-a136-4204-a5f8-d776899d0d80 / ae544498-2d93-4af3-8dd2-6b0e93275754 | 58daa877be69e55d81b5cb929412e3b643f8b7c6dc399b796ce4c850103a06cb | VERIFIED_REAL_H3：既有产物完整性/全帧解码；仅软参考；原状态 NEEDS_REVIEW |

对应 Prompt ID 仍为 A `799f6e84-df47-4a07-8255-cca6e025ddf5`、
B `12bac085-726b-4d7c-b9f1-cb6e59b98bc1`、C `171bac3a-60a5-4991-8343-6dd7e9928c50`。
三者均 480×864、约 5.166 秒，实际哈希=登记哈希=Attempt 输出哈希，大小匹配。
本轮 ComfyUI 8188 拒绝连接，因此实时 History/节点重验证 BLOCKED；不能冒充重新完成生成验收。

用户本轮播放反馈：**声音正常；对白逐字准确；口型同步**。该明确人工反馈单独记录为
VERIFIED_MANUAL（用户本轮自述播放结论），不把 Agent 观看或音轨存在当成试听证明。
它没有自动写成新的授权逐项审片事件，也不能代替身份、风格、画质和连续性等其他门。
A 原审批使用旧脚本，不能追认成新权限协议证据；历史不改写。

## 10. 仍未闭合的验收边界

| 项目 | 状态 | 影响与下一步 |
| --- | --- | --- |
| 三个样本正式画质 | BLOCKED | 用户认为画质太差；480×864 是联调规格，不能认定电影质量通过 |
| 更高分辨率真实对照 | PENDING_H3_TEST | 后续需用户另行批准；独立副本保持 INT8/Ref2VA/素材/seed/20步，仅比较分辨率 |
| 严格首尾帧锚定 | PENDING_H3_TEST | PENDING_HARD_CONTINUITY_TEST；现证据构图不连续 |
| 视频参考真实传输 | PENDING_H3_TEST | SUPPORTED_NODE_UNVERIFIED_TRANSFER；不启用动态能力 |
| ComfyUI 实时 History | BLOCKED | 8188 当前离线；本轮没有启动后端或清理队列来规避 |
| 正式操作员逐项写回 | BLOCKED | 由本机管理员配置凭据，操作员本人按新界面提交；Agent 不代审批 |
| 自动 ASR | BLOCKED | 未配置、未下载；用户明确人工对白反馈已经记录，不能继续称声音存在已知缺陷 |

画质诊断：480×864 约 0.415MP；768×1344 约 1.032MP，为前者 2.49 倍像素。
后者可作为后续独立对照规格，但本轮没有验证其 16GB 显存、速度或画质收益，不能预先保证。
低分辨率是明确限制；人物细节还受采样、参考缩放、模型及压缩影响，不能仅凭观感归因。
不通过放大原片、改报告或更改旧 Profile 来制造“修复画质”的证据。

## 11. 安全与交付记录

仅修改 Shine 源码、配置元数据、测试和文档。无模型下载、无权重/原始素材/视频/密钥/数据库
进入 Git；ComfyUI 原始工作流、缓存、队列和历史均未改；额外 GPU 提交数为 0。
本地测试进程使用隔离库；未开启真实 Worker。没有自行合并 PR。

- 主实现提交：`ffc36e74d5a12e45ed5004e8229c448c05c644f2`。
- Pull Request：[PR #4](https://github.com/anjingdtl/shine-video-studio/pull/4)，未合并。
- 主实现 CI：[run 39612420035](https://github.com/anjingdtl/shine-video-studio/actions/runs/39612420035)，Ubuntu backend / Windows backend / Frontend 均 TESTED（success）。
- 同 SHA 的 PR 事件 CI：[run 39612982665](https://github.com/anjingdtl/shine-video-studio/actions/runs/39612982665)，三项也均 success。
- 上述两轮后端均 243 passed；已读取 Ubuntu 与 Windows 日志，Windows 完整测试为 291.18 秒。
- 最终补丁增加连续性输入类型防护，并保证无对白镜头的任一人工音频 FAIL 也不能放行；
  本机最终全量为 254 passed，审片/Ref2VA 专项 49 passed，Ruff 全绿。
- 补丁和本报告所处最新提交及其最终 CI：以 [PR 最新 Checks](https://github.com/anjingdtl/shine-video-studio/pull/4/checks) 为准，交付前另核对具体 head SHA；报告不伪造自引用的提交 hash。
- 整体艺术质量仍为 BLOCKED，三项软件 CI 成功不解除本报告第 10 节的验收边界。
