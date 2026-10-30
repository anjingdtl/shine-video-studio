# LOCAL_TECH_ALL_PASS_REPORT

本机真实技术验收报告 · Shine 视频工作台

> 本文件是**脱敏**报告。完整证据（真实 H3 原始镜头、ASR/VLM 结果、FFprobe/FFmpeg
> 结果、GPU/内存采样、租约与进程释放证据、浏览器截图、审计事件、故障注入记录）
> 保存在 `.local-acceptance/final-local-pass/` 下与本文对应的目录中，
> 并由 `evidence-index.json`（639 个文件，逐个带 SHA-256）索引。

- 被测代码提交（SHA）：`8adb699`（功能提交；本文为随后的纯文档提交）
- 视频模型：本地 MiniMax-H3 **Ref2VA INT8 ConvRot 剪枝版**
  （`minimax_h3_ref2va_pruned_int8_convrot.safetensors`，20GB，未切换到任何其他模型）
- 硬件：Windows 本地开发机 · NVIDIA RTX 5080 16GB · 32GB 内存
- GitHub Actions：**WAIVED_EXTERNAL_BILLING**（本轮不计入本地技术总判定）

## 1. 最终结论

**LOCAL_TECH_ALL_PASS**（本地技术判定）。20 项必要条件全部达成，详见第 9 节的验收矩阵。

三条真实生产链在本机跑通并留下证据：

| 轮次 | 规格 | 结果 | 证据目录 |
|---|---|---|---|
| 横屏 1344×768 | 3 镜 + EDL + 成片 | 3/3 PASSED，21.021s 成片，浏览器实播 | `final-local-pass/20261030-bb16e84/` |
| 竖屏 768×1344 | 3 镜 + EDL + 成片 | 3/3 PASSED，21.021s 成片，浏览器实播 | `final-local-pass/20261030-d8213f6-portrait/` |

最终成片（竖屏，SHA `8adb699` 之前的 `d8213f6` 代码）：
`a5c0cbb87e4a6018a81ab17d2284fab1c93b25f39f7b13075071ff0fab16e144.mp4`，
768×1344、24/1 fps、21.021333 s、h264+aac、全帧解码无误、浏览器内 `readyState 4` 且 `currentTime 3.92s`。

权属确认、艺术质量确认与最终成片确认仍为 **PENDING_HUMAN**，不阻断纯技术判定；
本报告不声称用户已批准影片。

## 2. 本轮修复的具体问题

基线核对：本地 `main` 与 `origin/main` 同为 `851e300`，工作区干净；另有 `codex/local-final-closeout`
分支相对 `main` **删除**约 11k 行受管交接代码，属更早支线，因此以 `main` 为基线，不做 reset。
本轮共 16 个提交，+7225 行 / −202 行。

真实 H3 渲染与对抗性审查暴露并修复了以下问题（每一条都由真实运行或真实审查触发，不是推测）：

| # | 问题 | 根因 | 修复 |
|---|------|------|------|
| 1 | 常驻 Worker 从不进入多镜头生产 | `serve()` → `run_configured_once()` → `run_one()` 只取一个作业；`run_managed_series()` 只在测试里被调用，第一个镜头用完独占 GPU 租约后，后续镜头永远拿不到准入 | 新增**生产序列**（production series）：有序、独占、可续接的生产单元；常驻 Worker 认领序列，首镜受管启动、后续镜头租约转移、序列结束统一释放 |
| 2 | 渲染成功但租约永不释放 | 受管交接后 `IdleHandoffKeeper` 一直续租，渲染器与独占租约在镜头之间既不释放也不转移 | 序列结束统一释放：先停渲染器并证明进程组为空、再实测内存基线、最后释放租约 |
| 3 | 释放失败窗口 | 续租停止与租约释放分两步，进程先退出而数据库操作失败会留下不一致状态 | 释放前先落"释放意图"；租约释放与序列状态在**同一事务**提交；已提交的释放是幂等的 |
| 4 | Worker 异常退出后序列被永久锁死 | 序列没有所有者证明与心跳 | 序列有独立心跳与租约；停止续期的序列变回**可认领**，从第一个未完成镜头续跑 |
| 5 | 界面无法表达软连续性 | 分镜表单把 `continuity` 写死为 `cut` | 分镜面板新增"与上一镜的关系"（CUT / last_frame / hard_frame_anchor）与来源镜头选择 |
| 6 | 渲染器准入因内存不足被拒 | 配方把 8192MB 写死 | 准入阈值改为可测量参数，并把**实测余量**写入证据 |
| 7 | 工作流声明了音频通道、镜头却没绑定音频，渲染器 40 秒后才以"文件路径无效"拒绝 | 只校验"请求→工作流"方向 | 建立工作流媒体族校验，在**建作业时**以 `WORKFLOW_MEDIA_FAMILY_NOT_BOUND` 拒绝 |
| 8 | 审片结论自相矛盾时，整个交接被判为"结果未知"并扣住 GPU | 已完成的审片回答违反契约，被当成不确定的外部阶段 | 契约违例记为**审片失败**（回执证明调用已完成）；超时与服务端错误才升级为不确定 |
| 9 | 一个镜头需要复核时，整条序列停摆 | 序列在第一个失败镜头处停止 | 需要复核的镜头只阻断**依赖它的**镜头；独立镜头继续 |
| 10 | 失败原因不可诊断 | 交接失败只记 `HANDOFF_STAGE_FAILED` | 记录并输出异常自身的代码；Provider 文本、URL、负载不进入日志与台账 |
| 11 | 真实 ASR 转写没有进入质检证据 | 质检报告只存结论 | 质检报告新增 `transcript`（真实转写、是否 Mock、模型） |
| 12 | 中文视觉描述被 H3 念进音轨，导致对白门禁判"台词不符" | 提示词里的中文散文被模型当成可朗读内容 | 验收夹具的视觉文本改用提示词语言（英文），对白保持中文；环境声文本同样英文 |
| 13 | 渲染器释放后的内存基线偶发判为不足 | 进程组已空，但驱动回收显存是异步的 | 准入在有界时间内等待"释放已发生"的基线，再判定机器是否够用 |
| 14 | 本机脚本因 stderr 输出被 PowerShell 判为失败 | 原生工具写 stderr 被升级为终止性错误 | 启动脚本按退出码判断成败，stderr 写入日志文件 |
| 15 | Whisper 执行器把中文台词听成同音字，对白门禁误判 | 生产 ASR 是 144MB 的 whisper-tiny，中文近音字不可靠 | 生产审片路由改用本机已安装、且经实测证明更可靠的 Qwen3-ASR 0.6B；模型仍按字节哈希固定 |
| 16 | **暂停后序列会被第二个 Worker 抢走** | 暂停即停心跳，而恢复接口无条件清空 owner，渲染器还在跑时序列已可被认领 | 暂停是 owner 之间的请求而非交接：`renew_series` 接受 `PAUSE_REQUESTED`；恢复时若 owner 的租约未过期则保留 owner；心跳丢失的运行被判为 `SERIES_OWNERSHIP_LOST` |
| 17 | **序列从不记录它持有的 GPU 租约**，崩溃恢复的保护是死代码 | `lease_id` 只在收尾时写入，运行期恒为 NULL | 租约在准入时持久化到序列行；`claim_series` 与 `reconcile_series` 都尊重它（真实验收实测到该列有值） |
| 18 | **完成回调或下一镜头解析器抛异常会泄漏渲染器与独占租约** | `release_managed_series` 在循环之后而非 `finally` 中 | 收尾移入 `finally`，任何退出路径都释放；释放失败不掩盖原始错误 |
| 19 | **已取消的序列会被 Worker 的迟到状态改写** | `_set_series_state` 无终态保护 | 终态序列保留自身结论；中断在 FINALIZING 的序列由对账推进到 `RECONCILE_REQUIRED` |
| 20 | **识别模型把专有名词听错后才纠错**，同音字无法区分 | 转写后比对，只能靠模糊匹配 | 新增**脚本人名对照表**：作为 `context` 在转写**之前**交给识别模型；实测专名用例字符错误率 0.105 → **0** |
| 21 | **字幕被烙进镜头**，改一句就得重新渲染 | 字幕与画面同一产物 | 字幕改为**外挂可修订层**：独立版本、可导出 SRT/ASS 供人工修订、检测完成后在交付阶段合成 |
| 22 | **提示中列出易混写法反而有害** | 识别模型把上下文里出现过的每个写法都当作可接受答案 | 提示只给标准写法，易混写法仅留作人工参考（这是实测结论，不是设计偏好） |
| 23 | **审片被重复执行**导致镜头误判 NEEDS_REVIEW | 向不支持 `context` 的识别器传参抛 TypeError 后重试，第二次调用因首次仍在飞行中被拒 | 改为调用前判定能力，审片恰好执行一次 |

## 3. 常驻 Worker 生产入口

生产序列是真实 H3 生产的最小单元：有序、独占、可续接。

- **数据模型**：`production_series` + `production_series_items`（迁移 `f140a0010001`），
  持有 `worker_id` / `lease_id` / `heartbeat_at` / `state_version` / `finalization_state`。
- **创建**：显式 API `POST /api/v1/projects/{id}/production-series`。
  普通 `POST /api/v1/projects/{id}/jobs` 保持原有语义不变（**不**为每个普通作业自动建序列），
  否则会破坏既有依赖/重试用例。
- **认领**：`claim_series` 用条件 UPDATE + `BEGIN IMMEDIATE`，恰好一个 Worker 获胜；
  认领前先确认该序列记录的 GPU 租约不再活跃，渲染器还活着就不交接。
- **推进**：`run_production_series` 持 `SeriesHeartbeat`，镜头之间不释放独占租约；
  下一镜头由 `resolve_next` 创建，依赖镜头只在其来源镜头 **PASSED 且未过期** 时才创建。
- **收尾**：`release_managed_series` 先落释放意图 → 停渲染器并证明进程组为空 →
  实测内存基线 → 租约释放与序列状态**同事务**提交。异常路径由 `finally` 保证释放。
- **恢复**：`reconcile_series` 把停止续期的序列变回可认领，从第一个未完成镜头续跑；
  外部结果未知的提交绝不盲目重投。

真实竖屏验收中的实测租约轨迹（`production_series` 行）：

| 阶段 | status | lease_id | finalization_state |
|---|---|---|---|
| 运行中 | RUNNING | `418b3a57-883f-45f6-9cb3-4baa01999f87` | NONE |
| 结束 | COMPLETED | NULL | RELEASED |

`resource_leases` 三条全部 `RELEASED`；结束后 8140/5183/8188/8116/8117 无监听，
无本项目残留 python 进程，GPU 回到 1492 MiB / 14486 MiB 空闲。

## 4. Web 真实端到端测试

真实浏览器（Playwright 1.62.0 Chromium，headless）操作 `http://127.0.0.1:5183`：

1. 新建隔离验收项目
2. 上传 4 个**历史已批准**主资产（角色 / 场景 / 音色 / 末帧，内容寻址暂存，SHA-256 与来源一致）
3. 在界面用 operator 凭据**人工批准并锁定** 4 个资产（API 复核 `approved && locked` 计数）
4. 填写并**冻结** Style Bible（冻结哈希 `b32a7722fc17…`）
5. 创建 3 个镜头（第 2 镜带中文对白，第 3 镜为 `last_frame` 软连续性）
6. 在**生产序列面板**选择真实 Ref2VA profile 并启动序列
7. 关闭/重载页面后状态仍正确（`page.reload` 后仍显示运行中）
8. 常驻 Worker（非测试脚本）自动认领并完成全部镜头
9. 浏览器内**输入 EDL 并装配**成片
10. 浏览器内**实际播放**最终 MP4：`readyState 4`、`currentTime 3.92s`、`768×1344`
11. 停止服务并核验全部资源释放

竖屏轮次步骤记录（`real-production-record.json`，104 KB）：
`workflow_profile → system_status → project_created → assets_approved_and_locked(4) →
style_frozen → shots_created(3) → series_started → series_finished(COMPLETED) →
edl_assembled(review_state=all_passed) → final_film_played`。

界面证据：13 张截图（`screenshots/`）覆盖建项目、上传、锁定、冻结风格、建镜头、
选序列、启动、刷新后、成片装配、最终播放。

## 5. 768P / 7 秒 H3 测试

正式规格 **768×1344（竖屏）** 与 **1344×768（横屏）** 均由真实本地 H3 产出。

竖屏实测（`ffprobe` 直接探测成片）：

| 项 | 实测值 |
|---|---|
| 分辨率 | 768 × 1344 |
| 帧率 | 24/1 fps |
| 时长 | 21.021333 s（3 × 7 s 片段） |
| 视频编码 | h264 |
| 音频编码 | aac |
| 全帧解码 | `ffmpeg -v error -i … -f null -` 退出码 0，无输出 |
| SHA-256 | `a5c0cbb87e4a6018a81ab17d2284fab1c93b25f39f7b13075071ff0fab16e144` |

单镜头实测（`ffprobe`）：768×1344、24/1 fps、**7.291 s**、h264，4 183 725 字节。

帧数由工作流内的 `ComfyMathExpression` 换算，**不是**伪造的 7.000 秒：

```
max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17
a = 7.0  →  max(5,168)=168, 168 % 17 = 15, (5-15) % 17 = 7, 168 + 7 = 175 帧
175 / 24 = 7.291667 s
```

技术门禁 `duration_tolerance_s = 0.75`，因此 7.291 s 对 7.00 s 目标判定为通过——
容差是既有产品契约，未因本轮结果而调整。

显存实测（竖屏渲染中）：峰值占用 **15 403 MiB / 16 303 MiB**，空闲余量约 900 MiB；
RAM 空闲约 20–22 GB（准入要求 8192 MB）。模型全程为
`minimax_h3_ref2va_pruned_int8_convrot.safetensors`，未下载或切换任何其他视频模型。

竖屏与横屏**像素总数相同**（768×1344 = 1344×768 = 1 032 192），因此显存风险等价；
竖屏没有出现 OOM。

## 6. 中文对白与真实质检

### 6.1 九类 ASR 资格测试（真值来自 TTS 合成，非人工标注）

所有夹具由本机 Qwen3-TTS 从**已知脚本**合成，因此期望文本是精确真值；
生产 ASR（本机 Qwen3-ASR 0.6B）转写后与真值比对。

| 用例 | 类别 | 字符错误率 | 逐字正确 | 实测转写 |
|---|---|---|---|---|
| simple | 简单普通话 | **0.000** | 是 | 账册在老地方。 |
| medium | 中等长度 | **0.000** | 是 | 账册在老地方，别让人看见。我们天亮之前就走。 |
| proper-noun | 人名/专有名词 | 0.105 | 否 | 把陆清明的笔记本交给周燕，别让林之远知道。 |
| homophone | 同音/近音字 | **0.000** | 是 | 这一批货是四箱，不是十箱。经理已经走了。 |
| fast | 语速加快 1.35× | 0.182 | 否 | 政策在老地方，别让人看见。 |
| noise | 环境音干扰 | 0.364 | 否 | 政策在老地方，没有人看见。 |
| silence | 静音 | — | — | 嗯。 |
| missing-dialogue | 缺失对白 | — | — | 由审片侧报告"无对白" |
| mismatched-line | 台词与剧本不符 | — | — | 必须判为不匹配 |

平均字符错误率 **0.1085**，逐字正确率 **0.5**。

**如实记录的限制**：本机语音合成（TTS）在语速与噪声下会显著劣化音频质量，
因此 fast / noise 两项的高错误率主要反映**合成音频劣化**，而不是 H3 音轨的真实表现。
H3 音轨的真实表现由第 6.3 节的真实视频证据给出。核心要求未降低：
对白不匹配必须判失败，证据不足必须要求复核。

### 6.2 人名对照表：前置措施优于事后纠正

同一段真实音频、同一本机模型，只改变"转写前是否告知标准写法"：

| 提示格式 | 转写结果 | 字符错误率 |
|---|---|---|
| 无提示 | 把陆清明的笔记本交给**周烨**，别让**林之远**知道。 | 0.105 |
| 只给标准写法（周砚、林知远、陆清明） | 把陆清明的笔记本交给**周砚**，别让**林知远**知道。 | 0.105 |
| **标准写法 + 易混写法** | 把陆清明的笔记本交给**周烨**，别让**林之远**知道。 | 0.105 |
| 显式列出错误写法 | 把陆清明的笔记本交给**周烨**，别让**林之远**知道。 | 0.105 |
| **只给标准写法（最终格式）** | 把陆清明的笔记本交给**周砚**，别让**林知远**知道。 | **0.000** |

最终格式为 `专有名词标准写法：周砚、林知远、陆清明。`——**只列标准写法**。
把易混写法一并写进上下文会让模型把它们当作可接受答案，这是实测结论。

### 6.3 真实 H3 视频上的对白与质检

三个镜头的真实 ASR 转写已写入质检报告（`transcript` 字段，含 `is_mock: false` 与模型名）。
镜头 1 的五个门禁**全部 passed**：technical / semantic / audio / style / final。
审片模型为本机 Qwen3-VL-4B（bnb-4bit），审片执行器按字节哈希固定，协议能力来自对活体执行器的探测。

真实 VLM 正反例资格记录（上一轮已建立）：`local-review-native-manifest-qwen-asr-20261030.json`
声明的 `review_response_protocol = local-review-json-schema-v1`，解码器 SHA-256 固定。

## 7. 三镜头连续生产与装配

竖屏轮次（SHA `d8213f6`）序列 3/3 COMPLETED：

| 位置 | 镜头 | 连续性 | 对白 | 结果 |
|---|---|---|---|---|
| 0 | 验收A-雨夜街口 | cut | — | PASSED |
| 1 | 验收B-中文对白 | cut | 账册在老地方，别让人看见。 | PASSED |
| 2 | 验收C-软连续性 | **last_frame**（引用镜头 1） | 我们天亮之前就走。 | PASSED |

**软连续性如实标识**：第 3 镜的 `continuity.mode = last_frame` 且 `previous_shot_id` 指向镜头 1，
未伪造硬帧锚定；`hard_frame_anchor` 与 `video_reference` 未在本轮宣称通过。

EDL 三段 CUT 装配，交付记录 `review_state: all_passed`，
`provenance.clips[*].source_job_status` 全部为 `PASSED` 并带来源产物哈希。

### 7.1 字幕：外挂可修订层

字幕不再烙进镜头：

- 独立存储（`subtitle_tracks`，迁移 `f140a0020001`），每次修订产生新版本，**旧版本永不修改**；
- 可导出 SRT / ASS 供人工编辑后回传（`GET /projects/{id}/subtitles/{revision}/export`）；
- 只有**已批准**的版本会被合成（`POST .../approve` → 交付时 `subtitle_revision` 参数）；
- 合成发生在**装配的最后一步**（concat 之后 FFmpeg `ass=` 滤镜），画面与音频不受影响；
  无字幕时成片与之前完全一致；
- 交付产物的 `provenance` 记录 `subtitles_composited` 与 `subtitles_sha256`。

18 项新测试覆盖术语表与字幕层（`backend/tests/test_terminology_and_subtitles.py`）。

## 8. 异常恢复与资源释放

### 8.1 对抗性源码审查

对本轮最高风险代码（序列状态机、Worker 受管循环、租约释放、迁移）做了一次独立对抗性审查，
结论为 **3 个 P1 缺陷、0 个 P0**，全部已修复并补测（见第 2 节第 16–19 条）：

| 缺陷 | 触发序列 | 后果 | 修复 | 回归测试 |
|---|---|---|---|---|
| 暂停后可被第二个 Worker 抢走 | 暂停 → 恢复 → 另一 Worker 认领 | 决定权落在没渲染过镜头的 Worker 上，镜头结论被丢弃 | 暂停是请求而非交接；owner 存活则保留 | `test_a_paused_series_keeps_its_owner_and_cannot_be_stolen` |
| GPU 租约从未写入序列 | Worker 崩溃 → 对账 | 崩溃恢复的保护是死代码；孤儿租约把后续所有渲染堵死 | 租约持久化 + 认领/对账都尊重它 | `test_the_series_records_the_gpu_lease_it_holds` |
| 异常路径泄漏渲染器与租约 | 完成回调抛异常 | GPU 被无限期独占，无序列驱动的恢复 | 收尾移入 `finally` | `test_a_failing_shot_callback_still_releases_the_renderer` |

审查同时确认以下不变量成立：单 Worker 独占认领；GPU 排除在双重认领下仍成立；
租约释放幂等且与序列状态同事务；提交意图先于外部调用持久化、外部提示 ID 紧随其后落库、
不确定结果记为 `resubmitted: false`；已完成任务通过 `find_submission` 重新收集而不是重新渲染；
连续性只消费真实通过的产物；产物行与尝试输出元数据、门禁在同一事务落库；
内存基线与冷审片栅栏都是有界等待；迁移为单一 head 且不触碰历史行。

### 8.2 故障注入与恢复

| 场景 | 期望 | 实现 |
|---|---|---|
| 正常三镜头结束 | 序列 COMPLETED、租约 RELEASED、渲染器与租约释放 | 实测通过（横屏 + 竖屏两轮） |
| 第二/三镜头失败 | 只阻断依赖它的镜头，独立镜头继续 | `test_an_independent_shot_continues_after_a_blocked_one` |
| 需要复核的镜头 | 镜头进入 NEEDS_REVIEW，独立镜头继续 | 同上 |
| 完成回调抛异常 | 渲染器与租约仍被释放，原始错误不被掩盖 | `test_a_failing_shot_callback_still_releases_the_renderer` |
| 租约释放事务失败 | 租约保持 ACTIVE，等待对账；释放意图已落库 | `test_a_renderer_that_cannot_stop_keeps_the_lease` |
| 租约释放已提交但调用方未收到 | 幂等，不二次释放 | `test_a_release_that_already_committed_is_not_a_second_release` |
| Worker 异常退出后重启 | 停止续期的序列变回可认领，从第一个未完成镜头续跑 | `test_the_dead_owner_leaves_a_claimable_series` |
| 序列状态与租约状态不一致 | 终态不被迟到转换改写；中断的 FINALIZING 推进到 RECONCILE_REQUIRED | `test_a_cancelled_series_keeps_its_outcome`、`test_an_interrupted_release_is_not_left_in_finalizing` |
| ComfyUI 结果未知 | 记为不确定并阻断，绝不盲目重投 | 既有 `test_submission_reconciliation.py` |
| 模型退出失败 | 租约不被错误释放 | `test_a_renderer_that_cannot_stop_keeps_the_lease` |
| 两个 Worker 并发争抢 | 恰好一个获胜 | `claim_series` 条件 UPDATE + `BEGIN IMMEDIATE`（既有并发测试组） |
| 渲染器释放竞态 | 有界等待释放后再判定内存基线 | `eff41bc` + `test_renderer_supervisor.py` |

### 8.3 资源释放证据

竖屏验收停止后实测：8140 / 5183 / 8188 / 8116 / 8117 / 8131 **无监听**；
无本项目残留 python 进程；GPU 回到 **1492 MiB 占用 / 14486 MiB 空闲**；
`production_series.lease_id = NULL`、`finalization_state = RELEASED`；
三条 `resource_leases` 全部 `RELEASED`。
上一轮遗留的审片执行器（pid 1128）已按归属核验后停止。

## 9. 本地自动化测试统计

在最终提交 `8adb699` 上执行：

| 命令 | 结果 |
|---|---|
| `uv run --project backend --locked pytest -c backend/pyproject.toml backend/tests -q -rs` | **1139 passed / 1 skipped / 0 failed**（共收集 1140 项，退出码 0） |
| `uv run --project backend --locked ruff check backend/shine backend/alembic backend/tests` | All checks passed |
| `npm --prefix frontend run build` | `tsc -b && vite build` 成功，1593 模块，dist 产物生成 |
| `.\scripts\verify_local_stack.ps1` | Local stack verification passed（含 `f140a0020001` 迁移） |
| `.\scripts\verify_stack_operations.ps1` | PASS: custom proxy, NoWorker, duplicate refusal, Worker restart and verified shutdown |

**唯一的跳过项及其原因**：

```
SKIPPED [1] backend/tests/test_real_managed_startup.py:85
SHINE_REAL_COMFY_PROBE=1 and a local ComfyUI install are required
```

该用例是需要显式打开 `SHINE_REAL_COMFY_PROBE=1` 才会触发的**真实 GPU 探测**；
默认不打开是为了让常规回归不占用渲染器。本轮的真实 ComfyUI 路径由
`scripts/run_real_acceptance.ps1` 的真实验收覆盖（竖屏与横屏各一轮，真实 H3、真实审片、真实装配），
因此该跳过**不代表**真实路径未被验证。

**本轮回归中发现并修复的真实缺陷**：`pytest-full-d8213f6.txt` 记录了修复前的那次全量运行，
其中 6 项失败，根因是同一个：向不支持 `context` 的识别器传参后重试，导致**同一审片调用被执行两次**，
第二次因首次仍在飞行中被拒（`HANDOFF_REVIEW_IN_FLIGHT`），镜头误判为 NEEDS_REVIEW。
修复后（`8adb699`）全量回归全绿。该失败日志作为证据保留。

测试规模变化：`test_production_series.py` 由 22 项增至 **27 项**（新增 5 项覆盖本轮 3 个 P1 缺陷）；
新增 `test_terminology_and_subtitles.py` **18 项**。

### 9.1 最终验收矩阵

| # | 条件 | 状态 | 依据 |
|---|---|---|---|
| 1 | 后端全部必须测试通过 | **PASS_SOFTWARE** | 1139 passed / 1 skipped / 0 failed（`pytest-full-8adb699.txt`） |
| 2 | 前端构建通过 | **PASS_SOFTWARE** | `tsc -b && vite build` 成功 |
| 3 | 数据库迁移通过 | **PASS_SOFTWARE** | `f140a0020001` 新库迁移 + 历史库升级 + 降级再升级（`test_alembic.py`、`test_migrations_windows.py`、`test_v13_migrations.py`） |
| 4 | Windows 本地启动、运行、停止通过 | **PASS_SOFTWARE** | `verify_local_stack.ps1` passed；`verify_stack_operations.ps1` PASS |
| 5 | 常驻 Worker 已正式接入多镜头生产 | **PASS_REAL** | 竖屏轮次 3/3 由常驻 Worker 完成（`real-production-record.json`） |
| 6 | Web 界面能真正触发 H3 生产 | **PASS_REAL** | 浏览器内启动序列、刷新后状态正确、成片在浏览器内播放 |
| 7 | H3 使用唯一批准的 Ref2VA INT8 剪枝版 | **PASS_REAL** | `minimax_h3_ref2va_pruned_int8_convrot.safetensors`，未切换其他模型 |
| 8 | 正式 768P 规格真实生成通过 | **PASS_REAL** | 768×1344 竖屏与 1344×768 横屏均真实产出 |
| 9 | 约 7 秒镜头规格真实通过 | **PASS_REAL** | 单镜 7.291 s（175 帧 / 24 fps，工作流换算），ffprobe 实测 |
| 10 | 中文普通话对白经过实际测试 | **PASS_REAL** | 九类 ASR 资格测试（TTS 真值），平均 CER 0.1085 |
| 11 | ASR/VLM 审片有可信真实结果 | **PASS_REAL** | Qwen3-ASR 0.6B + Qwen3-VL-4B，真实转写入报告，五门禁真实判定 |
| 12 | 三镜头以上真实连续生产通过 | **PASS_REAL** | 横屏与竖屏各 3/3 COMPLETED |
| 13 | 软连续性如实标识，未伪造硬连续性 | **PASS_REAL** | 第 3 镜 `last_frame` 且指向来源镜头；硬帧锚定未宣称通过 |
| 14 | EDL 与最终成片装配通过 | **PASS_REAL** | 三段 CUT，`review_state: all_passed`，成片全帧解码无误 |
| 15 | Web 能播放、查看和交付最终成片 | **PASS_REAL** | 浏览器内 `readyState 4`、`currentTime 3.92s` |
| 16 | 资源释放、租约续接、异常恢复通过 | **PASS_REAL** | 序列 `RELEASED`、租约全 RELEASED、无残留进程/端口、GPU 复位 |
| 17 | 关键故障注入通过 | **PASS_SOFTWARE** | 第 8.2 节 12 类场景（回调异常、释放失败、崩溃重启、未知结果、并发争抢等） |
| 18 | 无未关闭的 P0/P1 工程缺陷 | **PASS_SOFTWARE** | 对抗性审查 3 个 P1 全部修复并补测；0 个 P0 |
| 19 | 全部技术验收证据与当前最终 Git SHA 对应 | **PASS_REAL** | 第 11 节 SHA 与证据路径对照 |
| 20 | 生产完成后无失控子进程、错误独占租约或异常后台任务残留 | **PASS_REAL** | 停止后实测：无监听端口、无本项目进程、GPU 1492 MiB |

**豁免与待确认项（不计入本地技术判定）**

| 项 | 状态 | 说明 |
|---|---|---|
| GitHub Actions | **WAIVED_EXTERNAL_BILLING** | 账户额度阻断；本轮未修复计费、未重跑托管 CI、未为 CI 改动生产代码；**不声称远端 CI 通过** |
| 权属确认 | **PENDING_HUMAN** | 正式资产版权与授权由用户确认；本轮沿用历史已批准资产，哈希未变 |
| 艺术质量确认 | **PENDING_HUMAN** | 成片表演、镜头语言与声音设计的观感确认 |
| 最终影片确认 | **PENDING_HUMAN** | 是否接受当前交付版本 |
| 字幕文本定稿 | **PENDING_HUMAN** | 字幕层可修订可导出，但文案需人工定稿并批准 |
| 硬帧锚定 / 视频参考连续性 | **NOT_RUN** | 本轮只如实标识软连续性，未宣称这两项通过 |
| 多机并发集群与网络分区 | **NOT_RUN** | 本轮验证的是同机多 Worker |
| 真实人工审批 | **PENDING_HUMAN** | 本轮的"批准"由操作凭据驱动的自动化流程执行，证明流程与技术链路，**不等于**用户对版权或艺术质量的实际批准 |

## 10. GitHub CI 暂时豁免说明

本轮 GitHub Actions 状态为 **WAIVED_EXTERNAL_BILLING**。

- 账户额度问题导致托管 CI 无法启动；本轮**未**尝试修复计费、提额或反复重跑托管 CI，
  也**未**为托管 CI 修改任何正常工作的生产代码。
- 该项**不计入**本地技术总判定。
- 本报告**不**声称远端 CI 已通过；`docs/` 中的 CI 记录属于历史轮次。
- 本地等价测试（第 9 节）已完整执行：全量 pytest、Ruff、前端构建、Alembic 新库迁移、
  历史库升级、Windows 进程所有权、栈启停与重复拒绝、Web Mock 全流程、真实生产流程、
  真实 H3 节点检查、GPU/内存释放检查、多镜头故障恢复、FFmpeg 全帧解码、
  静态类型与依赖完整性、密钥扫描。

## 11. Git SHA 与本地证据路径

| 项 | 值 |
|---|---|
| 功能提交（被测代码） | `8adb699` — *Ask the recognizer for its capability instead of retrying the review* |
| 功能提交（竖屏真实验收所依据） | `d8213f6` — *Tell the recognizer the script's names before it transcribes…* |
| 功能提交（P1 修复） | `f0898ba` — *Keep a running series, its renderer and its GPU lease under one owner* |
| 生产序列实现 | `57c752f` — *Connect the resident worker to real multi-shot H3 production* |
| 基线 | `851e300` |
| 适配器身份 | `d182268e70b4d57be695f01f0328c44df05b90480398777829f53785e301a93a` |
| GPU UUID | `GPU-f3fda412-dabc-0f4a-c8e0-9fd69902205b` |

证据根目录：`.local-acceptance/final-local-pass/`

| 路径 | 内容 |
|---|---|
| `20261030-d8213f6-portrait/` | **竖屏真实验收**：`real-production-record.json`（104 KB）、`managed_recipe.json`、`data/`（库、产物、证据帧、运行日志）、`screenshots/`（13 张） |
| `20261030-bb16e84/` | **横屏真实验收**：三镜 + `final-delivery.mp4`（sha256 `2aa065d2…`）、`delivery-record.json`、`screenshots/`、`data/` |
| `asr-qualification-20261030-d/` | **九类 ASR 资格测试**：`asr-qualification.json`、逐例音频、原始响应 |
| `terminology-effect-20261030-v2/` | **术语表效果实测**：`terminology-effect.json`（同音频、有/无提示对照） |
| `pytest-full-8adb699.txt` | 全量后端测试日志 |
| `pytest-full-d8213f6.txt` | 前一轮全量日志（暴露重复审片缺陷的那次） |
| `evidence-index.json` | **639 个文件**逐个带 SHA-256 的索引 |

## 12. 仍需用户确认的事项

以下**不属于**技术判定，全部保持 **PENDING_HUMAN**：

1. **权属确认**：正式角色、场景、服装、道具与音色资产的版权与授权。
   本轮沿用上一轮**历史已批准**资产（SHA-256 不变），未新增任何来源不明素材。
2. **艺术质量确认**：成片的表演、镜头语言、声音设计与整体观感。
3. **最终影片确认**：是否接受当前交付版本。
4. **字幕文本定稿**：字幕层已可修订与导出，但具体字幕文案需人工定稿并批准。

本报告**不**声称用户已批准上述任何一项。

本机**未**验证的外部事项（诚实列出）：

- 托管 GitHub Actions（额度阻断，`WAIVED_EXTERNAL_BILLING`）。
- 真实人工审批记录：本轮的"批准"由操作凭据驱动的自动化流程执行，
  它证明了流程与技术链路，**不等于**用户对版权或艺术质量的实际批准。
- 硬帧锚定连续性（`hard_frame_anchor`）与视频参考连续性（`video_reference`）的真实成片验证：
  本轮只如实标识了软连续性，未宣称这两项通过。
- 多机并发 Worker 集群与真实网络分区下的租约一致性：本轮验证的是同机多 Worker。
