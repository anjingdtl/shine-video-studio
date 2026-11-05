# 交接：受托素材轮进度、测试报告与后续 Agent 提示词（2026-11-05 深夜）

- 分支：`codex/director-closeout-20261105`（相对 `main` 领先：`cbfbee0`→`4a6a72c`→`ec2a40e`→`1daa65b`→`5d836a7`→`34fa3f7`→本文档提交）。**未推送**。
- 用户已明确授权：以 local-user 身份做拟人化操作（回答澄清、评审候选、晋升、冻结风格、排分镜），本文件如实记录已做的每一个决定。
- 真实栈：`http://127.0.0.1:8133`（API）/ `5193`（Web），数据目录 `.local-acceptance/live-browser`，`render_provider=comfyui`、runtime ACTIVE、mode READY。栈进程运行的是工作树代码（与 `34fa3f7` 一致）。
- 工作树干净（仅未跟踪 `.zcode/`，会话工件，不提交）。

## 1. 本轮（受托轮）已完成

1. **9 项澄清全部以 local-user 名义回答并重新生成**（新 v2 修订，seed=rev+1，`questions` 清空，回答并入英文外观描述）。回答原文见 `.zcode/tmp/delegate_material_round.py` 的 `ANSWERS`（表=当代手表且表壳开启机芯外露；灯罩=简式工业金属圆顶；船坞夜=木桩码头琥珀灯+大片风化栈桥；齿轮与镊子=仅此两物；黄铜怀表=去掉缠指细节、链条松盘于旁；海边石阶黄昏=阶顶复古铸铁路灯；旧船票=不可读淡印+单角微翘；周野=男性青年/黑色短发微乱/无须；钟表铺白昼=日光优先）。
2. **林素（LLM_FORMAT）resume 重试**，出品人重新产出合格 brief。
3. **渲染全部真实完成**：12/12 需求现有 CANDIDATE_READY 真实 Qwen 候选（9 个 v2 + 林素 v1 重试 + 老式煤油灯/顾晚 v1）。历史 v1 失败行按设计保留。
4. **两个真实缺陷已修复并提交（`34fa3f7`）**：
   - 澄清死循环：UI 不显示出品人问题、regenerate 原样回传 `questions` → 渲染必再拒。现 UI 显示问题、重生成消费问题；后端 regenerate 对带问题 brief 返回 422 `MATERIAL_BRIEF_QUESTIONS_UNANSWERED`。
   - 实例行卡死：重启时出图渲染器空闲停用 → `comfy_instances` 行永久停在 `RUNNING_PENDING_HEALTH`（行修复只按行上的 process 标记匹配，而出图运行时故意不在共享行上留标记）→ 所有排队项 `IMAGE_RENDERER_UNAVAILABLE`。现释放路径额外按记录里的 `instance_id` 修复，`stop_image_renderer` 对已停记录也回写行状态。实机上已用受管 release 动作把行修复为 STOPPED 并成功重启渲染。
5. **评审进行中（见 §3）**；候选大图已下载到 `.zcode/tmp/candidates/*.png`（文件名=实体名，映射见下表）。

## 2. 测试报告（本轮实跑）

| 检查 | 结果 |
| --- | --- |
| 全量后端套件（在 `ec2a40e` 树，含 offline_e2e/film_crew/v13 系列/production_series/handoff 等） | **EXIT=0 零失败** |
| `test_material_director_flow.py`（含新增 422 专项） | 通过 |
| `test_managed_process_release.py`（含新增 2 个卡行回归） | 通过 |
| `test_story_continuity.py`（R3 版本化连续性 3 专项）+ 邻接模块（story/segments/series/film_crew） | 通过 |
| Ruff（shine+alembic+tests） | All checks passed |
| 前端 vitest（19 文件 136 测试，含新增澄清用例） | 全绿 |
| `tsc --noEmit` / `npm run build` | 通过 |
| 真实栈：stop→start、`/health` 200、`/system/status` 1.7s（渲染器空闲，探针修复生效）、worker 在线 | 通过 |
| 受管 release 动作修复实例行（RUNNING_PENDING_HEALTH→STOPPED）后 10/10 排队项真实出图 | 实跑通过 |
| **未做**：`34fa3f7` 之后的全量后端/前端回归（只跑了上述定向模块）；安装包仍是从 `1daa65b` 构建的旧包，**需从最终提交树重建** | NOT_RUN |

## 3. 候选评审状态（12 项）

| 实体 | 版本 | candidate_id | 评审 | 备注 |
| --- | --- | --- | --- | --- |
| 顾晚 | v1 | `b768b7e0…` | **PASS** | 单人、头完整、当代风衣、中性背景、无水印 |
| 林素 | v1 | `c2cb6819…` | **PASS** | 单人、正面、深色大衣、持怀表、中性背景 |
| 周野 | v2 | `5f6111e7…` | **PASS** | 由用户直接贴图、本 agent 目检：符合男性青年/黑短发微乱/无须设定 |
| 正在修理的表 | v2 | `48a8d32a…` | **PASS** | 当代手表、表壳开启机芯外露（与回答一致） |
| 暖黄灯罩 | v2 | `48e50ca6…` | **PASS** | 做旧金属圆顶暖光（与回答一致） |
| 黄铜怀表 | v2 | `59ffe91d…` | **PASS** | 链条松盘于旁、无手指（与回答一致） |
| 老式煤油灯 | v1 | `70a47810…` | 待评审 | 前轮真实小样已验技术链路，美术评审未做 |
| 齿轮与镊子 | v2 | `cba5dbc3…` | 待评审 | |
| 旧船票 | v2 | `f7bf241b…` | 待评审 | |
| 旧船坞·夜 | v2 | `9f309677…` | 待评审 | |
| 海边石阶·黄昏 | v2 | `18723806…` | 待评审 | |
| 钟表铺（白昼） | v2 | `e26c9138…` | 待评审 | |

**已知障碍**：外部视觉工具（zai analyze_image）对部分图片（含周野人像、老式煤油灯等）稳定 30s 超时，疑似内容过滤；对另一些图片正常。并发调用也易超时。**可靠替代**：图片已在本地 `.zcode/tmp/candidates/`，让用户直接贴图目检，或一次一张重试、或换 `mcp__4_5v_mcp__analyze_image`。

## 4. 晋升/后续操作路径（与 UI 完全等价，供下一个 agent 执行）

1. 评审剩余 6 张（上表）。
2. 逐项晋升 PASS 候选：`POST /preparation/approval-grants {action:'promote', target_id:<candidate_id>}` → `POST /candidate-assets/{id}/promote {grant_id, snapshot, offline:false, evidence:'导演模式人工选用并晋升素材候选（用户授权 local-user 代理）。'}`。
3. 汇总决策后 `POST /projects/1b0dcb51…/material-preparation/confirm {plan_revision_id, expected_snapshot, decisions:[{requirement_id, strategy:'reuse_master', asset_id:<晋升产物>, entity_id, brief_note}…], visual_direction}`（strategy/asset_id 以晋升结果为准；未晋升项保持原样，不得假装 ready）。
4. `POST …/material-preparation/complete` → 读 `materials_ready`（服务端判定，页面不自称）。
5. 风格冻结（七字段；`visual_direction` 已有：contemporary coastal town / cool sea mist vs warm amber / low-key night one practical / photoreal worn metal damp wood）。
6. 正式分镜（真实 crew 链，5 角色 MiniMax；或长剧本 section 路径）→ H3 生产。
7. 全量回归 → 文档 → 从最终提交树 `pwsh -File scripts/build_test_package.ps1 -OutDir releases -Verify`。

## 5. 给下一个 Agent 的提示词

见下方代码块（可直接粘贴）。

```text
你在 F:\ClaudeWorkSpace\projects\shine-video-studio 继续导演模式建设的「受托素材轮→正式出片」阶段。用户已明确授权你以 local-user 身份做拟人化操作（评审候选、晋升、冻结风格、排分镜、启动 H3），一切决定如实写入文档与审计，不得伪造人工验收结果。

一、开工必读与现状核对
1. 先读 AGENTS.md（按顺序的权威文档）、docs/HANDOVER_20261105_DELEGATED_MATERIAL_ROUND.md（本交接）、docs/ACCEPTANCE_20261105_DIRECTOR_CLOSEOUT_REPORT.md。
2. 重查：git status/HEAD（应为本文档所在提交，分支 codex/director-closeout-20261105，未推送）、8133 栈健康（curl http://127.0.0.1:8133/health 与 /api/v1/system/status，期望 worker_online=true、runtime=ACTIVE）、素材状态（GET /api/v1/projects/1b0dcb51-62b3-472d-b804-a4ade1b71092/material-preparation）。
3. 真实栈数据目录 .local-acceptance/live-browser；写操作都要带 x-shine-local-action: 1。不要混用 8000/8143 栈。

二、立即要做：完成候选评审（12 项中已完成 6 项 PASS，见交接文档 §3 表格）
已 PASS：顾晚(b768b7e0)、林素(c2cb6819)、周野(5f6111e7)、正在修理的表(48a8d32a)、暖黄灯罩(48e50ca6)、黄铜怀表(59ffe91d)。
待评审 6 项：老式煤油灯(70a47810,v1)、齿轮与镊子(cba5dbc3,v2)、旧船票(f7bf241b,v2)、旧船坞·夜(9f309677,v2)、海边石阶·黄昏(18723806,v2)、钟表铺·白昼(e26c9138,v2)。
评审方法（重要，前一个 agent 在这里卡死）：大图已下载在 .zcode/tmp/candidates/<实体名>.png；也可 GET http://127.0.0.1:8133/api/v1/candidate-assets/<id>/media。外部视觉工具（zai analyze_image）对部分图片 30s 超时（疑似内容过滤），且并发必超时——一次只评一张；超时就换 mcp__4_5v_mcp__analyze_image 或让用户贴图后你直接目检。评审标准：道具=单物完整、无人手、无杂背景、无可读文字；场景=环境与设定一致；角色=单人、头完整、符合设定。判 FAIL 的项：把原因写进外观描述再走 regenerate（务必 questions: []，后端对带问题的 brief 会 422）。

三、评审通过后的链路（严格按 UI 等价的 API 路径，交接文档 §4 有完整 JSON 形状）
1. 逐项晋升：POST /api/v1/preparation/approval-grants {action:'promote',target_id} → POST /api/v1/candidate-assets/{id}/promote {grant_id,snapshot,offline:false,evidence:'导演模式人工选用并晋升素材候选（用户授权 local-user 代理）。'}。
2. confirm（decisions 里晋升项 strategy='reuse_master' + 新 asset_id，其余保持；visual_direction 用现值）→ POST …/material-preparation/complete → 核对 materials_ready=true（服务端说了算）。
3. 冻结风格（七字段全比对；visual_direction：contemporary coastal town / cool sea mist against warm amber practical light / low-key night with one visible practical source / photoreal, worn metal and damp wood）。
4. 出分镜：真实 crew 链（5 角色，MiniMax 路由已配好；SHINE_AGENT_STEP_DELAY_MS 默认 1200ms 别动）；短剧本走 /storyboard/generate，长剧本走 story-preparation 大纲→确认→分段（注意 R3 新语义：上一段末状态验证过后段免填承接事实）。
5. H3 正式生产：一键开拍走生产 series；渲染器受管启动，空闲即停属正常。语言合同、参考槽位、冻结合同不变。

四、收尾要求（每条都必须实跑）
1. 34fa3f7 之后的全量后端 pytest、Ruff、Alembic、前端 vitest/tsc/build（此前只跑过定向模块）。
2. 更新 docs/ACCEPTANCE_20261105_DIRECTOR_CLOSEOUT_REPORT.md（证据分级：IMPLEMENTED/OFFLINE_VERIFIED/REAL_QWEN_VERIFIED/REAL_H3_VERIFIED/HUMAN_REVIEW/BLOCKED；真实出片要写 run/候选/artifact id 与 sha）。
3. 从最终提交树重建安装包：pwsh -File scripts\build_test_package.ps1 -OutDir releases -Verify（当前 releases 里的 1daa65b 包已过期）。旧包保留。
4. git add 明确路径提交；不推送、不发 Release，除非用户明说。VLM 重校准仍 BLOCKED（正式项目库不在本机，钉住文件本轮未改）。
5. 任何没跑通的写「未验证」，不得口头声称完成。
```
