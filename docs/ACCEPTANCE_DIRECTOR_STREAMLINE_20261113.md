# 导演模式收束改造 · 验收报告（2026-11-13）

> 分级口径：工程验证 / 真实 H3 出片 / 人工艺术验收 三者分开判定。
> 本报告只陈述有证据的结论；无证据一律标「未验证」并给出复验步骤。

## 0. 基线与提交

- 主仓基线 `origin/main = 4aa70df`；功能分支 `feature/director-journey-streamline-20261113`
  起点 `b944b25`（main + 1 docs commit）。
- 独立 worktree：`F:/ClaudeWorkSpace/projects/shine-director-streamline-20261113`，
  未改动主工作区与原始生产数据。
- 本轮提交：`3a532cd feat(director): streamline the seven-stage journey into a convergent one-click path`
  （29 文件，+3586/−140）。
- `worker.py` 恢复零 diff：导演分镜任务钩子移入 `materialprep/story.py`
  （`run_pending_story_task` 尾部委托 `run_pending_director_storyboard`），
  **不触发** `client_review_identity` 重校准义务。

## 1. 实现摘要（①–⑦ 收束）

- ① 大纲 Markdown 直编 + 一次确认（`api_streamline` / `director/streamline.py` /
  `OutlineConfirmationPanel`）；疑问降为提示，确认自动登记当前疑问列表。
- ② 四输入（导演风格/影片类型/集数/每集秒数）分镜书；集数优先、整场切分、偏差如实写入
  `boundary_note`（`derive_episode_plan_by_count`）；分集与风格成对一次确认。
- ③ 一次后台分镜（常驻 Worker 逐场推进）+ 按集镜头表（成片秒/镜间切开接拍/镜内切点/H3 生成段）
  + 按集落镜/单集重试/暂停继续；坏格式先本地无损 salvage（`agents/crew_salvage.py`）。
- ④ 按已确认镜头去重素材清单（entity+variant，列出使用集/镜）+ 复用/上传/AI/跳过决策；
  新增 `skip` 策略（非 character 族，语义同 omit_prop）；只有明确选中才送素材中心。
- ⑤⑥⑦ 导演包 `episode_ids` 子集冻结 + journey 按集状态 + 包范围勾选 UI；
  就绪集可独立打包/生产/导出，另一集保持 pending。
- 五工种共享文学链调用预算（`compile_request` 一次渲染、`plan_call_budget` 60% 硬帽、
  `precompiled` 逐字发送），溢出在发送前具名拒绝。

## 2. 工程验证（已验证）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| Ruff | `python -m ruff check backend/shine backend/tests` | 全绿 |
| 前端类型 | `npx tsc --noEmit` | 0 |
| 前端测试 | `npm.cmd test` | 42 文件 270/270 |
| 前端构建 | `npm.cmd run build` | 0 |
| 新增回归 | `pytest backend/tests/test_director_streamline.py` | 16 通过 |
| F05 同意守卫 | `pytest test_four_stage_closeout.py -k f05` | 退出码 0（修复后） |
| 后端全量 | `pytest backend/tests -q` | **2470 通过，EXIT=0，0 失败** |

F05 修复说明：film.py 预算集成改传 `precompiled` 后，测试传输 double 读
`kwargs["system"]` 报 KeyError；已适配 mock 从 `precompiled` 读取同一字节，
**保留全部同意/NOT_SENT 断言**，非削弱测试。

## 3. 真实 H3 出片（未验证 · 有明确阻塞）

- 正常入口 `start_dev.ps1` 启动 API 8000 / Web 5173（均环回实测）。
- 为保护原始生产数据，复制旧库 `witch-main-20261111` →
  `.local-acceptance/streamline-20261113`。
- LLM 连接 + 结构化生成真实通过；权重 41GB 哈希匹配、0 项未通过。
- 环境恢复链已完成：目录迁移曾使服务验证证据陈旧
  （`SERVICE_VALIDATION_STALE_CONFIGURATION/STALE_RECIPE`），且验证守卫要求先停 Worker
  （`STOP_WORKER_BEFORE_SERVICE_VALIDATION`，`comfyops/service_validation.py:511`）。
  以启动器 `-NoWorker` 编排后重跑 `setup/services/validate` 返回 **READY（1281 节点，
  4 个 Ref2VA 方案核验通过；VLM/ASR 为 advisory 不阻断）**，`setup/runtime/activate` 成功
  （services_verified=true），带 Worker 重启后 `production_mode=READY`、worker 在线。
- 但 **本会话未执行两集真实旅程**，尚未产生任何真实 H3 attempt/artifact/成片；
  重启续作、不重拍证明均未执行。
- 人工艺术验收：未做（无真实出片）。

### 复验步骤（解除阻塞后执行）
1. 停 Worker 与受管实例（产品编排：RealProductionWizard「启用真实制作」或启动器收尾）。
2. `POST /api/v1/setup/services/validate`（body: project_id + 候选 recipe_sha256）。
3. `POST /api/v1/setup/runtime/activate`；重启栈，确认 `production_mode=READY`。
4. 新建作品导入小说 → ① 确认大纲 → ② 分镜书(2集) → ③ 生成分镜、第 2 集留一场 pending、
   确认第 1 集 → ④ 素材清单制作/回绑 → ⑤ 仅第 1 集打包 → ⑥ 真实 H3 → ⑦ 导出 MP4。
5. 记录第 1 集 Job ID/镜头 artifact SHA/成片 hash；重启续作第 2 集并导出；
   比对第 1 集三者不变（不重拍证明）。旧作品副本做兼容读取。

## 4. 可控测试（部分覆盖）

- 坏格式 salvage / 预算 / 按集落镜 / 单集重试 / 包范围：`test_director_streamline.py`（16）。
- 断流/死亡 Worker、重复提交幂等、UNKNOWN 核对/一次授权重发：既有
  `test_scene_dead_worker.py`、`test_story_unknown_resend.py`、`test_film_unknown_outcomes.py`、
  `test_story_outline_unblock.py` 等在全量中覆盖（暂 0 失败）。

## 5. URL 约束遵守

本会话带 URL 的调用仅本机环回 `127.0.0.1:8000/5173`（OS 端口表实测 PID 6780/11868，
curl 与快照 URL 交叉验证）与页内已保存 `127.0.0.1:8188`；测试夹具
`text.example.invalid` 为不可达占位。未使用 routify/临时代理，未用 WebFetch/搜索类 URL 工具。