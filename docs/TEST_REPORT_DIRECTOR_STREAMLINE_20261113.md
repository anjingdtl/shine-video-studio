# 导演模式收束改造 · 测试报告（2026-11-13）

> 口径：工程验证 / 真实 H3 出片 / 人工艺术验收 分开判定。无证据一律标「未验证」。
> 所有后端命令在仓库根目录执行（`backend/tests` 依赖根目录相对路径）。

## 0. 基线

- `origin/main = 4aa70df`；功能分支 `feature/director-journey-streamline-20261113`。
- 实现提交 `3a532cd`（29 文件 +3586/−140）；本报告与验收报告随附提交。
- `worker.py` 零 diff（钩子在 `materialprep/story.py`），不触发 `client_review_identity` 重校准。

## 1. 工程验证（已验证 · 全绿）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 静态检查 | `backend/.venv/Scripts/python.exe -m ruff check backend/shine backend/tests` | All checks passed |
| 前端类型 | `cd frontend && npx tsc --noEmit` | 退出码 0 |
| 前端测试 | `cd frontend && npm.cmd test` | 42 文件 **270/270** 通过 |
| 前端构建 | `cd frontend && npm.cmd run build` | 退出码 0 |
| 新增回归 | `python -m pytest backend/tests/test_director_streamline.py -q` | **16 通过** |
| 同意守卫 F05 | `python -m pytest backend/tests/test_four_stage_closeout.py -k f05 -q` | 退出码 0（修复后） |
| 后端全量 | `python -m pytest backend/tests -q` | **2470 选中 / 2470 通过，EXIT=0，0 失败** |

后端全量分级：unit=213 / integration=561 / full=1696（含默认提升），无 windows_only/media/external/gpu 维度被误选。

### F05 修复说明
film.py 预算集成改传 `precompiled` 后，`test_four_stage_closeout.py` 的传输 double 读
`kwargs["system"]` 抛 KeyError。已适配 mock 从 `precompiled` 读取同一渲染字节，
**保留全部同意/NOT_SENT/不重发断言**（非削弱）。修复后 F05 与全量均绿。

## 2. 可控测试覆盖

- 坏格式无损 salvage / 文学预算 / 按集落镜 / 单集重试 / 包范围子集：
  `test_director_streamline.py`（16 项，含 `test_crew_salvage_*`、`test_crew_chain_records_the_literary_budget`、
  `test_episode_scoped_package_freezes_only_ready_episodes`）。
- 断流/死亡 Worker、重复提交幂等、UNKNOWN 核对与一次授权重发：既有
  `test_scene_dead_worker.py`、`test_story_unknown_resend.py`、`test_film_unknown_outcomes.py`、
  `test_story_outline_unblock.py`、`test_material_scene_safety.py` 等，均在全量 2470 内通过。

## 3. 真实 H3 出片（未验证 · 环境已就绪，旅程未跑）

- 正常入口启动 API 8000 / Web 5173（环回实测）；旧库复制保护原始数据。
- 环境恢复链已完成：权重 41GB 哈希匹配 → 登记/验证 4 个 Ref2VA 方案 →
  停 Worker 后 `setup/services/validate` 返回 **READY（1281 节点，VLM/ASR 为 advisory 不阻断）** →
  `setup/runtime/activate` 成功（services_verified=true）→ 带 Worker 重启后 `production_mode=READY`。
- **本会话未执行两集真实旅程**（导入小说→首集 H3→导出→重启续第二集），故无真实
  attempt/artifact/成片证据；不重拍证明未做。人工艺术验收未做。
- 复验步骤见 `ACCEPTANCE_DIRECTOR_STREAMLINE_20261113.md` §3。

## 4. URL 约束遵守

带 URL 调用仅本机环回 `127.0.0.1:8000/5173`（OS 端口表实测 PID，curl 与快照 URL 交叉验证）
与页内已保存 `127.0.0.1:8188`；`git push` 用真实 origin
`https://github.com/anjingdtl/shine-video-studio.git`。测试夹具 `text.example.invalid` 为不可达占位。
未用 routify/临时代理，未用 WebFetch/搜索类 URL 工具。